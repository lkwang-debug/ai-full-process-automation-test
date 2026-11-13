"""Business-operation protocol expectations; never used to construct packets."""
from __future__ import annotations

from typing import Dict, Iterable, Optional

from .models import ClosureType, DeliveryPolicy, EventExpectation, OperationExpectation


def _op(name: str, request: Optional[int], *, response: Optional[int] = None,
        notify: Optional[int] = None, delivery: DeliveryPolicy = DeliveryPolicy.NONE,
        closure: ClosureType = ClosureType.FM_TO_FM,
        fields: Iterable[str] = (), forbidden=(),
        notify_kind: str = "notify") -> OperationExpectation:
    source = "entrance" if request == 1000 else "front" if request and 30000 <= request < 40000 else "conf"
    return OperationExpectation(
        operation=name, closure_type=closure, request_cmdid=request,
        response=(EventExpectation(source=source, cmdid=response, kind="response") if response else None),
        notifies=((EventExpectation(source="conf", cmdid=notify, kind=notify_kind),) if notify else ()),
        delivery=delivery, forbidden_delivery=tuple(forbidden), key_fields=tuple(fields))


OPERATION_EXPECTATIONS: Dict[str, OperationExpectation] = {}


def _add(names, request, **kwargs):
    for name in names:
        OPERATION_EXPECTATIONS[name] = _op(name, request, **kwargs)

_add(['send_relay_message'], 6700, notify=6700, delivery=DeliveryPolicy.TARGET_ONLY)


# Entrance and Front.
_add(["entrance_get_config"], 1000, response=1100)
_add(["front_authenticate"], 30000, response=30001)
_add(["front_roomlist", "front_roomlist_classified", "list_rooms", "front_discover_node"], 30002, response=30003)
_add(["front_login", "front_login_by_invite_code", "front_login_guest"], 30004, response=30005)
_add(["front_logout"], 30008, response=30009)
_add(["front_login_by_meeting_no", "front_getroomid"], 30012, response=30013)
_add(["front_get_auth_type"], 30014, response=30015)

# Core Conf flows.
_add(["conf_login"], 6501, response=6601)
_add(["conf_logout"], 6505, notify=6605, delivery=DeliveryPolicy.OTHERS_ONLY,
     forbidden=("actor",))
_add(["conf_get_offline_users", "offline_contacts"], 6503, response=6603)
_add(["conf_get_dept_info"], 6528, response=6628)
# The aggregate contacts APIs use DeptInfo cached from 6601 and refresh only
# offline members through 6503/6603. They must never trigger the disabled 6528 handler.
_add(["conf_get_contacts", "contacts_snapshot"], 6503, response=6603)
_add(["conf_get_room_config"], 6534, response=6634)
# 6604 is the host-facing knock notification and 6617 tells the waiting
# client it is queued. 6504 is the host's fire-and-forget decision and does
# not itself produce a paired response/notify.
_add(["allow_knock"], 6504)
_add(["kick_user"], 6506, notify=6606, delivery=DeliveryPolicy.TARGET_ONLY)
_add(["close_room"], 6507, notify=6607, delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["report_av_info"], 6508, notify=6608, delivery=DeliveryPolicy.OTHERS_ONLY)

_add(["set_audio_state", "set_video_state", "mic_on", "mic_off", "mic_request",
      "camera_on", "camera_off", "set_user_audio", "set_user_video", "set_role",
      "start_aux_stream", "stop_aux_stream"],
     6509, notify=6609, delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["set_conf_state", "start_record", "pause_record", "stop_record", "lock_room", "unlock_room"],
     6510, notify=6610, delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["set_chat_check"], 6510, notify=6610,
     delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["apply_host", "release_host"], 6511, notify=6609, delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["chat"], 6512, notify=6612,
     delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["set_window_state", "set_layout"], 6513, notify=6613,
     delivery=DeliveryPolicy.OTHERS_ONLY, forbidden=("actor",))
_add(["mute_all"], 6514, notify=6614, delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["set_subtitle"], 6515, notify=6615, delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["send_file"], 6516, notify=6616, delivery=DeliveryPolicy.TARGET_ONLY,
     forbidden=("actor",))
_add(["send_sysmsg"], 6518, notify=6618, delivery=DeliveryPolicy.TARGET_ONLY)
_add(["set_room_data"], 6519, notify=6619, delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["get_av_mix_params"], 6520, response=6620)
_add(["set_av_mix_params"], 6521)
_add(["set_video_subtitle_params"], 6522, notify=6622, delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["create_group"], 6523, response=6623)
_add(["enter_group"], 6524, notify=6624, delivery=DeliveryPolicy.TARGET_ONLY)
_add(["end_groups"], 6525, response=6625)
_add(["bring_to_main"], 6526, notify=6626, delivery=DeliveryPolicy.TARGET_ONLY)
_add(["rename_meeting"], 6527, response=6627)
_add(["request_right", "release_right"], 6530, response=6630)
_add(["set_role_permissions"], 6532, notify=6632, delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["configure_polling"], 6533, notify=6633, delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["set_user_permissions"], 6535, notify=6635, delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["close_subtitle"], 6550, response=6650)
_add(["recall_msg"], 6568, response=6668, notify=6669,
     delivery=DeliveryPolicy.OTHERS_ONLY, forbidden=("actor",))

# Extended Conf 2.0 business capabilities added by the 2026-09-07 SDK.
_add(["set_multi_data_sharer"], 6538, response=6638, notify=6638,
     delivery=DeliveryPolicy.OTHERS_ONLY, forbidden=("actor",))
_add(["get_user_features"], 6544, response=6644)
_add(["query_feature_users"], 6545, response=6645)
_add(["unsubscribe_feature_users"], 6545)
_add(["set_user_state"], 6509, notify=6609,
     delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["reply_self_state_change"], 6546)
_add(["renegotiate_layout"], 6560, response=6660)
_add(["send_h5_vote"], 6561, notify=6661,
     delivery=DeliveryPolicy.OTHERS_ONLY, forbidden=("actor",))
_add(["modify_group_config"], 6565, response=6665)
_add(["change_group_state"], 6525, response=6625)
_add(["allow_waiting_users"], 6570, response=6670)
_add(["set_asr_subtitle"], 6571, response=6671)
_add(["set_user_custom_data"], 6572, notify=6672,
     delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["review_chat_message", "review_chat_message_and_wait"], 6529, notify=6629,
     delivery=DeliveryPolicy.ROLE_TARGETED)
_add(["set_interactive_language", "set_interactive_language_and_wait"], 6540,
     notify=6640, delivery=DeliveryPolicy.TARGET_ONLY)
_add(["request_remote_av"], 6542, notify=6642,
     delivery=DeliveryPolicy.TARGET_ONLY)
_add(["reply_remote_av", "respond_remote_av"], 6543, notify=6643,
     delivery=DeliveryPolicy.TARGET_ONLY)
_add(["request_face_recognition"], 6555, response=6655)
_add(["start_face_rollcall"], 6556, response=6656, notify=6657,
     delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["report_face_rollcall_result"], 6558, notify=6658,
     delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["cancel_face_rollcall"], 6559, notify=6659,
     delivery=DeliveryPolicy.ALL_PARTICIPANTS)

# Telephone/H323: immediate response followed by asynchronous state notification.
_add(["conf_invite_call", "invite_terminals"], 6741, response=6742, notify=6745,
     delivery=DeliveryPolicy.SENDER_ONLY)
_add(["conf_hangup"], 6743, response=6744)
_add(["conf_query_call_list"], 6746, response=6747)

# HTTP facade operations. HTTP response is asserted from the method return; optional Fm Notify
# details can be overridden by the Excel case.
_add(["invite_users", "invite_contacts"], None,
     closure=ClosureType.HTTP_TO_FM)
_add(["lock_layout"], None, closure=ClosureType.HTTP_TO_FM)
_add(["create_instant_meeting", "create_reserved_meeting", "cancel_reserved_meeting"], None,
     closure=ClosureType.HTTP_TO_FM_JOIN)

# Direct WebApiServiceDriver triggers. They are server operations; Fm clients remain
# the only XML receivers.
_add(["control_user_audio", "control_user_video", "set_user_manager", "set_user_presenter"],
     None, closure=ClosureType.HTTP_TO_FM, notify=6609,
     delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["set_layout_http"], None, closure=ClosureType.HTTP_TO_FM, notify=6613,
     delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["lock_meeting", "operate_record"], None, closure=ClosureType.HTTP_TO_FM,
     notify=6610, delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["mute_all_http"], None, closure=ClosureType.HTTP_TO_FM,
     notify=6614,
     delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["send_subtitles"], None, closure=ClosureType.HTTP_TO_FM, notify=6615,
     delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["remove_user", "kick_out_user", "kick_out_user_by_id"], None,
     closure=ClosureType.HTTP_TO_FM, notify=6606, delivery=DeliveryPolicy.TARGET_ONLY)
_add(["close_meeting"], None, closure=ClosureType.HTTP_TO_FM, notify=6607,
     delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["invite_terminals_http"], None, closure=ClosureType.HTTP_TO_FM, notify=6602,
     delivery=DeliveryPolicy.ALL_PARTICIPANTS)
_add(["send_room_message"], None, closure=ClosureType.HTTP_TO_FM, notify=6654,
     delivery=DeliveryPolicy.TARGET_ONLY)


def get_expectation(operation: str) -> OperationExpectation:
    from .http_driver import WebApiServiceDriver
    if operation in WebApiServiceDriver.RESULT_OPERATIONS:
        return _op(operation, None, closure=ClosureType.HTTP_RESULT)
    try:
        return OPERATION_EXPECTATIONS[operation]
    except KeyError as exc:
        raise ValueError(f"no operation expectation registered: {operation}") from exc
