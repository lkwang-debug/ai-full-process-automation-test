import json
from types import SimpleNamespace

import pytest

from fm_client import FmTestClient, OnlineError, OnlineInvitation, PaasOnlineClient
from fm_client import online as module
from examples import fm_invited_join_demo as demo


def config():
    return {"cp_address": "TCP:127.0.0.1:50003;", "app_id": "a" * 32,
            "token": "test-only-token", "user_id": "1002", "client_guid": "unit-test-device",
            "protocol_version": "test", "mutex_type": "PC", "os_type": "test"}


def invitation(seq=17, code=123456789):
    return {"business": "GS", "id": 14002, "seq_id": seq, "user_id": "1001",
            "user_name": "邀请者<&>", "group_id": "opaque-group-not-a-room-id",
            "extend_info": json.dumps({"version": "1.0", "method": "InviteUser",
                "params": {"inviteCode": code, "roomCompanyId": 1,
                           "inviter": {"userName": "邀请者<&>"}}}, ensure_ascii=False)}


@pytest.fixture
def cp(monkeypatch):
    class ScriptedSession:
        instances = []
        login_result = 0

        def __init__(self, listener, cfg):
            self.listener, self.config = listener, cfg
            self.sent, self.closed = [], False
            self.instances.append(self)

        def connect(self, address):
            self.address = address
            return True

        def send(self, data):
            # CP accepts raw JSON; the Conf compression prefix would fail here.
            assert data.startswith(b'{')
            message = json.loads(data)
            self.sent.append(message)
            if message["id"] == 10000:
                self.deliver({"business": "BASE", "id": 10001, "result": self.login_result})
            elif message["id"] == 10002:
                self.deliver({"business": "BASE", "id": 10003, "result": 0})
            return not self.closed

        def deliver(self, message):
            self.listener.on_data(json.dumps(message, ensure_ascii=False).encode())

        def close(self):
            self.closed = True
            self.listener.on_closed()

    monkeypatch.setattr(module, "TcpReliableSession", ScriptedSession)
    return ScriptedSession


def test_online_login_receive_accept_logout_raw_protocol(cp):
    client = PaasOnlineClient(config(), timeout=0.1)
    assert client.login()["result"] == 0
    session = cp.instances[-1]
    assert session.config.application_id == 1
    assert session.config.enable_reconnect is False
    assert session.sent[0] == {**{k: v for k, v in config().items() if k != "cp_address"},
                               "business": "BASE", "id": 10000, "registered_user": 1, "force_login": 0}
    session.deliver(invitation())
    received = client.wait_invitation()
    assert received.invite_code == 123456789
    assert received.params["inviter"]["userName"] == "邀请者<&>"
    client.respond_invitation(received)
    assert session.sent[-1] == {"business": "GS", "id": 14003, "seq_id": 17,
        "group_id": "opaque-group-not-a-room-id", "operate": 0, "extend_info": ""}
    with pytest.raises(OnlineError, match="already answered"):
        client.respond_invitation(received)
    client.logout()
    assert not client.is_online and session.closed


@pytest.mark.parametrize("cancel_id", [14004, 14008])
def test_cancel_and_duplicate_invitations_do_not_join(cp, cancel_id):
    client = PaasOnlineClient(config(), timeout=0.01)
    client.login()
    session = cp.instances[-1]
    session.deliver(invitation())
    session.deliver(invitation())
    received = client.wait_invitation()
    session.deliver({"business": "GS", "id": cancel_id, "seq_id": 17, "user_id": "1001"})
    with pytest.raises(OnlineError, match="cancelled"):
        client.respond_invitation(received)
    with pytest.raises(TimeoutError):
        client.wait_invitation()
    assert len(session.sent) == 1
    client.close()


def test_auth_failure_and_forced_offline_are_not_reported_as_online(cp):
    cp.login_result = 123
    client = PaasOnlineClient(config(), timeout=0.01)
    with pytest.raises(OnlineError, match="result=123"):
        client.login()
    assert cp.instances[-1].closed and not client.is_online
    cp.login_result = 0
    client.login()
    session = cp.instances[-1]
    session.deliver({"business": "BASE", "id": 10004, "result": 1})
    with pytest.raises(OnlineError, match="forced offline"):
        client.wait_invitation()
    client.close()


def test_offline_wakes_pending_invitation_wait(cp):
    client = PaasOnlineClient(config(), timeout=0.1)
    client.login()
    cp.instances[-1].close()
    with pytest.raises(OnlineError, match="closed"):
        client.wait_invitation()


@pytest.mark.parametrize("code", [None, "", 0, 10000, "bad", True])
def test_target_must_be_from_valid_invitation_not_group_or_config(code):
    with pytest.raises(ValueError, match="inviteCode"):
        OnlineInvitation.from_message(invitation(code=code))


def test_unrelated_or_malformed_invitation_cannot_be_accepted(cp):
    client = PaasOnlineClient(config(), timeout=0.01)
    client.login()
    session = cp.instances[-1]
    message = invitation()
    message["extend_info"] = '{"method":"OtherMessage"}'
    session.deliver(message)
    client.on_data(b'not-json')
    with pytest.raises(TimeoutError):
        client.wait_invitation()
    assert len(session.sent) == 1
    client.close()


def test_reject_reply_uses_same_invitation_identity(cp):
    client = PaasOnlineClient(config(), timeout=0.1)
    client.login()
    cp.instances[-1].deliver(invitation())
    client.respond_invitation(client.wait_invitation(), accept=False)
    assert cp.instances[-1].sent[-1]["operate"] == 1
    client.close()


def test_real_front_and_conf_sockets_join_only_after_received_invitation(cp, fm_mock_server):
    client = FmTestClient(fm_mock_server.host, fm_mock_server.port, request_timeout=1)
    receiver = None
    try:
        client.front_authenticate("invitee", "password")
        assert client.room_user_id is None
        assert not any(cmd.cmdid in (30004, 6501) for _, cmd in fm_mock_server.received_commands)
        receiver = PaasOnlineClient({**config(), "user_id": str(client.local_user_id)}, timeout=1)
        receiver.login()
        cp.instances[-1].deliver(invitation())
        received = receiver.wait_invitation()
        reply = receiver.accept_and_join(received, client)
        assert str(reply["front"]["RoomID"]) == "123456789"
        assert client.get_user_features(timeout=1)["UserID"] == str(client.room_user_id)
        ids = [cmd.cmdid for _, cmd in fm_mock_server.received_commands]
        assert ids.count(30000) == 1  # no second authenticate replacing identity
        assert ids.index(30004) < ids.index(6501)
        assert cp.instances[-1].sent[-1]["id"] == 14003
    finally:
        if receiver:
            receiver.close()
        client.close()


@pytest.mark.parametrize("state", ["wrong_user", "already_joined", "no_front"])
def test_join_checks_identity_and_existing_meeting_before_accept(cp, state):
    receiver = PaasOnlineClient(config(), timeout=0.1)
    receiver.login()
    session = cp.instances[-1]
    session.deliver(invitation())
    received = receiver.wait_invitation()
    client = SimpleNamespace(local_user_id="999" if state == "wrong_user" else "1002",
                             _conf=None, room_user_id="1002" if state == "already_joined" else None)
    if state == "no_front":
        client.local_user_id = None
    with pytest.raises(OnlineError):
        receiver.accept_and_join(received, client)
    assert len(session.sent) == 1
    receiver.close()


@pytest.mark.parametrize("method", ["invite_users", "invite_contacts"])
@pytest.mark.parametrize("deliver", [True, False])
def test_demo_requires_received_invitation_and_actual_join(cp, fm_mock_server, monkeypatch,
                                                         capsys, method, deliver):
    cfg = {"host": fm_mock_server.host, "port": fm_mock_server.port, "room_id": 10000,
           "room_node_id": "1", "initialization_wait": 0, "conf_protocol_version": "2.0",
           "accounts": [{"username": "a", "password": "p"}, {"username": "b", "password": "p"}]}
    monkeypatch.setattr(demo, "load_config", lambda _path: cfg)
    receivers = []

    def make_receiver(_path, **kwargs):
        # Front mock allocates invited B first as 1001; PaaS config must match.
        receiver = PaasOnlineClient({**config(), "user_id": "1001"}, **kwargs)
        receivers.append(receiver)
        return receiver

    monkeypatch.setattr(demo.PaasOnlineClient, "from_config", make_receiver)

    class Http:
        base_url = "http://" + fm_mock_server.host
        closed = []

        def create_instant_meeting(self, **kwargs):
            return {"code": 0, "data": {"roomId": 100100100, "nodeId": "1"}}

        def invite_users(self, room_id, ids):
            assert ids == [1001]
            if deliver:
                cp.instances[-1].deliver(invitation(code=100100100))
            return {"code": 0, "data": {"result": 0, "errors": []}}

        def close_meeting(self, room_id):
            self.closed.append(room_id)
            return {"code": 0, "data": {"result": 0}}

    api = Http()
    monkeypatch.setattr(demo.WebApiClient, "from_config", lambda _path: api)
    status = demo.main(["--config", "unused", "--online-config", "unused",
                       "--http-config", "unused", "--invite-method", method,
                       "--timeout", "1", "--wait", "0.1"])
    output = capsys.readouterr().out
    assert status == (0 if deliver else 1), output
    assert ("[PASS] invited_join" in output) == deliver
    assert api.closed == [100100100]
    assert not receivers[0].is_online
