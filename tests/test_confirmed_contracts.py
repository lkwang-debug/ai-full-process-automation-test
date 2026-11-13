from types import SimpleNamespace
import pytest
from meeting_test_runtime.confirmed_contracts import read_group_capacity
from fm_client.ops.contacts_ops import ContactsOps


def test_product_features_requires_expected_fields_and_end():
    from meeting_test_runtime.confirmed_contracts import verify_product_features
    from types import SimpleNamespace

    fields = {'MeetingNow': {'val': '1', 'Attr': {'Renders': 'highlight'}},
              'ServerRecord': {'val': '1', 'Attr': {'Renders': 'highlight'}},
              'End': ''}
    driver = SimpleNamespace(wait_event=lambda *args: SimpleNamespace(cmdid=6639, fields=fields))
    verify_product_features(driver, 3, 1)
    fields.pop('End')
    with pytest.raises(AssertionError, match='6639'):
        verify_product_features(driver, 3, 1)


def test_role_permissions_require_codes_permissions_and_end():
    from meeting_test_runtime.confirmed_contracts import verify_role_permissions

    fields = {
        'Role': [
            {'RoleCode': 'mentee', 'Permissions': {'Audio': {'val': '1'}}},
            {'RoleCode': 'chairman', 'Permissions': {'ServerRecord': '1'}},
        ],
        'RoleEnd': '',
    }
    driver = SimpleNamespace(
        wait_event=lambda *args: SimpleNamespace(cmdid=6631, fields=fields))
    verify_role_permissions(driver, 3, 1)

    for invalid in (
        {'Role': [], 'RoleEnd': ''},
        {'Role': [{'Permissions': {'Audio': '1'}}], 'RoleEnd': ''},
        {'Role': [{'RoleCode': 'mentee', 'Permissions': {}}], 'RoleEnd': ''},
        {'Role': [{'RoleCode': 'mentee', 'Permissions': {'Audio': '1'}}]},
    ):
        driver.wait_event = lambda *args, value=invalid: SimpleNamespace(
            cmdid=6631, fields=value)
        with pytest.raises(AssertionError, match='6631'):
            verify_role_permissions(driver, 3, 1)


def test_waiting_route_requires_all_related_connection_fields():
    from meeting_test_runtime.waiting_review import verify_waiting_route
    fields = {'RoomID': '70001', 'RoomSrvNodeID': 'NID:1',
              'SrvAddr': 'TCP:127.0.0.1:1089', 'SrvAuthCode': 'secret'}
    verify_waiting_route(fields)
    for key in fields:
        incomplete = dict(fields)
        incomplete.pop(key)
        with pytest.raises((AssertionError, ValueError)):
            verify_waiting_route(incomplete)


def test_group_parent_switches_cover_independent_binary_combinations():
    from meeting_test_runtime.confirmed_contracts import group_switch_values
    for join, leave in ((0, 0), (0, 1), (1, 0), (1, 1)):
        assert group_switch_values(f'GROUP_CONFIG_{join}{leave}') == {
            'EnableJoinGroupMeeting': join, 'EnableLeaveGroupMeeting': leave}
    with pytest.raises(ValueError):
        group_switch_values('BASIC_MEETING')


def test_sdk_public_conf_state_preserves_group_switch_fields():
    from fm_client.ops.conf_ops import ConfOps
    commands = []
    client = SimpleNamespace(_require_conf=lambda: object(), _send=lambda _, cmd: commands.append(cmd))
    ConfOps.set_conf_state(client, EnableJoinGroupMeeting=1, EnableLeaveGroupMeeting=0)
    assert commands[0].cmdid == 6510
    assert commands[0].fields == {'EnableJoinGroupMeeting': 1, 'EnableLeaveGroupMeeting': 0}


def test_excel_group_matrix_covers_each_parent_and_request_pair():
    from itertools import product
    from meeting_test_runtime.excel_loader import ExcelCaseLoader
    cases = [c for c in ExcelCaseLoader('tests/e2e/data/fm_e2e_cases.xlsx').load_cases()
             if c.operation == 'create_group']
    actual = {(c.scene_type[-2:], c.params['rooms'][0]['EnableJoin'],
               c.params['rooms'][0]['EnableLeave']) for c in cases}
    assert actual == {(f'{a}{b}', c, d) for a, b, c, d in product((0, 1), repeat=4)}
    for case in cases:
        for i in range(len(case.params['rooms'])):
            assert case.expected_response[f'RoomInfo.{i}.EnableJoin'] == '${group.enable_join}'
            assert case.expected_response[f'RoomInfo.{i}.EnableLeave'] == '${group.enable_leave}'


def test_config_query_default_unchanged_and_group_opt_in():
    commands = []
    client = SimpleNamespace(_require_conf=lambda: object(),
                             _request=lambda _, cmd, *args: commands.append(cmd) or SimpleNamespace(fields={}))
    ContactsOps.conf_get_room_config(client)
    ContactsOps.conf_get_room_config(client, group_room_config=True)
    assert commands[0].fields == {'InviteCode': {}}
    assert commands[1].fields == {'GroupRoomConfig': {}}


def test_group_capacity_requires_independent_configuration():
    response = {'GroupRoomConfig': {'GroupRoomMaxUserCount': '16'}}
    driver = SimpleNamespace(mark_events=lambda: 7, wait_event=lambda *a: None)
    runtime = SimpleNamespace(clients={'operator': driver}, variables={}, event_timeout=1,
                              call=lambda *a: response)
    read_group_capacity(runtime, 'operator')
    assert runtime.variables['group.max_user_count'] == 16
    response.clear()
    with pytest.raises(AssertionError, match='6634'):
        read_group_capacity(runtime, 'operator')
