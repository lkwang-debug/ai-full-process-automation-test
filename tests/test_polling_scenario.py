from meeting_test_runtime.polling_scenario import resume_inactive_add
from meeting_test_runtime.polling_scenario import assert_ignored
from types import SimpleNamespace
import pytest


@pytest.mark.parametrize('bad_event,healthy', [(False, True), (True, True), (False, False)])
def test_ignored_polling_needs_no_matching_event_and_healthy_clients(bad_event, healthy):
    checked = []
    class Driver:
        def expect_no_event(self, event, mark, timeout):
            assert event.cmdid == 6633 and event.match == {'VideoPolling.val': 2002}
            assert timeout == 10
            checked.append(mark)
            if bad_event: raise AssertionError('收到不应出现的轮询通知')
    runtime = SimpleNamespace(clients={'operator': Driver(), 'observer': Driver()},
        event_timeout=10, is_healthy=lambda: healthy, snapshot_all_events=lambda marks: {})
    if bad_event or not healthy:
        with pytest.raises(AssertionError):
            assert_ignored(runtime, {'polling_id': 2002}, {'operator': 3, 'observer': 6})
    else:
        assert_ignored(runtime, {'polling_id': 2002}, {'operator': 3, 'observer': 6})
        assert checked == [3, 6]


def test_inactive_add_verifies_quiet_then_resumes_with_explicit_complete_config():
    calls = []
    class Driver:
        def expect_no_event(self, expected, mark, timeout):
            calls.append(('quiet', mark, expected.match))
    class Runtime:
        clients = {'operator': Driver(), 'observer': Driver()}
        quiet_timeout = 2
        def is_healthy(self): return True
        def mark_all_events(self): return {'operator': 11, 'observer': 12}
        def call(self, actor, operation, params): calls.append(('resume', params))
    marks = resume_inactive_add(Runtime(), 'operator', {
        'polling_id': 17, 'name': 'test', 'user_ids': [1, 2],
        'interval': 60, 'screen': 4, 'window': 2, 'status': 1},
        {'operator': 4, 'observer': 5})
    assert [call[0] for call in calls] == ['quiet', 'quiet', 'resume']
    assert calls[-1][1] == {'operation': 2, 'polling_id': 17, 'name': 'test',
                          'user_ids': [1, 2], 'interval': 60,
                          'screen': 4, 'window': 2, 'status': 2}
    assert marks == {'operator': 11, 'observer': 12}
