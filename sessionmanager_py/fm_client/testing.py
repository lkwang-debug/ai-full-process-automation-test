"""fm_client 测试辅助层：typed 返回 + 事件收集 + 等推送 + pytest fixture。

给自动化测试人员的"糖衣"——**不改动 FmClient 本身**（仍是生产/开发用的协议门面），
这层在其之上提供：

  - `FmTestClient`：typed 返回（Room/Contact/UserEnter dataclass、int 字段自动解析）+ 绑定事件收集器
  - `EventCollector`：线程安全收集所有推送，提供 `wait_for_*` 等待方法（不用自己写 `_wait_for`）
  - typed 事件/数据对象：`Room`、`Contact`、`UserEnter`、`ChatMsg`
  - `wait_for(predicate, timeout)`：通用轮询

典型测试写法：
    client = FmTestClient(host, port)            # 自带 EventCollector 当 listener
    client.front_login("A7", "<configure-locally>", room_id=10000, room_node_id="396593")
    assert client.user_right_int == 3            # int，不用自己 int()
    client.mic_on()
    enters = client.wait_for_user_enter(timeout=5)
    assert any(e.user_id == 1001 for e in enters)
    rooms = client.list_rooms()                  # 返回 List[Room]，不是 raw dict
    assert any(r.meeting_type == "instant" for r in rooms)
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any, Callable, List, Optional

from . import conf as C
from . import front as F
from ._helpers import _as_list, _dept_list, _to_int
from .client import FmClient, FmListener, ReceivedEvent, cmdid_name
from .interaction_testing import InteractionTestOps, NotificationTimeout

_MISSING = object()


def wait_for(predicate: Callable[[], bool], timeout: float = 5.0,
             interval: float = 0.01) -> bool:
    """轮询直到 predicate() 真值或超时；返回最终 predicate()。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _polling_op(fields: dict):
    """6633 的 Operation 字段藏在 <VideoPolling> 子结构里，提取出来。"""
    vp = fields.get("VideoPolling") if isinstance(fields, dict) else None
    return vp.get("Operation") if isinstance(vp, dict) else None


# -------------------------------------------------------------- typed 对象 ---
@dataclass
class Room:
    """会议室列表里一个房间（typed）。"""
    room_id: int
    name: str
    meeting_type: Optional[str] = None        # fixed/reserved/instant（None=未推算）
    raw: dict = field(default_factory=dict)


@dataclass
class Contact:
    """一个联系人/成员（typed）。"""
    user_id: int
    name: str
    mobile: str = ""
    tel: str = ""
    email: str = ""
    dept_id: Optional[int] = None
    raw: dict = field(default_factory=dict)


@dataclass
class UserEnter:
    """6602 入会成员（typed）。"""
    user_id: int
    name: str
    right: int                                 # 1旁听/2出席/3主席
    raw: dict = field(default_factory=dict)


@dataclass
class ChatMsg:
    """6612 文字消息（typed）。"""
    src_user_id: int
    dst_user_id: int
    text: str
    msg_id: Optional[str] = None


# -------------------------------------------------------------- 事件收集器 ---
class EventCollector(FmListener):
    """线程安全收集所有业务推送；每个事件存进对应 deque，提供 wait_for_* 等待。

    所有回调在会话接收线程触发，内部用锁保护 deque。测试线程用 wait_for_* / snapshot 读。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._last_received_event_id = 0
        # 连接事件
        self.front_connected = threading.Event()
        self.conf_connected = threading.Event()
        self.disconnects: deque = deque()
        self.create_failures: deque = deque()
        # 业务推送（typed 或 dict）
        self.user_enters: deque = deque()       # [UserEnter]
        self.user_leaves: deque = deque()       # [user_id:str]
        self.user_states: deque = deque()       # [dict] 6609
        self.chats: deque = deque()             # [ChatMsg]
        self.kicks: deque = deque()             # [dict]
        self.room_closes: deque = deque()       # [dict]
        self.av_infos: deque = deque()          # [dict]
        self.conf_states: deque = deque()       # [dict] 6610
        self.recording_states: deque = deque()  # [int] 6610 的 ServerRecordStatus
        self.chair_rets: deque = deque()        # [dict]
        self.silents: deque = deque()           # [dict]
        self.call_states: deque = deque()       # [dict] 6745
        self.knocks: deque = deque()            # [dict]
        self.wait_open_doors: deque = deque()   # [int] 计数
        self.subtitles: deque = deque()         # [dict]
        self.send_files: deque = deque()        # [dict]
        self.window_states: deque = deque()     # [dict]
        self.role_perms: deque = deque()        # [dict]
        self.system_messages: deque = deque()   # [dict] 6618
        self.group_enters: deque = deque()      # [dict] 6624
        self.main_enters: deque = deque()       # [dict] 6626
        self.subtitle_closes: deque = deque()   # [dict] 6650
        self.room_data_updates: deque = deque()  # [dict] 6619
        self.video_subtitles: deque = deque()   # [dict] 6622
        self.chat_check_results: deque = deque()  # [dict] 6629
        self.chat_check_requests: deque = deque()  # complete 6612 NeedCheck=1
        self.interactive_languages: deque = deque()  # 6640
        self.room_languages: deque = deque()  # 6641 pages, including End
        self.remote_av_requests: deque = deque()  # 6642
        self.remote_av_results: deque = deque()  # 6643
        self.role_permission_changes: deque = deque()  # [dict] 6632
        self.user_permission_changes: deque = deque()  # [dict] 6635
        self.system_notifies: deque = deque()   # [dict] 6636
        self.server_record_errors: deque = deque()  # [{error,fields}] 6637 / legacy 6636
        self.waiting_user_lists: deque = deque()  # 6647 pages, including UserList.End
        self.multicast_states: deque = deque()  # [dict] 6651
        self.face_rollcall_starts: deque = deque()  # [dict] 6657
        self.face_rollcall_results: deque = deque()  # [dict] 6658
        self.face_rollcall_cancels: deque = deque()  # [dict] 6659
        self.relay_messages: deque = deque()    # [dict] 6700
        self.chat_recalls: deque = deque()      # [dict]
        self.web_kicks: deque = deque()
        self.web_room_closes: deque = deque()
        self.web_notifies: deque = deque()
        self.received: deque = deque()       # 统一日志 [ReceivedEvent]：每条下行（回复+推送）
        self.polling_states: deque = deque()  # [dict] 6633 视频轮询
        self.multi_data_sharers: deque = deque()
        self.feature_users: deque = deque()
        self.product_features: deque = deque()
        self.self_state_requests: deque = deque()
        self.h5_votes: deque = deque()
        self.group_user_states: deque = deque()
        self.waiting_rooms: deque = deque()
        self.user_custom_data: deque = deque()

    def on_multi_data_sharer(self, fields) -> None:
        with self._lock:
            self.multi_data_sharers.append(dict(fields))

    def on_feature_users(self, fields) -> None:
        with self._lock:
            self.feature_users.append(dict(fields))

    def on_product_features(self, fields) -> None:
        with self._lock:
            self.product_features.append(dict(fields))

    def on_modify_self_state(self, fields) -> None:
        with self._lock:
            self.self_state_requests.append(dict(fields))

    def on_h5_vote(self, fields) -> None:
        with self._lock:
            self.h5_votes.append(dict(fields))

    def on_group_user_state(self, fields) -> None:
        with self._lock:
            self.group_user_states.append(dict(fields))

    def on_waiting_room(self, fields) -> None:
        with self._lock:
            self.waiting_rooms.append(dict(fields))

    def on_user_custom_data(self, fields) -> None:
        with self._lock:
            self.user_custom_data.append(dict(fields))

    # ---- 连接事件 ----
    def on_front_connected(self) -> None:
        self.front_connected.set()

    def on_conf_connected(self) -> None:
        self.conf_connected.set()

    def on_create_failed(self, which: str, reason: str) -> None:
        with self._lock:
            self.create_failures.append({"which": which, "reason": reason})

    def on_disconnected(self, which: str) -> None:
        with self._lock:
            self.disconnects.append(which)

    # ---- 业务推送（typed 化常用的几个）----
    def on_user_enter(self, users) -> None:
        with self._lock:
            for u in users:
                self.user_enters.append(UserEnter(
                    user_id=_to_int(u.get("UserID")),
                    name=u.get("DisplayName") or u.get("Name") or "",
                    right=_to_int(u.get("Right")),
                    raw=dict(u),
                ))

    def on_user_leave(self, user_id) -> None:
        with self._lock:
            self.user_leaves.append(user_id)

    def on_user_state_changed(self, fields) -> None:
        with self._lock:
            self.user_states.append(dict(fields))

    def on_chat(self, src_user_id, dst_user_id, text, msg_id=None) -> None:
        with self._lock:
            self.chats.append(ChatMsg(
                src_user_id=_to_int(src_user_id), dst_user_id=_to_int(dst_user_id),
                text=text or "", msg_id=msg_id))

    def on_user_kicked(self, kick_id, user_id, fields) -> None:
        with self._lock:
            self.kicks.append({"kick_id": kick_id, "user_id": user_id, "fields": dict(fields)})

    def on_room_closed(self, reason, description, fields) -> None:
        with self._lock:
            self.room_closes.append({"reason": reason, "description": description, "fields": dict(fields)})

    def on_av_info(self, fields) -> None:
        with self._lock:
            self.av_infos.append(dict(fields))

    def on_conf_state_changed(self, fields) -> None:
        with self._lock:
            self.conf_states.append(dict(fields))
            if isinstance(fields, dict) and "ServerRecordStatus" in fields:
                self.recording_states.append(_to_int(fields.get("ServerRecordStatus")))

    def on_chair_right_ret(self, fields) -> None:
        with self._lock:
            self.chair_rets.append(dict(fields))

    def on_silent(self, fields) -> None:
        with self._lock:
            self.silents.append(dict(fields))

    def on_call_state_notify(self, fields) -> None:
        with self._lock:
            self.call_states.append(dict(fields))

    def on_knock(self, user_id, display_name, fields) -> None:
        with self._lock:
            self.knocks.append({"user_id": user_id, "display_name": display_name, "fields": dict(fields)})

    def on_wait_open_door(self) -> None:
        with self._lock:
            self.wait_open_doors.append(1)

    def on_subtitle(self, fields) -> None:
        with self._lock:
            self.subtitles.append(dict(fields))

    def on_send_file(self, src_user_id, file_info, fields) -> None:
        with self._lock:
            self.send_files.append({"src_user_id": src_user_id, "file_info": file_info, "fields": dict(fields)})

    def on_window_state(self, fields) -> None:
        with self._lock:
            self.window_states.append(dict(fields))

    def on_video_polling(self, fields) -> None:
        with self._lock:
            self.polling_states.append(dict(fields))

    def on_role_permission(self, roles, fields) -> None:
        with self._lock:
            self.role_perms.append({"roles": list(roles) if isinstance(roles, list) else [roles],
                                    "fields": dict(fields)})

    def on_system_message(self, fields) -> None:
        with self._lock:
            self.system_messages.append(dict(fields))

    def on_group_enter(self, fields) -> None:
        with self._lock:
            self.group_enters.append(dict(fields))

    def on_main_enter(self, fields) -> None:
        with self._lock:
            self.main_enters.append(dict(fields))

    def on_subtitle_closed(self, fields) -> None:
        with self._lock:
            self.subtitle_closes.append(dict(fields))

    def on_room_data(self, items, fields) -> None:
        with self._lock:
            self.room_data_updates.append({
                "items": list(items) if isinstance(items, list) else [items],
                "fields": dict(fields),
            })

    def on_video_subtitle(self, fields) -> None:
        with self._lock:
            self.video_subtitles.append(dict(fields))

    def on_chat_check_result(self, fields) -> None:
        with self._lock:
            self.chat_check_results.append(dict(fields))

    def on_chat_check_request(self, fields) -> None:
        with self._lock:
            self.chat_check_requests.append(dict(fields))

    def on_interactive_language(self, fields) -> None:
        with self._lock:
            self.interactive_languages.append(dict(fields))

    def on_room_languages(self, fields) -> None:
        with self._lock:
            self.room_languages.append(dict(fields))

    def on_remote_av_request(self, fields) -> None:
        with self._lock:
            self.remote_av_requests.append(dict(fields))

    def on_remote_av_result(self, fields) -> None:
        with self._lock:
            self.remote_av_results.append(dict(fields))

    def on_role_permission_changed(self, fields) -> None:
        with self._lock:
            self.role_permission_changes.append(dict(fields))

    def on_user_permission_changed(self, user_id, fields) -> None:
        with self._lock:
            self.user_permission_changes.append({
                "user_id": user_id, "fields": dict(fields)})

    def on_system_notify(self, fields) -> None:
        with self._lock:
            self.system_notifies.append(dict(fields))

    def on_server_record_error(self, error, fields) -> None:
        with self._lock:
            self.server_record_errors.append({
                "error": _to_int(error), "fields": dict(fields)})

    def on_waiting_user_list(self, fields) -> None:
        with self._lock:
            self.waiting_user_lists.append(dict(fields))

    def on_multicast_state(self, multicast, fields) -> None:
        with self._lock:
            self.multicast_states.append({
                "multicast": multicast, "fields": dict(fields)})

    def on_face_rollcall_started(self, fields) -> None:
        with self._lock:
            self.face_rollcall_starts.append(dict(fields))

    def on_face_rollcall_result(self, fields) -> None:
        with self._lock:
            self.face_rollcall_results.append(dict(fields))

    def on_face_rollcall_cancelled(self, fields) -> None:
        with self._lock:
            self.face_rollcall_cancels.append(dict(fields))

    def on_relay_message(self, messages, fields) -> None:
        with self._lock:
            self.relay_messages.append({
                "messages": list(messages) if isinstance(messages, list) else [messages],
                "fields": dict(fields),
            })

    def on_chat_recalled(self, fields) -> None:
        with self._lock:
            self.chat_recalls.append(dict(fields))

    def on_web_kicked(self, user_id, fields) -> None:
        with self._lock:
            self.web_kicks.append({"user_id": user_id, "fields": dict(fields)})

    def on_web_room_closed(self, fields) -> None:
        with self._lock:
            self.web_room_closes.append(dict(fields))

    def on_web_notify(self, fields) -> None:
        with self._lock:
            self.web_notifies.append(dict(fields))

    def on_raw_event(self, event: "ReceivedEvent") -> None:
        """统一下行日志：每条回复/推送都记一份（含原始 XML、cmdid、时间）。"""
        with self._condition:
            if event.event_id <= self._last_received_event_id:
                event = replace(event, event_id=self._last_received_event_id + 1)
            self.received.append(event)
            self._last_received_event_id = event.event_id
            self._condition.notify_all()

    # ---- 读取 / 等待 ----
    def snapshot(self, dq: deque) -> list:
        """线程安全拷贝某事件 deque 的当前内容（快照，断言用）。"""
        with self._lock:
            return list(dq)

    def mark(self) -> int:
        """返回当前统一事件游标；后续等待传 ``after=mark`` 可排除历史事件。"""
        with self._lock:
            return self._last_received_event_id

    def snapshot_received(self, *, after: int = 0) -> List[ReceivedEvent]:
        """返回游标之后的统一下行事件快照。"""
        with self._lock:
            return [event for event in self.received if event.event_id > after]

    @staticmethod
    def _read_path(value: Any, path: str) -> Any:
        current = value
        for key in path.split("."):
            if isinstance(current, dict):
                if key in current:
                    current = current[key]
                elif key == "0":
                    # XML 中可重复节点只有一个时会被解析成 dict；把它视为
                    # 单元素列表，使 Foo.0.Bar 同时兼容单个和多个节点。
                    continue
                else:
                    return _MISSING
            elif isinstance(current, (list, tuple)) and key.isdigit():
                index = int(key)
                if index >= len(current):
                    return _MISSING
                current = current[index]
            elif hasattr(current, key):
                current = getattr(current, key)
            else:
                return _MISSING
        return current

    @classmethod
    def _matches_fields(cls, fields: dict, expected: Optional[dict]) -> bool:
        if not expected:
            return True
        for path, expected_value in expected.items():
            actual = cls._read_path(fields, path)
            if actual is _MISSING or str(actual) != str(expected_value):
                return False
        return True

    @classmethod
    def _matches_received(
        cls,
        event: ReceivedEvent,
        *,
        source: Optional[str],
        cmdid: Optional[int],
        kind: Optional[str],
        match: Optional[dict],
    ) -> bool:
        return (
            (source is None or event.source == source)
            and (cmdid is None or event.cmdid == cmdid)
            and (kind is None or event.kind == kind)
            and cls._matches_fields(event.fields, match)
        )

    def wait_event(
        self,
        *,
        after: int = 0,
        source: Optional[str] = None,
        cmdid: Optional[int] = None,
        kind: Optional[str] = None,
        match: Optional[dict] = None,
        timeout: float = 5.0,
    ) -> Optional[ReceivedEvent]:
        """等待游标之后第一条满足来源、cmdid、类型和字段条件的统一事件。"""
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for event in self.received:
                    if event.event_id > after and self._matches_received(
                        event, source=source, cmdid=cmdid, kind=kind, match=match
                    ):
                        return event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def assert_no_event(
        self,
        *,
        after: int = 0,
        source: Optional[str] = None,
        cmdid: Optional[int] = None,
        kind: Optional[str] = None,
        match: Optional[dict] = None,
        duration: float = 1.0,
    ) -> None:
        """断言指定时间内游标之后没有出现匹配事件。"""
        event = self.wait_event(
            after=after,
            source=source,
            cmdid=cmdid,
            kind=kind,
            match=match,
            timeout=duration,
        )
        if event is not None:
            raise AssertionError(
                "unexpected received event: "
                f"event_id={event.event_id}, source={event.source}, "
                f"cmdid={event.cmdid}, kind={event.kind}, fields={event.fields}"
            )

    def wait_until(self, predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
        """通用等待：轮询 predicate()（基于 snapshot）。"""
        return wait_for(predicate, timeout)

    def _wait_deque(self, dq: deque, predicate: Optional[Callable[[Any], bool]] = None,
                    timeout: float = 5.0) -> list:
        pred = predicate or (lambda _x: True)
        ok = wait_for(lambda: any(pred(x) for x in self.snapshot(dq)), timeout)
        return [x for x in self.snapshot(dq) if pred(x)] if ok else []

    # 便捷 wait_for_*（常用推送）
    def wait_front_connected(self, timeout: float = 5.0) -> bool:
        return self.front_connected.wait(timeout)

    def wait_conf_connected(self, timeout: float = 5.0) -> bool:
        return self.conf_connected.wait(timeout)

    def wait_for_user_enter(self, user_id: Optional[int] = None, timeout: float = 5.0) -> List[UserEnter]:
        pred = None if user_id is None else (lambda e: e.user_id == user_id)
        return self._wait_deque(self.user_enters, pred, timeout)

    def wait_for_user_leave(self, user_id: Optional[str] = None, timeout: float = 5.0) -> list:
        pred = None if user_id is None else (lambda uid: str(uid) == str(user_id))
        return self._wait_deque(self.user_leaves, pred, timeout)

    def wait_for_kicked(self, user_id: Optional[int] = None, timeout: float = 5.0) -> List[dict]:
        """等被踢事件（6606）；user_id 指定则等该用户被踢。"""
        pred = None if user_id is None else (lambda d: str(d.get("user_id")) == str(user_id))
        return self._wait_deque(self.kicks, pred, timeout)

    def wait_for_user_state(self, user_id: Optional[int] = None, timeout: float = 5.0) -> List[dict]:
        pred = None if user_id is None else (lambda f: _to_int(f.get("UserID")) == user_id)
        return self._wait_deque(self.user_states, pred, timeout)

    def wait_for_chat(self, text: Optional[str] = None, src: Optional[int] = None,
                      timeout: float = 5.0) -> List[ChatMsg]:
        pred = None
        if text is not None or src is not None:
            def pred(m: ChatMsg) -> bool:
                if text is not None and m.text != text:
                    return False
                if src is not None and m.src_user_id != src:
                    return False
                return True
        return self._wait_deque(self.chats, pred, timeout)

    def wait_for_recording_state(self, state: int, timeout: float = 5.0) -> bool:
        """等录制状态变更（6610 的 ServerRecordStatus == state）。"""
        return wait_for(lambda: state in self.snapshot(self.recording_states), timeout)

    def wait_for_conf_state(self, timeout: float = 5.0) -> List[dict]:
        return self._wait_deque(self.conf_states, None, timeout)

    def wait_for_call_state(self, call_state: Optional[int] = None, timeout: float = 5.0) -> List[dict]:
        pred = None if call_state is None else (lambda f: _to_int(f.get("CallState")) == call_state)
        return self._wait_deque(self.call_states, pred, timeout)

    def wait_for_subtitle(self, text: Optional[str] = None, timeout: float = 5.0) -> List[dict]:
        pred = None if text is None else (lambda f: f.get("Data") == text)
        return self._wait_deque(self.subtitles, pred, timeout)

    def wait_for_knock(self, user_id: Optional[int] = None, timeout: float = 5.0) -> List[dict]:
        pred = None if user_id is None else (lambda d: _to_int(d.get("user_id")) == user_id)
        return self._wait_deque(self.knocks, pred, timeout)

    def wait_for_send_file(self, timeout: float = 5.0) -> List[dict]:
        return self._wait_deque(self.send_files, None, timeout)

    def wait_for_polling(self, operation: Optional[int] = None, timeout: float = 5.0) -> List[dict]:
        """等视频轮询下行(6633)；operation 指定 POLLING_NOTIFY_CHANGE/STOP。"""
        pred = None if operation is None else (lambda f: _to_int(_polling_op(f)) == operation)
        return self._wait_deque(self.polling_states, pred, timeout)

    def wait_for_server_record_error(
        self, error: Optional[int] = None, timeout: float = 5.0,
    ) -> List[dict]:
        """等待 6637 录制异常（兼容旧版 6636）；error 省略时匹配任意错误。"""
        pred = None if error is None else (lambda item: item.get("error") == error)
        return self._wait_deque(self.server_record_errors, pred, timeout)

    def wait_for_cmdid(
        self,
        cmdid: int,
        timeout: float = 5.0,
        *,
        after: int = 0,
        source: Optional[str] = None,
    ) -> List[ReceivedEvent]:
        """等指定 cmdid 的下行（从统一 received 日志）。新下行 Notify 没有专用 wait_for_* 时用这个。"""
        matched = self.wait_event(
            after=after, source=source, cmdid=cmdid, timeout=timeout)
        if matched is None:
            return []
        return [
            event for event in self.snapshot_received(after=after)
            if event.cmdid == cmdid and (source is None or event.source == source)
        ]

    def clear(self) -> None:
        """清空所有事件（测试分段断言用）。"""
        with self._lock:
            for name, dq in self.__dict__.items():
                if isinstance(dq, deque):
                    dq.clear()


# -------------------------------------------------------------- 测试客户端 ---
class FmTestClient(InteractionTestOps, FmClient):
    """FmClient 的测试糖衣：自带 EventCollector、typed 返回、wait_for_* 代理。

    所有 FmClient 方法仍可用（继承）；额外提供 typed 便捷方法与事件等待。
    """

    def __init__(self, *args, collector: Optional[EventCollector] = None, **kwargs) -> None:
        coll = collector or EventCollector()
        super().__init__(*args, listener=coll, **kwargs)
        self.events: EventCollector = coll

    @property
    def collector(self) -> EventCollector:
        return self.events

    # ---- typed 状态访问（int 字段自动解析，不用自己 int()）----
    @property
    def user_right_int(self) -> Optional[int]:
        return _to_int(self.user_right) if self.user_right is not None else None

    @property
    def local_user_id_int(self) -> Optional[int]:
        return _to_int(self.local_user_id) if self.local_user_id is not None else None

    @property
    def room_user_id_int(self) -> Optional[int]:
        return _to_int(self.room_user_id) if self.room_user_id is not None else None

    # ---- typed 返回的便捷方法 ----
    def list_rooms(self, *, classified: bool = True,
                   is_get_all: int = 1, is_get_desc: int = 0,
                   timeout: Optional[float] = None) -> List[Room]:
        """拉房间列表，返回 typed Room 列表（room_id 为 int，meeting_type 已推算）。"""
        rep = (self.front_roomlist_classified(is_get_all=is_get_all, is_get_desc=is_get_desc, timeout=timeout)
               if classified else self.front_roomlist(is_get_all=is_get_all, is_get_desc=is_get_desc, timeout=timeout))
        rooms = []
        for r in _as_list(rep.get("RoomInfo")):
            rooms.append(Room(
                room_id=_to_int(r.get("RoomID")),
                name=r.get("Name") or "",
                meeting_type=(r.get("MeetingType") if classified else F.infer_meeting_type(r)),
                raw=dict(r),
            ))
        return rooms

    def offline_contacts(self, *, timeout: Optional[float] = None) -> List[Contact]:
        """离线/历史成员 → typed Contact 列表。"""
        def _opt(v):
            return v if v not in (None, "") else None
        return [Contact(
            user_id=_to_int(u.get("UserID")),
            name=u.get("DisplayName") or u.get("Name") or "",
            mobile=u.get("Mobile") or "",
            tel=u.get("Tel") or "",
            email=u.get("EMail") or "",
            dept_id=_opt(_to_int(u.get("DeptID"))) if u.get("DeptID") not in (None, "") else None,
            raw=dict(u),
        ) for u in self.conf_get_offline_users(timeout=timeout)]

    def contacts_snapshot(self, *, timeout: Optional[float] = None) -> dict:
        """通讯录快照：{depts, local_user, offline_contacts(typed)}。"""
        raw = self.conf_get_contacts(timeout=timeout)
        return {
            "depts": _dept_list(raw.get("depts")),
            "local_user": raw.get("local_user"),
            "offline": self.offline_contacts(timeout=timeout),
        }

    # ---- 事件等待代理（转发到 EventCollector，少写一层）----
    def wait_for_user_enter(self, user_id: Optional[int] = None, timeout: float = 5.0) -> List[UserEnter]:
        return self.events.wait_for_user_enter(user_id, timeout)

    def wait_for_chat(self, text: Optional[str] = None, src: Optional[int] = None,
                      timeout: float = 5.0) -> List[ChatMsg]:
        return self.events.wait_for_chat(text, src, timeout)

    def wait_for_user_state(self, user_id: Optional[int] = None, timeout: float = 5.0) -> List[dict]:
        return self.events.wait_for_user_state(user_id, timeout)

    def wait_for_recording_state(self, state: int, timeout: float = 5.0) -> bool:
        return self.events.wait_for_recording_state(state, timeout)

    def wait_for_call_state(self, call_state: Optional[int] = None, timeout: float = 5.0) -> List[dict]:
        return self.events.wait_for_call_state(call_state, timeout)

    def wait_for_server_record_error(
        self, error: Optional[int] = None, timeout: float = 5.0,
    ) -> List[dict]:
        return self.events.wait_for_server_record_error(error, timeout)


# ==============================================================
# JSON 配置化（多账号 / 多端测试）
#
# 配置文件格式（JSON）：
#   {
#     "host": "192.168.7.154", "port": 1089,
#     "room_id": 10000, "room_node_id": "396593", "room_app_id": 11,
#     "request_timeout": 8.0, "conf_protocol_version": "2.0",
#     "initialization_wait": 2.0,
#     "accounts": [
#       {"username": "A7", "password": "<configure-locally>"},
#       {"username": "B1", "password": "xxxxxx"}
#     ]
#   }
# host/port/room_id 必填，其余可选（有默认）。accounts 里每个账号 = 一个模拟端身份。
# ==============================================================

def load_config(path: Optional[str] = None) -> dict:
    """读 JSON 测试配置。path 省略则取环境变量 FM_CONFIG。"""
    path = path or os.environ.get("FM_CONFIG")
    if not path:
        raise FileNotFoundError("未指定配置：传 path 或设环境变量 FM_CONFIG=<配置.json>")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def connect_client(config: dict, account: Optional[dict] = None, *,
                   account_index: Optional[int] = None,
                   user_name: Optional[str] = None,
                   conf_login: bool = True) -> "FmTestClient":
    """按 config 建一个 FmTestClient 并登录入会（front_login + conf_login）。

    account={username,password} 直接指定；或 account_index 取 config["accounts"][i]；
    都不给则用 accounts[0]。conf_login=False 时只登录 Front 不进会场。"""
    if account is None:
        accounts = config.get("accounts") or []
        if not accounts:
            raise ValueError("config 里没有 accounts")
        idx = 0 if account_index is None else account_index
        account = accounts[idx]
    client = FmTestClient(
        config["host"], int(config.get("port", 1089)),
        request_timeout=float(config.get("request_timeout", 8.0)),
        conf_protocol_version=config.get("conf_protocol_version", C.CONF_VER_CS))
    try:
        client.front_login(
            account["username"], account.get("password", ""),
            room_id=int(config["room_id"]),
            room_node_id=str(config.get("room_node_id", "1")),
            room_app_id=int(config.get("room_app_id", 11)))
        if conf_login:
            options = dict(config.get("conf_login_options") or {})
            options.update(account.get("conf_login_options") or {})
            if user_name is not None:
                options["user_name"] = user_name
            options.setdefault("user_name", account["username"])
            client.conf_login(**options)
    except BaseException:
        client.close()
        raise
    return client


def connect_pair(config: dict, *, conf_login: bool = True):
    """便捷：用 accounts[0] 当操作端 A、accounts[1] 当观察端 B，都登录入会。返回 (a, b)。"""
    if len(config.get("accounts", [])) < 2:
        raise ValueError("config.accounts 至少要 2 个账号（操作端 + 观察端）")
    a = None
    b = None
    try:
        a = connect_client(config, account_index=0, conf_login=conf_login)
        b = connect_client(config, account_index=1, conf_login=conf_login)
        wait = float(config.get("initialization_wait", 0)) if conf_login else 0
        if wait < 0:
            raise ValueError("config.initialization_wait must be >= 0")
        if wait:
            time.sleep(wait)
        return a, b
    except Exception:
        if b is not None:
            b.close()
        if a is not None:
            a.close()
        raise
