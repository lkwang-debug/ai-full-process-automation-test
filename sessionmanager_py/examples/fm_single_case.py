"""Run exactly one HTTP API or Conf protocol verification case.

Examples:
    python3 examples/fm_single_case.py list
    python3 examples/fm_single_case.py conf --mock --case chat
    python3 examples/fm_single_case.py conf \
        --config examples/multi_client.json --case user_audio
    python3 examples/fm_single_case.py http \
        --http-config examples/http_api.json \
        --method list_departments --kwargs '{"page_number": 1}'

The built-in Conf cases assert a response or an observer-side notification.
The generic modes are intended for environment-specific parameters.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fm_client import conf as C  # noqa: E402
from fm_client import front as F  # noqa: E402
from fm_client.errcodes import FmError, INVALIDARG, UNSUPPORTED  # noqa: E402
from fm_client.http_api import WebApiClient  # noqa: E402
from fm_client.mockserver import MockFmServer  # noqa: E402
from fm_client.testing import FmTestClient, connect_pair, load_config  # noqa: E402


JsonDict = Dict[str, Any]


def _json_value(raw: Optional[str], default: Any) -> Any:
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {raw!r}: {exc}") from exc


def _json_dict(raw: Optional[str]) -> JsonDict:
    value = _json_value(raw, {})
    if not isinstance(value, dict):
        raise ValueError("value must be a JSON object")
    return value


def _json_list(raw: Optional[str]) -> List[Any]:
    value = _json_value(raw, [])
    if not isinstance(value, list):
        raise ValueError("value must be a JSON array")
    return value


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _read_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            if part in current:
                current = current[part]
            elif part == "val" and "@val" in current:
                current = current["@val"]
            else:
                return None
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        elif part == "0":
            # XML repeated nodes are a dict when the server sends one item.
            continue
        else:
            return None
    return current


def _matches(value: Any, expected: JsonDict) -> bool:
    if not isinstance(value, dict):
        return False
    return all(str(_read_path(value, path)) == str(wanted)
               for path, wanted in expected.items())


def _recent(client: FmTestClient, after: int) -> List[dict]:
    return [
        {
            "event_id": event.event_id,
            "source": event.source,
            "cmdid": event.cmdid,
            "kind": event.kind,
            "fields": event.fields,
        }
        for event in client.events.snapshot_received(after=after)[-10:]
    ]


def _close_client(client: Optional[FmTestClient]) -> None:
    if client is None:
        return
    try:
        client.conf_logout()
    except Exception:
        pass
    try:
        client.front_logout()
    except Exception:
        pass
    client.close()


class ConfContext:
    def __init__(self, operator: FmTestClient, observer: FmTestClient,
                 timeout: float, params: JsonDict) -> None:
        self.operator = operator
        self.observer = observer
        self.timeout = timeout
        self.params = params

    def wait_action(
        self,
        action: Callable[[], Any],
        *,
        client: str,
        source: str,
        cmdid: int,
        kind: str,
        match: Optional[JsonDict] = None,
    ) -> JsonDict:
        target = self.operator if client == "operator" else self.observer
        mark = target.events.mark()
        try:
            result = action()
        except Exception as exc:
            raise AssertionError(
                f"action failed: {type(exc).__name__}: {exc}; "
                f"recent={_dump(_recent(target, mark))}") from exc
        event = target.events.wait_event(
            after=mark,
            source=source,
            cmdid=cmdid,
            kind=kind,
            match=match or None,
            timeout=self.timeout,
        )
        if event is None:
            raise AssertionError(
                f"event timeout: client={client}, source={source}, "
                f"cmdid={cmdid}, kind={kind}, match={match or {}}, "
                f"recent={_dump(_recent(target, mark))}")
        return {"result": result, "event": event.fields}

    def notify(self, action: Callable[[], Any], cmdid: int,
               match: Optional[JsonDict] = None,
               *, client: str = "observer") -> JsonDict:
        return self.wait_action(
            action, client=client, source="conf", cmdid=cmdid,
            kind="notify", match=match)

    def response(self, action: Callable[[], Any], cmdid: int,
                 match: Optional[JsonDict] = None,
                 *, source: str = "conf") -> JsonDict:
        return self.wait_action(
            action, client="operator", source=source, cmdid=cmdid,
            kind="response", match=match)

    @contextmanager
    def right(self, right_type: int):
        response = self.operator.request_right(right_type, timeout=self.timeout)
        if str(response.get("Result", "0")) != "0":
            raise AssertionError(f"request_right failed: {response}")
        try:
            yield
        finally:
            try:
                self.operator.release_right(right_type, timeout=self.timeout)
            except Exception as exc:
                print(f"[WARN] release_right({right_type}) failed: {exc}")


@dataclass(frozen=True)
class ConfCase:
    name: str
    protocols: str
    description: str
    handler: Callable[[ConfContext], JsonDict]
    destructive: bool = False
    requires: str = ""


def _case_front_auth_type(ctx: ConfContext) -> JsonDict:
    return ctx.response(
        ctx.operator.front_get_auth_type, F.GETAUTHTYPE_REP, source="front")


def _case_front_room_list(ctx: ConfContext) -> JsonDict:
    return ctx.response(
        ctx.operator.front_roomlist, F.ROOMLIST_REP, source="front")


def _case_front_discover_node(ctx: ConfContext) -> JsonDict:
    result = ctx.response(
        ctx.operator.front_discover_node, F.ROOMLIST_REP, source="front")
    if result.get("result") in (None, ""):
        raise AssertionError(f"ROOMLIST response has no RoomNodeID: {result}")
    return result


def _case_offline_users(ctx: ConfContext) -> JsonDict:
    return ctx.response(
        lambda: ctx.operator.conf_get_offline_users(timeout=ctx.timeout),
        C.OUTLINE,
    )


def _case_offline_users_repeat(ctx: ConfContext) -> JsonDict:
    first = ctx.operator.conf_get_offline_users(timeout=ctx.timeout)
    second = ctx.operator.conf_get_offline_users(timeout=ctx.timeout)
    if first != second:
        raise AssertionError(
            f"cached offline users changed: first={first}, second={second}")
    return {
        "first_count": len(first),
        "second_count": len(second),
        "second_source": "conf-session-cache",
    }


def _case_offline_users_refresh(ctx: ConfContext) -> JsonDict:
    first = ctx.operator.conf_get_offline_users(timeout=ctx.timeout)
    second = ctx.response(
        lambda: ctx.operator.conf_get_offline_users(
            timeout=ctx.timeout, refresh=True),
        C.OUTLINE,
    )
    return {"first_count": len(first), "second": second}


def _case_dept_info(ctx: ConfContext) -> JsonDict:
    return ctx.response(
        lambda: ctx.operator.conf_get_dept_info(timeout=ctx.timeout),
        C.DEPTINFOREP,
    )


def _case_room_config(ctx: ConfContext) -> JsonDict:
    return ctx.response(
        lambda: ctx.operator.conf_get_room_config(timeout=ctx.timeout),
        C.GETROOMCONFIGREP,
    )


def _case_chat(ctx: ConfContext) -> JsonDict:
    text = str(ctx.params.get("text") or f"single-case-chat-{int(time.time() * 1000)}")
    return ctx.notify(
        lambda: ctx.operator.chat(text), C.CHATMSGNOTIFY, {"Text": text})


def _case_av_info(ctx: ConfContext) -> JsonDict:
    audio = [C.ConfCmd.av_device(C.AV_OP_ADD, 0, "single-case-mic")]
    return ctx.notify(
        lambda: ctx.operator.report_av_info(audio=audio),
        C.AVINFONOTIFY,
        {"Audio.0.ID": 0, "Audio.0.Name": "single-case-mic"},
    )


def _case_user_audio(ctx: ConfContext) -> JsonDict:
    target = ctx.observer.room_user_id_int
    ctx.operator.set_user_audio(target, C.AUDIO_OFF)
    try:
        return ctx.notify(
            lambda: ctx.operator.set_user_audio(target, C.AUDIO_SPEAKING),
            C.USERSTATECHANGED,
            {"UserID": target, "Audio.State": C.AUDIO_SPEAKING},
        )
    finally:
        ctx.operator.set_user_audio(target, C.AUDIO_OFF)


def _case_user_video(ctx: ConfContext) -> JsonDict:
    target = ctx.observer.room_user_id_int
    ctx.operator.set_user_video(target, C.VIDEO_OFF)
    try:
        return ctx.notify(
            lambda: ctx.operator.set_user_video(target, C.VIDEO_BROADCAST),
            C.USERSTATECHANGED,
            {"UserID": target, "Video.State": C.VIDEO_BROADCAST},
        )
    finally:
        ctx.operator.set_user_video(target, C.VIDEO_OFF)


def _case_record(ctx: ConfContext) -> JsonDict:
    ctx.operator.stop_record()
    try:
        return ctx.notify(
            ctx.operator.start_record,
            C.CONFSTATECHANGED,
            {"ServerRecordStatus": C.RECORD_RECORDING},
        )
    finally:
        ctx.operator.stop_record()


def _case_room_lock(ctx: ConfContext) -> JsonDict:
    ctx.operator.unlock_room()
    try:
        return ctx.notify(
            ctx.operator.lock_room, C.CONFSTATECHANGED, {"Lock": 1})
    finally:
        ctx.operator.unlock_room()


def _case_mute_all(ctx: ConfContext) -> JsonDict:
    return ctx.notify(ctx.operator.mute_all, C.SILENTNOTIFY)


def _case_subtitle(ctx: ConfContext) -> JsonDict:
    text = str(ctx.params.get("text") or f"single-subtitle-{int(time.time() * 1000)}")
    try:
        return ctx.notify(
            lambda: ctx.operator.set_subtitle(text, duration=1),
            C.MEETINGCHARNOTIFY,
            {"Data": text},
        )
    finally:
        try:
            ctx.operator.close_subtitle(timeout=ctx.timeout)
        except Exception:
            pass


def _case_close_subtitle(ctx: ConfContext) -> JsonDict:
    ctx.operator.set_subtitle("single-case-close", duration=1)
    return ctx.notify(
        lambda: ctx.operator.close_subtitle(timeout=ctx.timeout),
        C.CLOSESUBTITLEREP,
    )


def _case_system_message(ctx: ConfContext) -> JsonDict:
    sys_msg_id = int(ctx.params.get("sys_msg_id", 6))
    return ctx.notify(
        lambda: ctx.operator.send_sysmsg(
            sys_msg_id=sys_msg_id,
            dst_user_id=ctx.observer.room_user_id_int),
        C.SYSMSG,
    )


def _case_host_guard(ctx: ConfContext) -> JsonDict:
    try:
        ctx.operator.apply_host(chair_pwd="")
    except ValueError as exc:
        guard_error = str(exc)
    else:
        raise AssertionError("empty chair password was sent instead of being rejected locally")

    text = f"host-guard-chat-{int(time.time() * 1000)}"
    result = ctx.notify(
        lambda: ctx.operator.chat(text), C.CHATMSGNOTIFY, {"Text": text})
    result["guard_error"] = guard_error
    return result


def _case_send_file(ctx: ConfContext) -> JsonDict:
    name = str(ctx.params.get("name") or "single-case.txt")
    info = C.ConfCmd.file_info(guid=f"single-{int(time.time() * 1000)}",
                               name=name, size=1)
    return ctx.notify(
        lambda: ctx.operator.send_file([ctx.observer.room_user_id_int], info),
        C.SENDFILENOTIFY,
        {"FileInfo.Name": name},
    )


def _case_room_data(ctx: ConfContext) -> JsonDict:
    value = str(ctx.params.get("value") or "started")
    items = [C.ConfCmd.room_data_item(0, {"ClassState": value})]
    return ctx.notify(
        lambda: ctx.operator.set_room_data(items),
        C.ROOMDATANOTIFY,
        {"DataItem.0.ItemID": 0, "DataItem.0.RoomData.ClassState": value},
    )


def _case_av_mix_get(ctx: ConfContext) -> JsonDict:
    mark = ctx.operator.events.mark()
    try:
        result: Any = ctx.operator.get_av_mix_params(timeout=ctx.timeout)
    except FmError as exc:
        if exc.code != UNSUPPORTED:
            raise
        result = {"unsupported": True, "error_code": exc.code}
    event = ctx.operator.events.wait_event(
        after=mark,
        source="conf",
        cmdid=C.GETAVMIXPARAMREP,
        kind="response",
        timeout=ctx.timeout,
    )
    if event is None:
        raise AssertionError(
            f"6620 response missing: recent={_dump(_recent(ctx.operator, mark))}")
    return {"result": result, "event": event.fields}


def _case_invalid_right_types(ctx: ConfContext) -> JsonDict:
    responses = []
    for right_type in (0, 13, 99):
        mark = ctx.operator.events.mark()
        try:
            ctx.operator.request_right(right_type, timeout=ctx.timeout)
        except FmError as exc:
            if exc.code != INVALIDARG:
                raise
        else:
            raise AssertionError(
                f"RightType={right_type} unexpectedly succeeded")
        event = ctx.operator.events.wait_event(
            after=mark,
            source="conf",
            cmdid=C.OPERATIONRIGHTREP,
            kind="response",
            match={"Result": INVALIDARG, "RightType": right_type},
            timeout=ctx.timeout,
        )
        if event is None:
            raise AssertionError(
                f"RightType={right_type} has no 6630/0x1008 response: "
                f"recent={_dump(_recent(ctx.operator, mark))}")
        responses.append(event.fields)
    return {"responses": responses}


def _case_server_fix_regression(ctx: ConfContext) -> JsonDict:
    """Run the repaired server paths in one session, then prove it remains usable."""
    return {
        "offline_users": _case_offline_users(ctx),
        "dept_info": _case_dept_info(ctx),
        "av_mix_get": _case_av_mix_get(ctx),
        "right_invalid": _case_invalid_right_types(ctx),
        "room_config": _case_room_config(ctx),
        "session_health": _case_chat(ctx),
    }


def _case_av_mix_roundtrip(ctx: ConfContext) -> JsonDict:
    before = ctx.operator.get_av_mix_params(timeout=ctx.timeout)
    audio, video = before.get("Audio"), before.get("Video")
    if not isinstance(audio, dict) and not isinstance(video, dict):
        raise AssertionError(f"AV mix response has no Audio/Video: {before}")
    ctx.operator.set_av_mix_params(
        enable_default=False,
        audio=audio if isinstance(audio, dict) else None,
        video=video if isinstance(video, dict) else None,
    )
    after = ctx.operator.get_av_mix_params(timeout=ctx.timeout)
    if audio != after.get("Audio") or video != after.get("Video"):
        raise AssertionError(f"AV mix roundtrip differs: before={before}, after={after}")
    return {"before": before, "after": after}


def _case_video_subtitle(ctx: ConfContext) -> JsonDict:
    style = C.ConfCmd.video_subtitle_style(pos=1, size=2, color=0xFFFFFF)
    return ctx.notify(
        lambda: ctx.operator.set_video_subtitle_params(
            date_time=style, user_name=style),
        C.VIDEOSUBTITLENOTIFY,
        {"DateTime.Pos": 1, "UserName.Color": 0xFFFFFF},
    )


def _case_window_state(ctx: ConfContext) -> JsonDict:
    relay_id = int(ctx.params.get("layout_type", 1001))
    return ctx.notify(
        lambda: ctx.operator.set_window_state(
            mode=C.WND_MODE_VIDEO, video_relay_id=relay_id),
        C.WNDSTATENOTIFY,
        {"VideoRelayID": relay_id},
    )


def _case_layout(ctx: ConfContext) -> JsonDict:
    relay_id = int(ctx.params.get("layout_type", 1001))
    a_uid = ctx.operator.room_user_id_int
    b_uid = ctx.observer.room_user_id_int
    with ctx.right(C.RIGHT_TYPE_LAYOUT):
        return ctx.notify(
            lambda: ctx.operator.set_layout(
                relay_id, [a_uid, b_uid], target_user_ids=[b_uid]),
            C.WNDSTATENOTIFY,
            {"VideoRelayID": relay_id,
             "Video.0.UserID": a_uid, "Video.1.UserID": b_uid},
        )


def _case_role_presenter(ctx: ConfContext) -> JsonDict:
    target = ctx.observer.room_user_id_int
    try:
        return ctx.notify(
            lambda: ctx.operator.set_role(target, C.ROLE_PRESENTER, enabled=True),
            C.USERSTATECHANGED,
            {"UserID": target, "DataState": C.DATASTATE_PRESENTER},
        )
    finally:
        ctx.operator.set_role(target, C.ROLE_PRESENTER, enabled=False)


def _case_role_admin(ctx: ConfContext) -> JsonDict:
    target = ctx.observer.room_user_id_int
    try:
        return ctx.notify(
            lambda: ctx.operator.set_role(target, C.ROLE_ADMIN, enabled=True),
            C.USERSTATECHANGED,
            {"UserID": target, "ManagerState": C.MANAGERSTATE_ADMIN},
        )
    finally:
        ctx.operator.set_role(target, C.ROLE_ADMIN, enabled=False)


def _case_aux(ctx: ConfContext, kind: str, right_type: int,
              field: str, start_state: int) -> JsonDict:
    with ctx.right(right_type):
        try:
            return ctx.notify(
                lambda: ctx.operator.start_aux_stream(
                    kind=kind,
                    audio_share_id=(int(ctx.params.get("audio_share_id", 1))
                                    if kind == "media" else None),
                    video_share_id=(int(ctx.params.get("video_share_id", 1))
                                    if kind == "media" else None)),
                C.USERSTATECHANGED,
                {"UserID": ctx.operator.room_user_id_int, field: start_state},
            )
        finally:
            ctx.operator.stop_aux_stream(kind=kind)


def _case_aux_media(ctx: ConfContext) -> JsonDict:
    return _case_aux(ctx, "media", C.RIGHT_TYPE_MEDIA_SHARE,
                     "MediaShareState", C.MEDIASHARE_START)


def _case_aux_vnc(ctx: ConfContext) -> JsonDict:
    return _case_aux(ctx, "vnc", C.RIGHT_TYPE_SCREEN_SHARE,
                     "VNCState", C.VNC_ON)


def _case_polling(ctx: ConfContext) -> JsonDict:
    polling_id = int(ctx.params.get("polling_id", 7001))
    users = [ctx.operator.room_user_id_int, ctx.observer.room_user_id_int]
    with ctx.right(C.RIGHT_TYPE_VIDEO_POLLING):
        try:
            return ctx.notify(
                lambda: ctx.operator.configure_polling(
                    C.POLLING_OP_ADD, polling_id, "single-case", users,
                    interval=5, screen=1, window=1,
                    status=C.POLLING_STATUS_RUNNING),
                C.VIDEOPOLLINGNOITFY,
                {"VideoPolling.val": polling_id,
                 "VideoPolling.Operation": C.POLLING_NOTIFY_CHANGE},
            )
        finally:
            ctx.operator.configure_polling(
                C.POLLING_OP_DEL, polling_id, "single-case", users)


def _case_role_permissions(ctx: ConfContext) -> JsonDict:
    role = str(ctx.params.get("role") or "mentee")
    try:
        return ctx.notify(
            lambda: ctx.operator.set_role_permissions(
                role, operate_public_chat=False, operate_private_chat=True),
            C.ROLEPERMISSIONCHANGED,
            {"RoleCode": role, "OperatePublicChat": 0,
             "OperatePrivateChat": 1},
        )
    finally:
        ctx.operator.set_role_permissions(
            role, operate_public_chat=True, operate_private_chat=True)


def _case_user_permissions(ctx: ConfContext) -> JsonDict:
    target = ctx.observer.room_user_id_int
    try:
        return ctx.notify(
            lambda: ctx.operator.set_user_permissions(
                target, operate_public_chat=True, operate_private_chat=False),
            C.USERPERMISSIONCHANGED,
            {"UserID": target, "OperatePublicChat": 1,
             "OperatePrivateChat": 0},
        )
    finally:
        ctx.operator.set_user_permissions(
            target, operate_public_chat=True, operate_private_chat=True)


def _room_infos(response: JsonDict) -> List[dict]:
    value = response.get("RoomInfo")
    if isinstance(value, list):
        return value
    return [value] if isinstance(value, dict) else []


def _case_group_create(ctx: ConfContext) -> JsonDict:
    response = ctx.operator.create_group([
        C.ConfCmd.group_room(theme="single-case-group")
    ], timeout=ctx.timeout)
    room_ids = [room.get("RoomID") for room in _room_infos(response)
                if room.get("RoomID") is not None]
    if not room_ids:
        raise AssertionError(f"create_group returned no RoomID: {response}")
    try:
        return {"created": response}
    finally:
        ctx.operator.end_groups(room_ids, timeout=ctx.timeout)


def _case_group_enter(ctx: ConfContext) -> JsonDict:
    created = ctx.operator.create_group([
        C.ConfCmd.group_room(theme="single-case-enter")
    ], timeout=ctx.timeout)
    room_ids = [room.get("RoomID") for room in _room_infos(created)
                if room.get("RoomID") is not None]
    if not room_ids:
        raise AssertionError(f"create_group returned no RoomID: {created}")
    room_id = room_ids[0]
    users = [C.ConfCmd.group_user(id=ctx.observer.room_user_id_int)]
    try:
        return ctx.notify(
            lambda: ctx.operator.enter_group(room_id, users),
            C.ENTERGROUPREP,
            {"RoomID": room_id},
        )
    finally:
        ctx.operator.end_groups(room_ids, timeout=ctx.timeout)


def _case_recall_message(ctx: ConfContext) -> JsonDict:
    text = f"single-recall-{int(time.time() * 1000)}"
    chat = ctx.notify(
        lambda: ctx.operator.chat(text), C.CHATMSGNOTIFY, {"Text": text})
    fields = chat["event"]
    msg_id = fields.get("MsgID")
    if msg_id is None:
        raise AssertionError(f"chat notify has no MsgID: {fields}")
    return ctx.notify(
        lambda: ctx.operator.recall_msg(msg_id, timeout=ctx.timeout),
        C.RECALLCHATMSGNOTIFY,
        {"MsgID": msg_id, "SrcUserID": ctx.operator.room_user_id_int},
    )


def _case_call_list(ctx: ConfContext) -> JsonDict:
    return ctx.response(ctx.operator.conf_query_call_list, C.CALL_LIST_REP)


def _case_terminal_call(ctx: ConfContext) -> JsonDict:
    sns = str(ctx.params.get("terminal_sns") or "")
    if not sns:
        raise ValueError("terminal_call requires --params '{\"terminal_sns\": \"8001\"}'")
    try:
        return ctx.notify(
            lambda: ctx.operator.invite_terminals([sns]),
            C.CALLSTATE_NOTIFY,
            {"CalleeNum": sns},
            client="operator",
        )
    finally:
        try:
            ctx.operator.conf_hangup(
                [sns], call_type=C.CALLTYPE_H323, timeout=ctx.timeout)
        except Exception as exc:
            print(f"[WARN] terminal hangup failed: {exc}")


def _case_logout(ctx: ConfContext) -> JsonDict:
    target = ctx.observer.room_user_id_int
    return ctx.notify(
        ctx.observer.conf_logout, C.USERLEAVE, {"UserID": target},
        client="operator")


def _case_kick_user(ctx: ConfContext) -> JsonDict:
    target = ctx.observer.room_user_id_int
    return ctx.notify(
        lambda: ctx.operator.kick_user(target),
        C.USERKICKED,
        {"UserID": target},
        client="operator",
    )


def _case_close_room(ctx: ConfContext) -> JsonDict:
    return ctx.notify(
        lambda: ctx.operator.close_room(
            reason=0, description="single-case-close"),
        C.ROOMCLOSED,
        {"Description": "single-case-close"},
    )


CONF_CASES: Dict[str, ConfCase] = {
    case.name: case for case in [
        ConfCase("front_auth_type", "30014/30015", "Front authentication type", _case_front_auth_type),
        ConfCase("front_room_list", "30002/30003", "Front room list", _case_front_room_list),
        ConfCase("front_discover_node", "30002/30003", "Get RoomNodeID even when room list fails", _case_front_discover_node),
        ConfCase("offline_users", "6503/6603", "Offline member query", _case_offline_users),
        ConfCase(
            "offline_users_repeat",
            "6503/6603",
            "Repeated API reads reuse the per-login 6503 cache",
            _case_offline_users_repeat,
        ),
        ConfCase(
            "offline_users_refresh",
            "6503/6603",
            "Optional repeated 6503 wire-query capability",
            _case_offline_users_refresh,
            requires="server branch with repeated 6503 support",
        ),
        ConfCase("dept_info", "6528/6628", "Department query", _case_dept_info),
        ConfCase("room_config", "6534/6634", "Room configuration query", _case_room_config),
        ConfCase("chat", "6512/6612", "A sends chat, B observes", _case_chat),
        ConfCase("av_info", "6508/6608", "A reports an audio device", _case_av_info),
        ConfCase("user_audio", "6509/6609", "A controls B audio and restores it", _case_user_audio),
        ConfCase("user_video", "6509/6609", "A controls B video and restores it", _case_user_video),
        ConfCase("record", "6510/6610", "Start recording and restore stopped state", _case_record),
        ConfCase("room_lock", "6510/6610", "Lock room and restore unlocked state", _case_room_lock),
        ConfCase("mute_all", "6514/6614", "Mute-all notification", _case_mute_all),
        ConfCase("subtitle", "6515/6615", "Set subtitle and close it", _case_subtitle),
        ConfCase("close_subtitle", "6550/6650", "Close subtitle response/observer notification", _case_close_subtitle),
        ConfCase("system_message", "6518/6618", "Configured system message to target", _case_system_message),
        ConfCase("host_guard", "6511+6512/6612", "Reject empty chair password and keep Conf healthy", _case_host_guard),
        ConfCase("send_file", "6516/6616", "File-transfer notification only", _case_send_file),
        ConfCase("room_data", "6519/6619", "Room custom data", _case_room_data),
        ConfCase("av_mix_get", "6520/6620", "Query AV mix parameters", _case_av_mix_get),
        ConfCase("right_invalid", "6530/6630", "Reject invalid operation-right types", _case_invalid_right_types),
        ConfCase("server_fix_regression", "6503/6520/6528/6530", "Mainline server response paths in one session", _case_server_fix_regression),
        ConfCase("av_mix_roundtrip", "6520/6521", "Write current AV mix values and read back", _case_av_mix_roundtrip),
        ConfCase("video_subtitle", "6522/6622", "Video subtitle style", _case_video_subtitle),
        ConfCase("window_state", "6513/6613", "Window state notification", _case_window_state),
        ConfCase("layout", "6530+6513/6613", "Acquire layout right and set layout", _case_layout),
        ConfCase("role_presenter", "6509/6609", "Set and restore presenter role", _case_role_presenter),
        ConfCase("role_admin", "6509/6609", "Set and restore administrator role", _case_role_admin),
        ConfCase("aux_media", "6530+6509/6609", "Start and stop media share", _case_aux_media),
        ConfCase("aux_vnc", "6530+6509/6609", "Start and stop screen share", _case_aux_vnc),
        ConfCase("polling", "6530+6533/6633", "Create and delete video polling", _case_polling),
        ConfCase("role_permissions", "6532/6632", "Change and reset role chat permissions", _case_role_permissions,
                 destructive=True, requires="dedicated test tenant"),
        ConfCase("user_permissions", "6535/6635", "Change and reset user chat permissions", _case_user_permissions,
                 destructive=True, requires="dedicated test user"),
        ConfCase("group_create", "6523/6623+6525/6625", "Create and end a group", _case_group_create),
        ConfCase("group_enter", "6523-6526", "Create group and move observer into it", _case_group_enter),
        ConfCase("recall_message", "6512/6612+6568/6668/6669", "Send and recall a chat message", _case_recall_message),
        ConfCase("call_list", "6746/6747", "Query terminal call list", _case_call_list),
        ConfCase("terminal_call", "6741-6745", "Call and hang up an H323 terminal", _case_terminal_call,
                 destructive=True, requires="terminal_sns and H323 gateway"),
        ConfCase("logout", "6505/6605", "Observer leaves and operator observes", _case_logout),
        ConfCase("kick_user", "6506/6606", "Kick observer from room", _case_kick_user,
                 destructive=True, requires="dedicated test meeting"),
        ConfCase("close_room", "6507/6607", "Close the test room", _case_close_room,
                 destructive=True, requires="disposable test meeting"),
    ]
}


HTTP_INFRA_METHODS = {
    "from_config", "request", "request_bytes", "download",
    "get", "post", "put", "delete", "control",
}
HTTP_READ_ONLY_METHODS = {
    "list_departments", "list_users", "user_online",
    "download_meeting_summary", "list_room_record_summaries",
    "list_record_summaries", "export_meeting_summary", "export_transcript",
    "get_record_summary", "list_record_participants", "list_record_tasks",
    "list_records", "get_record_share", "get_web_control_url",
    "get_meeting_summary", "query_record_settings", "list_rooms_http",
    "get_room_login_addr", "query_meeting_log", "list_online_rooms",
    "query_room_log", "get_room_user_num", "check_user_in_room",
    "list_user_rooms", "list_room_participants", "list_authorized_users",
    "query_user_info", "list_meeting_users", "query_meeting_info",
    "list_subscriptions",
}
CONF_DESTRUCTIVE_METHODS = {
    "kick_user", "close_room", "set_role_permissions", "set_user_permissions",
    "rename_meeting", "end_groups", "conf_hangup", "invite_terminals",
}


def _http_methods() -> List[str]:
    return sorted(
        name for name, member in inspect.getmembers(WebApiClient, callable)
        if not name.startswith("_") and name not in HTTP_INFRA_METHODS
    )


def _build_conf(args) -> Tuple[Optional[MockFmServer], FmTestClient, FmTestClient, JsonDict]:
    if args.mock:
        server = MockFmServer()
        config = {
            "host": server.host,
            "port": server.port,
            "room_id": 100001,
            "room_node_id": "1",
            "room_app_id": 11,
            "request_timeout": args.timeout,
            "accounts": [
                {"username": "single-operator", "password": "pwd"},
                {"username": "single-observer", "password": "pwd"},
            ],
        }
    else:
        server = None
        config = load_config(args.config)
    operator: Optional[FmTestClient] = None
    observer: Optional[FmTestClient] = None
    try:
        operator, observer = connect_pair(config)
        return server, operator, observer, config
    except Exception:
        _close_client(observer)
        _close_client(operator)
        if server is not None:
            server.stop()
        raise


def _references(ctx: ConfContext) -> Dict[str, Any]:
    return {
        "$operator.room_user_id": ctx.operator.room_user_id_int,
        "$operator.local_user_id": ctx.operator.local_user_id_int,
        "$observer.room_user_id": ctx.observer.room_user_id_int,
        "$observer.local_user_id": ctx.observer.local_user_id_int,
        "$room_id": ctx.operator.room_id,
    }


def _resolve(value: Any, refs: Dict[str, Any]) -> Any:
    if isinstance(value, str) and value in refs:
        return refs[value]
    if isinstance(value, list):
        return [_resolve(item, refs) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, refs) for key, item in value.items()}
    return value


def _public_method(client: Any, name: str) -> Callable[..., Any]:
    if name.startswith("_"):
        raise ValueError("private methods are not allowed")
    method = getattr(client, name, None)
    if not callable(method):
        raise ValueError(f"unknown public method: {name}")
    return method


def _run_generic_conf(ctx: ConfContext, args) -> JsonDict:
    if args.method in CONF_DESTRUCTIVE_METHODS and not args.allow_destructive:
        raise ValueError(
            f"{args.method} changes shared/external state; add --allow-destructive")
    refs = _references(ctx)
    call_args = _resolve(_json_list(args.call_args), refs)
    call_kwargs = _resolve(_json_dict(args.call_kwargs), refs)
    expected = _resolve(_json_dict(args.expect_fields), refs)
    cleanup_args = _resolve(_json_list(args.cleanup_args), refs)
    cleanup_kwargs = _resolve(_json_dict(args.cleanup_kwargs), refs)
    method = _public_method(ctx.operator, args.method)

    target_name = args.expect_client
    if args.expect_kind == "response":
        target_name = "operator"
    target = ctx.operator if target_name == "operator" else ctx.observer
    mark = target.events.mark()
    right_acquired = False
    try:
        if args.right_type is not None:
            response = ctx.operator.request_right(
                args.right_type, timeout=ctx.timeout)
            if str(response.get("Result", "0")) != "0":
                raise AssertionError(f"request_right failed: {response}")
            right_acquired = True
        result = method(*call_args, **call_kwargs)
        event_fields = None
        if args.expect_cmdid is not None:
            event = target.events.wait_event(
                after=mark,
                source=args.expect_source,
                cmdid=args.expect_cmdid,
                kind=args.expect_kind,
                match=expected or None,
                timeout=ctx.timeout,
            )
            if event is None:
                raise AssertionError(
                    f"event timeout: recent={_dump(_recent(target, mark))}")
            event_fields = event.fields
        else:
            print("[WARN] no --expect-cmdid: only method completion is verified")
        return {"result": result, "event": event_fields}
    finally:
        if args.cleanup_method:
            try:
                cleanup = _public_method(ctx.operator, args.cleanup_method)
                cleanup(*cleanup_args, **cleanup_kwargs)
            except Exception as exc:
                print(f"[WARN] cleanup {args.cleanup_method} failed: {exc}")
        if right_acquired:
            try:
                ctx.operator.release_right(args.right_type, timeout=ctx.timeout)
            except Exception as exc:
                print(f"[WARN] release_right failed: {exc}")


def run_conf(args) -> int:
    case = CONF_CASES.get(args.case) if args.case else None
    if case and case.destructive and not args.allow_destructive:
        raise ValueError(
            f"case {case.name} changes shared/external state; "
            "use a dedicated environment and add --allow-destructive")

    server: Optional[MockFmServer] = None
    operator: Optional[FmTestClient] = None
    observer: Optional[FmTestClient] = None
    try:
        server, operator, observer, config = _build_conf(args)
        params = dict(config.get("case_params") or {})
        params.update(_json_dict(args.params))
        ctx = ConfContext(operator, observer, args.timeout, params)
        print(
            f"operator={operator.room_user_id_int}, "
            f"observer={observer.room_user_id_int}, room={operator.room_id}")
        result = case.handler(ctx) if case else _run_generic_conf(ctx, args)
        label = case.name if case else args.method
        print(f"[PASS] {label}\n{_dump(result)}")
        return 0
    finally:
        _close_client(observer)
        _close_client(operator)
        if server is not None:
            server.stop()


def run_http(args) -> int:
    methods = _http_methods()
    if args.method not in methods:
        raise ValueError(
            f"unknown HTTP business method {args.method!r}; use 'list --channel http'")
    if args.method not in HTTP_READ_ONLY_METHODS and not args.allow_destructive:
        raise ValueError(
            f"{args.method} changes server state; add --allow-destructive")
    api = WebApiClient.from_config(args.http_config)
    method = getattr(api, args.method)
    result = method(*_json_list(args.call_args), **_json_dict(args.call_kwargs))
    expected = _json_dict(args.expect)
    if expected and not _matches(result, expected):
        raise AssertionError(
            f"HTTP response mismatch: expected={expected}, response={result}")
    if isinstance(result, bytes):
        if not result:
            raise AssertionError("HTTP download returned empty bytes")
        printable: Any = {"bytes": len(result), "prefix_hex": result[:16].hex()}
    else:
        printable = result
    print(f"[PASS] {args.method}\n{_dump(printable)}")
    return 0


def run_list(args) -> int:
    if args.channel in ("all", "conf"):
        print("Conf built-in cases:")
        for case in CONF_CASES.values():
            flags = []
            if case.destructive:
                flags.append("destructive")
            if case.requires:
                flags.append(case.requires)
            suffix = f" [{'; '.join(flags)}]" if flags else ""
            print(f"  {case.name:20s} {case.protocols:22s} {case.description}{suffix}")
    if args.channel in ("all", "http"):
        print("HTTP business methods:")
        for method in _http_methods():
            suffix = " [changes server state]" if method not in HTTP_READ_ONLY_METHODS else ""
            print(f"  {method}{suffix}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one fm_client HTTP API or Conf protocol verification.")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="list available cases/methods")
    list_parser.add_argument(
        "--channel", choices=("all", "conf", "http"), default="all")

    conf = sub.add_parser("conf", help="run one Conf/Front case")
    mode = conf.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true", help="use MockFmServer")
    mode.add_argument("--config", help="real-server multi_client.json")
    action = conf.add_mutually_exclusive_group(required=True)
    action.add_argument("--case", choices=tuple(CONF_CASES), help="built-in case")
    action.add_argument("--method", help="generic FmTestClient public method")
    conf.add_argument("--params", default="{}", help="JSON object for a built-in case")
    conf.add_argument("--args", dest="call_args", default="[]", help="generic positional JSON array")
    conf.add_argument("--kwargs", dest="call_kwargs", default="{}", help="generic keyword JSON object")
    conf.add_argument("--expect-client", choices=("operator", "observer"), default="observer")
    conf.add_argument("--expect-source", choices=("entrance", "front", "conf"), default="conf")
    conf.add_argument("--expect-cmdid", type=int)
    conf.add_argument("--expect-kind", choices=("response", "notify"), default="notify")
    conf.add_argument("--expect-fields", default="{}", help="dot-path JSON object")
    conf.add_argument("--right-type", type=int)
    conf.add_argument("--cleanup-method")
    conf.add_argument("--cleanup-args", default="[]")
    conf.add_argument("--cleanup-kwargs", default="{}")
    conf.add_argument("--timeout", type=float, default=8.0)
    conf.add_argument("--allow-destructive", action="store_true")

    http = sub.add_parser("http", help="run one WebApiClient method")
    http.add_argument("--http-config", required=True)
    http.add_argument("--method", required=True)
    http.add_argument("--args", dest="call_args", default="[]", help="positional JSON array")
    http.add_argument("--kwargs", dest="call_kwargs", default="{}", help="keyword JSON object")
    http.add_argument("--expect", default="{}", help="dot-path JSON response assertions")
    http.add_argument("--allow-destructive", action="store_true")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "list":
            return run_list(args)
        if args.command == "conf":
            return run_conf(args)
        return run_http(args)
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
