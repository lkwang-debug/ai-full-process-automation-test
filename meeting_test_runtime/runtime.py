"""Multi-client orchestration independent of concrete TestClient implementations."""
from __future__ import annotations

from dataclasses import replace
import threading
import time
from typing import Any, Dict, Mapping, Optional, Sequence

from .drivers import TestClientDriver, create_client_driver
from .expectations import get_expectation
from .models import ClientProfile, DeliveryPolicy, EventExpectation


class MeetingTestRuntime:
    def __init__(self, *, event_timeout: float = 10.0, quiet_timeout: float = 0.3) -> None:
        self.event_timeout = float(event_timeout); self.quiet_timeout = float(quiet_timeout)
        self.clients: Dict[str, TestClientDriver] = {}
        self.profiles: Dict[str, ClientProfile] = {}
        self.services: Dict[str, Any] = {}
        self.variables: Dict[str, Any] = {}; self.responses: Dict[str, Any] = {}
        self.cleanup_callbacks = []
        self.cleanup_results = []
        self._async_calls: Dict[str, Dict[str, Any]] = {}
        self.dirty = False

    def create_client(self, alias: str, profile: ClientProfile) -> TestClientDriver:
        if alias in self.clients:
            raise ValueError(f"client alias already exists: {alias}")
        driver = create_client_driver(profile)
        self.clients[alias] = driver; self.profiles[alias] = profile
        return driver

    def replace_client(self, alias: str) -> TestClientDriver:
        """Rebuild one failed endpoint using its existing profile."""
        if alias not in self.profiles:
            raise KeyError(f"client profile not found: {alias}")
        old = self.clients.pop(alias, None)
        if old is not None:
            old.close()
        driver = create_client_driver(self.profiles[alias])
        self.clients[alias] = driver
        for key in [key for key in self.variables if key.startswith(f"{alias}.")]:
            self.variables.pop(key, None)
        return driver

    def add_service(self, alias: str, service: Any) -> Any:
        if alias in self.services:
            raise ValueError(f"service alias already exists: {alias}")
        self.services[alias] = service
        return service

    def attach_http(self, client_alias: str, service_alias: str = "http_admin") -> None:
        driver = self._client(client_alias)
        service = self.services[service_alias]
        native = getattr(service, "native_client", service)
        attach = getattr(driver, "attach_http", None)
        if attach is None:
            raise TypeError(f"client {client_alias} does not support attach_http")
        attach(native)

    def register_cleanup(self, callback, *args, **kwargs) -> None:
        self.cleanup_callbacks.append((callback, args, kwargs))

    def join_related(self, parent_alias, alias, fields):
        if alias in self.clients:
            raise ValueError(f"client alias already exists: {alias}")
        driver = self._client(parent_alias).connect_related(fields)
        self.clients[alias] = driver
        for key, value in driver.identity.items():
            self.variables[f"{alias}.{key}"] = value
        return driver

    def join(self, alias: str, room_id: int) -> Mapping[str, Any]:
        driver = self._client(alias); result = driver.join(int(room_id))
        for name, value in driver.identity.items():
            self.variables[f"{alias}.{name}"] = value
        return result

    def call(self, alias: str, operation: str, params: Optional[dict] = None) -> Any:
        driver = self._client(alias)
        response = driver.call(operation, params or {})
        self.responses[alias] = response
        # Lifecycle flow steps use call(front_login/conf_login) separately.
        # Refresh dynamic identity so following steps can resolve room/user IDs.
        for name, value in driver.identity.items():
            self.variables[f"{alias}.{name}"] = value
        return response

    def call_service(self, alias: str, operation: str, params: Optional[dict] = None) -> Any:
        response = self.services[alias].call(operation, params or {})
        self.responses[alias] = response
        return response

    def start_async_call(self, handle: str, alias: str, operation: str,
                         params: Optional[dict] = None) -> None:
        """Start one public client operation without blocking the scenario thread."""
        if handle in self._async_calls:
            raise ValueError(f"async call handle already exists: {handle}")
        state: Dict[str, Any] = {
            "alias": alias, "operation": operation, "params": dict(params or {}),
            "marks": self.mark_all_events(), "response": None, "error": None,
            "outbound_marks": self.mark_all_outbound(), "done": threading.Event(),
        }

        def invoke() -> None:
            try:
                state["response"] = self.call(alias, operation, state["params"])
            except BaseException as exc:  # Preserve the original protocol error for the flow.
                state["error"] = exc
            finally:
                state["done"].set()

        thread = threading.Thread(
            target=invoke, name=f"meeting-test-{handle}", daemon=True)
        state["thread"] = thread
        self._async_calls[handle] = state
        thread.start()

    def async_call_state(self, handle: str) -> Mapping[str, Any]:
        try:
            return self._async_calls[handle]
        except KeyError as exc:
            raise KeyError(f"async call handle not found: {handle}") from exc

    def wait_async_call(self, handle: str, timeout: Optional[float] = None) -> Any:
        state = self.async_call_state(handle)
        wait = self.event_timeout if timeout is None else float(timeout)
        if not state["done"].wait(wait):
            raise TimeoutError(f"async call {handle} did not finish within {wait}s")
        if state["error"] is not None:
            raise state["error"]
        return state["response"]

    def mark_all_events(self) -> Dict[str, int]:
        return {alias: client.mark_events() for alias, client in self.clients.items()}

    def snapshot_all_events(self, marks: Mapping[str, int]) -> Dict[str, list]:
        return {alias: client.snapshot_events(marks.get(alias, 0))
                for alias, client in self.clients.items()}

    def mark_all_outbound(self) -> Dict[str, Any]:
        marks = {alias: client.mark_outbound()
                 for alias, client in self.clients.items()
                 if hasattr(client, "mark_outbound")}
        marks.update({alias: service.mark_outbound()
                      for alias, service in self.services.items()
                      if hasattr(service, "mark_outbound")})
        return marks

    def snapshot_all_outbound(self, marks: Mapping[str, Any]) -> Dict[str, list]:
        result = {alias: client.snapshot_outbound(marks.get(alias, {}))
                  for alias, client in self.clients.items()
                  if hasattr(client, "snapshot_outbound")}
        result.update({alias: service.snapshot_outbound(marks.get(alias, {}))
                       for alias, service in self.services.items()
                       if hasattr(service, "snapshot_outbound")})
        return result

    def assert_operation(self, operation: str, actor: str, target: Optional[str],
                         match: Mapping[str, Any], marks: Mapping[str, int], *,
                         timeout: Optional[float] = None,
                         notify_overrides: Sequence[EventExpectation] = (),
                         response_match: Optional[Mapping[str, Any]] = None,
                         expected_receivers: Sequence[str] = (),
                         forbidden_receivers: Sequence[str] = ()) -> Dict[str, Any]:
        metadata = get_expectation(operation)
        expected = self.resolve_variables(dict(match)); wait = timeout or self.event_timeout
        results: Dict[str, Any] = {}

        if metadata.response is not None:
            response_event = replace(
                metadata.response,
                match=self.resolve_variables(dict(response_match or metadata.response.match)))
            results[f"{actor}:response"] = self._client(actor).wait_event(
                response_event, marks[actor], float(wait))

        notifies = tuple(notify_overrides) or metadata.notifies
        if expected_receivers:
            recipients = list(expected_receivers)
        elif notifies and all(notify.receivers for notify in notifies):
            recipients = []
        else:
            recipients = self._recipients(metadata.delivery, actor, target)
        for index, notify in enumerate(notifies):
            specific = self.resolve_variables(notify.match)
            conflicts = [k for k in specific if k in expected and str(expected[k]) != str(specific[k])]
            if conflicts: raise ValueError(f'用例与消息级字段预期冲突: {conflicts}')
            resolved_match = {**expected, **specific}
            event_expectation = replace(notify, match=resolved_match)
            notify_recipients = list(notify.receivers) or recipients
            for alias in notify_recipients:
                results[f"{alias}:notify:{index}"] = self._client(alias).wait_event(
                    event_expectation, marks[alias], float(wait))

            forbidden = set(forbidden_receivers) | self._forbidden_aliases(
                metadata.forbidden_delivery, actor, target) | set(notify.forbidden_receivers)
            for alias in forbidden:
                if alias in self.clients and alias not in notify_recipients:
                    self._client(alias).expect_no_event(
                        event_expectation, marks[alias], self.quiet_timeout)

        if not metadata.response and not notifies:
            results[actor] = self.responses.get(actor)
        return results

    def assert_no_operation_event(self, operation: str, actor: str, target: Optional[str],
                                  match: Mapping[str, Any], marks: Mapping[str, int]) -> None:
        metadata = get_expectation(operation); expected = self.resolve_variables(dict(match))
        if metadata.delivery in {DeliveryPolicy.TARGET_ONLY,
                                 DeliveryPolicy.ROLE_TARGETED} and target is None:
            # An invalid/nonexistent target has no Runtime alias. In a failure
            # case, prove that no connected participant received a success
            # Notify; positive target delivery still requires an explicit alias.
            recipients = list(self.clients)
        else:
            recipients = self._recipients(metadata.delivery, actor, target)
        for notify in metadata.notifies:
            event = replace(notify, match=expected or notify.match)
            for alias in recipients:
                self._client(alias).expect_no_event(event, marks[alias], self.quiet_timeout)

    def reset_operation(self, operation: str, actor: str,
                        params: Optional[dict] = None):
        try:
            return self._client(actor).reset(operation, params or {})
        except Exception:
            self.dirty = True
            raise

    def is_healthy(self) -> bool:
        return not self.dirty and all(client.is_healthy() for client in self.clients.values())

    def close_all(self) -> None:
        cleanup_start = len(self.cleanup_results)
        # Endpoint sessions must leave first. HTTP-created room resources are
        # deleted only after all persistent Front/Conf connections are closed.
        for client in reversed(list(self.clients.values())):
            try: client.close()
            except Exception as exc:
                self.dirty = True
                self.cleanup_results.append({'callback':'client.close','ok':False,'error':repr(exc)})
        for state in self._async_calls.values():
            thread = state.get("thread")
            if thread is not None and thread.is_alive():
                thread.join(timeout=1.0)
        for callback, args, kwargs in reversed(self.cleanup_callbacks):
            try:
                name = getattr(callback, '__name__', str(callback))
                retry_errors = []
                for attempt in range(2):
                    try:
                        result = callback(*args, **kwargs)
                        break
                    except TimeoutError as exc:
                        if name not in {'close_meeting', 'delete_room'} or attempt == 1:
                            raise
                        retry_errors.append(repr(exc))
                        time.sleep(0.5)
                if isinstance(result, dict):
                    if 'code' in result and str(result['code']) != '0':
                        raise RuntimeError(f'清理接口失败: {result.get("code")}')
                    data = result.get('data')
                    if isinstance(data, dict) and 'result' in data and str(data['result']) != '0':
                        if name == 'close_meeting' and str(data['result']) == '4':
                            self.cleanup_results.append({
                                "callback": name, "ok": True,
                                "already_absent": True, "result": result,
                                "attempts": 1 + len(retry_errors),
                                "retry_errors": retry_errors})
                            continue
                        raise RuntimeError(f'清理业务失败: {data["result"]}')
                self.cleanup_results.append({"callback": name,
                                             "ok": True, "result": result,
                                             "attempts": 1 + len(retry_errors),
                                             "retry_errors": retry_errors})
            except Exception as exc:
                name = getattr(callback, '__name__', str(callback))
                absent = (name == 'delete_room' and str(getattr(exc,'code','')) == '1021') or (name == 'close_meeting' and str(getattr(exc,'code','')) == '4014')
                if not absent: self.dirty = True
                self.cleanup_results.append({"callback": getattr(callback, "__name__", str(callback)),
                                             "ok": absent, "already_absent": absent, "error": repr(exc)})
        self.cleanup_callbacks.clear()
        for service in reversed(list(self.services.values())):
            close = getattr(service, "close", None)
            if close:
                try: close()
                except Exception as exc:
                    self.dirty = True
                    self.cleanup_results.append({'callback':'service.close','ok':False,'error':repr(exc)})
        self.clients.clear(); self.profiles.clear(); self.services.clear()
        self.responses.clear(); self.variables.clear()
        self._async_calls.clear()
        failures = [r for r in self.cleanup_results[cleanup_start:] if not r['ok']]
        if failures:
            raise RuntimeError(f'测试资源清理失败: {failures}')

    def resolve_variables(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.resolve_variables(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve_variables(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.resolve_variables(item) for item in value)
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            name = value[2:-1]
            if name not in self.variables:
                raise KeyError(f"runtime variable not found: {name}")
            return self.variables[name]
        return value

    def _client(self, alias: str) -> TestClientDriver:
        if alias not in self.clients:
            raise KeyError(f"client alias not found: {alias}")
        return self.clients[alias]

    def _recipients(self, policy: DeliveryPolicy, actor: str, target: Optional[str]) -> list:
        aliases = list(self.clients)
        if policy == DeliveryPolicy.NONE: return []
        if policy == DeliveryPolicy.SENDER_ONLY: return [actor]
        if policy == DeliveryPolicy.OTHERS_ONLY: return [a for a in aliases if a != actor]
        if policy == DeliveryPolicy.ALL_PARTICIPANTS: return aliases
        if policy in {DeliveryPolicy.TARGET_ONLY, DeliveryPolicy.ROLE_TARGETED}:
            if target is None: raise ValueError(f"target required for {policy.value}")
            return [target]
        if policy == DeliveryPolicy.SENDER_AND_TARGET:
            if target is None: raise ValueError("target required for sender_and_target")
            return list(dict.fromkeys((actor, target)))
        raise ValueError(f"unsupported delivery policy: {policy}")

    @staticmethod
    def _forbidden_aliases(tokens, actor, target) -> set:
        values = set()
        for token in tokens:
            values.add(actor if token == "actor" else target if token == "target" else token)
        return {value for value in values if value}
