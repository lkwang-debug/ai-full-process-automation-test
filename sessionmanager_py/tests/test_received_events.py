from collections import deque

import pytest

from fm_client import ReceivedEvent
from fm_client import conf as C
from fm_client import front as F
from fm_client.client import _PendingRequest
from fm_client.testing import EventCollector, FmTestClient
from fm_client.xmlcmd import XmlCommand


def test_received_event_keeps_original_constructor_compatible():
    event = ReceivedEvent(
        "conf", 6609, "USERSTATECHANGED", {"UserID": "1"}, b"<cmd/>", 1.0)

    assert event.source == "conf"
    assert event.cmdid == 6609
    assert event.event_id == 0
    assert event.kind == "unknown"
    assert event.seq is None
    assert event.request_cmdid is None
    assert event.decode_error is None


def test_response_and_notify_have_uniform_metadata(fm):
    response_mark = fm.events.mark()
    fm.front_get_auth_type()

    response = fm.events.wait_event(
        after=response_mark,
        source="front",
        cmdid=F.GETAUTHTYPE_REP,
        kind="response",
        match={"AuthType": "FSMeetingAuth"},
        timeout=3,
    )
    assert response is not None
    assert response.event_id > response_mark
    assert response.seq is not None
    assert response.request_cmdid == F.GETAUTHTYPE_REQ
    assert response.raw_xml.startswith(b"<cmd")
    assert response.raw_payload
    assert response.received_monotonic > 0

    fm.front_login("event-user", "pwd", room_id=100001, room_node_id="1")
    fm.conf_login(user_name="event-user")
    notify_mark = fm.events.mark()
    fm.mic_on()

    notify = fm.events.wait_event(
        after=notify_mark,
        source="conf",
        cmdid=C.USERSTATECHANGED,
        kind="notify",
        match={"UserID": fm.room_user_id_int, "Audio.State": C.AUDIO_SPEAKING},
        timeout=3,
    )
    assert notify is not None
    assert notify.event_id > response.event_id
    assert notify.seq is None
    assert notify.request_cmdid is None


def test_event_fields_are_a_snapshot_of_the_wire_response(fm):
    fm.front_authenticate("snapshot-user", "pwd")
    mark = fm.events.mark()
    response_fields = fm.front_roomlist()
    event = fm.events.wait_event(
        after=mark, source="front", cmdid=F.ROOMLIST_REP, kind="response", timeout=3)

    assert event is not None
    original_name = event.fields["RoomInfo"][0]["Name"]
    response_fields["RoomInfo"][0]["Name"] = "mutated-by-caller"
    assert event.fields["RoomInfo"][0]["Name"] == original_name


def test_decode_failure_is_exposed_as_received_event(fm):
    mark = fm.events.mark()
    fm._on_data("conf", 123, b"\xff")

    event = fm.events.wait_event(
        after=mark, source="conf", kind="decode_error", timeout=1)
    assert event is not None
    assert event.cmdid is None
    assert event.name == "DECODE_ERROR"
    assert event.fields == {}
    assert event.raw_xml == b""
    assert event.raw_payload == b"\xff"
    assert event.decode_error


def test_mismatched_response_seq_does_not_consume_newer_request(fm):
    session_key = 12345
    response_cmdid = F.GETAUTHTYPE_REP
    pending = _PendingRequest(seq=8, request_cmdid=F.GETAUTHTYPE_REQ)
    key = (session_key, response_cmdid)
    fm._pending[key] = deque([pending])

    late = XmlCommand(response_cmdid, {"AuthType": "late"}, seq=7)
    assert fm._take_pending_response(session_key, late) is None
    assert list(fm._pending[key]) == [pending]

    current = XmlCommand(response_cmdid, {"AuthType": "current"}, seq=8)
    assert fm._take_pending_response(session_key, current) is pending
    assert key not in fm._pending


def test_event_cursor_excludes_history_and_supports_negative_assertion(fm):
    fm.front_get_auth_type()
    mark = fm.events.mark()

    assert fm.events.wait_event(
        after=mark, cmdid=F.GETAUTHTYPE_REP, timeout=0.02) is None
    fm.events.assert_no_event(
        after=mark, cmdid=F.GETAUTHTYPE_REP, duration=0.02)

    with pytest.raises(AssertionError):
        fm.events.assert_no_event(
            cmdid=F.GETAUTHTYPE_REP, kind="response", duration=0.02)


def test_event_match_treats_single_repeated_xml_node_as_index_zero():
    fields = {
        "DataItem": {
            "ItemID": "0",
            "RoomData": {"ClassState": "started"},
        },
    }

    assert EventCollector._matches_fields(fields, {
        "DataItem.0.ItemID": 0,
        "DataItem.0.RoomData.ClassState": "started",
    })
    assert not EventCollector._matches_fields(
        fields, {"DataItem.1.ItemID": 0})


def test_conf_protocol_response_pairs_are_registered():
    expected = {
        C.GETAVMIXPARAMREQ: C.GETAVMIXPARAMREP,
        C.FACERECOGNITIONREQ: C.FACERECOGNITIONREP,
        C.FACEROLLCALLSTARTREQ: C.FACEROLLCALLSTARTREP,
    }
    assert {request: C.PAIRS.get(request) for request in expected} == expected


@pytest.mark.parametrize(
    ("cmdid", "fields", "queue_name"),
    [
        (C.ROOMDATANOTIFY,
         {"DataItem": [{"ItemID": 1, "Duration": 0, "RoomData": "class"}]},
         "room_data_updates"),
        (C.VIDEOSUBTITLENOTIFY,
         {"DateTime": {"Pos": 1, "Size": 2, "Color": 0}},
         "video_subtitles"),
        (C.CHATCHECKRESULT,
         {"SerialNumber": "sn-1", "CheckResult": 1, "CheckUserID": 1001},
         "chat_check_results"),
        (C.ROLEPERMISSIONCHANGED,
         {"RoleCode": "mentee", "OperatePublicChat": 0},
         "role_permission_changes"),
        (C.USERPERMISSIONCHANGED,
         {"UserID": 1002, "OperatePrivateChat": 1},
         "user_permission_changes"),
        (C.SYSTEMNOTIFY,
         {"ServerRecord": {"Error": 1}},
         "system_notifies"),
        (C.MULTICASTNOTIFY,
         {"Multicast": 1},
         "multicast_states"),
        (C.FACEROLLCALLSTARTNOTIFY,
         {"RollcallID": 7, "RollcallTitle": "daily"},
         "face_rollcall_starts"),
        (C.FACEROLLCALLRESULTNOTIFY,
         {"RollcallID": 7, "FaceID": 1002, "UserName": "member"},
         "face_rollcall_results"),
        (C.FACEROLLCALLCANCELNOTIFY,
         {"RollcallID": 7},
         "face_rollcall_cancels"),
        (C.RELAYMESSAGE,
         {"SrcUserID": 1001, "DstUserID": 1002,
          "msg": {"id": "6701", "EnableControl": 1}},
         "relay_messages"),
    ],
)
def test_new_conf_notify_types_are_uniform_events(fm, cmdid, fields, queue_name):
    mark = fm.events.mark()
    payload = fm._packer.pack(XmlCommand(cmdid, fields).to_bytes())

    fm._on_data("conf", 12345, payload)

    event = fm.events.wait_event(
        after=mark, source="conf", cmdid=cmdid, kind="notify", timeout=1)
    assert event is not None
    assert event.name != f"CMD_{cmdid}"
    assert fm.events.snapshot(getattr(fm.events, queue_name))


def test_current_http_remove_uses_standard_6606_kick_event(fm):
    """HTTP USER_REMOVE 按当前产品约定走 6606，6652 仅保留兼容。"""
    mark = fm.events.mark()
    fields = {
        "KickID": -2,
        "UserID": 1002,
        "KickType": 0,
        "Tips": "BOSS Admin Kick User",
    }
    payload = fm._packer.pack(XmlCommand(C.USERKICKED, fields).to_bytes())

    fm._on_data("conf", 12345, payload)

    event = fm.events.wait_event(
        after=mark,
        source="conf",
        cmdid=C.USERKICKED,
        kind="notify",
        match={"KickID": -2, "UserID": 1002},
        timeout=1,
    )
    assert event is not None
    kicked = fm.events.wait_for_kicked(user_id=1002, timeout=1)
    assert kicked and str(kicked[-1]["kick_id"]) == "-2"
    assert fm.events.snapshot(fm.events.web_kicks) == []


def test_dual_use_responses_are_notifies_for_observers(fm_mock_server):
    a = FmTestClient("127.0.0.1", fm_mock_server.port, request_timeout=3.0)
    b = FmTestClient("127.0.0.1", fm_mock_server.port, request_timeout=3.0)
    try:
        a.front_login("observer-a", "pwd", room_id=100001, room_node_id="1")
        a.conf_login(user_name="observer-a")
        b.front_login("observer-b", "pwd", room_id=100001, room_node_id="1")
        b.conf_login(user_name="observer-b")
        a.events.clear()
        b.events.clear()

        checks = [
            (
                C.SYSMSG,
                lambda: a.send_sysmsg(
                    sys_msg_id=6, dst_user_id=b.room_user_id_int),
                "system_messages",
                False,
            ),
            (
                C.ENTERGROUPREP,
                lambda: a.enter_group(
                    70001, [C.ConfCmd.group_user(id=b.room_user_id_int)]),
                "group_enters",
                False,
            ),
            (
                C.ENTERMAINREP,
                lambda: a.bring_to_main([b.room_user_id_int]),
                "main_enters",
                False,
            ),
            (
                C.CLOSESUBTITLEREP,
                a.close_subtitle,
                "subtitle_closes",
                True,
            ),
        ]
        for cmdid, action, queue_name, has_response in checks:
            mark = b.events.mark()
            response = action()
            assert isinstance(response, dict) if has_response else response is None
            event = b.events.wait_event(
                after=mark, source="conf", cmdid=cmdid,
                kind="notify", timeout=2)
            assert event is not None
            assert b.events.snapshot(getattr(b.events, queue_name))

        role_mark = b.events.mark()
        a.set_role_permissions(
            ["mentee", "audit"],
            operate_public_chat=False,
            operate_private_chat=True,
            permissions={"ConfigChatCheck": 1},
        )
        for role_code in ("mentee", "audit"):
            event = b.events.wait_event(
                after=role_mark,
                source="conf",
                cmdid=C.ROLEPERMISSIONCHANGED,
                kind="notify",
                match={
                    "RoleCode": role_code,
                    "OperatePublicChat": 0,
                    "OperatePrivateChat": 1,
                    "ConfigChatCheck": 1,
                },
                timeout=2,
            )
            assert event is not None
        assert b.events.wait_until(
            lambda: len(b.events.snapshot(
                b.events.role_permission_changes)) >= 2,
            timeout=2,
        )

        user_mark = b.events.mark()
        a.set_user_permissions(
            b.room_user_id_int,
            operate_public_chat=True,
            operate_private_chat=False,
        )
        event = b.events.wait_event(
            after=user_mark,
            source="conf",
            cmdid=C.USERPERMISSIONCHANGED,
            kind="notify",
            match={
                "UserID": b.room_user_id_int,
                "OperatePublicChat": 1,
                "OperatePrivateChat": 0,
            },
            timeout=2,
        )
        assert event is not None
        changes = b.events.snapshot(b.events.user_permission_changes)
        assert changes and str(changes[-1]["user_id"]) == str(b.room_user_id_int)
    finally:
        b.close()
        a.close()


def test_meeting_data_av_mix_video_subtitle_and_record_error(fm_mock_server):
    a = FmTestClient("127.0.0.1", fm_mock_server.port, request_timeout=3.0)
    b = FmTestClient("127.0.0.1", fm_mock_server.port, request_timeout=3.0)
    try:
        a.front_login("extended-a", "pwd", room_id=100001, room_node_id="1")
        a.conf_login(user_name="extended-a")
        b.front_login("extended-b", "pwd", room_id=100001, room_node_id="1")
        b.conf_login(user_name="extended-b")
        a.events.clear()
        b.events.clear()

        room_mark = b.events.mark()
        a.set_room_data([
            C.ConfCmd.room_data_item(0, {"ClassState": "started"}),
            C.ConfCmd.room_data_item(1, {"Course": "math"}),
        ])
        room_event = b.events.wait_event(
            after=room_mark,
            source="conf",
            cmdid=C.ROOMDATANOTIFY,
            kind="notify",
            match={
                "DataItem.0.ItemID": 0,
                "DataItem.0.RoomData.ClassState": "started",
                "DataItem.1.ItemID": 1,
            },
            timeout=2,
        )
        assert room_event is not None
        room_updates = b.events.snapshot(b.events.room_data_updates)
        assert room_updates and len(room_updates[-1]["items"]) == 2

        style = C.ConfCmd.video_subtitle_style(
            pos=1, size=2, color=0xFFFFFF)
        subtitle_mark = b.events.mark()
        a.set_video_subtitle_params(date_time=style, user_name=style)
        subtitle_event = b.events.wait_event(
            after=subtitle_mark,
            source="conf",
            cmdid=C.VIDEOSUBTITLENOTIFY,
            kind="notify",
            match={"DateTime.Pos": 1, "UserName.Color": 0xFFFFFF},
            timeout=2,
        )
        assert subtitle_event is not None
        assert b.events.snapshot(b.events.video_subtitles)

        audio = C.ConfCmd.av_mix_audio(fec_type=2, encoder_id=7)
        video = C.ConfCmd.av_mix_video(
            width=1280, height=720, frame_rate=25, encoder_id=3,
            encoder_mode=1, bitrate=2048, vbr_quality=60,
            key_frame_interval=50,
        )
        a.set_av_mix_params(
            enable_default=False, audio=audio, video=video)
        params = a.get_av_mix_params()
        assert params["Audio"] == {"FecType": "2", "EncoderID": "7"}
        assert params["Video"]["Width"] == "1280"
        assert params["Video"]["KeyFrameInterval"] == "50"

        error_mark = b.events.mark()
        payload = b._packer.pack(XmlCommand(
            C.SYSTEMNOTIFY, {"ServerRecord": {"Error": 3}}).to_bytes())
        b._on_data("conf", 12345, payload)
        error_event = b.events.wait_event(
            after=error_mark,
            source="conf",
            cmdid=C.SYSTEMNOTIFY,
            kind="notify",
            match={"ServerRecord.Error": 3},
            timeout=1,
        )
        assert error_event is not None
        errors = b.wait_for_server_record_error(error=3, timeout=1)
        assert errors and errors[-1]["error"] == 3
    finally:
        b.close()
        a.close()
