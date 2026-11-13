"""Structured multi-step dependency-flow execution."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from .models import CaseStatus, DependencyFlow
from .allure_reporting import (
    actual_call_evidence, alias_name, attach_json, operation_name, parameter_summary,
    protocol_notify_evidence, protocol_outbound_evidence,
    protocol_response_evidence, report_step,
)
from .expectations import get_expectation


class DependencyFlowExecutor:
    """Execute only reviewed flow definitions marked IMPLEMENTED."""

    def execute(self, flow: DependencyFlow, runtime) -> list[dict]:
        if flow.status is not CaseStatus.IMPLEMENTED:
            raise RuntimeError(
                f"dependency flow is not executable: {flow.flow_id} status={flow.status.value}")
        results = []
        for step in flow.steps:
            summary = parameter_summary(step.params)
            detail = f"（{summary}）" if summary else ""
            evidence_actor = step.actor
            evidence_operation = step.operation
            evidence_params = runtime.resolve_variables(step.params)
            outbound_marks = (runtime.mark_all_outbound()
                              if hasattr(runtime, "mark_all_outbound") else {})
            if step.operation in {"wait_async_events", "wait_async_call"}:
                async_state = runtime.async_call_state(str(evidence_params["handle"]))
                evidence_actor = str(async_state["alias"])
                evidence_operation = str(async_state["operation"])
                evidence_params = dict(async_state["params"])
                outbound_marks = async_state.get("outbound_marks", {})
            with report_step(
                    f"步骤{step.sequence}：{alias_name(step.actor)}执行"
                    f"【{operation_name(step.operation)}】{detail}"):
                try:
                    result = self._execute_step(step, runtime)
                finally:
                    outbound = (runtime.snapshot_all_outbound(outbound_marks)
                                if hasattr(runtime, "snapshot_all_outbound") else {})
                    try:
                        request_cmdid = get_expectation(evidence_operation).request_cmdid
                    except ValueError:
                        request_cmdid = None
                    attach_json(
                        actual_call_evidence(
                            outbound, actor=evidence_actor,
                            operation=evidence_operation,
                            fallback_params=evidence_params),
                        "实际公开方法传参（变量已替换）")
                    attach_json(
                        protocol_outbound_evidence(
                            outbound, actor=evidence_actor,
                            operation=evidence_operation,
                            request_cmdid=request_cmdid),
                        "实际发送协议（请求报文）")
                results.append(result)
                response_expected = bool(step.expected_response)
                try:
                    response_expected = (
                        response_expected or
                        get_expectation(step.operation).response is not None or
                        step.actor in runtime.services)
                except ValueError:
                    pass
                attach_json(
                    protocol_response_evidence(
                        result.get("response"), result.get("events"),
                        response_expected=response_expected,
                        error_code=result.get("expected_error_code")),
                    "实际Response（服务器返回）")
                attach_json(
                    protocol_notify_evidence(result.get("events")),
                    "实际Notify（各端接收）")
        return results

    def _execute_step(self, step, runtime) -> dict:
        marks = runtime.mark_all_events()
        params = runtime.resolve_variables(step.params)
        try:
            if step.operation == "replace_client":
                runtime.replace_client(step.actor)
                return {"sequence": step.sequence, "lifecycle": "client_replaced"}
            if step.operation == 'join_related':
                driver = runtime.join_related(step.actor, params['alias'], params['fields'])
                return {'sequence': step.sequence, 'related_room': dict(driver.identity)}
            if step.operation == 'close_related':
                runtime.clients.pop(params['alias']).close()
                return {'sequence': step.sequence, 'related_closed': params['alias']}
            if step.operation == "start_async_call":
                handle = str(params["handle"])
                runtime.start_async_call(
                    handle, step.actor, str(params["operation"]),
                    params.get("params") or {})
                return {"sequence": step.sequence, "async_started": handle}
            if step.operation == "wait_async_events":
                state = runtime.async_call_state(str(params["handle"]))
                events = runtime.assert_operation(
                    str(params.get("operation") or "allow_knock"),
                    str(state["alias"]), None, {}, state["marks"],
                    notify_overrides=step.expected_notifies,
                    expected_receivers=step.expected_receivers,
                    forbidden_receivers=step.forbidden_receivers)
                return {"sequence": step.sequence, "events": events}
            if step.operation == "wait_async_call":
                state = runtime.async_call_state(str(params["handle"]))
                response = runtime.wait_async_call(
                    str(params["handle"]), params.get("timeout"))
                events = runtime.assert_operation(
                    str(state["operation"]), str(state["alias"]), None, {},
                    state["marks"], notify_overrides=step.expected_notifies,
                    response_match=step.expected_response,
                    expected_receivers=step.expected_receivers,
                    forbidden_receivers=step.forbidden_receivers)
                self._capture_outputs(
                    runtime, {"response": response, "events": events},
                    step.output_variables)
                return {"sequence": step.sequence, "response": response, "events": events}
            if step.actor in runtime.services:
                response = runtime.call_service(step.actor, step.operation, params)
                response_actor = next(iter(runtime.clients), step.actor)
            else:
                response = runtime.call(step.actor, step.operation, params)
                response_actor = step.actor
            if step.expected_error_codes or step.expected_nonzero_result:
                raise AssertionError(
                    "flow step expected a business error but succeeded: "
                    f"sequence={step.sequence}, operation={step.operation}, "
                    f"expected_error_codes={step.expected_error_codes}, "
                    f"expected_nonzero_result={step.expected_nonzero_result}, "
                    f"response={response!r}")
            if (step.operation == 'configure_polling' and params.get('operation') == 2
                    and params.get('status') == 1):
                from .polling_scenario import assert_quiet
                assert_quiet(runtime, step.actor, params, marks)
                attach_json(runtime.snapshot_all_events(marks), '暂停期间事件快照')
                return {'sequence': step.sequence, 'paused': True, 'events': {}}
            events = runtime.assert_operation(
                step.operation, response_actor, None, {}, marks,
                notify_overrides=step.expected_notifies,
                response_match=step.expected_response,
                expected_receivers=step.expected_receivers,
                forbidden_receivers=step.forbidden_receivers)
            self._capture_outputs(
                runtime, {"response": response, "events": events},
                step.output_variables)
            return {"sequence": step.sequence, "response": response, "events": events}
        except Exception as exc:
            code = getattr(exc, "code", None)
            try:
                numeric_code = int(code) if code is not None else None
            except (TypeError, ValueError):
                numeric_code = code
            if step.expected_error_codes and numeric_code in step.expected_error_codes:
                return {"sequence": step.sequence,
                        "expected_error_code": numeric_code}
            if step.expected_nonzero_result and numeric_code not in (None, 0, "0"):
                metadata = get_expectation(step.operation)
                if metadata.response is None or step.actor not in runtime.clients:
                    raise AssertionError(
                        "nonzero Result must be proven by a real protocol response") from exc
                response_expectation = replace(
                    metadata.response,
                    match=runtime.resolve_variables(step.expected_response))
                response_event = runtime.clients[step.actor].wait_event(
                    response_expectation, marks[step.actor], runtime.event_timeout)
                fields = getattr(response_event, "fields", None)
                if fields is None and isinstance(response_event, Mapping):
                    fields = response_event.get("fields")
                result = self._read_path(fields or {}, "Result")
                if result in (None, 0, "0"):
                    raise AssertionError(
                        f"expected nonzero Result, actual={result!r}") from exc
                for notify in step.expected_notifies:
                    event = replace(
                        notify, match=runtime.resolve_variables(notify.match))
                    forbidden = set(step.forbidden_receivers) | set(
                        notify.forbidden_receivers)
                    for alias in forbidden:
                        runtime.clients[alias].expect_no_event(
                            event, marks[alias], runtime.quiet_timeout)
                return {
                    "sequence": step.sequence,
                    "expected_error_code": numeric_code,
                    "events": {f"{step.actor}:response": response_event},
                }
            runtime.dirty = True
            if step.stop_on_failure:
                raise
            return {"sequence": step.sequence, "failed": True}

    @classmethod
    def _capture_outputs(cls, runtime, response: Any, outputs: Mapping[str, str]) -> None:
        for variable, path in outputs.items():
            source = response
            resolved_path = str(path)
            # Backward compatibility: paths without response./events. still
            # address the public method's synchronous return value.
            if (isinstance(response, Mapping) and "response" in response and
                    not resolved_path.startswith(("response.", "events."))):
                source = response.get("response")
            value = cls._read_path(source, resolved_path)
            if value is None:
                raise AssertionError(
                    f"flow output not found: variable={variable}, path={path}, response={response!r}")
            runtime.variables[variable] = value

    @staticmethod
    def _read_path(value: Any, path: str) -> Any:
        current = value
        for part in str(path).split("."):
            if isinstance(current, Mapping):
                current = current.get(part)
            elif isinstance(current, (list, tuple)) and part.isdigit():
                index = int(part)
                current = current[index] if index < len(current) else None
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                return None
        return current
