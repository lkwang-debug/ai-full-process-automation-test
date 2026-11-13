from collections import deque

import pytest

from examples import fm_extended_protocol_test as extended
from fm_client import conf as C
from fm_client.client import _PendingRequest
from fm_client.errcodes import ConfLoginWaiting
from fm_client.mockserver import MockFmServer
from fm_client.testing import connect_client, connect_pair, wait_for
from fm_client.xmlcmd import XmlCommand


def config(server):
    return {"host": server.host, "port": server.port, "room_id": 10000,
            "room_node_id": "1", "request_timeout": 1, "conf_protocol_version": "2.0",
            "accounts": [{"username": "a", "password": "pwd"},
                         {"username": "b", "password": "pwd"}]}


@pytest.mark.parametrize("case", [name for name in extended.CASES if name != "waiting_room"])
def test_extended_cases_follow_delivery_contracts(fm_mock_server, case):
    extended.run_case(case, config(fm_mock_server), 1)


@pytest.mark.parametrize("case,field", [
    ("custom_data", "PublicData"), ("custom_private", "PrivateData"),
])
def test_custom_data_accepts_delivery_without_post_send_health(fm_mock_server, monkeypatch, case, field):
    a, b = connect_pair(config(fm_mock_server))
    monkeypatch.setattr(extended, "connect_pair", lambda _config: (a, b))

    def unexpected_health(*args):
        pytest.fail("custom-data cases must not check subsequent session health")

    monkeypatch.setattr(extended, "healthy", unexpected_health)
    send = a.set_user_custom_data

    def deliver_then_close(*args, **kwargs):
        am, bm = a.events.mark(), b.events.mark()
        send(*args, **kwargs)
        receivers = ((a, am), (b, bm)) if field == "PublicData" else ((b, bm),)
        for client, mark in receivers:
            extended.event(client, mark, C.USERCUSTOMDATANOTIFY, 1)
        a.close()

    monkeypatch.setattr(a, "set_user_custom_data", deliver_then_close)
    result = extended.run_case(case, config(fm_mock_server), 1)
    assert result
    requests = [cmd for _, cmd in fm_mock_server.received_commands
                if cmd.cmdid == C.SETUSERCUSTOMDATA]
    assert len(requests) == 1
    assert field in requests[0].fields
    other_field = "PrivateData" if field == "PublicData" else "PublicData"
    assert other_field not in requests[0].fields
    assert not any(cmd.cmdid == C.QUERYUSERFEATURES
                   for _, cmd in fm_mock_server.received_commands)


@pytest.mark.parametrize("case,field", [
    ("custom_data", "PublicData"), ("custom_private", "PrivateData"),
])
@pytest.mark.parametrize("invalid_delivery", ["missing", "wrong_content"])
def test_custom_data_still_requires_correct_delivery(fm_mock_server, monkeypatch,
                                                     case, field, invalid_delivery):
    original = fm_mock_server._extended_conf

    def handle(cmd, send, ctx):
        if cmd.cmdid == C.SETUSERCUSTOMDATA:
            if invalid_delivery == "missing":
                return True
            cmd.fields[field] = "wrong-content"
        return original(cmd, send, ctx)

    monkeypatch.setattr(fm_mock_server, "_extended_conf", handle)
    with pytest.raises(AssertionError, match="cmdid=6672"):
        extended.run_case(case, config(fm_mock_server), 0.1)


def test_custom_private_still_rejects_delivery_to_sender(fm_mock_server, monkeypatch):
    original = fm_mock_server._extended_conf

    def handle(cmd, send, ctx):
        handled = original(cmd, send, ctx)
        if cmd.cmdid == C.SETUSERCUSTOMDATA:
            send(XmlCommand(C.USERCUSTOMDATANOTIFY, {
                "SrcUserID": ctx["uid"], "UserID": cmd.get("UserID"),
                "PrivateData": cmd.get("PrivateData"),
            }))
        return handled

    monkeypatch.setattr(fm_mock_server, "_extended_conf", handle)
    with pytest.raises(AssertionError):
        extended.run_case("custom_private", config(fm_mock_server), 1)


def test_other_extended_cases_keep_health_checks(fm_mock_server, monkeypatch):
    checks = []
    monkeypatch.setattr(extended, "healthy", lambda a, b, timeout: checks.append(timeout))
    extended.run_case("renegotiate", config(fm_mock_server), 1)
    assert checks == [1]


def test_login_features_and_renegotiation_have_different_layout_shapes():
    login = C.ConfCmd.build_loginreq(room_srv_node_id="1", room_id=10000,
        user_id=7, user_name="a", user_type=1, token="token",
        support_features=["NewVote", "VNCRemote"], screen_count=2, layouts=["W1", "WV4"])
    parsed = XmlCommand.from_bytes(login.to_bytes()).fields
    assert parsed["SupportFeatures"] == "NewVote,VNCRemote"
    assert b'<SupportFeatures val="NewVote,VNCRemote"' in login.to_bytes()
    assert parsed["Layout"] == "W1,WV4"
    assert parsed["ScreenCount"] == "2"
    renegotiation = C.ConfCmd.build_renegotiate_layout(screen_count=2, layouts=["W1", "WV4"])
    parsed = XmlCommand.from_bytes(renegotiation.to_bytes()).fields
    assert parsed["Layout"] == {"ScreenCount": "2", "Code": "W1,WV4"}


def test_extended_state_fields_and_empty_values_are_preserved():
    cmd = C.ConfCmd.build_change_user_state(user_id=8,
        private_talk={"DstUserID": 7, "State": 2}, audio_out_mute=0,
        info={"Sex": 0, "SeatList": 0, "DisplayName": "a<&>", "Tel": "", "Mobile": "", "EMail": ""},
        wb_mark_state=0, enable_chat=0, group_state=0, online_state=0,
        record_state=0, speaker_state=0, camera_state=0, microphone_state=0, local_vp_state=0)
    parsed = XmlCommand.from_bytes(cmd.to_bytes()).fields
    assert parsed["PrivateTalk"] == {"DstUserID": "7", "State": "2"}
    assert parsed["Info"]["DisplayName"] == "a<&>"
    assert parsed["Info"]["Tel"] == ""
    assert parsed["WBMarkState"] == parsed["AudioOutMute"] == "0"
    assert parsed["LocalVPState"] == "0"


@pytest.mark.parametrize("builder,kwargs", [
    (C.ConfCmd.build_feature_users, {"feature": "", "query_type": 0}),
    (C.ConfCmd.build_feature_users, {"feature": "NewVote", "query_type": 3}),
    (C.ConfCmd.build_asr_subtitle, {"enabled": True, "translation": True}),
    (C.ConfCmd.build_renegotiate_layout, {"screen_count": 0, "layouts": []}),
    (C.ConfCmd.build_h5_vote, {"vote_id": "x", "state": 2}),
    (C.ConfCmd.build_h5_vote, {"vote_id": 0x80000000, "state": 2}),
    (C.ConfCmd.build_h5_vote, {"vote_id": 17, "state": 1}),
    (C.ConfCmd.build_h5_vote, {"vote_id": 17, "state": 2, "user_ids": [0, 7]}),
    (C.ConfCmd.build_h5_vote, {"vote_id": 17, "state": 2, "data": {"UserList": {}}}),
    (C.ConfCmd.build_allow_waiting_users, {"users": [{"UserID": 7, "Allow": 2}]}),
    (C.ConfCmd.build_modify_group_config, {"rooms": [{"Theme": "missing-id"}]}),
    (C.ConfCmd.build_user_custom_data, {"user_id": 7}),
])
def test_invalid_required_fields_fail_before_sending(builder, kwargs):
    with pytest.raises(ValueError):
        builder(**kwargs)


def test_acknowledgements_and_forwarding_are_not_request_pairs():
    assert C.MODIFYSELFSTATERESULT not in C.PAIRS
    assert C.H5VOTE not in C.PAIRS
    assert C.SETUSERCUSTOMDATA not in C.PAIRS
    assert C.PAIRS[C.QUERYFEATUREUSERS] == C.FEATUREUSERSREP


def test_paged_feature_query_and_subscription_updates(fm_mock_server):
    cfg = config(fm_mock_server)
    cfg["conf_login_options"] = {"support_features": ["NewVote"]}
    a, b = connect_pair(cfg)
    c = None
    try:
        a.query_feature_users("NewVote", subscribe=True)
        mark = a.events.mark()
        c = connect_client(cfg)
        extended.event(a, mark, C.FEATUREUSERSREP, 1,
                       {"Feature": "NewVote", "UserID": c.room_user_id})
        assert wait_for(lambda: len(a.events.snapshot(a.events.feature_users)) == 1, 1)
        a.unsubscribe_feature_users("NewVote")
        users = a.query_feature_users("NewVote")
        assert set(users["UserIDs"]) == {a.room_user_id, b.room_user_id, c.room_user_id}
        assert len(users["Pages"]) == 2
        assert "End" not in users["Pages"][0] and "End" in users["Pages"][-1]
        assert a.query_feature_users("missing-feature")["UserIDs"] == []
    finally:
        for client in (c, b, a):
            if client:
                client.close()


def test_feature_pages_require_end_marker():
    class IncompleteServer(MockFmServer):
        def _extended_conf(self, cmd, send, ctx):
            if cmd.cmdid == C.QUERYFEATUREUSERS:
                send(XmlCommand(C.FEATUREUSERSREP, {"Feature": cmd.get("Feature"), "UserID": "7"}))
                return True
            return super()._extended_conf(cmd, send, ctx)

    server = IncompleteServer()
    client = None
    try:
        client = connect_client(config(server))
        with pytest.raises(TimeoutError, match="end marker"):
            client.query_feature_users("NewVote", timeout=0.1)
    finally:
        if client:
            client.close()
        server.stop()


def test_unrelated_broadcast_does_not_complete_pending_request(fm):
    pending = _PendingRequest(seq=1, request_cmdid=C.QUERYFEATUREUSERS, collect_many=True,
        response_match=lambda cmd: cmd.get("Feature") == "NewVote")
    key = (123, C.FEATUREUSERSREP)
    fm._pending[key] = deque([pending])
    assert fm._take_pending_response(123, XmlCommand(C.FEATUREUSERSREP,
        {"Feature": "GroupMeeting", "End": {}})) is None
    assert fm._take_pending_response(123, XmlCommand(C.FEATUREUSERSREP,
        {"Feature": "NewVote", "End": {}})) is pending


@pytest.mark.parametrize("cmdid,queue", [
    (C.MULTIDATASHARERNOTIFY, "multi_data_sharers"),
    (C.FEATUREUSERSREP, "feature_users"),
    (C.PRODUCTFEATURESNOTIFY, "product_features"),
    (C.MODIFYSELFSTATENOTIFY, "self_state_requests"),
    (C.H5VOTENOTIFY, "h5_votes"),
    (C.GROUPUSERSTATENOTIFY, "group_user_states"),
    (C.WAITINGROOMINFO, "waiting_rooms"),
    (C.USERCUSTOMDATANOTIFY, "user_custom_data"),
])
def test_new_notifications_reach_raw_and_typed_listeners(fm, cmdid, queue):
    fields = {"test": "<&>"}
    fm._on_data("conf", 123, fm._packer.pack(XmlCommand(cmdid, fields).to_bytes()))
    assert fm.events.snapshot_received()[-1].kind == "notify"
    assert fm.events.snapshot(getattr(fm.events, queue)) == [fields]


class WaitingServer(MockFmServer):
    """Focused model: defer main login, admit only from a distinct group session."""
    waiting_id = 0x70000001

    def __init__(self):
        super().__init__()
        self.manager_uid = None
        self.deferred = None

    def _conf(self, cmd, send, ctx):
        if cmd.cmdid == C.CHANGECONFSTATE and str(cmd.get("EnableWaitingRoom")) == "1":
            self.manager_uid = ctx["uid"]
            send(XmlCommand(C.ENTERGROUPREP, self.route()))
            return
        if cmd.cmdid == C.LOGINREQ and self.manager_uid is not None:
            if str(cmd.get("RoomID")) != str(self.waiting_id) and cmd.get("UserID") != self.manager_uid:
                self.deferred = (cmd, send, ctx)
                send(XmlCommand(C.WAITINGROOMINFO, self.route()))
                return
        if cmd.cmdid == C.ALLOWWAITINGUSERS:
            if str(ctx.get("room")) != str(self.waiting_id):
                return
            users = cmd.get("UserList")["UserInfo"]
            if isinstance(users, dict):
                users = [users]
            results = []
            for user in users:
                pending = self.deferred
                found = pending is not None and user["UserID"] == pending[0].get("UserID")
                results.append({"UserID": user["UserID"], "Result": 0 if found else 0x1007})
                if found and str(user["Allow"]) == "1":
                    super()._conf(*pending)
                    self.deferred = None
            send(XmlCommand(C.ALLOWWAITINGUSERSREP, {"UserList": {"UserInfo": results}}))
            return
        super()._conf(cmd, send, ctx)

    def route(self):
        return {"RoomID": self.waiting_id, "RoomSrvNodeID": "1",
                "SrvAddr": self.conf_address_link, "SrvAuthCode": "not-a-password"}


def test_waiting_room_preserves_main_login_and_uses_group_session():
    server = WaitingServer()
    try:
        result = extended.run_case("waiting_room", config(server), 1)
        assert result["main_6601"] == "0"
        logins = [cmd for _, cmd in server.received_commands if cmd.cmdid == C.LOGINREQ]
        assert len(logins) == 4
        assert len({cmd.get("UserID") for cmd in logins}) == 2
        assert len({cmd.get("RoomID") for cmd in logins}) == 2
        assert all(cmd.get("RoomPassword") is None for cmd in logins)
    finally:
        server.stop()


def test_waiting_results_preserve_individual_errors():
    server = WaitingServer()
    a = manager = None
    try:
        a = connect_client(config(server))
        manager = a.connect_related_room(server.route())
        result = manager.allow_waiting_users([{"UserID": 999, "Allow": 1}])
        assert result["UserList"]["UserInfo"]["Result"] == str(0x1007)
        assert a.get_user_features()["UserID"] == a.room_user_id
    finally:
        for client in (manager, a):
            if client:
                client.close()
        server.stop()


def test_waiting_exception_exposes_6667_route():
    route = {"RoomID": "1879048193", "SrvAddr": "TCP:127.0.0.1:1089"}
    error = ConfLoginWaiting(10, notification=XmlCommand(C.WAITINGROOMINFO, route))
    assert error.notification_cmdid == 6667
    assert error.fields == route
