from dataclasses import replace
from types import SimpleNamespace

import pytest

from meeting_test_runtime.case_executor import FunctionalCaseExecutor
from meeting_test_runtime.models import CaseType, ExpectedOutcome, FunctionalCase


class BusinessError(Exception):
    def __init__(self, code):
        self.code = code


class FakeRuntime:
    def __init__(self, error=None):
        self.error = error; self.no_event_checked = False; self.services = {}; self.clients = {"operator": SimpleNamespace(identity={'room_user_id':1})}
        self.variables = {}; self.calls = []
    def mark_all_events(self): return {"operator": 0, "observer": 0}
    def resolve_variables(self, value): return value
    def call(self, alias, operation, params):
        self.calls.append((alias, operation, params))
        if operation == 'get_user_features': return {'UserID':1}
        if self.error: raise self.error
        if operation == "get_av_mix_params": return {"EnableDefault": 1}
        return {"Result": 0}
    def assert_operation(self, *args, **kwargs): return {"observer": {"ok": True}}
    def assert_no_operation_event(self, *args): self.no_event_checked = True
    def is_healthy(self): return True


def test_silent_rejection_does_not_pass_when_disconnected():
    runtime = FakeRuntime()
    runtime.is_healthy = lambda: False
    with pytest.raises(AssertionError, match='连接不健康'):
        FunctionalCaseExecutor().execute(_case(expected_outcome=ExpectedOutcome.NO_SUCCESS_EVENT,
                                               expected_error_codes=()), runtime)


def _case(**changes):
    case = FunctionalCase(
        "FM-X-E-001", "音频", CaseType.BUSINESS_EXCEPTION, "BASIC", "NONE",
        "operator", "observer", "mic_on", {}, "audio", {},
        ExpectedOutcome.ERROR_AND_NO_EVENT, (0x1008,), False, "P1", True)
    return replace(case, **changes)


def test_expected_business_error_and_no_success_event_passes():
    runtime = FakeRuntime(BusinessError(0x1008))
    result = FunctionalCaseExecutor().execute(_case(), runtime)
    assert result.error_code == 0x1008
    assert result.expected_failure
    assert runtime.no_event_checked


@pytest.mark.parametrize('healthy', [True, False])
def test_http_rejection_requires_no_notify_and_healthy_fm_clients(healthy):
    runtime = FakeRuntime()
    runtime.services = {'http_admin': object()}
    runtime.is_healthy = lambda: healthy
    def rejected(*args): raise BusinessError(4019)
    runtime.call_service = rejected
    case = _case(actor='http_admin', operation='control_user_audio', expected_error_codes=(4019,))
    if healthy:
        result = FunctionalCaseExecutor().execute(case, runtime)
        assert result.error_code == 4019 and runtime.no_event_checked
    else:
        with pytest.raises(AssertionError, match='不应断会'):
            FunctionalCaseExecutor().execute(case, runtime)


def test_unexpected_business_error_code_fails():
    with pytest.raises(AssertionError, match="业务错误码不符"):
        FunctionalCaseExecutor().execute(_case(), FakeRuntime(BusinessError(0x1007)))


def test_expected_error_but_operation_success_fails():
    with pytest.raises(AssertionError, match="预期业务失败"):
        FunctionalCaseExecutor().execute(_case(), FakeRuntime())


def test_notify_only_rejection_passes_when_success_notify_is_absent():
    runtime = FakeRuntime()
    result = FunctionalCaseExecutor().execute(
        _case(expected_outcome=ExpectedOutcome.NO_SUCCESS_EVENT,
              expected_error_codes=()), runtime)
    assert result.error_code is None
    assert result.expected_failure
    assert runtime.no_event_checked


def test_local_validation_error_is_not_reported_as_server_error():
    runtime = FakeRuntime(ValueError("unknown aux kind"))
    result = FunctionalCaseExecutor().execute(
        _case(operation="start_aux_stream",
              expected_outcome=ExpectedOutcome.VALIDATION_ERROR,
              expected_error_codes=()), runtime)
    assert result.error_code is None
    assert result.expected_failure
    assert not runtime.no_event_checked


def test_asynchronous_error_event_is_an_expected_failure():
    runtime = FakeRuntime()
    result = FunctionalCaseExecutor().execute(
        _case(operation="apply_host", expected_outcome=ExpectedOutcome.ERROR_EVENT,
              expected_error_codes=()), runtime)
    assert result.expected_failure
    assert result.events == {"observer": {"ok": True}}


def test_release_host_does_not_reapply_when_operator_is_already_host():
    runtime = FakeRuntime()
    runtime.variables["operator.user_right"] = 3
    FunctionalCaseExecutor().execute(
        replace(_case(), case_type=CaseType.NORMAL,
                operation="release_host", precondition="HOST_HELD",
                expected_outcome=ExpectedOutcome.SUCCESS_EVENT,
                expected_error_codes=()),
        runtime)
    assert [operation for _, operation, _ in runtime.calls] == ["release_host"]


def test_set_av_mix_is_verified_by_public_readback():
    runtime = FakeRuntime()
    result = FunctionalCaseExecutor().execute(
        replace(_case(), case_type=CaseType.NORMAL,
                operation="set_av_mix_params",
                expected_outcome=ExpectedOutcome.SUCCESS_RESPONSE,
                expected_error_codes=(), expected_response={"EnableDefault": 1}),
        runtime)
    assert result.response == {"EnableDefault": 1}
