from dataclasses import replace
from types import SimpleNamespace
import pytest
from meeting_test_runtime import ExcelCaseLoader
from meeting_test_runtime.case_executor import FunctionalCaseExecutor
from meeting_test_runtime.models import ExpectedOutcome


@pytest.mark.parametrize('actor_stays,observer_breaks', [(False, False), (True, False), (False, True)])
def test_expected_disconnect_requires_actor_closed_and_observer_healthy(actor_stays, observer_breaks):
    case = next(c for c in ExcelCaseLoader('tests/e2e/data/fm_e2e_cases.xlsx').load_cases()
                if c.operation == 'configure_polling')
    class Driver:
        def __init__(self, actor): self.actor = actor
        def is_healthy(self): return actor_stays if self.actor else not observer_breaks
        def wait_event(self, expectation, after, timeout):
            assert expectation.cmdid == 6605 and expectation.match == {'UserID': 1}
            assert after == 10
            return {'cmdid': 6605, 'fields': {'UserID': 1}}
        def expect_no_event(self, expectation, after, timeout):
            assert expectation.cmdid == 6633 and after == 10
    runtime = SimpleNamespace(
        clients={'operator': Driver(True), 'observer': Driver(False)}, services={},
        variables={'operator.room_user_id': 1}, event_timeout=0, quiet_timeout=0,
        dirty=False, is_healthy=lambda: True,
        mark_all_events=lambda: {'operator': 10, 'observer': 10},
        resolve_variables=lambda value: value, call=lambda *args: None)
    case = replace(case, precondition='NONE', isolated=True,
                   expected_outcome=ExpectedOutcome.EXPECTED_DISCONNECT,
                   params={'operation': 0, 'polling_id': 2001})
    if actor_stays or observer_breaks:
        with pytest.raises(AssertionError):
            FunctionalCaseExecutor().execute(case, runtime)
    else:
        result = FunctionalCaseExecutor().execute(case, runtime)
        assert result.expected_failure and runtime.dirty


@pytest.mark.parametrize('operation', [0, 4])
def test_polling_unsupported_operation_is_local_validation(operation):
    case = next(c for c in ExcelCaseLoader(
        'tests/e2e/data/fm_e2e_cases.xlsx').load_cases()
        if c.operation == 'configure_polling' and c.params.get('operation') == operation)

    assert case.expected_outcome is ExpectedOutcome.VALIDATION_ERROR
    assert case.precondition == 'NONE'
    assert case.environment_risk.value == 'SAFE_REUSABLE'
