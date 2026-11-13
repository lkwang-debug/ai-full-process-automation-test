"""会议控制补齐接口测试（方案 §4 的 13 个接口：真实命令 + 不在信令的 stub）。"""
from __future__ import annotations

import pytest

from fm_client import conf as C
from fm_client.errcodes import ProtocolNotInSignaling


def _join(fm):
    fm.front_login("a", "p", room_id=100001, room_node_id="1")
    fm.conf_login(user_name="a")
    fm.events.clear()


def test_set_user_audio_video(fm):
    _join(fm)
    uid = fm.room_user_id_int
    fm.set_user_video(uid, C.VIDEO_BROADCAST)
    st = fm.wait_for_user_state(user_id=uid, timeout=3)
    assert st and st[-1].get("Video", {}).get("State") == str(C.VIDEO_BROADCAST)
    fm.events.clear()
    fm.set_user_audio(uid, C.AUDIO_SPEAKING, dev_id=0)
    st2 = fm.wait_for_user_state(user_id=uid, timeout=3)
    assert st2 and st2[-1].get("Audio", {}).get("State") == str(C.AUDIO_SPEAKING)


def test_set_layout(fm):
    _join(fm)
    fm.set_layout(layout_type=1001, user_ids=[1001, 1002], target_user_ids=[2002])
    assert fm.events.wait_until(lambda: fm.events.snapshot(fm.events.window_states), timeout=3)
    w = fm.events.snapshot(fm.events.window_states)[-1]
    assert w.get("VideoRelayID") == "1001"
    assert isinstance(w.get("Video"), list) and len(w["Video"]) == 2


def test_set_layout_fullscreen_fields(fm):
    _join(fm)
    fm.set_layout(
        layout_type=4, user_ids=[1001, 1002], mode=3, full=1,
        full_video_id=1002, full_video_media_id=0, valid_full_mode=1)
    assert fm.events.wait_until(
        lambda: fm.events.snapshot(fm.events.window_states), timeout=3)
    fields = fm.events.snapshot(fm.events.window_states)[-1]
    assert fields["Full"] == "1"
    assert fields["FullVideoID"] == "1002"
    assert fields["FullVideoMediaID"] == "0"
    assert fields["ValidFullMode"] == "1"


def test_set_role_presenter_and_admin(fm):
    _join(fm)
    fm.set_role(2002, C.ROLE_PRESENTER, enabled=True)
    st = fm.wait_for_user_state(user_id=2002, timeout=3)
    assert st and st[-1].get("DataState") == str(C.DATASTATE_PRESENTER)
    fm.events.clear()
    fm.set_role(2002, C.ROLE_ADMIN, enabled=True)
    st2 = fm.wait_for_user_state(user_id=2002, timeout=3)
    assert st2 and st2[-1].get("ManagerState") == str(C.MANAGERSTATE_ADMIN)


def test_set_role_chairman_self_vs_other(fm):
    _join(fm)
    fm.set_role(
        fm.room_user_id, C.ROLE_CHAIRMAN, enabled=True, chair_pwd="1111")
    assert fm.wait_for_user_state(timeout=3)
    with pytest.raises(ProtocolNotInSignaling):                   # 指定他人为主持人：信令无
        fm.set_role(2002, C.ROLE_CHAIRMAN, enabled=True)


def test_permission_builders_validate_and_encode_common_fields():
    role_cmd = C.ConfCmd.build_change_role_permission(
        ["mentee", "audit"],
        {"OperatePublicChat": False, "OperatePrivateChat": True},
    )
    role_roundtrip = type(role_cmd).from_bytes(role_cmd.to_bytes())
    assert role_roundtrip.cmdid == C.CHANGEROLEPERMISSION
    assert role_roundtrip.fields["RoleCode"] == ["mentee", "audit"]
    assert role_roundtrip.fields["OperatePublicChat"] == "0"
    assert role_roundtrip.fields["OperatePrivateChat"] == "1"

    user_cmd = C.ConfCmd.build_change_user_permission(
        user_id=2002, permissions={"ConfigChatCheck": 1})
    user_roundtrip = type(user_cmd).from_bytes(user_cmd.to_bytes())
    assert user_roundtrip.cmdid == C.CHANGEUSERPERMISSION
    assert user_roundtrip.fields == {"UserID": "2002", "ConfigChatCheck": "1"}

    with pytest.raises(ValueError):
        C.ConfCmd.build_change_role_permission([], {"OperatePublicChat": 1})
    with pytest.raises(ValueError):
        C.ConfCmd.build_change_user_permission(user_id=2002, permissions={})


def test_meeting_data_av_mix_and_video_subtitle_builders():
    room_cmd = C.ConfCmd.build_set_room_data([
        C.ConfCmd.room_data_item(0, {"ClassState": "started"}),
    ])
    room_roundtrip = type(room_cmd).from_bytes(room_cmd.to_bytes())
    assert room_roundtrip.cmdid == C.SETROOMDATA
    assert room_roundtrip.fields["DataItem"]["RoomData"] == {
        "ClassState": "started"}

    audio = C.ConfCmd.av_mix_audio(fec_type=1, encoder_id=6)
    video = C.ConfCmd.av_mix_video(
        width=1280, height=720, frame_rate=25, encoder_id=3,
        encoder_mode=1, bitrate=2048, vbr_quality=60,
        key_frame_interval=50,
    )
    av_cmd = C.ConfCmd.build_set_av_mix_params(
        enable_default=False, audio=audio, video=video)
    av_roundtrip = type(av_cmd).from_bytes(av_cmd.to_bytes())
    assert av_roundtrip.cmdid == C.SETAVMIXPARAM
    assert av_roundtrip.fields["EnableDefault"] == "0"
    assert av_roundtrip.fields["Video"]["Width"] == "1280"

    style = C.ConfCmd.video_subtitle_style(pos=1, size=2, color=0xFFFFFF)
    subtitle_cmd = C.ConfCmd.build_set_video_subtitle(
        date_time=style, user_name=style)
    subtitle_roundtrip = type(subtitle_cmd).from_bytes(subtitle_cmd.to_bytes())
    assert subtitle_roundtrip.cmdid == C.SETVIDEOSUBTITLE
    assert subtitle_roundtrip.fields["DateTime"]["Pos"] == "1"

    with pytest.raises(ValueError):
        C.ConfCmd.build_set_room_data([])
    with pytest.raises(ValueError):
        C.ConfCmd.build_set_av_mix_params(enable_default=False)
    with pytest.raises(ValueError):
        C.ConfCmd.build_set_video_subtitle()
    with pytest.raises(ValueError, match="sys_msg_id"):
        C.ConfCmd.build_sysmsg(sys_msg_id=0)
    with pytest.raises(ValueError, match="chair_pwd"):
        C.ConfCmd.build_req_chairright(chair_right=C.CHAIR_APPLY)

    sysmsg = C.ConfCmd.build_sysmsg(sys_msg_id=6, dst_user_id=2002)
    assert sysmsg.fields == {"DstUserID": 2002, "SysMsgID": 6}


def test_aux_stream_media(fm):
    _join(fm)
    uid = fm.room_user_id_int
    fm.start_aux_stream(kind="media", audio_share_id=11, video_share_id=12)
    st = fm.wait_for_user_state(user_id=uid, timeout=3)
    assert st and st[-1].get("MediaShareState") == str(C.MEDIASHARE_START)
    assert st[-1].get("AudioShareID") == "11"
    assert st[-1].get("VideoShareID") == "12"
    fm.events.clear()
    fm.stop_aux_stream(kind="media")
    st2 = fm.wait_for_user_state(user_id=uid, timeout=3)
    assert st2 and st2[-1].get("MediaShareState") == str(C.MEDIASHARE_STOP)
    assert st2[-1].get("AudioShareID") == "0"
    assert st2[-1].get("VideoShareID") == "0"


def test_aux_stream_media_requires_share_ids(fm):
    _join(fm)
    with pytest.raises(ValueError, match="audio_share_id and video_share_id"):
        fm.start_aux_stream(kind="media")


def test_aux_stream_vnc(fm):
    _join(fm)
    uid = fm.room_user_id_int
    fm.start_aux_stream(kind="vnc")
    st = fm.wait_for_user_state(user_id=uid, timeout=3)
    assert st and st[-1].get("VNCState") == str(C.VNC_ON)
    assert C.VNC_ON == 2


def test_configure_polling(fm):
    _join(fm)
    fm.configure_polling(C.POLLING_OP_ADD, 7, "poll-A", [1001, 1002],
                         interval=5, screen=1, window=1, status=C.POLLING_STATUS_RUNNING)
    polls = fm.events.wait_for_polling(timeout=3)
    assert polls
    vp = polls[-1].get("VideoPolling")
    assert vp.get("val") == "7" and vp.get("CurrentUserID") == "1001"


@pytest.mark.parametrize("operation", [0, 4, -1, 99])
def test_configure_polling_rejects_unsupported_operation_before_send(fm, operation):
    _join(fm)
    sent = fm.mark_sent_commands()
    with pytest.raises(ValueError, match="polling operation"):
        fm.configure_polling(operation, 7, "invalid", [1001])
    assert fm.snapshot_sent_commands(after=sent) == []


def test_invite_terminals(fm):
    _join(fm)
    rep = fm.invite_terminals(["111", "222"])
    assert rep["CallType"] == str(C.CALLTYPE_H323)
    assert len(rep["ReqCallInfo"]) == 2


def test_not_in_signaling_stubs(fm):
    """信令里不存在的接口必须明确报错（避免误判发送成功）。"""
    _join(fm)
    cases = [
        ("invite_users", ([1001],)),
        ("invite_contacts", ([1001],)),
        ("create_reserved_meeting", ()),
        ("create_instant_meeting", ()),
        ("cancel_reserved_meeting", ()),
        ("lock_layout", ()),
    ]
    for name, args in cases:
        with pytest.raises(ProtocolNotInSignaling) as exc:
            getattr(fm, name)(*args)
        assert exc.value.channel          # 报错信息里带真实通道
