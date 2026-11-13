"""Generate expanded FM/HTTP closure cases and coverage ledgers."""
from __future__ import annotations

import argparse, hashlib, json, sys, copy
from pathlib import Path
from itertools import product

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

ROOT = Path(__file__).resolve().parents[1]
SESSIONMANAGER = ROOT / "sessionmanager_py"
sys.path[:0] = [str(ROOT), str(SESSIONMANAGER)]

from meeting_test_runtime.conf_audit import scan_conf_protocol  # noqa: E402
from meeting_test_runtime.expectations import OPERATION_EXPECTATIONS  # noqa: E402
from meeting_test_runtime.fm_driver import FmClientDriver  # noqa: E402
from meeting_test_runtime.http_driver import WebApiServiceDriver  # noqa: E402
from meeting_test_runtime.blockers import blocker_for_cmdid, blocker_for_operation  # noqa: E402
from meeting_test_runtime.models import DeliveryPolicy  # noqa: E402


MISSING_CMDIDS = set()
PARTIAL_CMDIDS = set()
REAL_SERVER_VERIFIED_CMDIDS = {
    6512, 6612, 6513, 6613,
    6529, 6629, 6542, 6642, 6543, 6643, 6631, 6637, 6639, 6654}
MISSING_SERVER_HANDLER_CMDIDS = {6528, 6628}
RISKY_PROTOCOL_PROBE_CMDIDS = {6534, 6634}
PRODUCT_UNUSED_OPERATIONS = {
    "front_roomlist", "front_roomlist_classified", "list_rooms",
    "get_av_mix_params", "set_av_mix_params",
}
DOWNLINK_OPERATIONS = {
    6517: ["conf_login"], 6602: ["conf_login"], 6611: ["apply_host"],
    6617: ["allow_knock"], 6629: ["chat"],
    6631: ["conf_login", "set_role_permissions"],
    6637: ["start_record"],
    6639: ["conf_login"], 6646: ["set_user_state"],
    6543: ["request_remote_av"], 6643: ["request_remote_av"],
    6666: ["create_group"], 6667: ["allow_waiting_users"],
}

BASE_PARAMS = {
    "front_roomlist": {"is_get_all": 1, "is_get_desc": 1},
    "front_roomlist_classified": {"is_get_all": 1, "is_get_desc": 1},
    "front_getroomid": {"login_room_id": "${env.meeting_no}"},
    "front_login_by_invite_code": {"invite_code": "${env.invite_code}"},
    "chat": {"text": "协议闭环<&>"}, "set_audio_state": {"state": 2},
    "set_video_state": {"state": 2}, "kick_user": {"user_id": "${observer.room_user_id}"},
    "close_room": {"reason": 0, "description": "自动化关会"},
    "report_av_info": {
        "audio": [{"Operation": 1, "ID": 0, "Name": "single-case-mic"}],
    },
    "apply_host": {"chair_pwd": "${env.chair_password}"},
    "set_conf_state": {"enable_chat": 0},
    "allow_knock": {"user_id": "${observer.room_user_id}", "allow": 1},
    "create_group": {"rooms": [{"Theme": "组1", "MaxUserCount": 2,
                                 "EnableJoin": 1, "EnableLeave": 1,
                                 "User": [{"ID": "${observer.room_user_id}", "Right": 2}]}]},
    "enter_group": {"room_id": 1, "users": [{"ID": "${observer.room_user_id}"}]},
    "end_groups": {"room_ids": [1]}, "bring_to_main": {"user_ids": ["${observer.room_user_id}"]},
    "request_right": {"right_type": 1}, "release_right": {"right_type": 1},
    "set_role_permissions": {"role_codes": ["mentee"], "permissions": {"OperatePublicChat": 0}},
    "set_user_permissions": {"user_id": "${observer.room_user_id}", "permissions": {"OperatePrivateChat": 0}},
    "set_room_data": {"items": [{"ItemID": 0,
                                    "RoomData": {"Key": "e2e",
                                                 "Value": "value<&>"}}]},
    "set_av_mix_params": {"enable_default": True},
    "set_video_subtitle_params": {
        "date_time": {"Pos": 1, "Size": 2, "Color": 0}},
    "rename_meeting": {"name": "自动化会议<&>"},
    "set_subtitle": {"data": "字幕<&>", "duration": 5},
    "send_sysmsg": {"dst_user_id": "${observer.room_user_id}",
                    "sys_msg_id": "${env.sys_msg_id}"},
    "send_file": {"dst_user_ids": ["${observer.room_user_id}"], "file_info": {"GUID": "e2e", "Name": "a.txt", "Size": 1}},
    "recall_msg": {"msg_id": "${env.message_id}"},
    "set_window_state": {"mode": 3, "video_relay_id": 1001},
    "set_user_audio": {"user_id": "${observer.room_user_id}", "state": 2, "dev_id": 0},
    "set_user_video": {"user_id": "${observer.room_user_id}", "state": 2, "dev_id": 1},
    "set_layout": {"layout_type": 1001,
                   "user_ids": ["${operator.room_user_id}", "${observer.room_user_id}"],
                   "target_user_ids": ["${observer.room_user_id}"]},
    "set_role": {"user_id": "${observer.room_user_id}", "role": "presenter", "enabled": True},
    "start_aux_stream": {"kind": "media", "audio_share_id": 1, "video_share_id": 1},
    "stop_aux_stream": {"kind": "media"},
    "configure_polling": {"operation": 1, "polling_id": 1001, "name": "轮询1",
                          "user_ids": ["${operator.room_user_id}", "${observer.room_user_id}"],
                          "interval": 5, "screen": 1, "window": 1, "status": 2},
    "conf_invite_call": {"callees": [{"CalleeNum": "${env.h323}"}], "call_type": 2},
    "invite_terminals": {"sns_list": ["${env.h323}"]},
    "conf_hangup": {"callee_nums": ["${env.h323}"], "call_type": 2},
    "conf_query_call_list": {"call_type": 2},
    "invite_users": {"user_ids": ["${env.invitee_user_id}"]},
    "invite_contacts": {"user_ids": ["${env.invitee_user_id}"]},
    "lock_layout": {"enabled": True},
    "create_instant_meeting": {"meeting_name": "${unique.name}", "user_name": "${env.http_user}"},
    "create_reserved_meeting": {"room_name": "${unique.name}", "hope_start_time": "${env.start_time}", "hope_end_time": "${env.end_time}"},
    "cancel_reserved_meeting": {"room_id": "${env.reserved_room_id}"},
    "set_multi_data_sharer": {"enabled": True, "force": False},
    "get_user_features": {"user_id": "${observer.room_user_id}"},
    "query_feature_users": {"feature": "NewVote", "subscribe": False},
    "unsubscribe_feature_users": {"feature": "NewVote"},
    "set_user_state": {"audio_out_mute": 1},
    "reply_self_state_change": {
        "dst_user_id": "${operator.room_user_id}", "vnc_state": 0},
    "renegotiate_layout": {
        "layouts": ["W1", "WV4", "WV9"], "screen_count": 1},
    "send_h5_vote": {
        "vote_id": 9001, "state": 2, "duration": 1,
        "user_ids": ["${observer.room_user_id}"],
        "data": {"Title": "E2E投票"}},
    "modify_group_config": {
        "rooms": [{"RoomID": "${group.room_id}", "Theme": "E2E修改分组"}]},
    "change_group_state": {"room_ids": ["${group.room_id}"], "state": 1},
    "allow_waiting_users": {
        "users": [{"UserID": "${observer.room_user_id}", "Allow": 1}]},
    "set_asr_subtitle": {
        "enabled": True, "translation": True,
        "src_language": "zh-cn", "target_language": "zh-cn"},
    "set_user_custom_data": {
        "user_id": "${operator.room_user_id}", "public_data": "E2E自定义数据<&>"},
    "request_remote_av": {
        "kind": "audio", "accept": True, "device_id": 0},
}

HTTP_PARAMS = {
    "invite_terminals_http": {"room_id": "${operator.room_id}", "call_info": [{"calleeNumber": "${env.h323}"}], "call_type": 2},
    "control_user_audio": {"room_id": "${operator.room_id}", "user_id": "${observer.room_user_id}", "device_id": 0, "state": 2},
    "control_user_video": {"room_id": "${operator.room_id}", "user_id": "${observer.room_user_id}", "device_id": 1, "state": 2},
    "set_layout_http": {
        "room_id": "${operator.room_id}",
        "layout": {"mode": 3, "full": 0, "dataActive": 6,
                   "videoRelayId": 4}},
    "lock_meeting": {"room_id": "${operator.room_id}", "lock": True},
    "mute_all_http": {"room_id": "${operator.room_id}"},
    "send_subtitles": {"room_id": "${operator.room_id}", "font_size": 24,
                       "font_color": 0xFFFFFF, "subtitle_type": 0,
                       "align": 1, "content": "HTTP字幕"},
    # HTTP会控录制枚举与Conf状态不同：2开始，0停止；1会返回4019。
    "operate_record": {"room_id": "${operator.room_id}", "state": 2},
    "set_user_manager": {"room_id": "${operator.room_id}", "user_id": "${observer.room_user_id}", "state": 2},
    "set_user_presenter": {"room_id": "${operator.room_id}", "user_id": "${observer.room_user_id}", "state": 2},
    "remove_user": {"room_id": "${operator.room_id}", "user_id": "${observer.room_user_id}"},
    "close_meeting": {"room_id": "${operator.room_id}"},
    "send_room_message": {
        "room_id": "${operator.room_id}", "user_field_type": 1,
        "user_field_value": "${observer.room_user_id}",
        "operator": "E2E自动化", "app_back_notify": "Web通知<&>\""},
}

RESULT_PARAMS = {
    "get_meeting_summary": {"groupId": "${env.record_group_id}"},
    "list_room_record_summaries": {"room_id": "${operator.room_id}"},
    "list_record_summaries": {}, "get_record_summary": {"group_id": "${env.record_group_id}"},
    "list_record_participants": {"group_id": "${env.record_group_id}"},
    "list_record_tasks": {"group_id": "${env.record_group_id}"},
    "list_records": {"group_id": "${env.record_group_id}"},
    "get_record_share": {"group_id": "${env.record_group_id}"},
    "download_meeting_summary": {"group_id": "${env.record_group_id}"},
    "export_meeting_summary": {"group_id": "${env.record_group_id}"},
    "export_transcript": {"group_id": "${env.record_group_id}"},
}


def _sheet(book, title, headers):
    sheet = book.create_sheet(title); sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF"); cell.fill = PatternFill("solid", fgColor="4472C4")
    sheet.freeze_panes = "A2"; sheet.auto_filter.ref = f"A1:{sheet.cell(1,len(headers)).coordinate}"
    return sheet


def _slug(value): return hashlib.sha1(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]


def _case_signature(case):
    """Identity of one business action; assertion subsets are merged below."""
    return (
        case["operation"],
        json.dumps(case["params"], sort_keys=True, ensure_ascii=False),
        case["kind"], case["pre"], case["outcome"], case["closure"],
    )


def _csv_union(left, right):
    return ",".join(dict.fromkeys(
        item.strip() for value in (left, right)
        for item in str(value or "").split(",") if item.strip()))


def _merge_dict(target, incoming, label):
    for key, value in incoming.items():
        if key in target and str(target[key]) != str(value):
            raise AssertionError(
                f"duplicate business case has conflicting {label}: {key}="
                f"{target[key]!r}/{value!r}")
        target[key] = value


def _merge_case(target, incoming):
    """Merge compatible assertion subsets instead of keeping duplicate rows."""
    _merge_dict(target["response"], incoming["response"], "response")
    _merge_dict(target["fields"], incoming["fields"], "field assertion")
    target["errors"] = list(dict.fromkeys(target["errors"] + incoming["errors"]))
    target["receivers"] = _csv_union(target["receivers"], incoming["receivers"])
    target["forbidden"] = _csv_union(target["forbidden"], incoming["forbidden"])
    notify_index = {
        (item.get("source"), item.get("cmdid"), item.get("kind")): item
        for item in target["notifies"]}
    for notify in incoming["notifies"]:
        key = (notify.get("source"), notify.get("cmdid"), notify.get("kind"))
        current = notify_index.get(key)
        if current is None:
            target["notifies"].append(notify)
            notify_index[key] = notify
            continue
        _merge_dict(current.setdefault("match", {}), notify.get("match", {}),
                    "notify assertion")
    if incoming.get("design_note") and incoming["design_note"] not in target.get("design_note", ""):
        target["design_note"] = "；".join(filter(None, (
            target.get("design_note"), incoming["design_note"])))


MANUAL_BLOCKER_IDS = {
    "configure_polling": "BLK-POLLING-BOUNDARY-CONTRACT",
    "set_layout": "BLK-LAYOUT-INVALID-ENUM",
    "request_right": "BLK-RIGHT-INVALID-ENUM",
    "release_right": "BLK-RIGHT-INVALID-ENUM",
    "set_video_state": "BLK-VIDEO-INVALID-ENUM",
    "set_conf_state": "BLK-CONF-STATE-CONTRACT",
    "reply_self_state_change": "BLK-REMOTE-STATE-FLOW",
    "unsubscribe_feature_users": "BLK-FEATURE-SUBSCRIPTION-FLOW",
    "recall_msg": "BLK-CHAT-RECALL-FLOW-ONLY",
    "allow_waiting_users": "BLK-WAITING-ROOM-FLOW",
    "bring_to_main": "BLK-DYNAMIC-GROUP-LIFECYCLE",
    "change_group_state": "BLK-DYNAMIC-GROUP-LIFECYCLE",
    "end_groups": "BLK-DYNAMIC-GROUP-LIFECYCLE",
    "enter_group": "BLK-DYNAMIC-GROUP-LIFECYCLE",
    "modify_group_config": "BLK-DYNAMIC-GROUP-LIFECYCLE",
}


def _blocker_id(case):
    if case.get('blocker_id'):
        return case['blocker_id']
    blocker = blocker_for_operation(case["operation"])
    if blocker is not None:
        if blocker.blocker_id:
            return blocker.blocker_id
        key = (min(blocker.cmdids) if blocker.cmdids else
               sorted(blocker.operations)[0].upper())
        return f"BLK-{blocker.kind.value}-{key}"
    if case["status"] == "SKIP_BLOCKED":
        return MANUAL_BLOCKER_IDS.get(case["operation"],
                                      f"BLK-{case['operation'].upper()}")
    return ""


def _custom_data_case(params, suffix):
    case = _case(
        "set_user_custom_data", params,
        "聊天、文件、系统消息、房间数据",
        isolated=True, suffix=suffix)
    common = {
        "SrcUserID": "${operator.room_user_id}",
        "UserID": params["user_id"],
    }
    notifies = []
    if "public_data" in params:
        notifies.append({
            "source": "conf", "cmdid": 6672, "kind": "notify",
            "match": {**common, "PublicData": params["public_data"]},
            "receivers": ["operator", "observer"],
        })
    if "private_data" in params:
        notifies.append({
            "source": "conf", "cmdid": 6672, "kind": "notify",
            "match": {**common, "PrivateData": params["private_data"]},
            "receivers": ["observer"],
            "forbidden_receivers": ["operator"],
        })
    case.update(
        notifies=notifies, receivers="", forbidden="", fields={},
        risk="ISOLATED_STATEFUL", status="IMPLEMENTED", enabled=True,
        design_note="6572无同步Response；按公共/私有投递范围断言6672，并检查A/B Conf健康")
    return case


def _receivers(policy):
    return {DeliveryPolicy.ALL_PARTICIPANTS: "operator,observer",
            DeliveryPolicy.OTHERS_ONLY: "observer", DeliveryPolicy.SENDER_ONLY: "operator",
            DeliveryPolicy.TARGET_ONLY: "observer", DeliveryPolicy.SENDER_AND_TARGET: "operator,observer"}.get(policy, "")


def _case(operation, params, module, *, kind="NORMAL", level="P1", pre="NONE",
          target="", actor="operator", closure=None, isolated=False, errors=(), suffix="base"):
    meta = OPERATION_EXPECTATIONS.get(operation)
    closure = closure or (meta.closure_type.value if meta else "HTTP_RESULT")
    notifies = [{"source": n.source, "cmdid": n.cmdid, "kind": n.kind,
                 "match": dict(n.match)}
                for n in (meta.notifies if meta else ())]
    receivers = _receivers(meta.delivery) if meta else ""
    forbidden = "operator" if meta and "actor" in meta.forbidden_delivery else ""
    outcome = "ERROR_AND_NO_EVENT" if kind == "BUSINESS_EXCEPTION" else (
        "SUCCESS_EVENT" if notifies else "SUCCESS_RESPONSE")
    field_match = {}
    if operation == "chat":
        field_match = {"SrcUserID": "${operator.room_user_id}",
                       "DstUserID": params.get("dst_user_id", 0), "Text": params.get("text", "")}
        if params.get("color") is not None:
            field_match["Color"] = params["color"]
        for key, value in (params.get("font") or {}).items():
            if value is not None:
                field_match[f"Font.{key}"] = value
    elif operation == "report_av_info":
        field_match = {"Audio.0.ID": 0, "Audio.0.Name": "single-case-mic"}
    elif operation == "set_window_state":
        field_match = {"Mode": params.get("mode", 3),
                       "VideoRelayID": params.get("video_relay_id", 1001)}
        for source, wire in (("full", "Full"), ("data_active", "DataActive"),
                             ("full_video_id", "FullVideoID"),
                             ("full_video_media_id", "FullVideoMediaID"),
                             ("valid_full_mode", "ValidFullMode")):
            if params.get(source) is not None:
                field_match[wire] = params[source]
    elif operation == "set_layout":
        field_match = {"Mode": params.get("mode", 3),
                       "VideoRelayID": params.get("layout_type")}
        for source, wire in (("full", "Full"), ("data_active", "DataActive"),
                             ("full_video_id", "FullVideoID"),
                             ("full_video_media_id", "FullVideoMediaID"),
                             ("valid_full_mode", "ValidFullMode")):
            if params.get(source) is not None:
                field_match[wire] = params[source]
        videos = params.get("videos") or [
            {"UserID": user_id, "MediaID": 0, "Pos": index}
            for index, user_id in enumerate(params.get("user_ids") or [])]
        for index, video in enumerate(videos):
            for field in ("UserID", "MediaID", "Pos"):
                if field in video:
                    field_match[f"Video.{index}.{field}"] = video[field]
    elif operation == "configure_polling":
        users = params.get("user_ids") or []
        field_match = {"VideoPolling.val": params.get("polling_id"),
                       "VideoPolling.Operation": 1}
        if users:
            field_match["VideoPolling.CurrentUserID"] = users[0]
        if len(users) > 1:
            field_match["VideoPolling.NextUserID"] = users[1]
        for source, wire in (("interval", "Interval"), ("screen", "Screen"),
                             ("window", "Window")):
            if params.get(source) is not None:
                field_match[f"VideoPolling.{wire}"] = params[source]
    elif operation == "set_multi_data_sharer":
        enabled = int(bool(params.get("enabled")))
        response_match = {"EnableMultiDataSharer": enabled}
        field_match = {"EnableMultiDataSharer": enabled}
    elif operation == "get_user_features":
        response_match = {"UserID": params.get("user_id")}
    elif operation == "query_feature_users":
        response_match = {"Feature": params.get("feature")}
    elif operation == "set_user_state":
        field_match = {"UserID": "${operator.room_user_id}"}
        wire_fields = {
            "audio_out_mute": "AudioOutMute", "wb_mark_state": "WBMarkState",
            "data_state": "DataState", "media_share_state": "MediaShareState",
            "vnc_state": "VNCState",
        }
        for source, wire in wire_fields.items():
            if source in params:
                field_match[wire] = params[source]
    elif operation == "renegotiate_layout":
        response_match = {"Layout.Code": ",".join(params.get("layouts") or [])}
    elif operation == "send_h5_vote":
        field_match = {"ID": params.get("vote_id"), "State": params.get("state")}
    elif operation == "set_asr_subtitle":
        # Root ConfProtocol defines 6671 as a requester-only response carrying
        # Result. It does not echo the requested language/enable fields.
        response_match = {"Result": 0}
    elif operation == "apply_host":
        field_match = {
            "UserID": "${operator.room_user_id}", "Right": 3}
    elif operation == "release_host":
        field_match = {
            "UserID": "${operator.room_user_id}", "Right": 2}
    elif operation == "operate_record":
        field_match = {"ServerRecordStatus": params.get("state")}
    elif operation in {"request_right", "release_right"}:
        operation_value = 1 if operation == "request_right" else 0
        response_match = {"Result": 0, "Operation": operation_value,
                          "RightType": params.get("right_type"),
                          "OccupiedUserID": ("${operator.room_user_id}"
                                             if operation == "request_right" else 0)}
    elif operation in {"conf_invite_call", "invite_terminals"}:
        response_match = {"OprResult": 0, "CallType": 2}
        field_match = {"CalleeNum": "${env.h323}"}
    else:
        response_match = {}
    if operation not in {
            "request_right", "release_right", "conf_invite_call", "invite_terminals",
            "set_multi_data_sharer", "get_user_features", "query_feature_users",
            "renegotiate_layout", "set_asr_subtitle"}:
        response_match = {}
    blocker = blocker_for_operation(operation)
    risk = ("BLOCKED" if blocker else
            "DESTRUCTIVE_ISOLATED" if isolated else
            "ISOLATED_STATEFUL" if pre != "NONE" else "SAFE_REUSABLE")
    return {"id": f"FM-{module}-{operation}-{kind[0]}-{suffix}-{_slug(params)}", "module": module,
            "closure": closure, "kind": kind, "scene": "BASIC_MEETING", "pre": pre,
            "actor": actor, "target": target, "operation": operation, "params": params,
            "outcome": outcome, "business": operation, "response": response_match, "notifies": notifies,
            "receivers": receivers, "forbidden": forbidden, "fields": field_match, "errors": list(errors),
            "isolated": isolated, "level": level, "enabled": blocker is None,
            "risk": risk, "status": "SKIP_BLOCKED" if blocker else "IMPLEMENTED",
            "flow_id": "", "design_note": ""}


def _build_existing_cases():
    cases = []
    deprecated_operations = {
        "front_login_by_meeting_no", "front_getroomid", "conf_get_dept_info",
        *PRODUCT_UNUSED_OPERATIONS}
    dynamic_flow_operations = {
        "enter_group", "end_groups", "bring_to_main", "recall_msg",
        "modify_group_config", "change_group_state", "allow_waiting_users",
        "reply_self_state_change", "unsubscribe_feature_users"}
    scenario_only_operations = {
        "reply_remote_av", "respond_remote_av", "set_chat_check",
        "review_chat_message", "review_chat_message_and_wait",
        "set_interactive_language", "set_interactive_language_and_wait",
        "request_face_recognition", "start_face_rollcall",
        "report_face_rollcall_result", "cancel_face_rollcall",
    }
    destructive = {"kick_user", "close_room", "conf_logout", "front_logout", "conf_hangup",
                   "conf_invite_call", "invite_terminals",
                   "cancel_reserved_meeting"}
    p0_operations = {
        "front_login", "conf_login", "mic_on", "camera_on", "set_user_audio",
        "set_layout", "set_role", "start_record", "lock_room", "mute_all",
        "set_subtitle", "invite_users", "create_instant_meeting", "kick_user", "close_room",
    }
    base_preconditions = {
        "mic_on": "AUDIO_OFF", "mic_off": "AUDIO_ON", "mic_request": "AUDIO_OFF",
        "camera_on": "VIDEO_OFF", "camera_off": "VIDEO_ON",
        "set_user_audio": "TARGET_AUDIO_OFF",
        "set_user_video": "TARGET_VIDEO_OFF",
        "start_record": "RECORDING_STOPPED", "pause_record": "RECORDING",
        "stop_record": "RECORDING", "lock_room": "ROOM_UNLOCKED",
        "unlock_room": "ROOM_LOCKED", "close_subtitle": "SUBTITLE_SET",
        "start_aux_stream": "MEDIA_RIGHT",
        "stop_aux_stream": "MEDIA_STARTED_WITH_RIGHT",
        "apply_host": "HOST_RELEASED",
        "release_host": "HOST_HELD",
        "set_layout": "LAYOUT_RIGHT",
        "configure_polling": "POLLING_RIGHT",
        "create_group": "GROUP_RIGHT",
        "release_right": "RIGHT_HELD",
    }
    for operation in FmClientDriver.METHOD_NAMES:
        if (operation in deprecated_operations or operation == 'send_relay_message'
                or operation in scenario_only_operations):
            continue
        if operation == "set_user_custom_data":
            case = _custom_data_case(
                BASE_PARAMS[operation], "custom-public")
        else:
            case = _case(operation, BASE_PARAMS.get(operation, {}), "基础能力",
                         level="P0" if operation in p0_operations else "P1",
                         pre=base_preconditions.get(operation, "NONE"),
                         isolated=operation in destructive)
        if operation == "allow_knock":
            case.update(
                enabled=False, status="SKIP_BLOCKED", risk="BLOCKED",
                design_note="MISSING_TRIGGER_CONTRACT：必须先收到真实6604，禁止直接发送6504冒充闭环")
        elif operation in dynamic_flow_operations:
            case.update(
                enabled=False, status="SKIP_BLOCKED", risk="BLOCKED",
                flow_id=("FLOW-CHAT-RECALL" if operation == "recall_msg" else
                         "FLOW-WAITING-ROOM" if operation in {
                             "allow_waiting_users", "reply_self_state_change"} else
                         "FLOW-GROUP-LIFECYCLE"),
                design_note="MISSING_DYNAMIC_PRECONDITION：必须使用前序真实Response/Notify产生的动态ID")
        elif operation == "lock_layout":
            case.update(
                isolated=True, risk="DESTRUCTIVE_ISOLATED",
                design_note="HTTP Response闭环；6654是通用Web通知，不能无字段证据推断为布局锁定Notify")
        elif operation in {"rename_meeting", "set_room_data",
                           "set_video_subtitle_params"}:
            case.update(
                isolated=True, risk="DESTRUCTIVE_ISOLATED",
                design_note="无公开原值查询/可靠反向恢复，使用动态独立会议防止污染共享状态")
        elif operation in {"send_sysmsg", "conf_get_room_config"}:
            case.update(
                isolated=True, risk="DESTRUCTIVE_ISOLATED",
                design_note="历史版本曾导致Conf断会；首次复测固定使用动态独立会议")
        elif operation in {
                "conf_get_offline_users", "offline_contacts",
                "conf_get_contacts", "contacts_snapshot"}:
            case.update(
                isolated=True, risk="ISOLATED_STATEFUL",
                design_note=(
                    "使用可授权固定会议，预置未入会授权成员；每个公开方法"
                    "独立发送6503并断言6603，避免缓存掩盖线路请求。"))
        elif operation == "set_role":
            case.update(
                isolated=True, risk="ISOLATED_STATEFUL",
                design_note="成员原始角色不可查询，使用一次性Runtime，避免反向操作猜测原值")
        elif operation in {"set_role_permissions", "set_user_permissions"}:
            case.update(
                isolated=True, risk="ISOLATED_STATEFUL",
                design_note="权限原值和恢复Notify不稳定；主Notify断言后销毁独立Runtime，不猜测反向恢复值")
        elif operation in {"set_user_audio", "set_user_video"}:
            case.update(
                isolated=True, risk="ISOLATED_STATEFUL",
                design_note="指定成员状态控制使用独立Runtime；主Notify通过后销毁会话，避免立即反向控制竞态")
        elif operation == "start_aux_stream":
            case.update(
                isolated=True, risk="ISOLATED_STATEFUL",
                design_note="辅流开始、停止闭环已由新版SDK真机验收；使用独立Runtime隔离媒体状态")
        elif operation in {"set_multi_data_sharer", "set_user_state",
                           "renegotiate_layout", "send_h5_vote",
                           "set_asr_subtitle"}:
            case.update(
                isolated=True, risk="ISOLATED_STATEFUL",
                design_note="新版SDK扩展协议；使用独立动态会议验证Response/Notify及连接健康")
        elif operation in {"start_record", "pause_record", "stop_record"}:
            case.update(
                isolated=True, risk="ISOLATED_STATEFUL",
                design_note=(
                    "录制状态用例使用动态独立会议；主6610通过后销毁Runtime，"
                    "完整开始/暂停/恢复/停止由依赖流程验证，避免共享会议反向恢复竞态。"))
        cases.append(case)

    cases.extend([
        _custom_data_case({
            "user_id": "${observer.room_user_id}",
            "private_data": "E2E私有数据<&>",
        }, "custom-private"),
        _custom_data_case({
            "user_id": "${observer.room_user_id}",
            "public_data": "E2E公共数据<&>",
            "private_data": "E2E私有数据<&>",
        }, "custom-public-private"),
        _custom_data_case({
            "user_id": "${observer.room_user_id}",
            "public_data": "", "private_data": "",
        }, "custom-clear"),
    ])

    chat_font_cases = [
        ("font-public", {
            "text": "字体群聊<&>", "dst_user_id": 0, "color": 0x112233,
            "font": {"Name": "宋体", "Width": 0, "Height": 16,
                     "Weight": 400, "Underline": 0, "StrikeOut": 0,
                     "CharSet": 134, "Italic": 0},
        }, "P1"),
        ("font-private", {
            "text": "字体私聊", "dst_user_id": "${observer.room_user_id}",
            "font": {"Name": "宋体", "Height": 18, "Weight": 700,
                     "Underline": 1, "Italic": 1},
        }, "P2"),
    ]
    for suffix, params, level in chat_font_cases:
        case = _case("chat", params, "消息字体", level=level, suffix=suffix)
        case.update(design_note="6512 Font按XML属性发送，接收端真实6612逐字段断言")
        cases.append(case)

    fullscreen_cases = [
        ("full-observer-media0", "set_layout", {
            "layout_type": 4,
            "user_ids": ["${operator.room_user_id}", "${observer.room_user_id}"],
            "target_user_ids": ["${observer.room_user_id}"],
            "mode": 3, "full": 1,
            "full_video_id": "${observer.room_user_id}",
            "full_video_media_id": 0, "valid_full_mode": 1,
        }, "P1"),
        ("full-observer-media1", "set_layout", {
            "layout_type": 4,
            "user_ids": ["${operator.room_user_id}", "${observer.room_user_id}"],
            "target_user_ids": ["${observer.room_user_id}"],
            "mode": 3, "full": 1,
            "full_video_id": "${observer.room_user_id}",
            "full_video_media_id": 1, "valid_full_mode": 1,
        }, "P2"),
        ("exit-fullscreen-zero", "set_window_state", {
            "mode": 3, "full": 0, "video_relay_id": 4,
            "full_video_id": 0, "full_video_media_id": 0,
            "valid_full_mode": 0,
        }, "P2"),
    ]
    for suffix, operation, params, level in fullscreen_cases:
        case = _case(operation, params, "布局全屏", level=level,
                     pre="LAYOUT_RIGHT", suffix=suffix)
        case.update(
            receivers="observer", forbidden="operator", risk="ISOLATED_STATEFUL",
            design_note="6513全屏字段真实转发；数值0必须保留，非目标端执行反向断言")
        cases.append(case)

    # Constrained full business combinations.
    # The server supports a single audio device (ID=0). IDs 1/2 belong to
    # rejection coverage and must not inflate the valid device matrix.
    for scope, states in (("self", [0, 1, 2]), ("other", [0, 2])):
        for state in states:
            params = {"state": state, "dev_id": 0}
            if scope == "other":
                params["user_id"] = "${observer.room_user_id}"
            precondition = (
                ("TARGET_AUDIO_ON" if state == 0 else "TARGET_AUDIO_OFF")
                if scope == "other"
                else ("AUDIO_ON" if state == 0 else "AUDIO_OFF")
            )
            case = _case(
                "set_audio_state", params, "音频",
                pre=precondition,
                isolated=scope == "other", suffix=f"{state}-{scope}-0")
            if scope == "other":
                case.update(risk="ISOLATED_STATEFUL")
            cases.append(case)
    # Standalone state changes may only address the already initialized
    # default video device 1. Device 2 must first be added by FLOW-DEVICE-AV.
    for state, scope, dev in product([0, 2], ["self", "other"], [1]):
        params = {"state": state, "dev_id": dev}
        if scope == "other": params["user_id"] = "${observer.room_user_id}"
        precondition = (
            ("TARGET_VIDEO_ON" if state == 0 else "TARGET_VIDEO_OFF")
            if scope == "other"
            else ("VIDEO_ON" if state == 0 else "VIDEO_OFF")
        )
        case = _case("set_video_state", params, "视频",
                     pre=precondition,
                     isolated=scope == "other", suffix=f"{state}-{scope}-{dev}")
        if scope == "other":
            case.update(risk="ISOLATED_STATEFUL")
        cases.append(case)
    # Formal layout mapping. Large layouts intentionally use only active
    # participants; empty windows are part of the protocol-level test.
    for layout in [0, 1, 2, 3, 4, 6, 9, 12, 16, 25, 36, 49, 64, 1001, 1002]:
        params = {"layout_type": layout,
                  "user_ids": ["${operator.room_user_id}", "${observer.room_user_id}"],
                  "mode": 3}
        case = _case("set_layout", params, "布局", pre="LAYOUT_RIGHT",
                     suffix=f"formal-{layout}")
        # Broadcast follow is proven on two independent receivers. 6513 is
        # forwarded to other participants, not echoed to the sender.
        case.update(receivers="observer,witness", forbidden="operator")
        cases.append(case)

    for layout in [4, 9, 1001]:
        target = {"layout_type": layout,
                  "user_ids": ["${operator.room_user_id}", "${observer.room_user_id}"],
                  "target_user_ids": ["${observer.room_user_id}"], "mode": 3}
        case = _case("set_layout", target, "布局", pre="LAYOUT_RIGHT",
                     suffix=f"target-b-{layout}")
        case.update(receivers="observer", forbidden="witness")
        cases.append(case)

        exclude = {"layout_type": layout,
                   "user_ids": ["${operator.room_user_id}", "${observer.room_user_id}"],
                   "exclude_user_ids": ["${witness.room_user_id}"], "mode": 3}
        case = _case("set_layout", exclude, "布局", pre="LAYOUT_RIGHT",
                     suffix=f"exclude-c-{layout}")
        case.update(receivers="observer", forbidden="witness")
        cases.append(case)

    for order, users in (
        ("abc", ["${operator.room_user_id}", "${observer.room_user_id}", "${witness.room_user_id}"]),
        ("cba", ["${witness.room_user_id}", "${observer.room_user_id}", "${operator.room_user_id}"]),
    ):
        videos = [{"UserID": user_id, "MediaID": index + 10, "Pos": index}
                  for index, user_id in enumerate(users)]
        cases.append(_case("set_layout", {"layout_type": 4, "videos": videos, "mode": 3},
                           "布局", pre="LAYOUT_RIGHT", suffix=f"order-{order}"))

    layout_edges = [
        ("four-empty", {"layout_type": 4, "user_ids": ["${operator.room_user_id}", "${observer.room_user_id}"]}),
        ("four-repeat", {"layout_type": 4, "user_ids": ["${operator.room_user_id}", "${operator.room_user_id}"]}),
        ("four-missing", {"layout_type": 4, "user_ids": ["${operator.room_user_id}", 999999999]}),
        ("four-repeat-command", {"layout_type": 4, "user_ids": ["${operator.room_user_id}", "${observer.room_user_id}"]}),
    ]
    for suffix, params in layout_edges:
        cases.append(_case("set_layout", {**params, "mode": 3}, "布局",
                           pre="LAYOUT_RIGHT", level="P2", suffix=suffix))
    # 分屏数表示布局模板，不要求存在同等数量的参会端。这里只用A/B验证
    # 4分屏及大分屏协议同步，空余窗口允许为空，不测试真实画面是否填满。
    for suffix, layout in (("four-ab-sync", 4), ("large-64-ab-sync", 64)):
        case = _case(
            "set_layout", {
                "layout_type": layout,
                "user_ids": ["${operator.room_user_id}", "${observer.room_user_id}"],
                "mode": 3,
                "full": 1,
            },
            "布局", pre="LAYOUT_RIGHT", level="P2", suffix=suffix)
        case.update(
            design_note="A设置分屏模板，B验证VideoRelayID及A/B成员字段；空余窗口允许为空")
        cases.append(case)
    for role, enabled, scope in product(["presenter", "admin", "chairman"], [True, False], ["self", "other"]):
        if role == "chairman" and scope == "other": continue
        uid = "${operator.room_user_id}" if scope == "self" else "${observer.room_user_id}"
        params = {"user_id": uid, "role": role, "enabled": enabled}
        if role == "chairman" and enabled:
            params["chair_pwd"] = "${env.chair_password}"
        case = _case("set_role", params,
                     "角色", isolated=True, suffix=f"{role}-{enabled}-{scope}")
        case.update(risk="ISOLATED_STATEFUL")
        if role in {"admin", "presenter"} and scope == "self" and enabled:
            case.update(
                kind="BUSINESS_EXCEPTION", outcome="NO_SUCCESS_EVENT",
                receivers="operator,observer", forbidden="", fields={},
                design_note="动态会议创建者A为主持端，角色互斥：设置自身管理员或主讲不广播成功6609")
        elif not enabled and role in {"admin", "presenter"} and scope == "other":
            case["pre"] = f"TARGET_{role.upper()}_ON"
        elif not enabled and role in {"admin", "presenter"} and scope == "self":
            case.update(
                design_note="取消创建者自身管理员/主讲按幂等成功处理，服务器广播6609终态0")
        if role == "chairman":
            case.update(
                pre="HOST_RELEASED" if enabled else "HOST_HELD",
                fields={"UserID": "${operator.room_user_id}",
                        "Right": 3 if enabled else 2},
                design_note=(
                    "set_role(chairman)内部调用6511；独立会议启用主持密码，"
                    "成功按6609.Right断言"))
        cases.append(case)
    for kind, action in product(["media", "vnc"], ["start", "stop"]):
        op = "start_aux_stream" if action == "start" else "stop_aux_stream"
        params = {"kind": kind}
        if kind == "media" and action == "start": params.update(audio_share_id=1, video_share_id=1)
        pre = (("MEDIA_RIGHT" if kind == "media" else "VNC_RIGHT")
               if action == "start" else
               ("MEDIA_STARTED_WITH_RIGHT" if kind == "media"
                else "VNC_STARTED_WITH_RIGHT"))
        case = _case(
            op, params, "辅流", pre=pre,
            isolated=action == "start", suffix=f"{kind}-{action}")
        cases.append(case)
    # 单条轮询用例只执行“新增”，修改/暂停/恢复/停止/删除必须依赖已创建ID，
    # 由FLOW-POLLING-LIFECYCLE覆盖。用例结束后Driver按同一ID删除。
    for screen, status, interval in product([0, 1, 2], [0, 1, 2], [5, 10, 60]):
        polling_id = 1100 + screen * 100 + status * 10 + interval
        params = {"operation": 1, "polling_id": polling_id, "name": f"轮询-{polling_id}",
                  "user_ids": ["${operator.room_user_id}", "${observer.room_user_id}"],
                  "status": status, "interval": interval, "screen": screen, "window": 0}
        case = _case("configure_polling", params, "轮询", pre="POLLING_RIGHT",
                     suffix=f"add-{screen}-{status}-{interval}")
        case.update(
            enabled=False, status="SKIP_BLOCKED", risk="BLOCKED",
            design_note=(
                "开发真机只证明最小新增轮询可收到6633；其他screen/status/interval"
                "组合等待完整状态机契约后复测。"))
        cases.append(case)
    for right in range(1, 13):
        for op in ["request_right", "release_right"]:
            cases.append(_case(
                op, {"right_type": right}, "操作权",
                pre="RIGHT_HELD" if op == "release_right" else "NONE",
                suffix=f"{op}-{right}"))

    # Single-cause business exceptions.
    exceptions = [
        ("set_audio_state", {"state": -1}, (0x1008,), "音频"),
        ("set_audio_state", {"state": 99}, (0x1008,), "音频"),
        ("set_audio_state", {"state": 2, "dev_id": 1}, (), "音频"),
        ("set_audio_state", {"state": 2, "dev_id": 2}, (), "音频"),
        ("set_video_state", {"state": -1}, (0x1008,), "视频"),
        ("set_layout", {"layout_type": 4, "target_user_ids": [1], "exclude_user_ids": [1]}, (0x1008,), "布局"),
        ("set_role", {"user_id": 999999999, "role": "admin"}, (0x1005,), "角色"),
        ("start_aux_stream", {"kind": "invalid"}, (0x1008,), "辅流"),
        ("configure_polling", {"operation": 1, "polling_id": 0, "name": "", "user_ids": []}, (0x1008,), "轮询"),
        ("pause_record", {}, (0x1008, 0x1007), "录制"),
        ("apply_host", {"chair_pwd": "wrong"}, (0x2114,), "主持"),
        ("chat", {"text": "", "dst_user_id": 0}, (), "消息"),
        ("chat", {"text": "invalid-target", "dst_user_id": 999999999}, (), "消息"),
        ("kick_user", {"user_id": 999999999}, (0x1005,), "成员"),
        ("allow_knock", {"user_id": 999999999}, (0x1005,), "敲门"),
        ("conf_hangup", {"callee_nums": ["${env.invalid_h323}"], "call_type": 2}, (0x1005, 0x1008), "H323"),
    ]
    # Expand single-cause business failures without inventing XML paths. For
    # fire-and-forget commands the expected result is no matching success
    # Notify; paired request methods assert their documented Result code.
    exceptions.extend(
        ("set_audio_state", params, (), "音频")
        for state in (-1, 3, 99)
        for params in (
            {"state": state, "dev_id": 0},
            {"state": state, "dev_id": 0, "user_id": "${observer.room_user_id}"},
        )
    )
    exceptions.extend(
        ("set_audio_state", {"state": state, "dev_id": dev_id}, (), "音频")
        for state in (0, 1, 2) for dev_id in (1, 2)
    )
    exceptions.extend(
        ("set_video_state", params, (), "视频")
        for state in (-1, 1, 3, 99)
        for params in (
            {"state": state, "dev_id": 1},
            {"state": state, "dev_id": 1, "user_id": "${observer.room_user_id}"},
        )
    )
    exceptions.extend([
        ("set_audio_state", {
            "state": 1, "dev_id": 0,
            "user_id": "${observer.room_user_id}"}, (), "音频"),
        ("set_audio_state", {"state": 2, "dev_id": 0, "user_id": 999999999}, (), "音频"),
        ("set_video_state", {"state": 2, "dev_id": 1, "user_id": 999999999}, (), "视频"),
        ("set_layout", {"layout_type": -1, "user_ids": ["${operator.room_user_id}"]}, (), "布局"),
        ("set_layout", {"layout_type": 5, "user_ids": ["${operator.room_user_id}"]}, (), "布局"),
        ("set_layout", {"layout_type": 9999, "user_ids": ["${operator.room_user_id}"]}, (), "布局"),
        ("set_role", {"user_id": "${observer.room_user_id}", "role": "invalid", "enabled": True}, (), "角色"),
        ("start_aux_stream", {"kind": ""}, (), "辅流"),
        ("stop_aux_stream", {"kind": "invalid"}, (), "辅流"),
        ("configure_polling", {"operation": 0, "polling_id": 2001, "name": "invalid", "user_ids": ["${operator.room_user_id}"]}, (), "轮询"),
        ("configure_polling", {"operation": 4, "polling_id": 2002, "name": "invalid", "user_ids": ["${operator.room_user_id}"]}, (), "轮询"),
        ("configure_polling", {"operation": 1, "polling_id": -1, "name": "invalid", "user_ids": ["${operator.room_user_id}"]}, (), "轮询"),
        ("configure_polling", {"operation": 1, "polling_id": 2003, "name": "invalid", "user_ids": [], "interval": 10}, (), "轮询"),
        ("configure_polling", {"operation": 1, "polling_id": 2004, "name": "invalid", "user_ids": [999999999], "interval": 10}, (), "轮询"),
        ("configure_polling", {"operation": 1, "polling_id": 2005, "name": "invalid", "user_ids": ["${operator.room_user_id}"], "interval": 0}, (), "轮询"),
        ("configure_polling", {"operation": 1, "polling_id": 2006, "name": "invalid", "user_ids": ["${operator.room_user_id}"], "interval": -1}, (), "轮询"),
        ("configure_polling", {"operation": 1, "polling_id": 2007, "name": "invalid", "user_ids": ["${operator.room_user_id}"], "screen": -1}, (), "轮询"),
        ("configure_polling", {"operation": 1, "polling_id": 2008, "name": "invalid", "user_ids": ["${operator.room_user_id}"], "window": -1}, (), "轮询"),
    ])
    exceptions.extend(
        (operation, {"right_type": right_type}, (0x1008,), "操作权")
        for operation in ("request_right", "release_right")
        for right_type in (0, 13, 99)
    )
    for i, (op, params, errors, module) in enumerate(exceptions):
        case = _case(op, params, module, kind="BUSINESS_EXCEPTION", level="P1",
                     errors=errors, isolated=op in destructive, suffix=f"invalid-{i}")
        if op == "set_layout":
            case.update(pre="LAYOUT_RIGHT", risk="ISOLATED_STATEFUL")
        elif op == "configure_polling":
            case.update(pre="POLLING_RIGHT", risk="ISOLATED_STATEFUL")
        elif op == "allow_knock":
            case.update(
                enabled=False, status="SKIP_BLOCKED", risk="BLOCKED",
                design_note="MISSING_TRIGGER_CONTRACT：无真实6604时无法证明6504业务异常")
        if op in {"start_aux_stream", "stop_aux_stream"} and params.get("kind") not in {"media", "vnc"}:
            # Unknown kind is rejected by the public API before XML is sent.
            # Keep it as input validation coverage, not a server error claim.
            case.update(outcome="VALIDATION_ERROR", errors=[])
        elif op == "apply_host":
            # 6511 failure is asynchronous 6611 to the requester; success is
            # a 6609 role change. There is no paired synchronous Response.
            case.update(
                outcome="ERROR_EVENT", errors=[], receivers="operator",
                forbidden="observer",
                notifies=[{"source": "conf", "cmdid": 6611, "kind": "notify",
                           "match": {}}], fields={})
        elif op == "set_role" and params.get("role") == "invalid":
            case.update(outcome="VALIDATION_ERROR", errors=[])
        elif op == "set_video_state" and params.get("state") == 1:
            case.update(
                enabled=False, status="SKIP_BLOCKED", risk="BLOCKED",
                design_note=(
                    "FAIL_SERVER：公开常量只定义视频0=关闭、2=广播；"
                    "真实服务器接受非法State=1并广播6609，且归一化为State=2。"
                    "开发需确认兼容语义或补参数校验。"),
            )
        elif op == "configure_polling":
            case.update(
                enabled=False, status="SKIP_BLOCKED", risk="BLOCKED",
                design_note="轮询非法参数错误契约尚未由开发真机验证")
        elif op == "set_layout" and params.get("layout_type") in {-1, 5, 9999}:
            case.update(
                enabled=False, status="SKIP_BLOCKED", risk="BLOCKED",
                design_note=(
                    "FAIL_SERVER：VideoRelayID不在正式布局集合内，"
                    "真实服务器仍广播6613而未返回业务错误。"
                    "开发需补布局枚举校验或更新正式支持集合。"),
            )
        elif op in {"request_right", "release_right"} and params.get("right_type") in {0, 13, 99}:
            case.update(
                enabled=False, status="SKIP_BLOCKED", risk="BLOCKED",
                design_note=(
                    "FAIL_SERVER：公开契约只允许RightType=1..12；"
                    "真实服务器对0不返回6630，对13/99返回成功。"
                    "开发需统一非法枚举的错误Response。"),
            )
        elif op == "chat" and params.get("text") == "" and params.get("dst_user_id") == 0:
            # 真实Conf 2.0接受空公聊，广播6612并分配MsgID；它是合法边界，
            # 不能按“服务器应拒绝”的假设设计为业务异常。
            case.update(
                kind="NORMAL", outcome="SUCCESS_EVENT", errors=[],
                receivers="operator,observer", forbidden="",
                fields={"SrcUserID": "${operator.room_user_id}",
                        "DstUserID": 0, "Text": ""},
                design_note="真实服务器接受空公聊并广播6612，按合法边界覆盖")
        elif op in {"set_audio_state", "set_video_state", "set_layout", "set_role",
                    "pause_record", "chat", "allow_knock", "configure_polling",
                    "kick_user"}:
            # These requests have no error Response pair. Rejection can only
            # be proven by absence of the matching success Notify.
            case.update(outcome="NO_SUCCESS_EVENT", errors=[])
            if (op == "set_audio_state" and params.get("state") == 1 and
                    params.get("user_id") == "${observer.room_user_id}"):
                case.update(
                    pre="TARGET_AUDIO_OFF", risk="ISOLATED_STATEFUL",
                    design_note="申请发言等待态必须由用户本人发起；主持人替其他用户设置时不得广播成功6609")
        cases.append(case)

    for operation, params in HTTP_PARAMS.items():
        case = _case(operation, params, "后台会控", actor="http_admin",
                     closure="HTTP_TO_FM", target="observer",
                     isolated=operation in {"remove_user", "close_meeting",
                                            "operate_record"})
        if operation == "operate_record":
            case.update(pre="NONE",
                        risk="ISOLATED_STATEFUL")
        elif operation == "control_user_audio":
            case["pre"] = "TARGET_AUDIO_OFF"
        elif operation == "control_user_video":
            case["pre"] = "TARGET_VIDEO_OFF"
        elif operation == "mute_all_http":
            case.update(
                isolated=True, risk="ISOLATED_STATEFUL",
                design_note="HTTP全体静音由服务器向A/B下发6614。")
        cases.append(case)
    for operation, params in RESULT_PARAMS.items():
        cases.append(_case(operation, params, "录制纪要", actor="http_admin",
                           closure="HTTP_RESULT", level="P2"))
    return cases


PLAN = {
    "登录、入会、成员生命周期": (55, (10, 27, 18)),
    "音视频、设备及多设备状态": (80, (22, 33, 25)),
    "布局、窗口及定向投递": (55, (3, 30, 22)),
    "角色、12类操作权及权限": (70, (6, 38, 26)),
    "录制、锁会、静音、字幕": (69, (7, 37, 25)),
    "聊天、文件、系统消息、房间数据": (58, (5, 28, 25)),
    "分组、等候室及敲门": (40, (0, 22, 18)),
    "辅流及视频轮询": (55, (0, 31, 24)),
    "HTTP创建、邀请和后台会控": (55, (5, 27, 23)),
    "录制结果、纪要、下载和导出": (30, (0, 13, 17)),
    "仅下行及阻塞协议设计": (25, (0, 3, 22)),
    "连接恢复、事件隔离和资源清理": (11, (0, 5, 6)),
}

OPERATION_MODULE = {
    **{name: "登录、入会、成员生命周期" for name in (
        "entrance_get_config", "front_authenticate", "front_login",
        "front_login_by_meeting_no", "front_login_by_invite_code", "front_login_guest",
        "front_roomlist", "front_roomlist_classified", "front_discover_node",
        "front_getroomid", "front_get_auth_type", "front_logout", "conf_login",
        "conf_logout", "conf_get_offline_users", "offline_contacts", "conf_get_contacts",
        "contacts_snapshot", "list_rooms")},
    **{name: "音视频、设备及多设备状态" for name in (
        "report_av_info", "set_audio_state", "set_video_state", "mic_on", "mic_off",
        "mic_request", "camera_on", "camera_off", "set_user_audio", "set_user_video",
        "set_user_state", "reply_self_state_change", "request_remote_av")},
    **{name: "布局、窗口及定向投递" for name in (
        "set_window_state", "set_layout", "lock_layout", "set_layout_http")},
    **{name: "角色、12类操作权及权限" for name in (
        "apply_host", "release_host", "request_right", "release_right", "set_role",
        "set_role_permissions", "set_user_permissions", "set_user_manager", "set_user_presenter",
        "get_user_features", "query_feature_users", "unsubscribe_feature_users")},
    **{name: "录制、锁会、静音、字幕" for name in (
        "set_conf_state", "start_record", "pause_record", "stop_record", "lock_room",
        "unlock_room", "mute_all", "set_subtitle", "close_subtitle", "rename_meeting",
        "get_av_mix_params", "set_av_mix_params", "set_video_subtitle_params",
        "lock_meeting", "operate_record", "send_subtitles", "set_asr_subtitle")},
    **{name: "聊天、文件、系统消息、房间数据" for name in (
        "chat", "send_file", "send_sysmsg", "recall_msg", "set_room_data",
        "send_h5_vote", "set_user_custom_data")},
    **{name: "分组、等候室及敲门" for name in (
        "allow_knock", "create_group", "enter_group", "end_groups", "bring_to_main",
        "modify_group_config", "change_group_state", "allow_waiting_users")},
    **{name: "辅流及视频轮询" for name in (
        "start_aux_stream", "stop_aux_stream", "configure_polling",
        "set_multi_data_sharer", "renegotiate_layout")},
    **{name: "HTTP创建、邀请和后台会控" for name in (
        "create_instant_meeting", "create_reserved_meeting", "cancel_reserved_meeting",
        "invite_users", "invite_contacts", "control_user_audio", "control_user_video",
        "mute_all_http", "remove_user", "close_meeting",
        "kick_out_user", "kick_out_user_by_id", "send_room_message")},
    **{name: "录制结果、纪要、下载和导出" for name in RESULT_PARAMS},
    **{name: "仅下行及阻塞协议设计" for name in (
        "conf_get_dept_info", "conf_get_room_config", "conf_invite_call", "invite_terminals",
        "invite_terminals_http", "conf_hangup", "conf_query_call_list")},
    **{name: "连接恢复、事件隔离和资源清理" for name in ("kick_user", "close_room")},
}

DESIGN_DIMENSIONS = {
    "登录、入会、成员生命周期": ["房间ID", "会议号", "邀请码", "访客", "固定会议", "预约会议", "即时会议", "重复登录", "重复入会", "无效Token", "会议未开始", "会议已结束", "成员进入", "成员退出", "初始化序列"],
    "音视频、设备及多设备状态": ["音频关闭", "音频申请", "音频发言", "视频关闭", "视频广播", "自身", "他人", "主持人", "普通成员", "设备新增", "设备修改", "默认视频切换", "设备删除", "重复状态", "离会用户"],
    "布局、窗口及定向投递": ["4分屏", "6分屏", "9分屏", "12分屏", "16分屏", "25分屏", "36分屏", "49分屏", "64分屏", "成员顺序", "指定目标", "排除目标", "布局锁定", "恢复自动", "非目标无事件"],
    "角色、12类操作权及权限": ["主持申请", "主持释放", "管理员设置", "管理员取消", "主讲设置", "主讲取消", "权限申请", "权限占用", "权限释放", "权限转移", "角色权限", "用户权限", "离会目标"],
    "录制、锁会、静音、字幕": ["录制开始", "录制暂停", "录制恢复", "录制停止", "重复录制", "锁会", "解锁", "全体静音", "字幕设置", "字幕更新", "字幕关闭", "特殊字符", "会议改名", "AV合成", "视频字幕参数"],
    "聊天、文件、系统消息、房间数据": ["公聊", "私聊", "颜色", "XML字符", "空消息", "长度边界", "文件单目标", "文件多目标", "特殊文件名", "系统消息", "广播消息", "房间数据", "消息撤回", "重复撤回"],
    "分组、等候室及敲门": ["敲门", "允许", "拒绝", "等待", "创建单组", "创建多组", "动态RoomID", "进入分组", "返回主会", "结束分组", "成员离会", "无效分组"],
    "辅流及视频轮询": ["媒体辅流开始", "媒体辅流停止", "VNC开始", "VNC停止", "辅流争抢", "轮询新增", "轮询修改", "轮询暂停", "轮询恢复", "轮询停止", "轮询删除", "成员顺序", "成员离会", "布局联动"],
    "HTTP创建、邀请和后台会控": ["即时会议", "预约会议", "取消预约", "普通用户邀请", "通讯录邀请", "在线用户", "离线用户", "已在会用户", "批量邀请", "音频会控", "视频会控", "布局会控", "锁会", "请出", "关会"],
    "录制结果、纪要、下载和导出": ["录制摘要", "参与人", "任务", "记录文件", "分享信息", "会议纪要", "字幕结果", "下载纪要", "导出纪要", "导出字幕", "处理中", "已完成", "无结果", "删除后"],
    "仅下行及阻塞协议设计": ["6528服务端缺失", "6534高风险", "H323响应阻塞", "人脸检测", "人脸识别", "人脸点名", "Relay", "服务器录制错误", "系统通知", "角色初始化", "审批结果"],
    "连接恢复、事件隔离和资源清理": ["事件游标", "历史消息隔离", "连接健康", "恢复失败", "Runtime重建", "请出清理", "关会清理", "退出清理", "会议清理", "录制清理"],
}

FALLBACK_OPERATION = {
    "登录、入会、成员生命周期": "front_login",
    "音视频、设备及多设备状态": "set_audio_state",
    "布局、窗口及定向投递": "set_layout",
    "角色、12类操作权及权限": "request_right",
    "录制、锁会、静音、字幕": "set_conf_state",
    "聊天、文件、系统消息、房间数据": "chat",
    "分组、等候室及敲门": "create_group",
    "辅流及视频轮询": "configure_polling",
    "HTTP创建、邀请和后台会控": "create_instant_meeting",
    "录制结果、纪要、下载和导出": "get_record_summary",
    "仅下行及阻塞协议设计": "relay_message",
    "连接恢复、事件隔离和资源清理": "conf_logout",
}

P0_SAFE_OPERATIONS = {
    "entrance_get_config", "front_authenticate", "front_login", "conf_login",
    "front_discover_node",
    "front_get_auth_type", "front_getroomid", "chat", "mic_on", "mic_off",
    "camera_on", "camera_off", "report_av_info", "request_right",
    "mic_request", "set_audio_state", "set_video_state", "set_user_audio",
    "set_user_video",
    "set_window_state", "set_layout", "start_record", "stop_record", "lock_room",
    "unlock_room", "mute_all", "set_subtitle", "close_subtitle",
    "create_instant_meeting", "create_reserved_meeting", "invite_users",
    "invite_contacts", "control_user_audio", "control_user_video",
    "send_subtitles", "send_sysmsg",
    "start_aux_stream", "stop_aux_stream",
}


def _is_p0_safe(row):
    if (not row["enabled"] or row["kind"] != "NORMAL" or
            row["operation"] not in P0_SAFE_OPERATIONS or
            row["risk"] in {"DESTRUCTIVE_ISOLATED", "BLOCKED"}):
        return False
    if row["operation"] == "set_layout":
        serialized = json.dumps(row["params"], ensure_ascii=False)
        user_ids = row["params"].get("user_ids") or []
        return (row["params"].get("layout_type") == 4 and
                set(user_ids) == {
                    "${operator.room_user_id}", "${observer.room_user_id}"} and
                len(user_ids) == 2 and
                "witness" not in serialized and
                "witness" not in row.get("receivers", "") and
                "witness" not in row.get("forbidden", ""))
    if row["operation"] == "report_av_info":
        videos = row["params"].get("video") or []
        return not videos or all(str(device.get("ID")) == "1" for device in videos)
    if row["operation"] in {"control_user_audio", "control_user_video"}:
        return row["params"].get("state") == 2
    return True


def _normalize_module(case):
    return OPERATION_MODULE.get(case["operation"], "连接恢复、事件隔离和资源清理")


def _designed_case(module, index):
    operation = FALLBACK_OPERATION[module]
    dimensions = DESIGN_DIMENSIONS[module]
    left = dimensions[index % len(dimensions)]
    right = dimensions[(index // len(dimensions)) % len(dimensions)]
    blocker = blocker_for_operation(operation)
    flow_ids = {
        "登录、入会、成员生命周期": "FLOW-HTTP-CREATE-JOIN",
        "布局、窗口及定向投递": "FLOW-LAYOUT-SWITCH",
        "分组、等候室及敲门": "FLOW-GROUP-LIFECYCLE",
        "辅流及视频轮询": "FLOW-POLLING-LIFECYCLE",
        "录制结果、纪要、下载和导出": "FLOW-RECORD-RESULT",
    }
    case = {
        "id": f"FM-PLAN-{list(PLAN).index(module)+1:02d}-{index+1:03d}-{_slug([left,right])}",
        "module": module, "closure": "HTTP_RESULT" if module == "录制结果、纪要、下载和导出" else "FM_TO_FM",
        "kind": "BUSINESS_EXCEPTION" if any(x in left+right for x in ("无效", "重复", "冲突", "离会", "拒绝", "已结束", "无结果")) else "NORMAL",
        "scene": "DEPENDENCY_FLOW" if module in flow_ids else "BUSINESS_MATRIX",
        "pre": "NONE", "actor": "operator", "target": "observer", "operation": operation,
        "params": BASE_PARAMS.get(operation, RESULT_PARAMS.get(operation, {})),
        "outcome": "SUCCESS_EVENT", "business": f"{left}×{right}", "response": {},
        "notifies": [], "receivers": "", "forbidden": "", "fields": {}, "errors": [],
        "isolated": module in {"HTTP创建、邀请和后台会控", "录制结果、纪要、下载和导出"},
        "level": "P2", "enabled": False,
        "risk": "BLOCKED" if blocker else ("DESTRUCTIVE_ISOLATED" if module in {"HTTP创建、邀请和后台会控", "录制结果、纪要、下载和导出"} else "ISOLATED_STATEFUL"),
        "status": "SKIP_BLOCKED" if blocker else "DESIGNED",
        "flow_id": flow_ids.get(module, ""),
        "design_note": f"待实现组合：{left} × {right}",
    }
    return case


def _expansion_case(module, index):
    """Return a reviewed executable combination, or None for dependency-only work."""
    if module == "登录、入会、成员生命周期":
        selector = index % 5
        if selector == 0:
            params = {
                "user_login_type": 1, "user_type": 1,
                "terminal_type": 1 + ((index // 5) % 3),
                "client_type": 1 + ((index // 15) % 2),
                "app_type": 11, "product_name": f"FMS-E2E-{index}",
            }
            return _case("front_authenticate", params, module,
                         suffix=f"auth-{index}")
        if selector == 1:
            return _case("front_authenticate", {
                "terminal_type": 1 + ((index // 5) % 3),
                "client_type": 1 + ((index // 10) % 2),
                "product_name": f"FMS-E2E-AUTH-{index}"},
                module, suffix=f"auth-extra-{index}")
        if selector == 2:
            return _case("entrance_get_config", {
                "terminal_type": 1 + ((index // 5) % 3),
                "client_version": f"3.15-compat-disabled-{index}",
                "device_id": f"fm-e2e-entry-{index}"},
                module, suffix=f"entrance-extra-{index}")
        if selector == 3:
            params = {"user_type": 1, "verify_mode": (index // 5) % 4,
                      "capability_mode": 1 + ((index // 10) % 2),
                      "down_bitrate": [128000, 500000, 2000000][(index // 5) % 3]}
            case = _case("conf_login", params, module, suffix=f"conf-{index}")
            case.update(isolated=True, risk="ISOLATED_STATEFUL")
            return case
        return _case("entrance_get_config", {
            "terminal_type": 1 + ((index // 5) % 3),
            "client_version": f"2.0.{index}", "device_id": f"fm-e2e-{index}"},
            module, suffix=f"entrance-{index}")

    if module == "聊天、文件、系统消息、房间数据":
        chat_values = [
            ("公开消息", 0, None), ("XML<&>字符", 0, 0x112233),
            ("中文消息", 0, 0xFFFFFF), ("space message", 0, 0),
            ("换行\n消息", 0, 0x00FF00), ("制表\t消息", 0, 0xFF0000),
            ("a" * 64, 0, 0x0000FF), ("边界-255-" + "x" * 245, 0, None),
            (f"私聊-{index}", "${observer.room_user_id}", None),
        ]
        text_value, dst, color = chat_values[index % len(chat_values)]
        params = {"text": f"{text_value}-{index}", "dst_user_id": dst}
        if color is not None:
            params["color"] = color
        case = _case("chat", params, module, suffix=f"matrix-{index}")
        case["fields"] = {"SrcUserID": "${operator.room_user_id}",
                          "DstUserID": dst, "Text": params["text"]}
        return case

    if module == "音视频、设备及多设备状态":
        # A single-step case may only add a device; modify/remove require a
        # preceding add and belong to FLOW-DEVICE-AV.
        operation = 1
        kind = "audio" if index % 2 == 0 else "video"
        # 协议样例只证明视频ID 1和默认ID 3；demo使用ID 1。正常域不臆造1..8全有效。
        dev_id = 0 if kind == "audio" else 1 + ((index // 2) % 3)
        name = f"e2e-{kind}-{dev_id}-{index}"
        params = {kind: [{"Operation": operation, "ID": dev_id, "Name": name}]}
        if kind == "video" and operation != 3:
            params["def_video_dev_id"] = dev_id
        case = _case("report_av_info", params, module, suffix=f"device-{index}")
        case["fields"] = {f"{kind.title()}.0.Operation": operation,
                          f"{kind.title()}.0.ID": dev_id,
                          f"{kind.title()}.0.Name": name}
        return case

    if module == "角色、12类操作权及权限":
        permission_names = [
            "OperatePublicChat", "OperatePrivateChat", "ConfigChatCheck",
            "OperateSendFile", "OperateRecord", "OperateLayout",
        ]
        # Enumerate scope x permission x state without collapsing tuples.
        scope_index = index % 2
        permission = permission_names[(index // 2) % len(permission_names)]
        enabled = (index // (2 * len(permission_names))) % 2
        if scope_index == 0:
            params = {"role_codes": ["mentee"], "permissions": {permission: enabled}}
            case = _case("set_role_permissions", params, module,
                         suffix=f"role-permission-{permission}-{enabled}-{index}")
            case["fields"] = {"RoleCode": "mentee", permission: enabled}
        else:
            params = {"user_id": "${observer.room_user_id}",
                      "permissions": {permission: enabled}}
            case = _case("set_user_permissions", params, module,
                         suffix=f"user-permission-{permission}-{enabled}-{index}")
            case["fields"] = {"UserID": "${observer.room_user_id}", permission: enabled}
        case.update(isolated=True, risk="DESTRUCTIVE_ISOLATED")
        return case

    if module == "录制、锁会、静音、字幕":
        state_fields = [
            ("enable_chat", "EnableChat"), ("enable_p2p_chat", "EnableP2PChat"),
            ("enable_pub_chat", "EnablePubChat"),
            ("enable_record", "EnableRecord"),
            ("enable_send_file", "EnableSendFile"),
            ("enable_waiting_room", "EnableWaitingRoom"),
            ("mode", "Mode"),
        ]
        if index < len(state_fields):
            argument, wire = state_fields[index]
            # Most conference switches default to enabled, while the waiting
            # room defaults to disabled in the real environment. Always issue
            # a real transition so the server must broadcast 6610.
            value = 1 if argument == "enable_waiting_room" else 0
            case = _case("set_conf_state", {argument: value}, module,
                         isolated=True,
                         suffix=f"conf-{argument}-{value}-{index}")
            case["fields"] = {wire: value}
            case.update(
                risk="ISOLATED_STATEFUL",
                design_note="会议状态字段在动态独立会议中验证真实状态切换，主6610后销毁Runtime")
            if argument == "enable_waiting_room":
                case.update(
                    enabled=False, status="SKIP_BLOCKED", risk="BLOCKED",
                    design_note=(
                        "MISSING_TRIGGER_CONTRACT：真实服务器对EnableWaitingRoom=0/1均不广播6610；"
                        "需开发明确是否改由6504/6604/6617等候室时序控制。"))
            elif argument == "mode":
                case.update(
                    enabled=False, status="SKIP_BLOCKED", risk="BLOCKED",
                    design_note=(
                        "MISSING_TRIGGER_CONTRACT：协议仅给Mode=2示例，未定义枚举、"
                        "初始值和转换前置；真实发送Mode=0无6610。需开发补正式契约。"))
            return case

        subtitle_values = [
            "普通字幕", "XML<&>字幕", "中文与English", "第一行\n第二行",
            "前后 空格", "制表\t字幕", "数字0123456789", "标点，。！？",
            "边界64-" + "x" * 57, "会议提示-自动化",
        ]
        subtitle_index = index - len(state_fields)
        if subtitle_index < len(subtitle_values) * 4:
            data = subtitle_values[subtitle_index % len(subtitle_values)]
            duration = [0, 1, 5, 60][subtitle_index // len(subtitle_values)]
            case = _case("set_subtitle", {"data": data, "duration": duration},
                         module, suffix=f"subtitle-{subtitle_index}")
            case["fields"] = {"Data": data, "Duration": duration}
            return case

        style_index = subtitle_index - len(subtitle_values) * 4
        pos = 1 + (style_index % 4)
        size = [1, 2, 3][(style_index // 4) % 3]
        color = [0, 0xFFFFFF][(style_index // 12) % 2]
        style = {"Pos": pos, "Size": size, "Color": color}
        case = _case("set_video_subtitle_params", {"date_time": style},
                     module, isolated=True,
                     suffix=f"video-subtitle-{pos}-{size}-{color}")
        case["fields"] = {f"DateTime.{key}": value
                          for key, value in style.items()}
        case.update(risk="ISOLATED_STATEFUL")
        return case

    if module == "HTTP创建、邀请和后台会控":
        operations = [
            ("control_user_audio", {"room_id": "${operator.room_id}",
                                    "user_id": "${observer.room_user_id}",
                                    "device_id": 0, "state": index % 3}),
            ("control_user_video", {"room_id": "${operator.room_id}",
                                    "user_id": "${observer.room_user_id}",
                                    "device_id": 1, "state": [0, 2][index % 2]}),
            ("set_layout_http", {"room_id": "${operator.room_id}",
                                 "layout": {
                                     "mode": 3, "full": 0, "dataActive": 6,
                                     "videoRelayId": [1, 4, 9, 16][index % 4]}}),
            ("lock_meeting", {"room_id": "${operator.room_id}",
                              "lock": bool(index % 2)}),
            ("send_subtitles", {"room_id": "${operator.room_id}",
                                "font_size": [16, 24, 36][index % 3],
                                "font_color": [0xFFFFFF, 0x00FF00, 0xFF0000][index % 3],
                                "subtitle_type": [0, 2][(index // 8) % 2], "align": 1,
                                "content": f"HTTP字幕-{index}"}),
            ("set_user_manager", {"room_id": "${operator.room_id}",
                                  "user_id": "${observer.room_user_id}",
                                  "state": [0, 2][index % 2]}),
            ("set_user_presenter", {"room_id": "${operator.room_id}",
                                    "user_id": "${observer.room_user_id}",
                                    "state": [0, 2][index % 2]}),
            ("operate_record", {"room_id": "${operator.room_id}",
                                "state": [0, 2][(index // 8) % 2]}),
        ]
        operation, params = operations[index % len(operations)]
        case = _case(operation, params, module, actor="http_admin",
                     closure="HTTP_TO_FM", target="observer",
                     isolated=True, suffix=f"http-{operation}-{index}")
        case.update(risk="ISOLATED_STATEFUL")
        if operation == "operate_record":
            case["pre"] = ("RECORDING" if params["state"] == 0
                           else "NONE")
        elif operation == "control_user_audio":
            case["pre"] = ("TARGET_AUDIO_ON" if params["state"] == 0
                           else "TARGET_AUDIO_OFF")
        elif operation == "control_user_video":
            case["pre"] = ("TARGET_VIDEO_ON" if params["state"] == 0
                           else "TARGET_VIDEO_OFF")
        elif operation == "set_user_manager" and params["state"] == 0:
            case["pre"] = "HTTP_MANAGER_ON"
        elif operation == "set_user_presenter" and params["state"] == 0:
            case["pre"] = "HTTP_PRESENTER_ON"
        return case

    if module == "布局、窗口及定向投递":
        layouts = [0, 1, 2, 3, 4, 6, 9, 12, 16, 25, 36, 49, 64, 1001, 1002]
        layout = layouts[index % len(layouts)]
        variant = (index // len(layouts)) % 4
        users = ["${operator.room_user_id}", "${observer.room_user_id}"]
        params = {"layout_type": layout, "mode": [1, 2, 3][index % 3],
                  "full": index % 2, "data_active": (index // 2) % 2}
        suffix = f"layout-{layout}-variant-{variant}-{index}"
        if variant == 0:
            params["user_ids"] = users
        elif variant == 1:
            params.update(user_ids=users,
                          target_user_ids=["${observer.room_user_id}"])
        elif variant == 2:
            params.update(user_ids=users,
                          exclude_user_ids=["${witness.room_user_id}"])
        else:
            ordered = (["${operator.room_user_id}", "${observer.room_user_id}",
                        "${witness.room_user_id}"] if index % 2 == 0 else
                       ["${witness.room_user_id}", "${observer.room_user_id}",
                        "${operator.room_user_id}"])
            params["videos"] = [
                {"UserID": user_id, "MediaID": 20 + pos, "Pos": pos}
                for pos, user_id in enumerate(ordered)]
        case = _case("set_layout", params, module, pre="LAYOUT_RIGHT", suffix=suffix)
        if variant in {1, 2}:
            case.update(receivers="observer", forbidden="witness")
        return case

    if module == "分组、等候室及敲门":
        # 6623返回动态RoomID；测试入口用该ID结束分组并释放分组权。
        member_sets = [
            [("${observer.room_user_id}", 1)],
            [("${observer.room_user_id}", 2)],
            [("${operator.room_user_id}", 3), ("${observer.room_user_id}", 2)],
            [("${observer.room_user_id}", 2), ("${witness.room_user_id}", 1)],
        ]
        members = member_sets[index % len(member_sets)]
        room_count = 2 if index % 7 == 6 else 1
        rooms = []
        for room_index in range(room_count):
            rooms.append({
                "Theme": f"E2E分组-{index}-{room_index}",
                "State": index % 2,
                "MaxUserCount": [2, 4, 16, 100][index % 4],
                "EnableJoin": (index // 2) % 2,
                "EnableLeave": (index // 3) % 2,
                "User": [{"ID": user_id, "Right": right}
                         for user_id, right in members],
            })
        case = _case("create_group", {"rooms": rooms}, module,
                     pre="GROUP_RIGHT", isolated=True,
                     suffix=f"group-{index}-{room_count}")
        case["response"] = {"RoomInfo.0.Theme": rooms[0]["Theme"]}
        case.update(risk="ISOLATED_STATEFUL", flow_id="FLOW-GROUP-LIFECYCLE")
        return case

    if module == "辅流及视频轮询":
        users = (["${operator.room_user_id}", "${observer.room_user_id}"]
                 if index % 2 == 0 else
                 ["${observer.room_user_id}", "${operator.room_user_id}"])
        polling_id = 3000 + index
        params = {
            "operation": 1, "polling_id": polling_id,
            "name": f"E2E轮询-{index}", "user_ids": users,
            "interval": [5, 10, 30, 60][index % 4],
            "screen": [0, 1, 2, 4][(index // 2) % 4],
            "window": [0, 1, 2, 3][(index // 3) % 4],
            "status": [0, 1, 2][index % 3],
        }
        case = _case("configure_polling", params, module,
                     pre="POLLING_RIGHT", suffix=f"polling-add-{index}")
        case["flow_id"] = "FLOW-POLLING-LIFECYCLE"
        case.update(
            enabled=False, status="SKIP_BLOCKED", risk="BLOCKED",
            design_note="暂停和修改不承诺逐操作即时Notify；由独立完整状态机流程复测")
        return case

    if module == "录制结果、纪要、下载和导出":
        operations = list(RESULT_PARAMS.items())
        operation, base = operations[index % len(operations)]
        params = dict(base)
        # 仅使用HTTP客户端明确支持的透传查询参数；文件接口也可按语言下载。
        if operation in {"list_record_summaries", "list_room_record_summaries",
                         "list_record_participants", "list_record_tasks", "list_records"}:
            params.update(page_num=1, page_size=[10, 20, 50][index % 3])
        elif operation in {"download_meeting_summary", "export_meeting_summary",
                           "export_transcript"}:
            params["language"] = ["zh-CN", "en-US"][index % 2]
        case = _case(operation, params, module, actor="http_admin",
                     closure="HTTP_RESULT", isolated=True,
                     suffix=f"record-result-{operation}-{index}")
        case.update(risk="DESTRUCTIVE_ISOLATED", flow_id="FLOW-RECORD-RESULT")
        return case

    if module == "连接恢复、事件隔离和资源清理":
        # 退出是破坏性协议，每条均在HTTP动态独立会议执行；Reason为客户端扩展字段。
        reason = index % 8
        case = _case("conf_logout", {"reason": reason}, module,
                     target="observer", isolated=True,
                     suffix=f"logout-reason-{reason}-case-{index}")
        case.update(risk="DESTRUCTIVE_ISOLATED")
        case["fields"] = {"UserID": "${operator.room_user_id}"}
        return case

    return None


def build_cases():
    grouped = {module: [] for module in PLAN}
    cases_by_signature = {}
    existing = _build_existing_cases()
    # Explicitly reviewed layout cases (formal IDs, routing, order and
    # capacity boundaries) must survive the per-module quota. Generic matrix
    # expansions are allowed to yield their slots, not these acceptance cases.
    existing.sort(key=lambda case: 0 if case["operation"] in {
        "set_layout", "set_user_custom_data"} else 1)
    for case in existing:
        module = _normalize_module(case)
        case["module"] = module
        key = _case_signature(case)
        if key in cases_by_signature:
            _merge_case(cases_by_signature[key], case)
            continue
        cases_by_signature[key] = case
        grouped[module].append(case)

    for module, (target, _) in PLAN.items():
        index = 0
        duplicate_attempts = 0
        max_duplicate_attempts = max(200, target * 20)
        while len(grouped[module]) < target:
            expanded = _expansion_case(module, index)
            index += 1
            candidate = expanded or _designed_case(module, index - 1)
            key = _case_signature(candidate)
            if key in cases_by_signature:
                _merge_case(cases_by_signature[key], candidate)
                duplicate_attempts += 1
                if duplicate_attempts >= max_duplicate_attempts:
                    break
                continue
            duplicate_attempts = 0
            cases_by_signature[key] = candidate
            grouped[module].append(candidate)

    result = []
    for module, (_, priorities) in PLAN.items():
        rows = grouped[module]
        # P0 is a safety gate, not simply the first N generated rows. Prefer
        # reviewed, non-destructive operations; unimplemented design rows fill
        # the remaining planning quota without entering real execution.
        rows.sort(key=lambda row: (
            0 if _is_p0_safe(row) else
            1 if row["status"] == "IMPLEMENTED" and row["kind"] == "NORMAL" else
            2 if row["status"] in {"SKIP_ENV", "DESIGNED"} else 3,
            row["id"],
        ))
        levels = (["P0"] * priorities[0] + ["P1"] * priorities[1] +
                  ["P2"] * priorities[2])
        if len(rows) > len(levels):
            levels.extend(["P2"] * (len(rows) - len(levels)))
        for row, level in zip(rows, levels):
            row["level"] = level
            result.append(row)
    assert all(_is_p0_safe(case) for case in result if case["level"] == "P0")
    signatures = [_case_signature(case) for case in result]
    assert len(signatures) == len(set(signatures))
    reviewed = []
    group_variants = {}
    fm_aux_added = False
    remote_av_added = False
    record_error_added = False
    chat_review_added = False
    for row in result:
        op = row['operation']
        if op in ('conf_invite_call', 'invite_terminals', 'invite_terminals_http', 'conf_hangup'):
            row.update(enabled=True, status='IMPLEMENTED', isolated=True, risk='ISOLATED_STATEFUL',
                       scene='H323_LIFECYCLE', pre='NONE', outcome='EVENT_SEQUENCE',
                       response={}, notifies=[], fields={}, errors=[],
                       design_note='环境恢复后真实CallType=2：响应、接通状态、查询、挂断；无效目标挂断按非零OprResult验证')
            if op == 'invite_terminals_http':
                row['params']['call_info'] = [{'calleeNumber': '${env.h323}', 'calleeNickname': 'E2E-H323', 'calleeUserId': 0}]
                row['design_note'] = 'HTTP业务成功+A/B实际终端6602；6745属于原后台呼叫者，不等待A收到。跨调用者挂断6744错路由单独保留缺陷'
            elif op == 'conf_invite_call':
                row['params']['callees'] = [{'CalleeNum': '${env.h323}', 'CalleeNickName': 'E2E-H323', 'CalleeUserID': 0}]
        if op == 'send_relay_message':
            continue  # 替换旧占位为唯一往返链，不重复生成新方法空参数用例。
        if op == 'relay_message':
            op = row['operation'] = 'send_relay_message'
            row['id'] = 'FM-RELAY-N-ROUNDTRIP'
            row.update(enabled=True, status='IMPLEMENTED', isolated=True, risk='ISOLATED_STATEFUL',
                       scene='RELAY_ROUNDTRIP', outcome='EVENT_SEQUENCE', pre='NONE',
                       params={'dst_user_id': '${observer.room_user_id}', 'messages': [
                           {'id': 6718, 'fields': {'Text': '中文<&>"', 'Item': [{'ID': 1}, {'ID': 2}]}}]},
                       response={}, notifies=[], fields={}, errors=[],
                       design_note='新版SDK公开send_relay_message定向往返；来源目标、嵌套重复字段、XML转义、不回显、后续能力查询')
        if op in ('invite_users', 'invite_contacts'):
            row.update(enabled=True, status='IMPLEMENTED', isolated=True, risk='ISOLATED_STATEFUL',
                       scene='PAAS_INVITED_JOIN', outcome='EVENT_SEQUENCE', pre='NONE',
                       params={'user_ids': ['${observer.local_user_id}']},
                       response={}, notifies=[], fields={}, errors=[],
                       design_note='B独立PaaS在线10001→真实14002→14003接受→Front/Conf→A收到6602；不能用HTTP成功代替')
        if op == 'set_conf_state' and set(row['params']) == {'mode'}:
            row.update(enabled=True, status='IMPLEMENTED', isolated=True, risk='ISOLATED_STATEFUL',
                       outcome='EVENT_SEQUENCE', errors=[], response={}, notifies=[], fields={}, pre='NONE',
                       design_note='Mode处理函数为空实现；验证无Mode字段6610、连接健康，并由B重入6601回读模式未改变；不禁止其他字段通知')
        if op == 'set_video_state' and row['params'].get('state') == 1:
            target = row['params'].get('user_id', '${operator.room_user_id}')
            remote = 'user_id' in row['params']
            if remote:
                # 管理员远程State=2已由2-other-1正常用例覆盖，合并旧代申请占位。
                continue
            row['id'] = row['id'].replace('-B-', '-N-')
            row.update(kind='NORMAL', enabled=True, status='IMPLEMENTED', isolated=True,
                       risk='ISOLATED_STATEFUL', outcome='SUCCESS_EVENT', errors=[], response={}, notifies=[],
                       pre='TARGET_VIDEO_OFF' if remote else 'VIDEO_OFF',
                       receivers='operator,observer', forbidden='',
                       fields={'UserID': target, 'Video.ID': 1, 'Video.State': 2},
                       design_note='管理员远程开启使用State=2' if remote else '本人State=1申请，服务器推进到State=2')
        if op == 'set_layout' and row['params'].get('layout_type') in (-1, 5, 9999):
            row['id'] = row['id'].replace('-B-', '-N-')
            row.update(kind='NORMAL', enabled=True, status='IMPLEMENTED', isolated=True,
                       risk='ISOLATED_STATEFUL', outcome='SUCCESS_EVENT', errors=[], response={}, notifies=[],
                       pre='LAYOUT_RIGHT', receivers='observer', forbidden='operator',
                       fields={'Mode': 3, 'VideoRelayID': row['params']['layout_type'],
                               'Video.UserID': '${operator.room_user_id}', 'Video.MediaID': 0, 'Video.Pos': 0},
                       design_note='VideoRelayID由服务器透传给终端；只验证6613原样字段和连接，不校验布局值或画面')
        if op in ('request_right', 'release_right') and row['params'].get('right_type') in (0, 13, 99):
            row.update(enabled=True, status='IMPLEMENTED', isolated=True, risk='ISOLATED_STATEFUL',
                       outcome='NO_SUCCESS_EVENT', errors=[], response={}, notifies=[], pre='NONE',
                       fields={'Operation': 1 if op == 'request_right' else 0,
                               'RightType': row['params']['right_type']},
                       design_note='不支持的RightType静默忽略；无匹配本次Operation/RightType的6630且连接健康，内部TRUE不等于Result=0')
            if row['params']['right_type'] in (13, 99):
                row['id'] = row['id'].replace('-B-', '-N-')
                row.update(kind='NORMAL', outcome='SUCCESS_RESPONSE', fields={},
                           pre='RIGHT_HELD' if op == 'release_right' else 'NONE',
                           response={'Result': 0, 'Operation': 1 if op == 'request_right' else 0,
                                     'RightType': row['params']['right_type'], 'RightIndex': 0,
                                     'OccupiedUserID': '${operator.room_user_id}' if op == 'request_right' else 0},
                           design_note='按用户确认的服务器实际返回验收：6630 Result=0；申请占用者A、释放占用者0；只证明协议结果，不推断该类型额外业务能力')
        if op in {'front_logout', 'unsubscribe_feature_users', 'allow_waiting_users'} or (op == 'set_conf_state' and 'enable_waiting_room' in row['params']):
            row.update(enabled=True, status='IMPLEMENTED', isolated=True,
                       risk='DESTRUCTIVE_ISOLATED', outcome='EVENT_SEQUENCE',
                       design_note='真实前置场景：事件队列注销响应/订阅变化/等候室子会话审批；不以方法返回None判成功')
        # 撤回必须用聊天产生的MsgID，独立空ID占位由已执行流程覆盖。
        if op == 'recall_msg' or (op == 'allow_knock' and row['kind'] == 'NORMAL'):
            continue
        if op == 'allow_knock':
            row.update(enabled=True, status='IMPLEMENTED', isolated=True, risk='ISOLATED_STATEFUL',
                       pre='ROOM_LOCKED', outcome='NO_SUCCESS_EVENT', errors=[], response={}, notifies=[],
                       design_note='新建独立会议，仅A/B入会并锁会；确认没有目标敲门前置，6504后无目标6602且两端能力查询正常；Keep省略使用默认0')
        if op == 'configure_polling' and row['kind'] == 'BUSINESS_EXCEPTION':
            row.update(outcome='NO_SUCCESS_EVENT', errors=[],
                       design_note='按字段职责核对服务端处理或透传契约，不统一要求断会，不编造同步Response')
            original = dict(row['params'])
            row['params'] = dict(interval=5, screen=1, window=1, status=2)
            row['params'].update(original)
            if original.get('operation') in (0, 4):
                row.update(enabled=True, status='IMPLEMENTED', isolated=False,
                           risk='SAFE_REUSABLE', outcome='VALIDATION_ERROR', pre='NONE',
                           response={}, notifies=[],
                           fields={},
                           design_note=('Operation只允许1新增/2修改/3删除；0/4由SDK本地拒绝，'
                                        '不向服务器发送非法6533，避免跨节点静默忽略或断会差异'))
                row['params']['user_ids'] = ['${operator.room_user_id}', '${observer.room_user_id}']
            elif original.get('screen') == -1:
                row['id'] = row['id'].replace('-B-', '-N-')
                row.update(enabled=True, status='IMPLEMENTED', isolated=True, kind='NORMAL',
                           risk='ISOLATED_STATEFUL', outcome='SUCCESS_EVENT', response={}, notifies=[],
                           fields={'VideoPolling.val': original['polling_id'], 'VideoPolling.Operation': 1,
                                   'VideoPolling.Screen': -1, 'VideoPolling.Interval': 5,
                                   'VideoPolling.Window': 1,
                                   'VideoPolling.CurrentUserID': '${operator.room_user_id}',
                                   'VideoPolling.NextUserID': '${observer.room_user_id}'})
                row['params']['user_ids'] = ['${operator.room_user_id}', '${observer.room_user_id}']
        if op == 'reply_self_state_change':
            row.update(enabled=True, status='IMPLEMENTED', isolated=True,
                       risk='ISOLATED_STATEFUL', outcome='EVENT_SEQUENCE',
                       design_note='先收到真实6646再由目标端回6546；无同步Response，拒绝不广播成功状态')
        if op == 'control_user_audio' and row['params'].get('state') in (0, 2):
            state = row['params']['state']
            row['params']['device_id'] = 1  # HTTP设备1映射Conf音频设备0。
            row.update(enabled=True, status='IMPLEMENTED', isolated=True, risk='ISOLATED_STATEFUL',
                       fields={'UserID': '${observer.room_user_id}', 'Audio.ID': 0, 'Audio.State': state},
                       pre='TARGET_AUDIO_ON' if state == 0 else 'TARGET_AUDIO_OFF',
                       design_note='验证HTTP直接会控路径及6609设备映射，不代表6542接受/拒绝链')
        elif op == 'control_user_audio':
            row['params']['device_id'] = 1
            row['id'] = row['id'].replace('-N-', '-B-')
            row.update(enabled=True, status='IMPLEMENTED', isolated=True, risk='ISOLATED_STATEFUL',
                       kind='BUSINESS_EXCEPTION', outcome='ERROR_AND_NO_EVENT', errors=[4019],
                       fields={'UserID': '${observer.room_user_id}'},
                       design_note='HTTP音频state=1违反0/2枚举，断言实际错误码4019和无成功6609；不要求Conf断会')
        if op == 'control_user_video':
            row['params']['device_id'] = 2
            row.update(enabled=True, status='IMPLEMENTED', isolated=True, risk='ISOLATED_STATEFUL',
                       fields={'UserID': '${observer.room_user_id}', 'Video.ID': 1, 'Video.State': 2},
                       pre='TARGET_VIDEO_OFF',
                       design_note='只验证HTTP直接视频会控与Conf设备1；6542/6642/6543/6643确认链由REMOTE_AV_CONSENT四组合独立覆盖')
        if op == 'configure_polling' and row['kind'] == 'NORMAL':
            row.update(enabled=True, status='IMPLEMENTED', isolated=True,
                       risk='ISOLATED_STATEFUL',
                       design_note='运行态断言6633；初始暂停/停止先断言无用户切换，再携带Interval/Screen/Window等完整配置启动同ID；不验证省略保留')
            if row['params'].get('screen') == -1:
                row['design_note'] = 'Screen由服务器透传给端；A/B收到6633.Screen=-1且核心字段正确，连接健康，不验证端画面'
        if op in {'enter_group', 'end_groups', 'bring_to_main',
                  'modify_group_config', 'change_group_state'}:
            row.update(enabled=True, status='IMPLEMENTED', isolated=True,
                       risk='ISOLATED_STATEFUL',
                       design_note='通过公开方法申请分组权、创建分组并使用动态RoomID；返回前建立分组Conf')
            if op == 'enter_group':
                row['params'] = {'room_id': '${group.room_id}', 'users': [
                    {'ID': '${observer.room_user_id}', 'Right': 2}]}
                row.update(receivers='observer', forbidden='operator',
                           fields={'RoomID': '${group.room_id}'})
            elif op in {'end_groups', 'change_group_state'}:
                row['params']['room_ids'] = ['${group.room_id}']
            elif op == 'modify_group_config':
                row['response'] = {'RoomInfo.RoomID': '${group.room_id}',
                                   'RoomInfo.Theme': 'E2E修改分组'}
            elif op == 'bring_to_main':
                row.update(receivers='observer', forbidden='operator')
        if (op == 'configure_polling' and row['params'].get('polling_id') in (-1, 0, 2003, 2004, 2005, 2006, 2008)
                and row['status'] == 'SKIP_BLOCKED'):
            row['id'] = row['id'].replace('-B-', '-N-')
            row.update(kind='NORMAL', enabled=True, status='IMPLEMENTED', isolated=True,
                       scene='POLLING_BOUNDARY', pre='POLLING_RIGHT', risk='ISOLATED_STATEFUL',
                       outcome='EVENT_SEQUENCE', errors=[], response={}, notifies=[], fields={},
                       design_note='Interval单位毫秒；内存配置边界与有限周期观察，不代表数据库持久化或长期回绕验证')
            if row['params']['user_ids'] and row['params']['user_ids'] != [999999999]:
                row['params']['user_ids'] = ['${operator.room_user_id}', '${observer.room_user_id}']
            if row['params']['polling_id'] == 0:
                row['params'].update(name='E2E批量轮询', user_ids=['${operator.room_user_id}', '${observer.room_user_id}'], interval=800)
                for operation in (2, 3):
                    extra = copy.deepcopy(row)
                    extra['id'] = f'FM-POLLING-N-BATCH-{operation}'
                    extra['params']['operation'] = operation
                    reviewed.append(extra)
        if row['closure'] == 'HTTP_RESULT':
            row.update(risk='SAFE_REUSABLE', isolated=False)
            if 'page_num' in row['params']:
                row['params']['page_number']=row['params'].pop('page_num')
            if row['operation']=='get_meeting_summary':
                row['params']={'roomId':'${operator.room_id}',
                               'beginTime':'${recording.window_begin}',
                               'endTime':'${recording.window_end}', 'pageNumber':1, 'pageSize':100}
                row['design_note']='按正式列表接口的roomId和时间窗口查询，不使用未定义groupId筛选'
            if 'language' in row['params']:
                if row['operation']=='download_meeting_summary':
                    continue  # 该接口无language参数，与默认下载重复，不凑用例数。
                if row['operation'] in ('export_meeting_summary','export_transcript'):
                    language=row['params'].pop('language')
                    row['params']['type']=1 if language=='zh-CN' else 2
                    row['id']=row['id'].replace('-N-', '-N-format-')
                    row['design_note']='按正式API的type=1 Markdown/type=2 PDF验证，不使用未定义language参数'
        if row['scene'] == 'BASIC_MEETING' and row['kind'] == 'NORMAL':
            from meeting_test_runtime.assertion_contracts import notify_fields, response_fields
            derived = notify_fields(row['operation'], row['params'], row['actor'])
            row['fields'] = {**derived, **row['fields']}
            row['response'] = {**response_fields(row['operation'], row['params']), **row['response']}
        if op == 'create_group' and row['kind'] == 'NORMAL':
            first = row['params']['rooms'][0]
            request_pair = (int(first.get('EnableJoin', 0)), int(first.get('EnableLeave', 0)))
            variant = group_variants.get(request_pair, 0)
            group_variants[request_pair] = variant + 1
            parent_join, parent_leave = divmod(variant % 4, 2)
            row.update(scene=f'GROUP_CONFIG_{parent_join}{parent_leave}',
                       isolated=True, risk='ISOLATED_STATEFUL')
            for i, room in enumerate(row['params']['rooms']):
                row['response'][f'RoomInfo.{i}.MaxUserCount'] = '${group.max_user_count}'
                row['response'][f'RoomInfo.{i}.EnableJoin'] = '${group.enable_join}'
                row['response'][f'RoomInfo.{i}.EnableLeave'] = '${group.enable_leave}'
            row['design_note'] = (f'主会议加入={parent_join}、离开={parent_leave}；先通过公开set_conf_state设置并验证A/B的6610，'
                                 '6623按主会议配置覆盖请求值；人数由6634查询；创建配置不代表实际加入/离开成功')
        if row['kind'] == 'NORMAL' and (op == 'mic_request' or
                (op == 'set_audio_state' and row['params'].get('state') == 1)):
            row.update(scene='AUDIO_APPLICATION_DIRECT', isolated=True, risk='ISOLATED_STATEFUL',
                       design_note='本人原状态0；显式授予申请及直接广播权限，申请1直接获准为2；非等待审批用例')
            row['fields']['Audio.State'] = 2
        if op == 'pause_record':
            continue  # 用户确认暂停不测试；在组合生成后剔除，避免补位凑数。
        if op == 'chat' and row['kind'] == 'NORMAL' and not chat_review_added:
            for repeat, suffix in ((False, 'REJECT'), (True, 'REJECT-THEN-APPROVE')):
                extra = copy.deepcopy(row)
                extra.update(
                    id=f'FM-CHAT-REVIEW-N-{suffix}', scene='CHAT_REVIEW',
                    pre='NONE', actor='operator', target='observer',
                    operation='review_chat_message_and_wait',
                    params={'repeat_after_reject': repeat}, isolated=True,
                    level='P1', risk='ISOLATED_STATEFUL',
                    status='IMPLEMENTED', enabled=True,
                    outcome='EVENT_SEQUENCE', response={}, notifies=[], fields={},
                    receivers='', forbidden='',
                    design_note=(
                        '独立会议开启公共聊天审批；B发送待审6612，A拒绝并收到6629 CheckResult=0；'
                        + ('再次同意相同SerialNumber静默忽略且正文不发布'
                           if repeat else 'B不收到被拒正文')))
                reviewed.append(extra)
            chat_review_added = True
        if op == 'start_record' and row['kind'] == 'NORMAL' and not record_error_added:
            extra = copy.deepcopy(row)
            extra.update(
                id='FM-RECORD-N-ERROR-6637', scene='RECORD_ERROR_6637',
                pre='NONE', params={}, isolated=True,
                level='P1',
                risk='ISOLATED_STATEFUL', status='IMPLEMENTED', enabled=True,
                outcome='EVENT_SEQUENCE', response={}, notifies=[], fields={},
                receivers='', forbidden='',
                design_note=(
                    '开会前通过CES配置recordMode=4/autoRecord=0/recordUsers=A平台ID；'
                    'A开始录制后定向收到6637.Error=0x3201，再收到6610.Error=0x3201，B不接收'))
            reviewed.append(extra)
            record_error_added = True
        if op == 'request_remote_av' and not remote_av_added:
            for kind, accept, device_id in (
                    ('audio', True, 0), ('audio', False, 0),
                    ('video', True, 1), ('video', False, 1)):
                extra = copy.deepcopy(row)
                decision = 'ACCEPT' if accept else 'REJECT'
                extra.update(
                    id=f'FM-REMOTE-AV-N-{kind.upper()}-{decision}',
                    scene='REMOTE_AV_CONSENT', pre='NONE',
                    params={'kind': kind, 'accept': accept,
                            'device_id': device_id},
                    actor='operator', target='observer', isolated=True,
                    risk='ISOLATED_STATEFUL', status='IMPLEMENTED', enabled=True,
                    outcome='EVENT_SEQUENCE', response={}, notifies=[], fields={},
                    receivers='', forbidden='',
                    design_note=(
                        'Conf 5.0独立会议：A发6542，B收6642并显式同意/拒绝；'
                        'A收6643，接受时A/B收6609，拒绝时无成功6609'))
                reviewed.append(extra)
            remote_av_added = True
            continue
        if op == 'send_room_message':
            row.update(
                scene='WEB_NOTIFY_6654', pre='NONE', isolated=True,
                risk='ISOLATED_STATEFUL', outcome='EVENT_SEQUENCE',
                response={}, notifies=[], fields={}, receivers='', forbidden='',
                design_note=(
                    'HTTP创建临时固定会议；后台send_room_message成功后仅B收到6654，'
                    '断言特殊字符、A无同消息及A/B连接健康'))
        if op == 'allow_waiting_users' and row['kind'] == 'NORMAL':
            for decision in ('reject', 'mixed'):
                extra = copy.deepcopy(row)
                extra.update(id=f'FM-WAITING-N-{decision.upper()}',
                             scene='WAITING_REVIEW', pre='NONE',
                             params={'decision': decision, **(
                                 {'witness': '${witness.room_user_id}'}
                                 if decision == 'mixed' else {})},
                             isolated=True, risk='DESTRUCTIVE_ISOLATED',
                             outcome='EVENT_SEQUENCE', response={}, notifies=[],
                             fields={}, receivers='', forbidden='',
                             flow_id='FLOW-WAITING-ROOM',
                             design_note='独立会议中B被拒绝；混合审批时C允许入会，逐用户断言6670与6602')
                reviewed.append(extra)
        if (op == 'start_aux_stream' and row['kind'] == 'NORMAL' and
                row['params'].get('kind') == 'vnc' and not fm_aux_added):
            extra = copy.deepcopy(row)
            extra.update(id='FM-AUX-N-FM-CONTENTION', scene='AUX_FM_CONTENTION',
                         pre='NONE', params={'kind': 'vnc'}, isolated=True,
                         risk='ISOLATED_STATEFUL', status='IMPLEMENTED', enabled=True,
                         blocker_id='',
                         outcome='EVENT_SEQUENCE',
                         response={}, notifies=[], fields={}, receivers='',
                         forbidden='', flow_id='FLOW-AUX-CONTENTION',
                         design_note='独立会议完整接替：B争抢返回8486，A停止后B接替并停止；断言A/B状态、权限及清理健康')
            reviewed.append(extra)
            fm_aux_added = True
        reviewed.append(row)
    return reviewed


def create_template(output: Path, protocol: Path):
    book = Workbook(); book.remove(book.active)
    profiles = _sheet(book, "终端配置", ["配置ID", "终端类型", "账号引用", "环境引用", "可承担角色", "启用"])
    profiles.append(["FM_01", "fm", "fm_operator", "fm_env", "operator", "是"])
    profiles.append(["FM_02", "fm", "fm_observer", "fm_env", "observer,target", "是"])
    profiles.append(["FM_03", "fm", "fm_witness", "fm_env", "witness", "是"])

    domains = _sheet(book, "参数域", ["参数域ID", "操作名", "参数路径", "正常值JSON", "边界值JSON", "异常值JSON", "组合约束"])
    domain_rows = [
        ("AUDIO_STATE", "set_audio_state", "state/dev_id", [[0,1,2],[0]], [0,2], [-1,3,99,1,2], "state×scope；音频有效DeviceID仅0，1/2为静默拒绝"),
        ("VIDEO_STATE", "set_video_state", "state", [0,2], [0,2], [-1,3,99], "state×scope×device"),
        ("LAYOUT", "set_layout", "layout_type", [0,1,2,3,4,6,9,12,16,25,36,49,64,1001,1002], [0,64,1001,1002], [-1,5,9999], "正式布局×投递范围×成员顺序；0为合法自动布局"),
        ("ROLE", "set_role", "role", ["presenter","admin","chairman"], [], ["invalid"], "chairman只允许self"),
        ("AUX", "start_aux_stream", "kind", ["media","vnc"], [], ["invalid"], "kind×action"),
        ("POLLING", "configure_polling", "operation/status/interval", [[1,2,3],[0,1,2],[1,10,60]], [], [], "operation×status×interval"),
        ("RIGHT", "request_right", "right_type", list(range(1,13)), [1,12], [0,13], "申请×释放"),
    ]
    for did, op, path, normal, boundary, invalid, constraint in domain_rows:
        domains.append([did, op, path, json.dumps(normal,ensure_ascii=False), json.dumps(boundary), json.dumps(invalid), constraint])

    headers = ["用例ID","模块","闭环类型","用例类型","场景类型","前置模板","操作角色","目标角色",
               "统一操作名","参数JSON","预期结果类型","预期业务键","预期Response","预期Notify序列JSON",
               "预期接收端","不应接收端","字段断言JSON","预期错误码JSON","独立执行","执行级别",
               "环境风险","用例状态","阻塞ID","依赖流程ID","设计说明","是否执行"]
    case_sheet = _sheet(book, "功能用例", headers); cases = build_cases()
    for c in cases:
        case_sheet.append([c["id"],c["module"],c["closure"],c["kind"],c["scene"],c["pre"],c["actor"],c["target"],
                           c["operation"],json.dumps(c["params"],ensure_ascii=False),c["outcome"],c["business"],
                           json.dumps(c["response"],ensure_ascii=False),json.dumps(c["notifies"],ensure_ascii=False),
                           c["receivers"],c["forbidden"],json.dumps(c["fields"],ensure_ascii=False),
                           json.dumps(c["errors"]),"是" if c["isolated"] else "否",c["level"],
                           c["risk"],c["status"],_blocker_id(c),c["flow_id"],c["design_note"],
                           "是" if c["enabled"] else "否"])

    flow_sheet = _sheet(book, "依赖流程", [
        "流程ID", "流程名称", "优先级", "环境风险", "步骤序号", "执行端", "统一操作名",
        "参数JSON", "输出变量JSON", "预期Response", "预期Notify序列JSON", "预期接收端",
        "不应接收端", "预期错误码JSON", "预期非零Result", "步骤说明",
        "失败是否终止", "清理要求", "实现状态",
    ])
    flows = [
        ("FLOW-HTTP-CREATE-JOIN", "HTTP建会到6602", "P0", "DESTRUCTIVE_ISOLATED", [
            ("http_admin", "create_instant_meeting", "动态会议参数", "meeting.room_id,meeting.node_id,meeting.meeting_no,meeting.invite_code", "HTTP成功"),
            ("operator", "front_login", "${meeting.room_id}", "operator.room_user_id", "Front成功"),
            ("operator", "conf_login", "${meeting.room_id}", "", "Conf初始化完成"),
            ("observer", "conf_login", "${meeting.room_id}", "observer.room_user_id", "operator收到6602"),
        ]),
        ("FLOW-LOCK-JOIN", "锁会入会失败再解锁", "P1", "DESTRUCTIVE_ISOLATED", [
            ("operator", "lock_room", "{}", "", "6610锁定"), ("target", "conf_login", "${meeting.room_id}", "", "入会失败且无6602"),
            ("operator", "unlock_room", "{}", "", "6610解锁"), ("target", "conf_login", "${meeting.room_id}", "target.room_user_id", "入会成功及6602")]),
        ("FLOW-HTTP-INVITE", "Front待邀请到入会", "P1", "DESTRUCTIVE_ISOLATED", [
            ("invitee", "front_authenticate", "{}", "", "Front在线"), ("http_admin", "invite_users", "${invitee.user_id}", "", "邀请Notify"),
            ("invitee", "conf_login", "${meeting.room_id}", "invitee.room_user_id", "observer收到6602")]),
        ("FLOW-WAITING-ROOM", "等候室允许或拒绝", "P1", "DESTRUCTIVE_ISOLATED", [
            ("target", "conf_login", "等待室模式", "", "operator收到6604且target收到6617"),
            ("operator", "allow_knock", "allow=0/1", "", "拒绝或入会结果")]),
        ("FLOW-RIGHT-TRANSFER", "操作权占用与转移", "P1", "ISOLATED_STATEFUL", [
            ("operator", "request_right", "right_type", "", "6630成功"), ("observer", "request_right", "同一right_type", "", "6630占用冲突"),
            ("operator", "release_right", "right_type", "", "6630释放"), ("observer", "request_right", "right_type", "", "6630成功"),
            ("observer", "release_right", "right_type", "", "6630释放")]),
        ("FLOW-LAYOUT-SWITCH", "4分屏到9分屏再自动", "P0", "ISOLATED_STATEFUL", [
            ("operator", "request_right", "right_type=1", "", "6630成功"),
            ("operator", "set_layout", "layout_type=1", "", "B收到6613及VideoRelayID=1"),
            ("operator", "set_layout", "layout_type=4", "", "B收到6613及VideoRelayID=4"),
            ("operator", "set_layout", "4分屏调整成员顺序和MediaID/Pos", "", "B收到新6613顺序"),
            ("operator", "set_layout", "layout_type=9", "", "B收到6613及VideoRelayID=9"),
            ("operator", "set_layout", "layout_type=1001", "", "B收到6613及焦点成员"),
            ("operator", "set_layout", "layout_type=0", "", "B收到6613及VideoRelayID=0"),
            ("operator", "release_right", "right_type=1", "", "6630释放")]),
        ("FLOW-DEVICE-AV", "设备和音视频状态链", "P1", "ISOLATED_STATEFUL", [
            ("operator", "report_av_info", "新增音视频设备", "device.ids", "observer收到6608"), ("operator", "set_audio_state", "音频状态", "", "A/B收到6609"),
            ("operator", "set_video_state", "视频状态", "", "A/B收到6609"), ("operator", "report_av_info", "默认视频切换", "", "observer收到6608"),
            ("operator", "report_av_info", "删除设备", "", "observer收到6608")]),
        # 本轮验收data_source=prepared：只查询预置录制记录并读取纪要/导出，
        # 不含录制生成、暂停、恢复、停止。动态录制状态机不在本流程验收范围。
        ("FLOW-RECORD-RESULT", "预置录制查询与纪要导出", "P1", "DESTRUCTIVE_ISOLATED", [
            ("http_admin", "get_record_summary", "预置roomId", "recording.group_id", "摘要就绪"),
            ("http_admin", "export_meeting_summary", "${recording.group_id}", "", "文件响应"),
            ("http_admin", "export_transcript", "${recording.group_id}", "", "文件响应")]),
        ("FLOW-CHAT-RECALL", "聊天消息撤回", "P1", "DESTRUCTIVE_ISOLATED", [
            ("operator", "chat", "唯一消息内容", "message.message_id", "observer收到6612"),
            ("operator", "recall_msg", "${message.message_id}", "", "operator收到6668且observer收到6669"),
            ("operator", "recall_msg", "${message.message_id}", "", "重复撤回失败且无成功6669")]),
        ("FLOW-GROUP-LIFECYCLE", "动态分组完整流程", "P1", "ISOLATED_STATEFUL", [
            ("operator", "request_right", "分组权", "", "6630成功"), ("operator", "create_group", "分组成员", "group.room_id", "6623成功"),
            ("operator", "enter_group", "${group.room_id}", "", "6624"), ("operator", "bring_to_main", "成员", "", "6626"),
            ("operator", "end_groups", "${group.room_id}", "", "6625"), ("operator", "release_right", "分组权", "", "6630释放")]),
        ("FLOW-AUX-CONTENTION", "辅流权争抢", "P1", "ISOLATED_STATEFUL", [
            ("operator", "request_right", "辅流权", "", "6630成功"), ("operator", "start_aux_stream", "media", "", "6609开始"),
            ("observer", "request_right", "辅流权", "", "6630占用冲突"), ("operator", "stop_aux_stream", "media", "", "6609停止"),
            ("operator", "release_right", "辅流权", "", "6630释放")]),
        ("FLOW-POLLING-LIFECYCLE", "轮询完整状态机", "P1", "ISOLATED_STATEFUL", [
            ("operator", "request_right", "轮询权", "", "6630成功"), ("operator", "configure_polling", "新增", "polling.polling_id", "6633"),
            ("operator", "configure_polling", "修改", "", "6633"), ("operator", "configure_polling", "暂停", "", "6633"),
            ("operator", "configure_polling", "恢复", "", "6633"), ("operator", "configure_polling", "停止", "", "6633"),
            ("operator", "configure_polling", "删除", "", "6633"), ("operator", "release_right", "轮询权", "", "6630释放")]),
    ]
    flows.extend([
        ('FLOW-H323-AUX-AUTO', 'Fm先发VNC、H323自动抢发', 'P1', 'RISKY_MANUAL', [
            ('operator', 'start_aux_stream', 'VNCState=2', '', 'A/B收到A共享6609'),
            ('h323', 'start_aux_stream', '真实终端主动发起', '',
             '本期不执行、不计阻塞；后续纳入时通过H323网关HTTP触发器驱动')]),
        ('FLOW-H323-AUX-APPROVAL', 'Fm审批H323抢发VNC', 'P1', 'RISKY_MANUAL', [
            ('operator', 'start_aux_stream', 'VNCState=2', '', 'A/B收到A共享6609'),
            ('h323', 'start_aux_stream', '真实终端主动申请', '',
             '本期不执行、不计阻塞；后续纳入时通过H323网关HTTP触发器驱动'),
            ('operator', 'set_user_state', '同意H323状态', '',
             '范围外流程，不作为当前验收项')]),
    ])
    executable_flows = {
        # 本轮验收配置recording.data_source=prepared：实际执行由RecordingDatasetProvider
        # 查询预置会议录制记录取得groupId，再读取纪要摘要并导出纪要/字幕。不启动、暂停、
        # 恢复或停止录制，也不校验录制/转写/纪要生成状态机；动态录制(2→1→2→0)未纳入验收。
        # Sheet步骤用于评审与Allure追溯，与test_dependency_flows.py的prepared分支一致。
        "FLOW-RECORD-RESULT": [
            ("http_admin", "get_record_summary",
             {"group_id": "${recording.group_id}"}, {}, {}, [], "", ""),
            ("http_admin", "export_meeting_summary",
             {"group_id": "${recording.group_id}"}, {}, {}, [], "", ""),
            ("http_admin", "export_transcript",
             {"group_id": "${recording.group_id}"}, {}, {}, [], "", ""),
        ],
        "FLOW-LOCK-JOIN": [
            ("http_admin", "create_instant_meeting",
             {"meeting_name": "E2E锁会入会", "user_name": "${env.http_user}",
              "meeting_type": 2},
             {"meeting.room_id": "response.data.roomId",
              "meeting.node_id": "response.data.nodeId"}, {}, [], "", ""),
            ("operator", "front_login",
             {"room_id": "${meeting.room_id}", "room_node_id": "${meeting.node_id}"},
             {}, {}, [], "", ""),
            ("operator", "conf_login", {}, {}, {}, [], "", ""),
            ("operator", "lock_room", {}, {}, {},
             [{"source": "conf", "cmdid": 6610, "kind": "notify",
               "match": {"Lock": 1}}], "operator", ""),
            ("observer", "front_login",
             {"room_id": "${meeting.room_id}", "room_node_id": "${meeting.node_id}"},
             {}, {}, [], "", ""),
            ("observer", "start_async_call",
             {"handle": "locked_join", "operation": "conf_login", "params": {}},
             {}, {}, [], "", ""),
            ("operator", "wait_async_events",
             {"handle": "locked_join", "operation": "allow_knock"}, {}, {},
             [{"source": "conf", "cmdid": 6617, "kind": "notify",
               "receivers": ["observer"]},
              {"source": "conf", "cmdid": 6604, "kind": "notify",
               "match": {"UserID": "${observer.room_user_id}"},
               "receivers": ["operator"]}], "", ""),
            ("operator", "allow_knock",
             {"user_id": "${observer.room_user_id}", "allow": 1},
             {}, {}, [], "", ""),
            ("observer", "wait_async_call", {"handle": "locked_join"}, {},
             {"Result": 0},
             [{"source": "conf", "cmdid": 6602, "kind": "notify",
               "match": {"User.0.UserID": "${observer.room_user_id}"},
               "receivers": ["operator"]}], "", ""),
            ("operator", "unlock_room", {}, {}, {},
             [{"source": "conf", "cmdid": 6610, "kind": "notify",
               "match": {"Lock": 0}}], "operator", ""),
        ],
        "FLOW-HTTP-CREATE-JOIN": [
            ("http_admin", "create_instant_meeting",
             {"meeting_name": "E2E动态建会闭环", "user_name": "${env.http_user}",
              "meeting_type": 2},
             {"meeting.room_id": "response.data.roomId",
              "meeting.node_id": "response.data.nodeId"}, {}, [], "", ""),
            ("operator", "front_login",
             {"room_id": "${meeting.room_id}", "room_node_id": "${meeting.node_id}"},
             {}, {}, [], "", ""),
            ("operator", "conf_login", {}, {}, {}, [], "", ""),
            ("observer", "front_login",
             {"room_id": "${meeting.room_id}", "room_node_id": "${meeting.node_id}"},
             {}, {}, [], "", ""),
            ("observer", "conf_login", {}, {}, {},
             [{"source": "conf", "cmdid": 6602, "kind": "notify",
               "match": {"User.0.UserID": "${observer.room_user_id}"}}],
             "operator", "observer"),
        ],
        "FLOW-CHAT-RECALL": [
            ("operator", "chat",
             {"text": "E2E待撤回消息", "dst_user_id": 0},
             {"message.id": "events.observer:notify:0.fields.MsgID"}, {},
             [{"source": "conf", "cmdid": 6612, "kind": "notify",
               "match": {"SrcUserID": "${operator.room_user_id}",
                         "DstUserID": 0, "Text": "E2E待撤回消息"}}],
             "operator,observer", ""),
            ("operator", "recall_msg",
             {"msg_id": "${message.id}"}, {},
             {"MsgID": "${message.id}", "Result": 0},
             [{"source": "conf", "cmdid": 6669, "kind": "notify",
               "match": {"MsgID": "${message.id}",
                         "SrcUserID": "${operator.room_user_id}"}}],
             "observer", "operator"),
            ("operator", "recall_msg",
             {"msg_id": "${message.id}"}, {},
             {"MsgID": "${message.id}"},
             [{"source": "conf", "cmdid": 6669, "kind": "notify",
               "match": {"MsgID": "${message.id}"},
               "forbidden_receivers": ["observer"]}],
             "", "observer", [], True),
        ],
        "FLOW-RIGHT-TRANSFER": [
            ("operator", "request_right", {"right_type": 1}, {},
             {"Result": 0, "Operation": 1, "RightType": 1,
              "OccupiedUserID": "${operator.room_user_id}"}, [], "", ""),
            ("observer", "request_right", {"right_type": 1}, {},
             {"Result": 0, "Operation": 1, "RightType": 1,
              "OccupiedUserID": "${observer.room_user_id}"}, [], "", ""),
            ("observer", "release_right", {"right_type": 1}, {},
             {"Result": 0, "Operation": 0, "RightType": 1}, [], "", ""),
            ("operator", "request_right", {"right_type": 1}, {},
             {"Result": 0, "Operation": 1, "RightType": 1,
              "OccupiedUserID": "${operator.room_user_id}"}, [], "", ""),
            ("operator", "release_right", {"right_type": 1}, {},
             {"Result": 0, "Operation": 0, "RightType": 1}, [], "", ""),
        ],
        "FLOW-AUX-CONTENTION": [
            ("operator", "request_right", {"right_type": 5}, {},
             {"Result": 0, "Operation": 1, "RightType": 5}, [], "", ""),
            ("operator", "start_aux_stream",
             {"kind": "media", "audio_share_id": 31, "video_share_id": 32},
             {}, {}, [{"source": "conf", "cmdid": 6609, "kind": "notify",
                       "match": {"UserID": "${operator.room_user_id}",
                                 "MediaShareState": 2,
                                 "AudioShareID": 31, "VideoShareID": 32}}],
             "operator,observer", ""),
            ("observer", "request_right", {"right_type": 5}, {},
             {}, [], "", "", [8478]),
            ("operator", "stop_aux_stream", {"kind": "media"}, {}, {},
             [{"source": "conf", "cmdid": 6609, "kind": "notify",
               "match": {"UserID": "${operator.room_user_id}",
                         "MediaShareState": 0}}], "operator,observer", ""),
            ("operator", "release_right", {"right_type": 5}, {},
             {"Result": 0, "Operation": 0, "RightType": 5}, [], "", ""),
        ],
        "FLOW-DEVICE-AV": [
            ("operator", "report_av_info",
             {"audio": [{"Operation": 1, "ID": 0, "Name": "flow-mic"}],
              "video": [{"Operation": 1, "ID": 1, "Name": "flow-camera", "Type": 0}],
              "def_video_dev_id": 1}, {}, {},
             [{"source": "conf", "cmdid": 6608, "kind": "notify",
               "match": {"Audio.Operation": 1, "Audio.ID": 0}},
              {"source": "conf", "cmdid": 6608, "kind": "notify",
               "match": {"Video.Operation": 1, "Video.ID": 1,
                         "DefVideoDevID": 1}}], "observer", "operator"),
            ("operator", "set_audio_state", {"state": 2, "dev_id": 0}, {}, {},
             [{"source": "conf", "cmdid": 6609, "kind": "notify",
               "match": {"UserID": "${operator.room_user_id}",
                         "Audio.ID": 0, "Audio.State": 2}}],
             "operator,observer", ""),
            ("operator", "set_video_state", {"state": 2, "dev_id": 1}, {}, {},
             [{"source": "conf", "cmdid": 6609, "kind": "notify",
               "match": {"UserID": "${operator.room_user_id}",
                         "Video.ID": 1, "Video.State": 2}}],
             "operator,observer", ""),
            ("operator", "report_av_info",
             {"video": [{"Operation": 2, "ID": 1, "Name": "flow-camera-main", "Type": 0}],
              "def_video_dev_id": 1}, {}, {},
             [{"source": "conf", "cmdid": 6608, "kind": "notify",
               "match": {"Video.0.Operation": 2, "Video.0.ID": 1,
                         "Video.0.Name": "flow-camera-main", "DefVideoDevID": 1}}],
             "observer", "operator"),
            ("operator", "report_av_info",
             {"audio": [{"Operation": 3, "ID": 0, "Name": "flow-mic"}],
              "video": [{"Operation": 3, "ID": 1, "Name": "flow-camera-main", "Type": 0}]},
             {}, {}, [{"source": "conf", "cmdid": 6608, "kind": "notify",
                       "match": {"Audio.Operation": 3, "Audio.ID": 0}},
                      {"source": "conf", "cmdid": 6608, "kind": "notify",
                       "match": {"Video.Operation": 3, "Video.ID": 1}}],
             "observer", "operator"),
        ],
        "FLOW-GROUP-LIFECYCLE": [
            ("operator", "request_right", {"right_type": 11}, {},
             {"Result": 0, "Operation": 1, "RightType": 11}, [], "", ""),
            ("operator", "create_group",
             {"rooms": [{"Theme": "E2E动态分组"}]},
             {"group.room_id": "RoomInfo.0.RoomID"},
             {"RoomInfo.0.Theme": "E2E动态分组"}, [], "", ""),
            ("operator", "enter_group",
             {"room_id": "${group.room_id}",
              "users": [{"ID": "${observer.room_user_id}", "Right": 2}]},
             {'group.route': 'events.observer:notify:0.fields'}, {}, [{"source": "conf", "cmdid": 6624, "kind": "notify",
                       "match": {"RoomID": "${group.room_id}"},
                       "receivers": ["observer"],
                       "forbidden_receivers": ["operator"]}], "", ""),
            ('observer', 'join_related', {'alias': 'observer_group', 'fields': '${group.route}'},
             {}, {}, [], '', ''),
            ("operator", "bring_to_main",
             {"user_ids": ["${observer.room_user_id}"]}, {}, {},
             [{"source": "conf", "cmdid": 6626, "kind": "notify",
               "receivers": ["observer"],
               "forbidden_receivers": ["operator"]}], "", ""),
            ('observer', 'close_related', {'alias': 'observer_group'}, {}, {}, [], '', ''),
            ("operator", "end_groups", {"room_ids": ["${group.room_id}"]},
             {}, {}, [], "", ""),
            ("operator", "release_right", {"right_type": 11}, {},
             {"Result": 0, "Operation": 0, "RightType": 11}, [], "", ""),
        ],
        "FLOW-LAYOUT-SWITCH": [
            ("operator", "request_right", {"right_type": 1}, {},
             {"Result": 0, "Operation": 1, "RightType": 1}, [], "", ""),
            *[("operator", "set_layout",
               {"layout_type": layout,
                "user_ids": ["${operator.room_user_id}", "${observer.room_user_id}"],
                "mode": 3}, {}, {},
               [{"source": "conf", "cmdid": 6613, "kind": "notify",
                 "match": {"Mode": 3, "VideoRelayID": layout,
                           "Video.0.UserID": "${operator.room_user_id}",
                           "Video.1.UserID": "${observer.room_user_id}"}}],
               "observer", "operator") for layout in (1, 4)],
            ("operator", "set_layout",
             {"layout_type": 4,
              "user_ids": ["${operator.room_user_id}", "${observer.room_user_id}"],
              "mode": 3}, {}, {},
             [{"source": "conf", "cmdid": 6613, "kind": "notify",
               "match": {"Mode": 3, "VideoRelayID": 4,
                         "Video.0.UserID": "${operator.room_user_id}",
                         "Video.1.UserID": "${observer.room_user_id}"}}],
             "observer", "operator"),
            ("operator", "set_layout",
             {"layout_type": 4, "mode": 3, "videos": [
                 {"UserID": "${observer.room_user_id}", "MediaID": 21, "Pos": 0},
                 {"UserID": "${operator.room_user_id}", "MediaID": 20, "Pos": 1}]},
             {}, {}, [{"source": "conf", "cmdid": 6613, "kind": "notify",
                       "match": {"VideoRelayID": 4,
                                 "Video.0.UserID": "${observer.room_user_id}",
                                 "Video.0.MediaID": 21, "Video.0.Pos": 0,
                                 "Video.1.UserID": "${operator.room_user_id}",
                                 "Video.1.MediaID": 20, "Video.1.Pos": 1}}],
             "observer", "operator"),
            *[("operator", "set_layout",
               {"layout_type": layout,
                "user_ids": ["${operator.room_user_id}", "${observer.room_user_id}"],
                "mode": 3}, {}, {},
               [{"source": "conf", "cmdid": 6613, "kind": "notify",
                 "match": {"Mode": 3, "VideoRelayID": layout,
                           "Video.0.UserID": "${operator.room_user_id}"}}],
               "observer", "operator") for layout in (9, 1001, 0)],
            ("operator", "release_right", {"right_type": 1}, {},
             {"Result": 0, "Operation": 0, "RightType": 1}, [], "", ""),
        ],
        "FLOW-POLLING-LIFECYCLE": [
            ("operator", "request_right", {"right_type": 2}, {},
             {"Result": 0, "Operation": 1, "RightType": 2}, [], "", ""),
            *[("operator", "configure_polling",
               {"operation": operation, "polling_id": 1001, "name": "轮询1",
                "user_ids": users, "interval": interval, "screen": screen,
                "window": window, "status": status}, {}, {},
               [] if status == 1 else [{"source": "conf", "cmdid": 6633, "kind": "notify",
                  "match": {"VideoPolling.val": 1001,
                           "VideoPolling.Operation": notify_operation,
                           "VideoPolling.Interval": interval,
                           "VideoPolling.Screen": screen,
                           "VideoPolling.Window": window}}],
               "operator,observer", "")
              for operation, users, interval, screen, window, status, notify_operation in (
                   (1, ["${operator.room_user_id}", "${observer.room_user_id}"], 5, 1, 1, 2, 1),
                  (2, ["${observer.room_user_id}", "${operator.room_user_id}"], 30, 2, 1, 2, 1),
                  (2, ["${observer.room_user_id}", "${operator.room_user_id}"], 30, 2, 1, 1, 1),
                  (2, ["${observer.room_user_id}", "${operator.room_user_id}"], 30, 2, 1, 2, 1),
                  (2, ["${observer.room_user_id}", "${operator.room_user_id}"], 30, 2, 1, 0, 2),
                  (2, ["${observer.room_user_id}", "${operator.room_user_id}"], 30, 2, 1, 2, 1),
                  (3, ["${observer.room_user_id}", "${operator.room_user_id}"], 30, 2, 1, 0, 2),
              )],
            ("operator", "release_right", {"right_type": 2}, {},
             {"Result": 0, "Operation": 0, "RightType": 2}, [], "", ""),
        ],
    }
    blocked_flows = {}
    # 逐步骤说明：用于写清能力边界，替代无信息的“可执行步骤N”。
    step_notes = {
        ("FLOW-RECORD-RESULT", 1): (
            "查询预置会议录制记录取得groupId并读取录制摘要"
            "（本轮验收data_source=prepared，不启动录制、不暂停/恢复/停止）"),
        ("FLOW-RECORD-RESULT", 2): "导出会议纪要文件（不验证录制、转写和纪要生成状态机）",
        ("FLOW-RECORD-RESULT", 3): "导出字幕/转写文件（不验证录制、转写和纪要生成状态机）",
    }
    for flow_id, name, level, risk, steps in flows:
        if flow_id == 'FLOW-HTTP-INVITE':
            steps = [
                ('invitee', 'front_authenticate', '{}', '', 'Front身份及PaaS在线10001'),
                ('operator', 'invite_users', '${invitee.user_id}', '', '14002邀请到达、14003接受'),
                ('invitee', 'conf_login', '${meeting.room_id}', 'invitee.room_user_id',
                 '30005/6601成功且A收到6602'),
            ]
        elif flow_id == 'FLOW-WAITING-ROOM':
            name = '等候室混合审批'
            steps = [
                ('operator', 'set_conf_state', 'EnableWaitingRoom=1', '', '管理端进入等候室'),
                ('observer', 'conf_login', '等待审批', '', 'B收到6667和有效子会场路由'),
                ('witness', 'conf_login', '混合审批', '', 'C收到6667和有效子会场路由'),
                ('waiting_manager', 'allow_waiting_users', 'B拒绝、C允许', '',
                 '6670逐成员结果；B无成功6601/6602，C成功入会且A收到6602'),
            ]
        elif flow_id == 'FLOW-AUX-CONTENTION':
            name = 'Fm辅流争抢'
            steps = [
                ('operator', 'request_right', 'RightType=4', '', '6630占用者A'),
                ('operator', 'start_aux_stream', 'kind=vnc', '', 'A/B收到A的VNCState=2'),
                ('observer', 'request_right', 'RightType=4', '',
                 '6630.Result=8486，A/B无共享者切换'),
                ('operator', 'stop_aux_stream', 'kind=vnc', '', 'A/B收到A停止'),
                ('observer', 'start_aux_stream', 'kind=vnc', '',
                 'A/B收到B接替，再停止并释放'),
            ]
        implemented = None if flow_id == 'FLOW-AUX-CONTENTION' else executable_flows.get(flow_id)
        if implemented is not None:
            for index, step in enumerate(implemented, 1):
                actor, operation, params, outputs, response, notifies, receivers, forbidden, *rest = step
                expected_errors = rest[0] if rest else []
                expected_nonzero = bool(rest[1]) if len(rest) > 1 else False
                flow_sheet.append([
                    flow_id, name, level, risk, index, actor, operation,
                    json.dumps(params, ensure_ascii=False),
                    json.dumps(outputs, ensure_ascii=False),
                    json.dumps(response, ensure_ascii=False),
                    json.dumps(notifies, ensure_ascii=False), receivers, forbidden,
                    json.dumps(expected_errors), "是" if expected_nonzero else "否",
                    (blocked_flows.get(flow_id) or step_notes.get((flow_id, index))
                     or f"可执行步骤{index}"),
                    "是", "finally恢复状态并释放操作权",
                    "SKIP_BLOCKED" if flow_id in blocked_flows else "IMPLEMENTED",
                ])
            continue
        for index, (actor, operation, params, outputs, expected) in enumerate(steps, 1):
            covered = (
                [c['id'] for c in cases if c['scene'] == 'PAAS_INVITED_JOIN'
                 and c['operation'] in ('invite_users', 'invite_contacts')]
                if flow_id == 'FLOW-HTTP-INVITE' else
                [c['id'] for c in cases if c['scene'] == 'WAITING_REVIEW']
                if flow_id == 'FLOW-WAITING-ROOM' else
                [c['id'] for c in cases if c['scene'] == 'AUX_FM_CONTENTION']
                if flow_id == 'FLOW-AUX-CONTENTION' else [])
            flow_sheet.append([flow_id, name, level, risk, index, actor, operation,
                               "{}", "{}", "{}", "[]", "", "", "[]", "否",
                               expected + (f"；关联功能用例: {','.join(covered)}" if covered else ""),
                               "是", "场景finally统一清理",
                               "OUT_OF_SCOPE" if flow_id.startswith('FLOW-H323-AUX-') else
                               "COVERED_BY_CASES" if covered
                               else "DESIGNED"])

    signal = _sheet(book, "Conf信令检查", ["序号","cmdid","版本","协议说明","方向","对应公开方法","正常用例ID","异常用例ID","封装状态","执行状态","缺口原因"])
    for definition in scan_conf_protocol(protocol):
        linked = [op for op, meta in OPERATION_EXPECTATIONS.items()
                  if definition.cmdid in ({meta.request_cmdid} | {e.cmdid for e in meta.notifies} |
                                           ({meta.response.cmdid} if meta.response else set()))]
        linked = list(dict.fromkeys(linked + DOWNLINK_OPERATIONS.get(definition.cmdid, [])))
        ids = [c["id"] for c in cases if c["operation"] in linked]
        linked_flows = [fid for fid, steps in executable_flows.items()
                        if any(step[1] in linked or any(n.get('cmdid') == definition.cmdid
                               for n in step[5]) for step in steps)]
        ids.extend(linked_flows)
        direction = "Request" if any(
            m.request_cmdid == definition.cmdid for m in OPERATION_EXPECTATIONS.values()
        ) else "Response/Notify"
        blocker = blocker_for_cmdid(definition.cmdid)
        if blocker is not None:
            status, reason = blocker.kind.value, blocker.reason
        elif definition.cmdid in MISSING_CMDIDS:
            status, reason = "MISSING_SESSIONMANAGER_API", "缺少公共上行业务方法"
        elif definition.cmdid in PARTIAL_CMDIDS:
            status, reason = "部分封装", "部分扩展字段无显式业务参数"
        else:
            status, reason = "已封装", ""
        if not ids and not reason:
            if direction == "Response/Notify":
                status, reason = "DOWNLINK_ONLY_NEEDS_TRIGGER", "已解析但缺少可重复触发的业务场景"
            else:
                status, reason = "MISSING_TEST_CASE", "公开协议尚未关联功能用例"
        signal.append([definition.ordinal,definition.cmdid,definition.version or "",definition.description,direction,
                       ",".join(linked),",".join(i for i in ids if "-N-" in i or i.startswith('FLOW-')),",".join(i for i in ids if "-B-" in i or i == 'FLOW-CHAT-RECALL'),
                       status,"不执行（产品未使用）" if blocker is not None and
                       blocker.kind.value == "UNSUPPORTED_PROTOCOL"
                       else "BLOCKED" if blocker is not None
                       else "真实服务器通过" if definition.cmdid in REAL_SERVER_VERIFIED_CMDIDS
                       else "待真实服务器执行" if ids else "BLOCKED",reason])

    method_sheet = _sheet(book, "业务方法检查", ["公开方法","分类","正常用例数","异常用例数","Driver状态","说明"])
    for operation in sorted(FmClientDriver.METHOD_NAMES):
        related = [c for c in cases if c["operation"] == operation]
        method_sheet.append([operation,"Fm业务操作",sum(c["kind"]=="NORMAL" for c in related),
                             sum(c["kind"]=="BUSINESS_EXCEPTION" for c in related),"已注册",
                             "当前产品不使用，不生成运行用例"
                             if operation in PRODUCT_UNUSED_OPERATIONS else
                             '依赖流程覆盖：' + ','.join(fid for fid, steps in executable_flows.items()
                                 if any(step[1] == operation for step in steps))])
    for operation in sorted(WebApiServiceDriver.RESULT_OPERATIONS | WebApiServiceDriver.TRIGGER_OPERATIONS):
        related = [c for c in cases if c["operation"] == operation and c["actor"] == "http_admin"]
        method_sheet.append([operation,"HTTP辅助",sum(c["kind"]=="NORMAL" for c in related),
                             sum(c["kind"]=="BUSINESS_EXCEPTION" for c in related),"已注册","不作为模拟端"])

    for sheet in book.worksheets:
        for col in sheet.columns:
            sheet.column_dimensions[col[0].column_letter].width = min(max(len(str(x.value or "")) for x in col)+2,45)
    output.parent.mkdir(parents=True,exist_ok=True); book.save(output)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,default=ROOT/"tests/e2e/data/fm_e2e_cases.xlsx")
    parser.add_argument("--protocol",type=Path,default=ROOT/"ConfProtocol.xml")
    args=parser.parse_args(); create_template(args.output,args.protocol); print(args.output)


if __name__ == "__main__": main()
