from types import SimpleNamespace
import pytest
from meeting_test_runtime.h323_scenario import execute_h323


@pytest.mark.parametrize('missing_enter', [False, True])
def test_http_invitation_requires_real_entry_without_conf_hangup(missing_enter):
    class Driver:
        def wait_event(self, expected, mark, timeout):
            assert expected.cmdid == 6602
            if missing_enter: raise AssertionError('no actual terminal entry')
            return SimpleNamespace(fields={'User': {'UserID': '123', 'UserIP': 'terminal'}})
    def forbidden(*args): raise AssertionError('HTTP邀请不应等待6745或由A调用6743')
    runtime = SimpleNamespace(variables={'env.h323': 'terminal'},
        clients={'operator': Driver(), 'observer': Driver()},
        mark_all_events=lambda: {'operator': 1, 'observer': 2}, resolve_variables=lambda x: x,
        call_service=lambda *args: {'code': '0', 'data': {'result': 0}}, call=forbidden,
        is_healthy=lambda: True)
    case = SimpleNamespace(operation='invite_terminals_http', actor='http_admin',
                           params={'call_info': [{'calleeNickname': 'E2E-H323'}]})
    if missing_enter:
        with pytest.raises(AssertionError): execute_h323(case, runtime)
    else: assert len(execute_h323(case, runtime).events) == 2


@pytest.mark.parametrize('result,healthy,passes', [(4097, True, True), (0, True, False), (4097, False, False)])
def test_invalid_hangup_requires_real_error_response_and_health(result, healthy, passes):
    class Driver:
        def wait_event(self, expected, mark, timeout):
            assert expected.cmdid == 6744 and expected.match['OprResult'] == str(result)
            return {'fields': {'OprResult': result}}
        def expect_no_event(self, expected, mark, timeout):
            assert expected.cmdid == 6745 and expected.match['CallState'] == 5
    runtime = SimpleNamespace(clients={'operator': Driver(), 'observer': Driver()},
        variables={'env.h323': 'test-terminal'}, mark_all_events=lambda: {'operator': 1, 'observer': 2},
        resolve_variables=lambda x: x, call=lambda *args: {'OprResult': str(result), 'CallType': '2'},
        event_timeout=1, quiet_timeout=0, is_healthy=lambda: healthy)
    case = SimpleNamespace(operation='conf_hangup', case_type=SimpleNamespace(value='BUSINESS_EXCEPTION'), params={'callee_nums': ['invalid-h323'], 'call_type': 2})
    if passes: assert execute_h323(case, runtime).expected_failure
    else:
        with pytest.raises(AssertionError): execute_h323(case, runtime)
