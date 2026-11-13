"""Socket-level SDK contracts; scripted replies are not live service evidence."""
import copy
import threading

import pytest

from examples import fm_interaction_protocol_test as demo
from fm_client import conf as C
from fm_client.errcodes import FmError
from fm_client import NotificationTimeout
from fm_client.testing import connect_pair
from fm_client.xmlcmd import XmlCommand


@pytest.fixture
def scripted(fm_mock_server, monkeypatch):
    server = fm_mock_server
    original = server._conf
    state = {"face_result": 0, "silent": set(), "bad_result": False, "chat_check": False,
             "confirm_rejection": False, "confirmation_overrides": {},
             "deliver_rejected": False}
    def handle(cmd, send, ctx):
        if cmd.cmdid in state["silent"]:
            return
        def reply(cid, fields):
            send(XmlCommand(cid, fields, seq=cmd.seq))
        def target(uid, cid, fields):
            server._send_to_users([uid], XmlCommand(cid, fields))
        if cmd.cmdid == 6510:
            if cmd.get("EnableChatMsgCheck") is not None:
                state["chat_check"] = cmd.get("EnableChatMsgCheck") == "1"
            return original(cmd, send, ctx)
        if cmd.cmdid == 6512 and state["chat_check"]:
            fields = {**cmd.fields, "SerialNumber": 51, "NeedCheck": 1,
                      "Font": {"@Name": "宋体<&>", "@Height": 16}}
            target("1001", 6612, fields)
        elif cmd.cmdid == 6508:
            server._broadcast(XmlCommand(6608, {**cmd.fields, "UserID": ctx["uid"]}))
        elif cmd.cmdid == 6529:
            if cmd.get("CheckResult") == "1" or state["confirm_rejection"]:
                reply(6629, {"SerialNumber": cmd.get("SerialNumber"),
                             "CheckResult": cmd.get("CheckResult"),
                             "CheckUserID": ctx["uid"], **state["confirmation_overrides"]})
            if cmd.get("CheckResult") == "1" or state["deliver_rejected"]:
                target(cmd.get("SrcUserID"), 6612, {**cmd.fields, "NeedCheck": 0})
        elif cmd.cmdid == 6540:
            server._broadcast(XmlCommand(6640, {**cmd.fields, "SrcUserID": ctx["uid"],
                                               **state["confirmation_overrides"]}))
        elif cmd.cmdid == 6542:
            target(cmd.get("DstUserID"), 6642, cmd.fields)
        elif cmd.cmdid == 6543:
            fields = dict(cmd.fields)
            if state["bad_result"]:
                fields["DstUserID"] = "9999"
            target(cmd.get("DstUserID"), 6643, fields)
            if cmd.get("Result") == "0" and cmd.get("Type") == "0":
                for key in ("Audio", "Video"):
                    if key in cmd.fields:
                        items = cmd.get(key)
                        for item in items if isinstance(items,list) else [items]:
                            server._broadcast(XmlCommand(6609, {"UserID": ctx["uid"],
                                key: {"ID": item["ID"], "State": 2}}))
        elif cmd.cmdid == 6555:
            reply(6655, {**cmd.fields, "Result": state["face_result"], "ServerDevID": "mock-face",
                "ServerAddr": "TCP:127.0.0.1:1234;", "AppID": 33, "CheckCode": "test-only"})
        elif cmd.cmdid == 6556:
            reply(6656, {"Result": state["face_result"], "RollcallID": 73})
            if state["face_result"] == 0:
                server._broadcast(XmlCommand(6657, {**cmd.fields, "RollcallID": 73}))
        elif cmd.cmdid == 6558:
            assert cmd.get("FaceID") == "42"
            server._broadcast(XmlCommand(6658, cmd.fields))
        elif cmd.cmdid == 6559:
            server._broadcast(XmlCommand(6659, cmd.fields))
        else:
            original(cmd, send, ctx)
            if cmd.cmdid == 6517:
                send(XmlCommand(6641, {"Language": {"SrcCode": "zh-CN", "DstCode": "en-US"}}))
                send(XmlCommand(6641, {"End": {}}))
    # Both mock client threads may broadcast during initialization. Serialize
    # scripted handlers so its socket sequence allocator preserves wire order.
    dispatch_lock = threading.RLock()
    def serialized_handle(cmd, send, ctx):
        with dispatch_lock:
            return handle(cmd, send, ctx)
    monkeypatch.setattr(server, "_conf", serialized_handle)
    cfg = {"host": server.host, "port": server.port, "room_id": 10000, "room_node_id": "1",
           "request_timeout": 0.5, "initialization_wait": 0, "conf_protocol_version": "5.0",
           "accounts": [{"username": "a", "password": "p"}, {"username": "b", "password": "p"}]}
    return server, cfg, state


@pytest.mark.parametrize("case", list(demo.CASES))
def test_interaction_demo_socket_contract(scripted, case):
    server, cfg, state = scripted
    state["confirm_rejection"] = True
    result = demo.run_case(case, cfg, 1, {"language": "zh-CN", "face_id": 42, "video_id": 0})
    assert result
    assert all(cmd.ver == "5.0" for app, cmd in server.received_commands if app == 11)


def test_chat_rejection_does_not_claim_completion(scripted):
    server, cfg, state = scripted
    with pytest.raises(NotificationTimeout, match="no matching notify 6629") as error:
        demo.run_case("chat_reject", cfg, 0.1, {})
    assert error.value.request_cmdid == 6529
    assert error.value.expected["CheckResult"] == 0
    assert error.value.session_open
    assert any(cmd.cmdid == 6529 and cmd.get("CheckResult") == "0" for _, cmd in server.received_commands)


@pytest.mark.parametrize("cmdid,case", [(6642, "remote_audio_accept"), (6643, "remote_video_reject"),
                                       (6640, "language")])
def test_missing_notifications_never_pass(scripted, monkeypatch, cmdid, case):
    server, cfg, state = scripted
    original = server._send_to_users
    if cmdid == 6640:
        state["silent"].add(6540)
    else:
        def drop(ids, cmd, **kwargs):
            if cmd.cmdid != cmdid:
                original(ids, cmd, **kwargs)
        monkeypatch.setattr(server, "_send_to_users", drop)
    error = NotificationTimeout if cmdid == 6640 else AssertionError
    with pytest.raises(error, match=str(cmdid)):
        demo.run_case(case, cfg, 0.1, {"language": "zh-CN"})


def test_wrong_result_identity_never_passes(scripted):
    server, cfg, state = scripted
    state["bad_result"] = True
    with pytest.raises(AssertionError, match="6643"):
        demo.run_case("remote_audio_reject", cfg, 0.1, {})


@pytest.mark.parametrize("method,args", [("request_face_recognition", (1002, 0)),
                                       ("start_face_rollcall", ("test",))])
def test_face_error_response_raises_and_is_recorded(scripted, method, args):
    server, cfg, state = scripted
    state["face_result"] = 8485
    a, b = connect_pair(cfg)
    try:
        mark = a.events.mark()
        with pytest.raises(FmError):
            getattr(a, method)(*args, timeout=1)
        response = a.events.wait_event(after=mark, kind="response", timeout=1)
        assert response.fields["Result"] == "8485"
    finally:
        a.close()
        b.close()


def test_callbacks_retain_fields_and_never_auto_accept(scripted):
    server, cfg, state = scripted
    a, b = connect_pair(cfg)
    try:
        a.request_remote_av(b.room_user_id, audio_ids=[0], video_ids=[0, 2])
        request = demo.received(b, 0, 6642, 1, {"Audio.ID": 0})
        assert b.events.wait_until(lambda: len(b.events.remote_av_requests) == 1, 1)
        assert b.events.remote_av_requests[0] == request
        assert not any(cmd.cmdid == 6543 for _, cmd in server.received_commands)
        with pytest.raises(ValueError, match="target"):
            a.respond_remote_av(request, accept=True)
        b.respond_remote_av(request, accept=False)
        result = demo.received(a, 0, 6643, 1, {"Result": 1})
        assert a.events.wait_until(lambda: bool(a.events.remote_av_results), 1)
        assert a.events.remote_av_results[0] == result
        assert result["Video"] == [{"ID": "0"}, {"ID": "2"}]
        a.set_interactive_language("zh-CN", user_id=b.room_user_id)
        demo.received(b, 0, 6640, 1)
        assert b.events.wait_until(lambda: bool(b.events.interactive_languages), 1)
        assert b.events.wait_until(lambda: len(b.events.room_languages) == 2, 1)
    finally:
        a.close()
        b.close()


def test_chat_review_preserves_font_as_attributes_without_mutating_message():
    msg = {"SerialNumber": "7", "SrcUserID": "8", "DstUserID": "0", "Text": '中<&>"',
           "NeedCheck": "1", "Font": {"Name": "宋体", "Height": "16", "Italic": "1"},
           "SrcUserType": "1", "SrcLiveUserId": "other"}
    before = copy.deepcopy(msg)
    cmd = C.ConfCmd.build_chat_check(msg, approved=True)
    xml = cmd.to_bytes()
    parsed = XmlCommand.from_bytes(xml)
    assert parsed.cmdid == 6529 and parsed.get("CheckResult") == "1"
    assert parsed.get("Text") == msg["Text"] and parsed.get("Font") == msg["Font"]
    assert b'<Font Name=' in xml and b'<Name ' not in xml
    assert "NeedCheck" not in parsed.fields and msg == before


@pytest.mark.parametrize("builder,args,kwargs", [
    (C.ConfCmd.build_chat_check, ({},), {"approved": True}),
    (C.ConfCmd.build_interactive_language, (1, ""), {}),
    (C.ConfCmd.build_remote_av_request, (1,2), {}),
    (C.ConfCmd.build_remote_av_request, (1,2), {"audio_ids": [1]}),
    (C.ConfCmd.build_remote_av_request, (1,2), {"audio_ids": [0], "operation": 2}),
    (C.ConfCmd.build_remote_av_request, (1,1), {"video_ids": [0]}),
    (C.ConfCmd.build_remote_av_reply, (1,2), {"audio_ids": [0], "result": None}),
    (C.ConfCmd.build_face_rollcall_result, (1,"张三"), {}),
    (C.ConfCmd.build_face_rollcall_start, ("",30), {}),
    (C.ConfCmd.build_face_rollcall_start, ("a"*256,30), {}),
    (C.ConfCmd.build_face_rollcall_start, ("t",0), {}),
    (C.ConfCmd.build_face_rollcall_cancel, (False,), {}),
    (C.ConfCmd.build_face_recognition, (1,-1), {}),
])
def test_invalid_parameters_fail_locally(builder, args, kwargs):
    with pytest.raises(ValueError):
        builder(*args, **kwargs)


def test_only_direct_face_replies_are_pairs():
    assert C.PAIRS[6555] == 6655 and C.PAIRS[6556] == 6656
    for cmdid in (6529, 6540, 6542, 6543, 6558, 6559):
        assert cmdid not in C.PAIRS
    assert {6640,6641,6642,6643}.issubset(C.PUSHES)


def test_preflight_failure_stops_before_creating_rooms(scripted, monkeypatch, capsys):
    _, cfg, _ = scripted
    monkeypatch.setattr(demo, "load_config", lambda _: cfg)
    class FakeApi:
        base_url = "http://" + cfg["host"]
        def create_instant_meeting(self, **kwargs):
            pytest.fail("must not create a meeting after authentication failure")
    monkeypatch.setattr(demo.WebApiClient, "from_config", lambda _: FakeApi())
    def fail_login(*args):
        raise demo.EnvironmentRequired("0x1801 UNAUTHORIZED")
    monkeypatch.setattr(demo, "preflight", fail_login)
    assert demo.main(["--config", "unused", "--http-config", "unused",
                      "--case", "remote_audio_accept"]) == 2
    assert "no temporary meeting created" in capsys.readouterr().out


@pytest.mark.parametrize("action_fails,cleanup_fails", [(False, False), (True, False), (False, True)])
def test_cli_always_cleans_up_and_does_not_pass_cleanup_failure(
        scripted, monkeypatch, capsys, action_fails, cleanup_fails):
    _, cfg, _ = scripted
    monkeypatch.setattr(demo, "load_config", lambda _: cfg)
    monkeypatch.setattr(demo, "preflight", lambda *_: None)
    closed = []
    class FakeApi:
        base_url = "http://" + cfg["host"]
        def create_instant_meeting(self, **kwargs):
            return {"code": 0, "data": {"roomId": 123, "nodeId": "1"}}
        def close_meeting(self, room_id):
            closed.append(room_id)
            if cleanup_fails:
                raise RuntimeError("cleanup failed")
            return {"code": 0}
    monkeypatch.setattr(demo.WebApiClient, "from_config", lambda _: FakeApi())
    def action(*args):
        if action_fails:
            raise AssertionError("missing notification")
        return {"verified": True}
    monkeypatch.setattr(demo, "run_case", action)
    expected = 1 if action_fails or cleanup_fails else 0
    assert demo.main(["--config", "unused", "--http-config", "unused",
                      "--case", "remote_audio_accept"]) == expected
    assert closed == [123]
    output = capsys.readouterr().out
    assert ("[PASS] remote_audio_accept" in output) == (expected == 0)


def test_rejection_ack_does_not_hide_message_delivery(scripted):
    _, cfg, state = scripted
    state["confirm_rejection"] = True
    state["deliver_rejected"] = True
    with pytest.raises(AssertionError, match="unexpected received event"):
        demo.run_case("chat_reject", cfg, 0.1, {})


@pytest.mark.parametrize("case,overrides", [
    ("chat_approve", {"SerialNumber": 999}),
    ("chat_approve", {"CheckResult": 0}),
    ("chat_approve", {"CheckUserID": 999}),
    ("language", {"SrcUserID": 999}),
    ("language", {"UserID": 999}),
    ("language", {"Language": "en-US"}),
])
def test_unrelated_notifications_do_not_confirm(scripted, case, overrides):
    _, cfg, state = scripted
    state["confirmation_overrides"] = overrides
    with pytest.raises(NotificationTimeout) as error:
        demo.run_case(case, cfg, 0.1, {"language": "zh-CN"})
    assert error.value.recent
    assert error.value.session_open


def test_wait_helpers_preserve_notify_callbacks_and_ignore_old_events(scripted):
    _, cfg, state = scripted
    a, b = connect_pair(cfg)
    message = {"SerialNumber": 7, "SrcUserID": b.room_user_id, "DstUserID": 0, "Text": "private-test-text"}
    try:
        checked = a.review_chat_message_and_wait(message, approved=True, timeout=1)
        assert checked["CheckResult"] == "1"
        assert a.events.wait_until(lambda: len(a.events.chat_check_results) == 1, 1)
        event = a.events.wait_event(cmdid=6629, kind="notify", timeout=1)
        assert event.fields == checked
        state["silent"].add(6529)
        with pytest.raises(NotificationTimeout) as error:
            a.review_chat_message_and_wait(message, approved=True, timeout=0.05)
        assert "private-test-text" not in str(error.value)
        # A late member-enter notify may legitimately arrive in this window.
        # Only the old approval must be excluded from the fresh observation.
        assert (6629, "notify") not in error.value.recent

        result = a.set_interactive_language_and_wait("zh-CN", user_id=b.room_user_id, observer=b, timeout=1)
        assert result["UserID"] == b.room_user_id
        assert b.events.wait_until(lambda: bool(b.events.interactive_languages), 1)
        assert b.events.wait_event(cmdid=6640, kind="notify", timeout=1).fields == result
        own = a.set_interactive_language_and_wait("zh-CN", timeout=1)
        assert own["SrcUserID"] == own["UserID"] == a.room_user_id
        state["silent"].add(6540)
        with pytest.raises(NotificationTimeout):
            a.set_interactive_language_and_wait("zh-CN", timeout=0.05)
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_confirmation_timeout_never_sends(scripted, timeout):
    server, cfg, _ = scripted
    a, b = connect_pair(cfg)
    try:
        with pytest.raises(ValueError, match="finite and positive"):
            a.set_interactive_language_and_wait("zh-CN", timeout=timeout)
        a.get_user_features(timeout=1)
        assert not any(cmd.cmdid == 6540 for _, cmd in server.received_commands)
    finally:
        a.close()
        b.close()


def test_confirmation_observer_must_be_in_same_room(scripted):
    server, cfg, _ = scripted
    a, b = connect_pair(cfg)
    try:
        b.real_room_id = "99999"
        with pytest.raises(ValueError, match="same Conf room"):
            a.set_interactive_language_and_wait("zh-CN", observer=b, timeout=0.1)
        with pytest.raises(ValueError, match="FmTestClient"):
            a.set_interactive_language_and_wait("zh-CN", observer=object(), timeout=0.1)
        a.get_user_features(timeout=1)
        assert not any(cmd.cmdid == 6540 for _, cmd in server.received_commands)
    finally:
        a.close()
        b.close()
