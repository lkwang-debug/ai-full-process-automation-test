from types import SimpleNamespace
import pytest
from meeting_test_runtime.state_scenarios import front_logout, remote_state_reply
from meeting_test_runtime.state_scenarios import ignored_right
from meeting_test_runtime.state_scenarios import assert_no_mode_notification
from meeting_test_runtime.state_scenarios import assert_no_target_enter


def test_absent_knock_rejects_target_anywhere_in_user_enter_list():
    assert_no_target_enter({'A': [{'cmdid': 6602, 'fields': {'User': {'UserID': 1}}}]}, 99)
    for users in ({'UserID': '99'}, [{'UserID': 1}, {'UserID': 99}]):
        with pytest.raises(AssertionError):
            assert_no_target_enter({'B': [{'cmdid': 6602, 'fields': {'User': users}}]}, 99)


def test_mode_ignored_allows_other_conf_notifications():
    assert_no_mode_notification({'operator': [{'cmdid': 6610, 'fields': {'Lock': '1'}}],
                                 'observer': [{'cmdid': 6613, 'fields': {'Mode': '3'}}]})
    with pytest.raises(AssertionError):
        assert_no_mode_notification({'observer': [{'cmdid': 6610, 'fields': {'Mode': '0'}}]})


@pytest.mark.parametrize('mode', ['ignored', 'returned', 'late_response', 'disconnected'])
def test_ignored_right_requires_absence_of_matching_response_and_health(mode):
    class Driver:
        def expect_no_event(self, event, mark, timeout):
            assert event.cmdid == 6630 and event.match == {'Operation': 1, 'RightType': 13}
            if mode == 'late_response': raise AssertionError('late matching 6630')
    def call(*args):
        if mode == 'returned': return {'Result': 0, 'RightType': 13}
        raise TimeoutError('no response')
    health_calls = []
    def healthy():
        health_calls.append(1)
        return mode != 'disconnected' or len(health_calls) == 1
    runtime = SimpleNamespace(mark_all_events=lambda: {'operator': 3, 'observer': 5},
        is_healthy=healthy, resolve_variables=lambda v: v, event_timeout=10, quiet_timeout=0,
        call=call, clients={'operator': Driver(), 'observer': Driver()}, snapshot_all_events=lambda m: {})
    case = SimpleNamespace(actor='operator', operation='request_right', params={'right_type': 13},
                           expected_fields={'Operation': 1, 'RightType': 13})
    if mode == 'ignored': assert ignored_right(case, runtime).expected_failure
    else:
        with pytest.raises(AssertionError): ignored_right(case, runtime)


@pytest.mark.parametrize('missing', [None, 30009, 30011])
def test_front_logout_requires_both_real_responses(missing):
    seen = []
    class Driver:
        def mark_events(self): return 7
        def wait_event(self, event, mark, timeout):
            assert event.source == 'front' and event.match == {'Result': 0} and mark == 7
            if event.cmdid == missing: raise AssertionError('missing actual response')
            seen.append(event.cmdid)
            return SimpleNamespace(cmdid=event.cmdid, fields={'Result': 0})
    runtime = SimpleNamespace(clients={'operator': Driver()}, event_timeout=1, call=lambda *args: None)
    if missing:
        with pytest.raises(AssertionError): front_logout(runtime)
    else:
        result = front_logout(runtime)
        assert seen == [30009, 30011] and len(result.events) == 2


def test_remote_reply_uses_received_source_and_never_waits_for_fictitious_response():
    calls = []
    class Driver:
        def wait_event(self, event, mark, timeout):
            assert event.cmdid == 6646 and event.match['SrcUserID'] == 12
            return SimpleNamespace(fields={'SrcUserID': '12', 'VNCState': '2'})
        def expect_no_event(self, event, mark, timeout):
            assert event.cmdid in (6609, 6646)
    runtime = SimpleNamespace(clients={'operator': Driver(), 'observer': Driver()},
        variables={'observer.room_user_id': 23, 'operator.room_user_id': 12},
        mark_all_events=lambda: {'operator': 4, 'observer': 6}, event_timeout=1, quiet_timeout=0,
        call=lambda *args: calls.append(args), is_healthy=lambda: True,
        snapshot_all_events=lambda marks: {})
    remote_state_reply(runtime)
    assert calls[1][0:2] == ('observer', 'reply_self_state_change')
    assert calls[1][2]['dst_user_id'] == '12'
