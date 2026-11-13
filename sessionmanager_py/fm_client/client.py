"""FmClient —— 4u 会议客户端编排核心：登录 → 入会 → 会中。

本模块只放 **共享状态 + 会话机械 + 推送分发**：
  - 两条 framecore 会话（Front app=20 / Conf app=11）的建立 / 收发 / 重连
  - 请求/响应配对（Front +1、Conf +100/+1）、超时与错误码上抛
  - 推送分发（6602/6605/6609/6612/... → FmListener）
  - 登录态：local_user_id / room_id / token / room_srv_node_id ...

**对接不同服务的业务方法** 按域/功能拆到独立 mixin（见 ``ops/``），FmClient 继承它们，
对外仍是统一的 ``client.front_login(...)`` / ``client.conf_invite_call(...)`` 平铺 API：

    client = FmClient("10.0.0.1", 1089, MyListener())
    client.front_login("guest1", "1111", room_id=100001, room_node_id="1")
    client.conf_login()
    client.chat("hello")
    client.close()
"""
from __future__ import annotations

import copy
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Tuple

from framecore_session import (
    SessionConfig,
    SessionListener,
    SessionState,
    TcpReliableSession,
    XmlMsgIOPack,
)

from . import conf as C
from . import entrance as E
from . import front as F
from .errcodes import ConfLoginWaiting, FmError, is_success
from .xmlcmd import DEFAULT_ENCODING, XmlCommand
from ._helpers import _as_list, _to_int
from .http_api import WebApiClient
from .ops.front_ops import FrontOps
from .ops.conf_ops import ConfOps
from .ops.contacts_ops import ContactsOps
from .ops.call_ops import CallOps
from .ops.meeting_ops import MeetingOps
from .ops.meeting_control_ops import MeetingControlOps
from .ops.entrance_ops import EntranceOps
from .ops.capability_ops import CapabilityOps
from .ops.interaction_ops import InteractionOps

logger = logging.getLogger("fm_client")

FRONT = "front"
CONF = "conf"

_PROTOCOL_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")


def _normalize_protocol_version(value, *, name: str) -> str:
    version = str(value).strip()
    if (not version or not _PROTOCOL_VERSION_RE.fullmatch(version)
            or int(version.split(".", 1)[0]) < 1):
        raise ValueError(f"{name} must be a positive numeric version such as '2.0'")
    return version


# ---------------------------------------------------------------- 统一下行事件
def _build_cmdid_name_map() -> dict:
    """从各域常量反查 cmdid→名字（过滤 <1000 的 APP_ID/AV_OP/STATE 等小常量）。"""
    names = {}
    for mod in (F, C, E):
        for attr, val in vars(mod).items():
            if attr.startswith("_") or not isinstance(val, int) or val < 1000:
                continue
            names.setdefault(val, attr)
    return names


_CMDID_NAMES = _build_cmdid_name_map()


def cmdid_name(cmdid: int) -> str:
    """cmdid → 可读名字（如 6609→USERSTATECHANGED），未知返回 CMD_<id>。"""
    return _CMDID_NAMES.get(cmdid, f"CMD_{cmdid}")


@dataclass
class ReceivedEvent:
    """一条服务器下行消息的统一记录（推送 + 回复都记），便于断言与 Allure 展示。

    前六个字段保持最初版本的顺序，新增字段均带默认值，兼容已有的位置参数和
    关键字参数构造。每条 incoming（包括无法解析的消息）都会经
    ``FmListener.on_raw_event`` 投出一份。
    """
    source: str            # "front"/"conf"/"entrance"
    cmdid: Optional[int]
    name: str              # cmdid_name(cmdid)
    fields: dict
    raw_xml: bytes
    received_at: float
    event_id: int = 0
    kind: str = "unknown"  # response/notify/unknown/decode_error
    seq: Optional[int] = None
    request_cmdid: Optional[int] = None
    received_monotonic: float = 0.0
    decode_error: Optional[str] = None
    raw_payload: bytes = b""


@dataclass(frozen=True)
class SentCommand:
    """一条已经交给会话发送队列的上行业务XML，只读用于测试取证。"""
    source: str
    cmdid: int
    name: str
    fields: dict
    raw_xml: bytes
    sent_at: float
    event_id: int
    ver: str
    seq: Optional[int] = None
    sent_monotonic: float = 0.0


class FmListener:
    """业务事件回调（在会话接收线程上调用，实现须非阻塞）。"""

    def on_front_connected(self) -> None:
        pass

    def on_conf_connected(self) -> None:
        pass

    def on_create_failed(self, which: str, reason: str) -> None:
        pass

    def on_disconnected(self, which: str) -> None:
        pass

    def on_user_enter(self, users: List[dict]) -> None:
        """6602：在线成员列表 / 新成员入会。每个 user 是字段字典。"""
        pass

    def on_user_leave(self, user_id) -> None:
        """6605：有人离开。"""
        pass

    def on_user_state_changed(self, fields: dict) -> None:
        """6609：某人音视频/发言/静音等状态变更（含 UserID 与变更字段）。"""
        pass

    def on_chat(self, src_user_id, dst_user_id, text, msg_id=None) -> None:
        """6612：收到文字消息（dst_user_id=0 为公聊）。"""
        pass

    def on_user_kicked(self, kick_id, user_id, fields: dict) -> None:
        """6606：有人被踢（kick_id=踢人者，user_id=被踢者）。"""
        pass

    def on_room_closed(self, reason, description, fields: dict) -> None:
        """6607：房间被关闭。"""
        pass

    def on_av_info(self, fields: dict) -> None:
        """6608：设备上报通知。"""
        pass

    def on_conf_state_changed(self, fields: dict) -> None:
        """6610：会议状态变更（Lock/EnableChat/EnableRecord 等）。"""
        pass

    def on_chair_right_ret(self, fields: dict) -> None:
        """6611：申请/放弃主持人失败返回（成功走 on_user_state_changed 改 Right）。"""
        pass

    def on_silent(self, fields: dict) -> None:
        """6614：全场静音通知。"""
        pass

    def on_call_state_notify(self, fields: dict) -> None:
        """6745：邀请呼叫的状态通知（CallState/ReleaseCause/CalleeNum/CallType）。"""
        pass

    def on_knock(self, user_id, display_name, fields: dict) -> None:
        """6604：有人敲门（主持人收）。"""
        pass

    def on_wait_open_door(self) -> None:
        """6617：你正在等待主持人开门（房间锁定时敲门端收）。"""
        pass

    def on_subtitle(self, fields: dict) -> None:
        """6615：会场字幕通知（Duration/Data）。"""
        pass

    def on_send_file(self, src_user_id, file_info, fields: dict) -> None:
        """6616：他人给你发文件通知。"""
        pass

    def on_window_state(self, fields: dict) -> None:
        """6613：视图/布局状态转发（Mode/VideoRelayID/Video...）。"""
        pass

    def on_role_permission(self, roles: list, fields: dict) -> None:
        """6631：角色权限配置（roles=list of {RoleCode, Permissions...}）。"""
        pass

    def on_system_message(self, fields: dict) -> None:
        """6618：系统文字/窗口/WEB/会议主题消息。"""
        pass

    def on_group_enter(self, fields: dict) -> None:
        """6624：服务器通知进入分组会议（含 Token/SrvAddr）。"""
        pass

    def on_main_enter(self, fields: dict) -> None:
        """6626：服务器通知返回主会议。"""
        pass

    def on_subtitle_closed(self, fields: dict) -> None:
        """6650：会场字幕已关闭。"""
        pass

    def on_room_data(self, items: list, fields: dict) -> None:
        """6619：会场/课堂 DataItem 状态更新。"""
        pass

    def on_video_subtitle(self, fields: dict) -> None:
        """6622：视频画面日期时间/用户名字幕参数。"""
        pass

    def on_chat_check_result(self, fields: dict) -> None:
        """6629：聊天消息审批结果。"""
        pass

    def on_chat_check_request(self, fields: dict) -> None:
        """6612 NeedCheck=1; preserves serial, original sender and Font."""
        pass

    def on_interactive_language(self, fields: dict) -> None:
        """6640 user interactive-language notification."""
        pass

    def on_room_languages(self, fields: dict) -> None:
        """6641 language configuration page; End terminates the initial list."""
        pass

    def on_remote_av_request(self, fields: dict) -> None:
        """6642 request addressed to this terminal; never automatically accepted."""
        pass

    def on_remote_av_result(self, fields: dict) -> None:
        """6643 target terminal's consent/refusal result."""
        pass

    def on_role_permission_changed(self, fields: dict) -> None:
        """6632：角色功能权限变更。"""
        pass

    def on_user_permission_changed(self, user_id, fields: dict) -> None:
        """6635：指定用户功能权限变更。"""
        pass

    def on_system_notify(self, fields: dict) -> None:
        """6636：操作权占用状态；兼容旧版系统通知。"""
        pass

    def on_server_record_error(self, error, fields: dict) -> None:
        """6637 录制异常；兼容旧版 6636/ServerRecord.Error，fields 保留原格式。"""
        pass

    def on_waiting_user_list(self, fields: dict) -> None:
        """6647 等候用户列表分页，含 UserList.End 的页也原样通知。

        仅接收服务器推送；没有配对的客户端查询，不自动批准用户。
        """
        pass

    def on_multicast_state(self, multicast, fields: dict) -> None:
        """6651：客户端组播状态变更。"""
        pass

    def on_face_rollcall_started(self, fields: dict) -> None:
        """6657：人脸点名开始通知。"""
        pass

    def on_face_rollcall_result(self, fields: dict) -> None:
        """6658：人脸点名识别结果广播。"""
        pass

    def on_face_rollcall_cancelled(self, fields: dict) -> None:
        """6659：人脸点名取消通知。"""
        pass

    def on_relay_message(self, messages: list, fields: dict) -> None:
        """6700：服务器透明中转消息，messages 为内层 <msg> 列表。"""
        pass

    def on_chat_recalled(self, fields: dict) -> None:
        """6669：聊天消息撤回广播。"""
        pass

    def on_web_kicked(self, user_id, fields: dict) -> None:
        """6652：兼容历史 Web 请出通知。

        当前产品的 HTTP USER_REMOVE 使用 6606，走 on_user_kicked。
        """
        pass

    def on_web_room_closed(self, fields: dict) -> None:
        """6653：Web 端关闭了会议室。"""
        pass

    def on_web_notify(self, fields: dict) -> None:
        """6654：Web 端发来的通知。"""
        pass

    def on_video_polling(self, fields: dict) -> None:
        """6633：视频轮询状态（Operation 1用户变更/2停止,CurrentUserID,NextUserID,Interval,Screen,Window）。"""
        pass

    def on_multi_data_sharer(self, fields: dict) -> None:
        """6638 unsolicited sharing-mode change (request replies use raw events)."""
        pass

    def on_feature_users(self, fields: dict) -> None:
        """6645 feature subscription update; UserID is a CSV string."""
        pass

    def on_product_features(self, fields: dict) -> None:
        """6639 product capabilities pushed during conference initialization."""
        pass

    def on_modify_self_state(self, fields: dict) -> None:
        """6646 targeted request. Caller processes it and sends 6546 explicitly."""
        pass

    def on_h5_vote(self, fields: dict) -> None:
        """6661 H5 vote start/end."""
        pass

    def on_group_user_state(self, fields: dict) -> None:
        """6666 group membership/state sent to the group manager."""
        pass

    def on_waiting_room(self, fields: dict) -> None:
        """6667 waiting-room route; main-room login may still be pending."""
        pass

    def on_user_custom_data(self, fields: dict) -> None:
        """6672 public broadcast or private targeted data."""
        pass

    def on_raw_event(self, event: "ReceivedEvent") -> None:
        """每条服务器下行（回复+推送）都投一份统一定义的事件对象（含原始 XML、时间）。

        EventCollector 实现里会把它存进 ``received`` 统一日志，供断言与 Allure 报告使用。"""
        pass


@dataclass
class _PendingRequest:
    seq: int
    request_cmdid: Optional[int] = None
    event: threading.Event = field(default_factory=threading.Event)
    response: Optional[XmlCommand] = None
    responses: List[XmlCommand] = field(default_factory=list)
    error: Optional[BaseException] = None
    terminal_notification: Optional[XmlCommand] = None
    collect_many: bool = False
    condition: threading.Condition = field(default_factory=threading.Condition)
    response_match: Optional[Callable[[XmlCommand], bool]] = None


class _Bridge(SessionListener):
    """把一条 framecore 会话的事件/数据桥接到 FmClient。"""

    def __init__(self, client: "FmClient", which: str) -> None:
        self._client = client
        self._which = which
        self._session_key = 0

    def bind(self, session: TcpReliableSession) -> None:
        self._session_key = id(session)

    def on_created(self) -> None:
        self._client._on_created(self._which)

    def on_create_failed(self, reason: str) -> None:
        self._client._on_create_failed(self._which, self._session_key, reason)

    def on_closed(self) -> None:
        self._client._on_closed(self._which, self._session_key)

    def on_data(self, data: bytes) -> None:
        self._client._on_data(self._which, self._session_key, data)

    def on_listener_error(self, event: str, exc: BaseException) -> None:
        logger.exception("%s session listener %r raised", self._which, event)


class FmClient(FrontOps, ConfOps, ContactsOps, CallOps, MeetingOps, MeetingControlOps, EntranceOps,
               CapabilityOps, InteractionOps):
    """4u 会议客户端：共享状态 + 会话机械 在此；各域业务方法来自 ops/ 的 mixin。

    对外 API 平铺：``client.front_login()`` / ``client.conf_login()`` / ``client.conf_invite_call()``
    等方法分别由 FrontOps / ConfOps / CallOps 提供（见 fm_client/ops/）。"""

    def __init__(
        self,
        host: str,
        port: Optional[int] = None,
        listener: Optional[FmListener] = None,
        *,
        encoding: str = DEFAULT_ENCODING,
        connect_timeout_ms: int = 8000,
        request_timeout: float = 8.0,
        conf_protocol_version: str = C.CONF_VER_CS,
        enable_reconnect: bool = False,
        offered_security_types=(0, 1, 2, 3),
    ) -> None:
        self._host = host
        self._port = port
        self._listener = listener or FmListener()
        self._encoding = encoding
        self._request_timeout = request_timeout
        self._conf_protocol_version = _normalize_protocol_version(
            conf_protocol_version, name="conf_protocol_version")
        self._packer = XmlMsgIOPack()
        self._lock = threading.RLock()
        # (session identity, response cmdid) -> requests in wire order
        self._pending: Dict[Tuple[int, int], Deque[_PendingRequest]] = {}
        self._next_request_seq = 0
        self._next_received_event_id = 1
        self._next_sent_event_id = 1
        self._sent_commands: Deque[SentCommand] = deque()

        self._front: Optional[TcpReliableSession] = None
        self._conf: Optional[TcpReliableSession] = None

        self._sess_cfg = dict(
            connect_timeout_ms=connect_timeout_ms,
            offered_security_types=offered_security_types,
            enable_reconnect=enable_reconnect,
        )

        # 登录后填充的会话态（供各域 mixin 复用）
        self.local_user_id: Optional[str] = None       # 30001 LocalUserID
        self.room_id: Optional[int] = None
        self.room_node_id: Optional[str] = None         # 30005 RoomNodeID
        self.room_user_id: Optional[str] = None        # 30005 RoomUserID
        self.room_srv_node_id: Optional[str] = None    # 30005 RoomSrvNodeID
        self.token: Optional[str] = None               # 30005 Token
        self.user_right: Optional[str] = None          # 30005 / 6601 Right
        self.room_verify_mode: Optional[str] = None    # 30005 RoomVerifyMode（1用户密码/2房间密码/3匿名）
        self.room_password: Optional[str] = None       # 进房密码（用户传入，供 conf_login 复用）
        self.conf_servers: List[dict] = []             # 30005 <Server> 列表
        self.real_room_id: Optional[str] = None        # 30005 RealRoomID（邀请码入会时 ≠ RoomID）
        self.dept_info: Optional[dict] = None          # 6601 DeptInfo（组织架构缓存）
        self.user_info: Optional[dict] = None          # 6601 UserInfo（本地用户联系人缓存）
        self._offline_users_cache: Optional[List[dict]] = None  # 主线每次 Conf 登录仅响应一次 6503
        self._conf_login_options: dict = {}
        self.http_api: Optional[WebApiClient] = None   # Web 后台 HTTP 客户端（建会/取消/邀请/布局锁定等信令外接口）

    def attach_http(self, config) -> WebApiClient:
        """附加 Web API 客户端，让建会/取消/邀请/布局锁定等（信令里没有的）接口走 HTTP。

        config: ``WebApiClient`` 实例 / 任意 duck-typed 客户端 / JSON 路径 / dict / 带 ``"http"`` 子节的 dict。
        附加后 ``client.create_instant_meeting(...)`` 等会自动走 HTTP；否则抛 ``ProtocolNotInSignaling``。"""
        if isinstance(config, (str, bytes, dict)) or hasattr(config, "__fspath__"):
            self.http_api = WebApiClient.from_config(config)
        else:   # WebApiClient 或 duck-typed 客户端对象（测试桩）
            self.http_api = config
        return self.http_api

    # ---------------------------------------------------------------- 连接
    @property
    def conf_protocol_version(self) -> str:
        """Version written to every command sent over the Conf session."""
        return self._conf_protocol_version

    def _make_session(self, app_id: int, which: str) -> TcpReliableSession:
        """创建一条 framecore 会话（含 _Bridge），不连接。front/conf 持久会话与 entrance 一次性查询共用。"""
        cfg = SessionConfig(application_id=app_id, **self._sess_cfg)
        bridge = _Bridge(self, which)
        sess = TcpReliableSession(bridge, cfg)
        bridge.bind(sess)
        return sess

    def _connect(
        self,
        app_id: int,
        which: str,
        address_link: Optional[str] = None,
    ) -> TcpReliableSession:
        sess = self._make_session(app_id, which)
        target = address_link if address_link is not None else self._host
        target_port = None if address_link is not None else self._port
        if not sess.connect(target, target_port):
            raise FmError(0x1004, f"{which} connect failed")
        return sess

    def _ensure_front(self) -> TcpReliableSession:
        if self._front is None or self._front.state is SessionState.CLOSED:
            self._front = self._connect(F.FRONT_APP_ID, FRONT)
        return self._front

    def _ensure_conf(self) -> TcpReliableSession:
        if self._conf is None or self._conf.state is SessionState.CLOSED:
            self._conf = self._connect(
                C.CONF_APP_ID, CONF, self._conf_address_link() or None)
        return self._conf

    def _conf_address_link(self) -> str:
        """Join Front's Server/SrvAddr values like the C++ fm_loadtest client."""
        items = []
        for server in self.conf_servers:
            address = server.get("SrvAddr") if isinstance(server, dict) else None
            if not address:
                continue
            items.extend(part.strip() for part in str(address).split(";") if part.strip())
        return ";".join(items)

    def _require_conf(self) -> TcpReliableSession:
        if self._conf is None:
            raise RuntimeError("conf session not established; call conf_login() first")
        return self._conf

    # ---------------------------------------------------------------- 收发核心
    def _send(self, sess: TcpReliableSession, cmd: XmlCommand) -> None:
        if sess is self._conf:
            cmd.ver = self._conf_protocol_version
        raw_xml = cmd.to_bytes(self._encoding)
        frame = self._packer.pack(raw_xml)
        if not sess.send(frame):
            raise FmError(0x1004, f"send failed for cmd {cmd.cmdid} (state={sess.state.name})")
        source = CONF if sess is self._conf else FRONT if sess is self._front else "entrance"
        with self._lock:
            event_id = self._next_sent_event_id
            self._next_sent_event_id += 1
            self._sent_commands.append(SentCommand(
                source=source, cmdid=cmd.cmdid, name=cmdid_name(cmd.cmdid),
                fields=copy.deepcopy(cmd.fields), raw_xml=raw_xml,
                sent_at=time.time(), event_id=event_id, ver=cmd.ver,
                seq=cmd.seq, sent_monotonic=time.monotonic()))

    def mark_sent_commands(self) -> int:
        """返回上行发送游标；后续snapshot只读取本次操作新增协议。"""
        with self._lock:
            return self._next_sent_event_id - 1

    def snapshot_sent_commands(self, *, after: int = 0) -> List[SentCommand]:
        """读取游标之后真实交给会话发送队列的上行XML。"""
        with self._lock:
            return [item for item in self._sent_commands if item.event_id > after]

    def _request(
        self,
        sess: TcpReliableSession,
        cmd: XmlCommand,
        pairs: dict,
        timeout: Optional[float] = None,
        raise_on_error: bool = True,
        *,
        response_match: Optional[Callable[[XmlCommand], bool]] = None,
    ) -> Optional[XmlCommand]:
        """发送 cmd 并（若 pairs 里有对应回复）等待回复。

        raise_on_error=True（默认）：回复 Result!=0 抛 FmError；
        =False：即便 Result 非 0 也返回 rep（用于从"失败回复"里提取仍存在的字段，
        如 ROOMLIST 在 0x1002 时仍带 RoomNodeID）。"""
        expect = pairs.get(cmd.cmdid)
        if expect is None:
            self._send(sess, cmd)
            return None
        timeout = timeout if timeout is not None else self._request_timeout
        with self._lock:
            if cmd.seq is None:
                cmd.seq = self._next_request_seq
                self._next_request_seq = (self._next_request_seq + 1) & 0x7FFFFFFF
            request = _PendingRequest(cmd.seq, cmd.cmdid, response_match=response_match)
            key = (id(sess), expect)
            self._pending.setdefault(key, deque()).append(request)
        try:
            self._send(sess, cmd)
            if not request.event.wait(timeout):
                if (cmd.cmdid == C.LOGINREQ and request.terminal_notification is not None
                        and request.terminal_notification.cmdid in (C.WAITOPENDOOR, C.WAITINGROOMINFO)):
                    raise ConfLoginWaiting(timeout, notification=request.terminal_notification)
                raise TimeoutError(
                    f"cmd {cmd.cmdid} no response (expect {expect}) within {timeout}s")
            if request.error is not None:
                raise request.error
            rep = request.response
        finally:
            with self._lock:
                self._remove_pending_locked(key, request)
        if rep is None:
            raise FmError(0x1002, f"empty response for cmd {cmd.cmdid}")
        # 部分回复（Entrance CONFIGREP / GetAuthType REP 等）无 Result 字段，成功隐含；
        # 仅当显式带 Result 且非 0 时才按业务错误上抛。
        result_field = rep.get("Result")
        if raise_on_error and result_field is not None:
            result = _to_int(result_field, default=-1)
            if not is_success(result):
                raise FmError(result)
        return rep

    def _request_pages(
        self,
        sess: TcpReliableSession,
        cmd: XmlCommand,
        pairs: dict,
        timeout: Optional[float] = None,
        *,
        complete: Optional[Callable[[XmlCommand], bool]] = None,
        idle_timeout: float = 0.25,
        raise_on_error: bool = True,
        response_match: Optional[Callable[[XmlCommand], bool]] = None,
    ) -> List[XmlCommand]:
        """Collect all responses for a paged request.

        Newer paged protocols provide ``complete`` (for example 6628's
        ``DeptEnd``).  Legacy 6603 has no end marker, so it is considered
        complete after ``idle_timeout`` without another page.
        """
        expect = pairs.get(cmd.cmdid)
        if expect is None:
            self._send(sess, cmd)
            return []
        timeout = timeout if timeout is not None else self._request_timeout
        with self._lock:
            if cmd.seq is None:
                cmd.seq = self._next_request_seq
                self._next_request_seq = (self._next_request_seq + 1) & 0x7FFFFFFF
            request = _PendingRequest(cmd.seq, cmd.cmdid, collect_many=True,
                                      response_match=response_match)
            key = (id(sess), expect)
            self._pending.setdefault(key, deque()).append(request)

        deadline = time.monotonic() + timeout
        try:
            self._send(sess, cmd)
            with request.condition:
                while not request.responses and request.error is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not request.condition.wait(remaining):
                        raise TimeoutError(
                            f"cmd {cmd.cmdid} no response (expect {expect}) "
                            f"within {timeout}s")

                while request.error is None:
                    latest = request.responses[-1]
                    result_field = latest.get("Result")
                    if (result_field is not None
                            and not is_success(_to_int(result_field, default=-1))):
                        break
                    if complete is not None and complete(latest):
                        break

                    seen = len(request.responses)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        if complete is None:
                            break
                        raise TimeoutError(
                            f"cmd {cmd.cmdid} paged response (expect {expect}) "
                            f"did not reach its end marker within {timeout}s")
                    wait_for = remaining if complete is not None else min(idle_timeout, remaining)
                    got_page = request.condition.wait_for(
                        lambda: len(request.responses) > seen or request.error is not None,
                        wait_for,
                    )
                    if not got_page and complete is None:
                        break
                    if not got_page:
                        raise TimeoutError(
                            f"cmd {cmd.cmdid} paged response (expect {expect}) "
                            f"did not reach its end marker within {timeout}s")

                if request.error is not None:
                    raise request.error
                responses = list(request.responses)
        finally:
            with self._lock:
                self._remove_pending_locked(key, request)

        if not responses:
            raise FmError(0x1002, f"empty response for cmd {cmd.cmdid}")
        if raise_on_error:
            for rep in responses:
                result_field = rep.get("Result")
                if result_field is not None:
                    result = _to_int(result_field, default=-1)
                    if not is_success(result):
                        raise FmError(result)
        return responses

    def _remove_pending_locked(
        self,
        key: Tuple[int, int],
        request: _PendingRequest,
    ) -> None:
        queue_ = self._pending.get(key)
        if queue_ is None:
            return
        try:
            queue_.remove(request)
        except ValueError:
            pass
        if not queue_:
            self._pending.pop(key, None)

    def _fail_pending(self, session_key: Optional[int], error: BaseException) -> None:
        with self._lock:
            keys = [key for key in self._pending if session_key is None or key[0] == session_key]
            requests = []
            for key in keys:
                requests.extend(self._pending.pop(key))
        for request in requests:
            with request.condition:
                request.error = error
                request.condition.notify_all()
            request.event.set()

    # ---------------------------------------------------------------- 生命周期
    def close(self) -> None:
        """关闭两条会话。"""
        sessions = ((CONF, self._conf), (FRONT, self._front))
        self._conf = None
        self._front = None
        self._offline_users_cache = None
        self._fail_pending(None, FmError(0x1004, "client closed"))
        for name, sess in sessions:
            if sess is not None:
                try:
                    sess.close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("%s close failed: %s", name, exc)

    # ---------------------------------------------- framecore 会话事件回调
    def _on_created(self, which: str) -> None:
        if which == FRONT:
            self._listener.on_front_connected()
        elif which == CONF:
            self._listener.on_conf_connected()
        # entrance 为一次性引导查询，不派发持久监听事件

    def _on_create_failed(self, which: str, session_key: int, reason: str) -> None:
        self._fail_pending(session_key, FmError(0x1004, reason))
        self._listener.on_create_failed(which, reason)

    def _on_closed(self, which: str, session_key: int) -> None:
        self._fail_pending(session_key, FmError(0x1004, f"{which} session closed"))
        if which == FRONT and self._front is not None and id(self._front) == session_key:
            self._front = None
        elif which == CONF and self._conf is not None and id(self._conf) == session_key:
            self._conf = None
            self._offline_users_cache = None
        self._listener.on_disconnected(which)

    def _make_received_event(
        self,
        which: str,
        *,
        cmd: Optional[XmlCommand],
        kind: str,
        raw_xml: bytes,
        raw_payload: bytes,
        request_cmdid: Optional[int] = None,
        decode_error: Optional[str] = None,
    ) -> ReceivedEvent:
        with self._lock:
            event_id = self._next_received_event_id
            self._next_received_event_id += 1
        return ReceivedEvent(
            source=which,
            cmdid=cmd.cmdid if cmd is not None else None,
            name=cmdid_name(cmd.cmdid) if cmd is not None else "DECODE_ERROR",
            fields=copy.deepcopy(cmd.fields) if cmd is not None else {},
            raw_xml=bytes(raw_xml),
            received_at=time.time(),
            event_id=event_id,
            kind=kind,
            seq=cmd.seq if cmd is not None else None,
            request_cmdid=request_cmdid,
            received_monotonic=time.monotonic(),
            decode_error=decode_error,
            raw_payload=bytes(raw_payload),
        )

    def _emit_received_event(self, event: ReceivedEvent) -> None:
        try:
            self._listener.on_raw_event(event)
        except Exception:  # noqa: BLE001
            logger.exception("on_raw_event listener raised")

    def _on_data(self, which: str, session_key: int, data: bytes) -> None:
        xml = b""
        try:
            xml = self._packer.unpack(data)
            cmd = XmlCommand.from_bytes(xml, self._encoding)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: decode business msg failed: %s", which, exc)
            self._emit_received_event(self._make_received_event(
                which,
                cmd=None,
                kind="decode_error",
                raw_xml=xml,
                raw_payload=data,
                decode_error=f"{type(exc).__name__}: {exc}",
            ))
            return
        logger.info("%s <- cmd=%s keys=%s", which, cmd.cmdid, list(cmd.fields.keys()))
        if which == CONF and cmd.cmdid in (C.WAITOPENDOOR, C.WAITINGROOMINFO):
            self._mark_login_waiting(session_key, cmd)
        request = self._take_pending_response(session_key, cmd)
        if request is not None:
            kind = "response"
        elif which == CONF and cmd.cmdid in C.PUSHES:
            kind = "notify"
        else:
            kind = "unknown"
        self._emit_received_event(self._make_received_event(
            which,
            cmd=cmd,
            kind=kind,
            raw_xml=xml,
            raw_payload=data,
            request_cmdid=request.request_cmdid if request is not None else None,
        ))
        if request is not None:
            if request.collect_many:
                with request.condition:
                    request.response = cmd
                    request.responses.append(cmd)
                    request.condition.notify_all()
            else:
                request.response = cmd
                request.event.set()
        else:
            self._dispatch_push(which, cmd)

    def _mark_login_waiting(self, session_key: int, cmd: XmlCommand) -> None:
        """记住 6617/6667，使 6501 超时时报告等待态及子会场路由。"""
        key = (session_key, C.LOGINREP)
        with self._lock:
            queue_ = self._pending.get(key)
            if queue_:
                queue_[0].terminal_notification = cmd

    def _take_pending_response(
        self,
        session_key: int,
        cmd: XmlCommand,
    ) -> Optional[_PendingRequest]:
        key = (session_key, cmd.cmdid)
        with self._lock:
            queue_ = self._pending.get(key)
            if not queue_:
                return None
            if cmd.seq is not None:
                request = next((item for item in queue_ if item.seq == cmd.seq), None)
                # A late response must not satisfy a newer request merely
                # because both commands share the same response cmdid.
                if request is None:
                    return None
            else:
                # Older servers do not always echo the XML seq; their TCP
                # replies are ordered, so retain FIFO only when seq is absent.
                request = next((item for item in queue_
                                if item.response_match is None or item.response_match(cmd)), None)
            if request is None or (request.response_match is not None
                                   and not request.response_match(cmd)):
                return None
            if not request.collect_many:
                queue_.remove(request)
                if not queue_:
                    self._pending.pop(key, None)
            return request

    def _dispatch_push(self, which: str, cmd: XmlCommand) -> None:
        try:
            if cmd.cmdid == C.USERENTER:
                users = cmd.get("User")
                if isinstance(users, dict):
                    users = [users]
                self._listener.on_user_enter(users or [])
            elif cmd.cmdid == C.USERLEAVE:
                self._listener.on_user_leave(cmd.get("UserID"))
            elif cmd.cmdid == C.USERSTATECHANGED:
                self._listener.on_user_state_changed(dict(cmd.fields))
            elif cmd.cmdid == C.CHATMSGNOTIFY:
                if str(cmd.get("NeedCheck")) == "1":
                    self._listener.on_chat_check_request(dict(cmd.fields))
                self._listener.on_chat(
                    cmd.get("SrcUserID"), cmd.get("DstUserID"),
                    cmd.get("Text"), cmd.get("MsgID"))
            elif cmd.cmdid == C.USERKICKED:
                self._listener.on_user_kicked(
                    cmd.get("KickID"), cmd.get("UserID"), dict(cmd.fields))
            elif cmd.cmdid == C.ROOMCLOSED:
                self._listener.on_room_closed(
                    cmd.get("Reason"), cmd.get("Description"), dict(cmd.fields))
            elif cmd.cmdid == C.AVINFONOTIFY:
                self._listener.on_av_info(dict(cmd.fields))
            elif cmd.cmdid == C.CONFSTATECHANGED:
                self._listener.on_conf_state_changed(dict(cmd.fields))
            elif cmd.cmdid == C.REQCHAIRRIGHTRET:
                self._listener.on_chair_right_ret(dict(cmd.fields))
            elif cmd.cmdid == C.SILENTNOTIFY:
                self._listener.on_silent(dict(cmd.fields))
            elif cmd.cmdid == C.CALLSTATE_NOTIFY:
                self._listener.on_call_state_notify(dict(cmd.fields))
            elif cmd.cmdid == C.KNOCKNOTIFY:
                self._listener.on_knock(cmd.get("UserID"), cmd.get("DisplayName"), dict(cmd.fields))
            elif cmd.cmdid == C.WAITOPENDOOR:
                self._listener.on_wait_open_door()
            elif cmd.cmdid == C.MEETINGCHARNOTIFY:
                self._listener.on_subtitle(dict(cmd.fields))
            elif cmd.cmdid == C.SENDFILENOTIFY:
                self._listener.on_send_file(cmd.get("SrcUserID"), cmd.get("FileInfo"), dict(cmd.fields))
            elif cmd.cmdid == C.WNDSTATENOTIFY:
                self._listener.on_window_state(dict(cmd.fields))
            elif cmd.cmdid == C.ROLEPERMISSIONNOTIFY:
                self._listener.on_role_permission(_as_list(cmd.get("Role")), dict(cmd.fields))
            elif cmd.cmdid == C.SYSMSG:
                self._listener.on_system_message(dict(cmd.fields))
            elif cmd.cmdid == C.ENTERGROUPREP:
                self._listener.on_group_enter(dict(cmd.fields))
            elif cmd.cmdid == C.ENTERMAINREP:
                self._listener.on_main_enter(dict(cmd.fields))
            elif cmd.cmdid == C.CLOSESUBTITLEREP:
                self._listener.on_subtitle_closed(dict(cmd.fields))
            elif cmd.cmdid == C.ROOMDATANOTIFY:
                self._listener.on_room_data(
                    _as_list(cmd.get("DataItem")), dict(cmd.fields))
            elif cmd.cmdid == C.VIDEOSUBTITLENOTIFY:
                self._listener.on_video_subtitle(dict(cmd.fields))
            elif cmd.cmdid == C.CHATCHECKRESULT:
                self._listener.on_chat_check_result(dict(cmd.fields))
            elif cmd.cmdid == C.INTERACTIVELANGUAGENOTIFY:
                self._listener.on_interactive_language(dict(cmd.fields))
            elif cmd.cmdid == C.ROOMLANGUAGESNOTIFY:
                self._listener.on_room_languages(dict(cmd.fields))
            elif cmd.cmdid == C.REMOTEAVNOTIFY:
                self._listener.on_remote_av_request(dict(cmd.fields))
            elif cmd.cmdid == C.REMOTEAVRESULTNOTIFY:
                self._listener.on_remote_av_result(dict(cmd.fields))
            elif cmd.cmdid == C.ROLEPERMISSIONCHANGED:
                self._listener.on_role_permission_changed(dict(cmd.fields))
            elif cmd.cmdid == C.USERPERMISSIONCHANGED:
                self._listener.on_user_permission_changed(
                    cmd.get("UserID"), dict(cmd.fields))
            elif cmd.cmdid == C.SYSTEMNOTIFY:
                fields = dict(cmd.fields)
                self._listener.on_system_notify(fields)
                server_record = cmd.get("ServerRecord")
                if isinstance(server_record, dict) and server_record.get("Error") is not None:
                    self._listener.on_server_record_error(
                        server_record.get("Error"), fields)
            elif cmd.cmdid == C.SERVERRECORDERRORNOTIFY:
                error = cmd.get("Error")
                legacy = cmd.get("ServerRecord")
                if error is None and isinstance(legacy, dict):
                    error = legacy.get("Error")
                if error is not None:
                    self._listener.on_server_record_error(error, dict(cmd.fields))
            elif cmd.cmdid == C.WAITINGUSERLISTNOTIFY:
                self._listener.on_waiting_user_list(dict(cmd.fields))
            elif cmd.cmdid == C.MULTICASTNOTIFY:
                self._listener.on_multicast_state(
                    cmd.get("Multicast"), dict(cmd.fields))
            elif cmd.cmdid == C.FACEROLLCALLSTARTNOTIFY:
                self._listener.on_face_rollcall_started(dict(cmd.fields))
            elif cmd.cmdid == C.FACEROLLCALLRESULTNOTIFY:
                self._listener.on_face_rollcall_result(dict(cmd.fields))
            elif cmd.cmdid == C.FACEROLLCALLCANCELNOTIFY:
                self._listener.on_face_rollcall_cancelled(dict(cmd.fields))
            elif cmd.cmdid == C.RELAYMESSAGE:
                self._listener.on_relay_message(
                    _as_list(cmd.get("msg")), dict(cmd.fields))
            elif cmd.cmdid == C.RECALLCHATMSGNOTIFY:
                self._listener.on_chat_recalled(dict(cmd.fields))
            elif cmd.cmdid == C.WEBKICKUSER:
                self._listener.on_web_kicked(cmd.get("UserID"), dict(cmd.fields))
            elif cmd.cmdid == C.WEBCLOSEROOM:
                self._listener.on_web_room_closed(dict(cmd.fields))
            elif cmd.cmdid == C.WEBSENDNOTIFY:
                self._listener.on_web_notify(dict(cmd.fields))
            elif cmd.cmdid == C.VIDEOPOLLINGNOITFY:
                self._listener.on_video_polling(dict(cmd.fields))
            elif cmd.cmdid == C.MULTIDATASHARERNOTIFY:
                self._listener.on_multi_data_sharer(dict(cmd.fields))
            elif cmd.cmdid == C.FEATUREUSERSREP:
                self._listener.on_feature_users(dict(cmd.fields))
            elif cmd.cmdid == C.PRODUCTFEATURESNOTIFY:
                self._listener.on_product_features(dict(cmd.fields))
            elif cmd.cmdid == C.MODIFYSELFSTATENOTIFY:
                self._listener.on_modify_self_state(dict(cmd.fields))
            elif cmd.cmdid == C.H5VOTENOTIFY:
                self._listener.on_h5_vote(dict(cmd.fields))
            elif cmd.cmdid == C.GROUPUSERSTATENOTIFY:
                self._listener.on_group_user_state(dict(cmd.fields))
            elif cmd.cmdid == C.WAITINGROOMINFO:
                self._listener.on_waiting_room(dict(cmd.fields))
            elif cmd.cmdid == C.USERCUSTOMDATANOTIFY:
                self._listener.on_user_custom_data(dict(cmd.fields))
            else:
                logger.debug("%s unhandled push cmd=%s fields=%s", which, cmd.cmdid, cmd.fields)
        except Exception:  # noqa: BLE001
            logger.exception("push dispatch failed (cmd=%s)", cmd.cmdid)
