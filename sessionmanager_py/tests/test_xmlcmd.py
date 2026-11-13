import pytest

from fm_client import conf as C
from fm_client.xmlcmd import XmlCommand


def test_xml_command_rejects_wrong_root_and_missing_id():
    with pytest.raises(ValueError):
        XmlCommand.from_bytes(b"<message id='1'/>")
    with pytest.raises(ValueError):
        XmlCommand.from_bytes(b"<cmd/>")


def test_xml_command_accepts_trailing_c_string_nul():
    command = XmlCommand.from_bytes(
        b'<cmd id="6634" ver="4.0"><InviteCode val="100100100"/></cmd>\x00')

    assert command.cmdid == C.GETROOMCONFIGREP
    assert command.fields["InviteCode"] == "100100100"


def test_room_config_request_contains_invite_code_selector():
    command = C.ConfCmd.build_getroomconfigreq()

    assert command.fields == {"InviteCode": {}}
    assert b"<InviteCode" in command.to_bytes()


def test_chat_font_uses_xml_attributes_and_round_trips():
    command = C.ConfCmd.build_chat(
        src_user_id=1001, dst_user_id=0, text='字体<&>', color=0x112233,
        font={"Name": "宋体<&>", "Width": 0, "Height": 16,
              "Weight": 400, "Underline": 0, "StrikeOut": 0,
              "CharSet": 134, "Italic": 1})

    payload = command.to_bytes()
    parsed = XmlCommand.from_bytes(payload)

    assert b'<Font Name=' in payload
    assert b'<Name ' not in payload
    assert parsed.fields["Font"] == {
        "Name": "宋体<&>", "Width": "0", "Height": "16",
        "Weight": "400", "Underline": "0", "StrikeOut": "0",
        "CharSet": "134", "Italic": "1"}


@pytest.mark.parametrize("font", [{}, {"Unknown": 1}, {"Name": None}])
def test_chat_font_rejects_empty_or_unknown_attributes(font):
    with pytest.raises(ValueError, match="font|attributes"):
        C.ConfCmd.build_chat(src_user_id=1001, text="bad", font=font)


def test_window_state_fullscreen_fields_preserve_zero_values():
    command = C.ConfCmd.build_wndstate(
        mode=3, full=0, video_relay_id=4,
        full_video_id=0, full_video_media_id=0, valid_full_mode=0)
    parsed = XmlCommand.from_bytes(command.to_bytes())

    assert parsed.fields == {
        "Mode": "3", "Full": "0", "VideoRelayID": "4",
        "FullVideoID": "0", "FullVideoMediaID": "0",
        "ValidFullMode": "0"}
