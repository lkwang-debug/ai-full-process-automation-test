"""Wire-format receive regressions; these are local tests, not server evidence."""
import json

import pytest

from fm_client import conf as C
from fm_client.http_api import WebApiClient
from fm_client.testing import FmTestClient


@pytest.fixture
def receiver():
    client = FmTestClient("127.0.0.1", 1)
    yield client
    client.close()


def receive(client, xml):
    wire = xml.encode("utf-8")
    mark = client.events.mark()
    client._on_data("conf", 12345, client._packer.pack(wire))
    event = client.events.snapshot_received(after=mark)[0]
    assert event.raw_xml == wire
    assert event.kind == "notify"
    return event


@pytest.mark.parametrize("xml,error", [
    ('<cmd id="6637"><RecordType val="0"/><Error val="12800"/>'
     '<Description val="中文&lt;&amp;&gt;"/></cmd>', 12800),
    ('<cmd id="6637"><ServerRecord><Error val="12806"/></ServerRecord></cmd>', 12806),
    ('<cmd id="6636"><ServerRecord><Error val="12806"/></ServerRecord></cmd>', 12806),
    ('<cmd id="6637"><Error val="12800"/><ServerRecord><Error val="7"/>'
     '</ServerRecord></cmd>', 12800),
])
def test_record_error_formats_keep_original_fields(receiver, xml, error):
    event = receive(receiver, xml)
    errors = receiver.wait_for_server_record_error(error=error, timeout=0.01)
    assert errors == [{"error": error, "fields": event.fields}]
    assert event.name != "CMD_6637"
    if "Description" in event.fields:
        assert event.fields["Description"] == "中文<&>"


@pytest.mark.parametrize("xml", [
    '<cmd id="6636"><RightType val="1"/><OccupiedUserID val="1002"/></cmd>',
    '<cmd id="6637"><RecordType val="0"/></cmd>',
])
def test_missing_error_and_right_state_do_not_invent_record_errors(receiver, xml):
    receive(receiver, xml)
    assert not receiver.events.server_record_errors


def test_waiting_list_pages_empty_end_and_singleton_are_preserved(receiver):
    wire_pages = [
        '<cmd id="6647"><UserList><UserInfo><UserID val="1002"/>'
        '<UserType val="1"/><DisplayName val="中文&lt;&amp;&gt;"/>'
        '<Online val="1"/><StartTime val="1639647071"/></UserInfo></UserList></cmd>',
        '<cmd id="6647"><UserList><UserInfo><UserID val="1003"/></UserInfo>'
        '<UserInfo><UserID val="1004"/></UserInfo><End/></UserList></cmd>',
        '<cmd id="6647"><UserList><End/></UserList></cmd>',
    ]
    events = [receive(receiver, xml) for xml in wire_pages]
    pages = list(receiver.events.waiting_user_lists)
    assert pages == [e.fields for e in events]
    assert all(e.name == "WAITINGUSERLISTNOTIFY" for e in events)
    assert pages[0]["UserList"]["UserInfo"]["DisplayName"] == "中文<&>"
    assert len(pages[1]["UserList"]["UserInfo"]) == 2
    assert "End" in pages[2]["UserList"]
    assert C.WAITINGUSERLISTNOTIFY not in C.PAIRS.values()


def test_web_notify_public_http_fields_and_receiver(receiver, monkeypatch):
    from fm_client import http_api
    captured = {}

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return b'{"code":0,"data":{"result":0}}'

    def urlopen(request, **kwargs):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        return Response()

    monkeypatch.setattr(http_api.urllib.request, "urlopen", urlopen)
    api = WebApiClient("http://unused.invalid/api", "unused", "unused")
    api.send_room_message(room_id=10000, user_field_type=1,
        user_field_value="1002", operator="tester", app_back_notify="中文<&>")
    assert captured == {"url": "http://unused.invalid/api/v1/room/sendMsg", "body": {
        "roomId": 10000, "userFieldType": 1, "userFieldValue": "1002",
        "operator": "tester", "appBackNotify": "中文<&>"}}
    event = receive(receiver, '<cmd id="6654"><UserID val="1002"/>'
        '<operator val="tester"/><appBackNotify val="中文&lt;&amp;&gt;"/></cmd>')
    assert event.name == "WEBSENDNOTIFY"
    assert list(receiver.events.web_notifies) == [event.fields]
    assert event.fields["appBackNotify"] == "中文<&>"


def test_0922_group_query_and_product_features_survive_sync(receiver):
    cmd = C.ConfCmd.build_getroomconfigreq(group_room_config=True)
    assert "GroupRoomConfig" in cmd.fields and "InviteCode" not in cmd.fields
    event = receive(receiver, '<cmd id="6639"><End/></cmd>')
    assert list(receiver.events.product_features) == [event.fields]


def test_waiting_acceptance_requires_target_and_its_own_end(receiver):
    from examples.fm_notification_protocol_test import waiting_snapshot
    receive(receiver, '<cmd id="6647"><UserList><End/></UserList></cmd>')
    assert not waiting_snapshot(receiver, 1002)["complete"]
    receive(receiver, '<cmd id="6647"><UserList><UserInfo><UserID val="1002"/>'
        '</UserInfo></UserList></cmd>')
    assert not waiting_snapshot(receiver, 1002)["complete"]
    receive(receiver, '<cmd id="6647"><UserList><End/></UserList></cmd>')
    assert waiting_snapshot(receiver, 1002)["complete"]


@pytest.mark.parametrize("fail_case,fail_delete", [(True, False), (False, True), (False, False)])
def test_fixed_probe_cleanup_and_exit_status(monkeypatch, fail_case, fail_delete):
    from examples import fm_notification_protocol_test as demo
    calls = []

    class AuthOnly:
        def __init__(self, *args, **kwargs):
            pass
        def front_authenticate(self, *args):
            pass
        def close(self):
            pass

    class Api:
        base_url = "http://local.invalid/api"
        def add_room(self, **kwargs):
            calls.append("create")
            return {"data": {"roomId": 42}}
        def make_room_fixed(self, *, room_id):
            assert room_id == 42
            calls.append("fixed")
            return {}
        def close_meeting(self, room_id):
            assert room_id == 42
            calls.append("close")
            return {}
        def delete_room(self, room_id):
            assert room_id == 42
            calls.append("delete")
            if fail_delete:
                raise RuntimeError("delete failure")
            return {}

    def probe(cfg, api, timeout):
        assert cfg["room_id"] == 42
        calls.append("probe")
        if fail_case:
            raise AssertionError("no 6654")
        return {"6654": "local stub only"}

    monkeypatch.setattr(demo, "load_config", lambda _: {
        "host": "local.invalid", "room_id": 10000, "room_node_id": "1",
        "accounts": [{"username": "a"}, {"username": "b"}]})
    monkeypatch.setattr(demo, "FmTestClient", AuthOnly)
    monkeypatch.setattr(demo.WebApiClient, "from_config", lambda _: Api())
    monkeypatch.setattr(demo, "web_notify", probe)
    assert demo.main(["--case", "web_notify"]) == (1 if fail_case or fail_delete else 0)
    assert calls == ["create", "fixed", "probe", "close", "delete"]
