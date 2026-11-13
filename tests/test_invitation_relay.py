from types import SimpleNamespace
import pytest
from meeting_test_runtime.invitation_relay import relay_roundtrip, validate_invitation_target
from meeting_test_runtime import ExcelCaseLoader
from meeting_test_runtime.fm_driver import FmClientDriver


def test_invitation_target_must_come_from_received_code():
    validate_invitation_target(SimpleNamespace(invite_code=123456789), '123456789')
    with pytest.raises(AssertionError):
        validate_invitation_target(SimpleNamespace(invite_code=987654321), 123456789)


def test_sdk_relay_registered_and_invitation_cases_use_online_flow():
    assert FmClientDriver.METHOD_NAMES['send_relay_message'] == 'send_relay_message'
    assert 'send_relay_message' not in FmClientDriver.MISSING_OPERATIONS
    cases = ExcelCaseLoader('tests/e2e/data/fm_e2e_cases.xlsx').load_cases()
    invited = [c for c in cases if c.operation in ('invite_users', 'invite_contacts')]
    assert len(invited) == 2 and all(c.scene_type == 'PAAS_INVITED_JOIN' for c in invited)
    relay = [c for c in cases if c.operation == 'send_relay_message']
    assert len(relay) == 1 and relay[0].scene_type == 'RELAY_ROUNDTRIP'


@pytest.mark.parametrize('corrupt', [False, True])
def test_relay_roundtrip_rejects_field_corruption(corrupt):
    sent = []
    class Driver:
        def __init__(self, uid): self.identity = {'room_user_id': uid}
        def wait_event(self, expected, after, timeout):
            message = sent[-1][2]['messages'][0]
            fields = {'id': str(message['id']), **message['fields']}
            if message['id'] == 6718:
                fields['Item'] = [{'ID': '1'}, {'ID': '2'}]
                if corrupt: fields['Text'] = 'corrupted'
            return SimpleNamespace(fields={'msg': fields})
        def expect_no_event(self, expected, after, timeout):
            assert 'msg.Probe' in expected.match
    def call(alias, op, params):
        if op == 'get_user_features': return {'UserID': 1 if alias == 'operator' else 2}
        sent.append((alias, op, params))
    runtime = SimpleNamespace(clients={'operator': Driver(1), 'observer': Driver(2)},
        resolve_variables=lambda p: p, mark_all_events=lambda: {'operator': 4, 'observer': 8},
        call=call, event_timeout=1, quiet_timeout=0, is_healthy=lambda: True)
    case = SimpleNamespace(params={'dst_user_id': 2,
        'messages': [{'id': 6718, 'fields': {'Text': '中文<&>"', 'Item': [{'ID': 1}, {'ID': 2}]}}]})
    if corrupt:
        with pytest.raises(AssertionError): relay_roundtrip(case, runtime)
    else:
        assert len(relay_roundtrip(case, runtime).events) == 2
        assert [s[0] for s in sent] == ['operator', 'observer']
