from types import SimpleNamespace
import pytest
from meeting_test_runtime import polling_boundaries as mod


@pytest.mark.parametrize('interval,count,passes', [(0, 1, False), (0, 3, True), (-1, 1, True), (-1, 2, False)])
def test_first_notify_alone_cannot_prove_zero_interval(monkeypatch, interval, count, passes):
    class Probe:
        def __init__(self, runtime): pass
        def send(self, *args, **kwargs): return {}
        def receive(self, *args, **kwargs): return {}
        def sequence(self, *args, **kwargs):
            values = [{'fields': {'VideoPolling': {'CurrentUserID': str(i % 2 + 1)}},
                       'received_monotonic': i * .31} for i in range(count)]
            return {'operator': values, 'observer': values}
    monkeypatch.setattr(mod, 'PollingProbe', Probe)
    runtime = SimpleNamespace(resolve_variables=lambda x: x, call=lambda *args: None,
                              is_healthy=lambda: True)
    case = SimpleNamespace(params={'polling_id': 2005, 'interval': interval, 'user_ids': [1, 2]})
    if passes:
        mod.execute_polling_boundary(case, runtime)
    else:
        with pytest.raises(AssertionError): mod.execute_polling_boundary(case, runtime)


@pytest.mark.parametrize('operation', [1, 3])
def test_id_zero_removal_requires_old_ids_not_restart_and_positive_control(operation):
    calls = []
    class Probe:
        runtime = SimpleNamespace(mark_all_events=lambda: {'operator': 7})
        def send(self, op, pid, *args, **kwargs):
            calls.append(('send', op, pid)); return {}
        def receive(self, marks, pid, operation=1, **fields): calls.append(('receive', operation, pid))
        def quiet(self, marks, ids): calls.append(('quiet', tuple(ids)))
    mod.batch_polling(Probe(), operation)
    assert ('receive', 2, 8101) in calls and ('receive', 2, 8102) in calls
    assert ('send', 2, 8101) in calls and ('send', 2, 8102) in calls
    assert ('quiet', (8101, 8102)) in calls
    assert calls[-2:] == [('send', 1, 8101), ('receive', 1, 8101)]
