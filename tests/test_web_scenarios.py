from types import SimpleNamespace
import pytest

from meeting_test_runtime.web_key_scenarios import user


def test_fm_identity_cache_only_reads_user_lists_from_member_messages():
    driver=SimpleNamespace(client_type='fm',snapshot_events=lambda after:[
        {'cmdid':6601,'fields':{'UserInfo':{'Name':'A'}}},
        {'cmdid':6613,'fields':{'User':'1001'}},
        {'cmdid':6602,'fields':{'User':[{'UserID':'1001','ManagerState':'2'}]}},
        {'cmdid':6609,'fields':{'UserID':'1001','DataState':'2'}},
    ])
    runtime=SimpleNamespace(clients={'operator':driver},variables={'operator.room_user_id':1001})
    assert user(runtime,'operator')=={'Name':'A','UserID':'1001','ManagerState':'2','DataState':'2'}


def test_paas_pong_is_known_heartbeat_and_not_a_decode_failure():
    from meeting_test_runtime.web_protocol import WebEventJournal
    event=WebEventJournal().append_frame('{pong}',1)
    assert event.kind=='heartbeat' and event.source=='paas'


def test_mixed_front_dual_stack_uses_only_explicitly_allowed_addresses():
    from meeting_test_runtime.web_driver import _allowed_conf_servers
    original = [{"NodeID": "185", "SrvAddr":
                 "TCP:192.168.7.185:28091;V6TCP:[240e:3bb::185]:28091;"},
                {"NodeID": "other", "SrvAddr": "TCP:192.168.6.160:28091;"}]
    selected = _allowed_conf_servers(original, ["192.168.7.185", "192.168.7.186"])
    assert selected == [{"NodeID": "185", "SrvAddr": "TCP:192.168.7.185:28091;"}]
    assert "V6TCP" in original[0]["SrvAddr"]


def test_mixed_front_rejects_routes_without_any_allowed_destination():
    import pytest
    from meeting_test_runtime.web_driver import _allowed_conf_servers
    with pytest.raises(RuntimeError, match="Front路由超出"):
        _allowed_conf_servers([{"SrvAddr": "TCP:192.168.6.160:28091;"}], ["192.168.7.185"])


def test_mixed_front_can_use_an_explicitly_allowed_ipv6_destination():
    from meeting_test_runtime.web_driver import _allowed_conf_servers
    servers = [{"SrvAddr": "V6TCP:[240e:3bb::185]:28091;"}]
    assert _allowed_conf_servers(servers, ["240e:3bb::185"]) == servers


@pytest.mark.parametrize("value,expected", [
    (-1742479884, 2552487412), ("2552487412", 2552487412),
    (-2147483648, 2147483648), (4294967295, 4294967295),
    (-1, 4294967295), ("1055369497", 1055369497),
])
def test_board_id_signed_and_unsigned_representations(value, expected):
    from meeting_test_runtime.web_key_scenarios import _board_id32
    assert _board_id32(value) == expected


@pytest.mark.parametrize("value", [True, 1.0, "1.0", "", "bad", None, 0, -2147483649, 4294967296])
def test_board_id_rejects_invalid_and_truncated_aliases(value):
    from meeting_test_runtime.web_key_scenarios import _board_id32
    with pytest.raises(ValueError):
        _board_id32(value)


@pytest.mark.parametrize("client_type,prefix", [("web", "Screen"), ("fm", "V3")])
def test_board_notification_matches_same_item_without_mutating_wire(client_type, prefix):
    import copy
    from meeting_test_runtime.web_key_scenarios import _whiteboard_matches
    fields = {prefix: {"DataArea": [{"ID": "1", "Item":
                                   {"Type": "1", "Pos": "0", "Data": "2552487412"}}]}}
    original = copy.deepcopy(fields)
    assert _whiteboard_matches(fields, client_type, 2552487412)
    assert fields == original


@pytest.mark.parametrize("item", [
    {"Type": 1, "Pos": 0, "Data": 2552487413},
    {"Type": 6, "Pos": 0, "Data": 2552487412},
    {"Type": 1, "Pos": 1, "Data": 2552487412},
    {"Type": 1, "Pos": 0, "Data": 2552487412 + 4294967296},
    [{"Type": 6, "Pos": 0, "Data": 2552487412}, {"Type": 1, "Pos": 0, "Data": 99}],
])
def test_board_notification_rejects_other_resources_and_cross_item_matches(item):
    from meeting_test_runtime.web_key_scenarios import _whiteboard_matches
    fields = {"Screen": [{"DataArea": [{"ID": 1, "Item": item}]}]}
    assert not _whiteboard_matches(fields, "web", 2552487412)


def test_board_rule_does_not_change_general_field_matching():
    from meeting_test_runtime.web_protocol import matches_fields
    assert not matches_fields({"UserID": 2552487412}, {"UserID": -1742479884})


def test_board_wait_ignores_old_and_wrong_resource_then_accepts_current_event():
    from meeting_test_runtime.web_protocol import WebEventJournal
    from meeting_test_runtime.web_driver import WebClientDriver
    from meeting_test_runtime.web_key_scenarios import wait_whiteboard
    import json
    journal = WebEventJournal()
    def append(resource):
        return journal.append_frame(json.dumps({"CmdID": 6613, "Data": {"Screen": [
            {"DataArea": [{"ID": 1, "Item": [{"Type": 1, "Pos": 0, "Data": resource}]}]}]}}), 1)
    append(2552487412)
    mark = journal.mark()
    append(99)
    current = append(2552487412)
    driver = object.__new__(WebClientDriver)
    driver.native_client = SimpleNamespace(events=journal)
    runtime = SimpleNamespace(clients={"observer": driver}, event_timeout=0.05)
    event = wait_whiteboard(runtime, "observer", {"observer": mark}, "whiteboard19;-1742479884")
    assert event.event_id == current.event_id
