import copy
import xml.etree.ElementTree as ET

import pytest

from examples import fm_extended_protocol_test as extended
from fm_client import ConfCmd, XmlCommand
from fm_client import conf as C
from fm_client.testing import connect_client, connect_pair, wait_for


def cfg(server):
    return {"host": server.host, "port": server.port, "room_id": 10000,
            "room_node_id": "1", "conf_protocol_version": "2.0", "request_timeout": 1,
            "accounts": [{"username": "a", "password": "p"}, {"username": "b", "password": "p"},
                         {"username": "c", "password": "p"}]}


def test_relay_wire_shape_multiple_messages_and_escaping():
    messages = [{"id": 6718}, {"id": 6719, "fields": {"Result": 0, "Text": "<&>\"中文",
                "Item": [{"ID": 1}, {"ID": 2}], "Nested": {"Inner": {"Value": ""}}}}]
    original = copy.deepcopy(messages)
    command = ConfCmd.build_relay_message(7, 8, messages, dst_user_ids=[8, 9, 9])
    root = ET.fromstring(command.to_bytes())
    assert [node.attrib for node in root.findall("msg")] == [{"id": "6718"}, {"id": "6719"}]
    assert root.find("msg/id") is None
    assert [node.get("val") for node in root.findall("DstUserList/UserID")] == ["9"]
    parsed = XmlCommand.from_bytes(command.to_bytes()).fields
    assert parsed["msg"][1]["Text"] == '<&>"中文'
    assert parsed["msg"][1]["Item"] == [{"ID": "1"}, {"ID": "2"}]
    assert messages == original
    assert C.RELAYMESSAGE not in C.PAIRS


@pytest.mark.parametrize("kwargs", [
    {"messages": {}}, {"messages": []}, {"messages": [{"fields": {}}]},
    {"messages": [{"id": 6718.5}]}, {"messages": [{"id": True}]},
    {"messages": [{"id": 6718, "xml": "<msg/>"}]},
    {"messages": [{"id": 6718, "fields": {"@id": 99}}]},
    {"messages": [{"id": 6718, "fields": {"Text": "bad\x00value"}}]},
    {"messages": [{"id": 6718, "fields": {"bad tag": "x"}}]},
    {"dst_user_id": -1}, {"dst_user_id": 0, "dst_user_ids": [9]},
    {"dst_user_ids": [0]}, {"dst_user_ids": []}, {"src_user_id": None},
])
def test_invalid_relay_fails_before_send(kwargs):
    values = dict(src_user_id=7, dst_user_id=8, messages=[{"id": 6718}])
    values.update(kwargs)
    with pytest.raises(ValueError):
        ConfCmd.build_relay_message(**values)


def test_relay_targets_and_callback_do_not_leak_to_other_room(fm_mock_server):
    config = cfg(fm_mock_server)
    clients = []
    try:
        a, b = connect_pair(config)
        clients.extend((a, b))
        c = connect_client(config, account_index=2)
        clients.append(c)
        other = connect_client({**config, "room_id": 20000}, account_index=2)
        clients.append(other)
        marks = {client: client.events.mark() for client in clients}
        a.send_relay_message(b.room_user_id, [{"id": 6718}], dst_user_ids=[c.room_user_id])
        for client in (b, c):
            extended.event(client, marks[client], C.RELAYMESSAGE, 1,
                           {"SrcUserID": a.room_user_id, "DstUserID": b.room_user_id, "msg.id": 6718})
        assert wait_for(lambda: len(b.events.snapshot(b.events.relay_messages)) == 1, 1)
        assert b.events.snapshot(b.events.relay_messages)[0]["messages"] == [{"id": "6718"}]
        extended.absent(a, marks[a], C.RELAYMESSAGE)
        extended.absent(other, marks[other], C.RELAYMESSAGE)
        bm = b.events.mark()
        cm = c.events.mark()
        a.send_relay_message(0, [{"id": 6718, "fields": {"Text": "broadcast"}}])
        for client, mark in ((b, bm), (c, cm)):
            extended.event(client, mark, C.RELAYMESSAGE, 1, {"DstUserID": 0, "msg.Text": "broadcast"})
        extended.absent(a, marks[a], C.RELAYMESSAGE)
        extended.absent(other, marks[other], C.RELAYMESSAGE)
        extended.healthy(a, b, 1)
    finally:
        for client in reversed(clients):
            client.close()


def test_relay_forwards_nested_attributes_and_multiple_messages_unchanged(fm_mock_server):
    a, b = connect_pair(cfg(fm_mock_server))
    try:
        mark = b.events.mark()
        messages = [{"id": 6718, "fields": {"Node": {"@id": "nested", "Value": "<&>"}}},
                    {"id": 6719, "fields": {"Result": 0}}]
        a.send_relay_message(b.room_user_id, messages)
        event = b.events.wait_event(after=mark, source="conf", cmdid=6700, timeout=1)
        assert event is not None
        assert event.fields["msg"][0]["Node"] == {"id": "nested", "Value": "<&>"}
        request = next(cmd for _, cmd in fm_mock_server.received_commands if cmd.cmdid == 6700)
        assert event.raw_xml == request._relay_raw_xml
    finally:
        b.close()
        a.close()
