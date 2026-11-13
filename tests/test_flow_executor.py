from __future__ import annotations

import pytest

from meeting_test_runtime.flow_executor import DependencyFlowExecutor
from meeting_test_runtime.models import (
    CaseStatus, DependencyFlow, EnvironmentRisk, FlowStep,
)


class FakeRuntime:
    def __init__(self):
        self.clients = {}
        self.services = {"http_admin": object()}
        self.variables = {}
        self.dirty = False
        self.event_timeout = 1
        self.quiet_timeout = 0

    def mark_all_events(self): return {}
    def resolve_variables(self, value): return value
    def call_service(self, alias, operation, params):
        return {"data": {"roomId": 12345}}
    def call(self, alias, operation, params): return {}
    def assert_operation(self, *args, **kwargs): return self.events if hasattr(self, "events") else {}


class BusinessError(RuntimeError):
    def __init__(self, code): self.code = code


def test_flow_executor_captures_dynamic_response_output():
    flow = DependencyFlow(
        "FLOW-1", "建会", "P0", EnvironmentRisk.DESTRUCTIVE_ISOLATED,
        "delete room", CaseStatus.IMPLEMENTED,
        (FlowStep(1, "http_admin", "create_instant_meeting",
                  output_variables={"meeting.room_id": "data.roomId"}),))
    runtime = FakeRuntime()

    DependencyFlowExecutor().execute(flow, runtime)

    assert runtime.variables["meeting.room_id"] == 12345


def test_flow_executor_captures_dynamic_notify_output():
    from dataclasses import dataclass

    @dataclass
    class Event:
        fields: dict

    flow = DependencyFlow(
        "FLOW-CHAT", "聊天撤回", "P1", EnvironmentRisk.SAFE_REUSABLE,
        "none", CaseStatus.IMPLEMENTED,
        (FlowStep(1, "http_admin", "chat",
                  output_variables={
                      "message.id": "events.observer:notify:0.fields.MsgID"}),))
    runtime = FakeRuntime()
    runtime.events = {"observer:notify:0": Event({"MsgID": "msg-123"})}

    DependencyFlowExecutor().execute(flow, runtime)

    assert runtime.variables["message.id"] == "msg-123"


def test_flow_executor_rejects_designed_only_flow():
    flow = DependencyFlow(
        "FLOW-1", "建会", "P0", EnvironmentRisk.DESTRUCTIVE_ISOLATED,
        "delete room", CaseStatus.DESIGNED, ())
    with pytest.raises(RuntimeError, match="not executable"):
        DependencyFlowExecutor().execute(flow, FakeRuntime())


def test_flow_executor_accepts_only_declared_business_error():
    runtime = FakeRuntime()
    runtime.clients = {"operator": object()}
    runtime.services = {}
    runtime.call = lambda *args, **kwargs: (_ for _ in ()).throw(BusinessError(8478))
    flow = DependencyFlow(
        "FLOW-RIGHT", "操作权冲突", "P1", EnvironmentRisk.ISOLATED_STATEFUL,
        "release right", CaseStatus.IMPLEMENTED,
        (FlowStep(1, "operator", "request_right", {"right_type": 1},
                  expected_error_codes=(8478,)),))

    result = DependencyFlowExecutor().execute(flow, runtime)

    assert result == [{"sequence": 1, "expected_error_code": 8478}]
    assert not runtime.dirty


def test_flow_executor_rejects_success_when_business_error_is_expected():
    runtime = FakeRuntime()
    runtime.clients = {"operator": object()}
    runtime.services = {}
    flow = DependencyFlow(
        "FLOW-RIGHT", "操作权冲突", "P1", EnvironmentRisk.ISOLATED_STATEFUL,
        "release right", CaseStatus.IMPLEMENTED,
        (FlowStep(1, "operator", "request_right", {"right_type": 1},
                  expected_error_codes=(8478,)),))

    with pytest.raises(AssertionError, match="expected a business error but succeeded"):
        DependencyFlowExecutor().execute(flow, runtime)

    assert runtime.dirty


def test_flow_executor_requires_real_nonzero_response_and_no_success_notify():
    from dataclasses import dataclass
    from meeting_test_runtime.models import EventExpectation

    @dataclass
    class ResponseEvent:
        fields: dict

    class Driver:
        def __init__(self, response=None):
            self.response = response
            self.no_event_checks = []

        def wait_event(self, expectation, after, timeout):
            assert expectation.cmdid == 6668
            assert expectation.match == {"MsgID": "m-1"}
            return self.response

        def expect_no_event(self, expectation, after, timeout):
            self.no_event_checks.append(expectation)

    runtime = FakeRuntime()
    runtime.services = {}
    runtime.clients = {
        "operator": Driver(ResponseEvent({"MsgID": "m-1", "Result": "9"})),
        "observer": Driver(),
    }
    runtime.mark_all_events = lambda: {"operator": 0, "observer": 0}
    runtime.call = lambda *args, **kwargs: (_ for _ in ()).throw(BusinessError(9))
    flow = DependencyFlow(
        "FLOW-RECALL", "重复撤回", "P1", EnvironmentRisk.DESTRUCTIVE_ISOLATED,
        "close room", CaseStatus.IMPLEMENTED,
        (FlowStep(
            1, "operator", "recall_msg", {"msg_id": "m-1"},
            expected_response={"MsgID": "m-1"},
            expected_notifies=(EventExpectation(
                cmdid=6669, kind="notify", match={"MsgID": "m-1"},
                forbidden_receivers=("observer",)),),
            forbidden_receivers=("observer",), expected_nonzero_result=True),))

    result = DependencyFlowExecutor().execute(flow, runtime)

    assert result[0]["expected_error_code"] == 9
    assert runtime.clients["observer"].no_event_checks
    assert not runtime.dirty
