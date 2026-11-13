"""Execute Excel business cases across FM and HTTP closure types."""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Dict, Mapping

from .models import ClosureType, ExpectedOutcome, FunctionalCase


@dataclass
class CaseExecutionResult:
    response: Any = None
    events: Dict[str, Any] | None = None
    error_code: int | None = None
    expected_failure: bool = False


class FunctionalCaseExecutor:
    PRECONDITIONS = {
        "NONE": (), "RECORDING": (("operator", "start_record", {}),),
        "HOST_RELEASED": (("operator", "release_host", {}),),
        "HOST_HELD": (("operator", "apply_host", {
            "chair_pwd": "${env.chair_password}",
        }),),
        "ROOM_LOCKED": (("operator", "lock_room", {}),),
        "RIGHT_OCCUPIED": (("observer", "request_right", {}),),
        "RIGHT_HELD": (("operator", "request_right", {}),),
        "USER_LEFT": (("observer", "conf_logout", {}),),
        "LAYOUT_RIGHT": (("operator", "request_right", {"right_type": 1}),),
        "POLLING_RIGHT": (("operator", "request_right", {"right_type": 2}),),
        "GROUP_RIGHT": (("operator", "request_right", {"right_type": 11}),),
        "MEDIA_RIGHT": (("operator", "request_right", {"right_type": 5}),),
        "VNC_RIGHT": (("operator", "request_right", {"right_type": 4}),),
        "AUDIO_ON": (("operator", "mic_on", {}),),
        "AUDIO_OFF": (("operator", "mic_off", {}),),
        "VIDEO_ON": (("operator", "camera_on", {}),),
        "VIDEO_OFF": (("operator", "camera_off", {}),),
        "TARGET_AUDIO_OFF": (("observer", "mic_off", {}),),
        "TARGET_AUDIO_ON": (("observer", "mic_on", {}),),
        "TARGET_VIDEO_OFF": (("observer", "camera_off", {}),),
        "TARGET_VIDEO_ON": (("observer", "camera_on", {}),),
        "RECORDING_STOPPED": (("operator", "stop_record", {}),),
        "ROOM_UNLOCKED": (("operator", "unlock_room", {}),),
        "SUBTITLE_SET": (("operator", "set_subtitle", {"data": "前置字幕", "duration": 1}),),
        "MEDIA_STARTED": (("operator", "start_aux_stream", {"kind": "media"}),),
        "VNC_STARTED": (("operator", "start_aux_stream", {"kind": "vnc"}),),
        "MEDIA_STARTED_WITH_RIGHT": (
            ("operator", "request_right", {"right_type": 5}),
            ("operator", "start_aux_stream", {"kind": "media",
                                                "audio_share_id": 1,
                                                "video_share_id": 1}),
        ),
        "VNC_STARTED_WITH_RIGHT": (
            ("operator", "request_right", {"right_type": 4}),
            ("operator", "start_aux_stream", {"kind": "vnc"}),
        ),
        "HTTP_MANAGER_ON": (("http_admin", "set_user_manager", {
            "room_id": "${operator.room_id}",
            "user_id": "${observer.room_user_id}", "state": 2}),),
        "HTTP_PRESENTER_ON": (("http_admin", "set_user_presenter", {
            "room_id": "${operator.room_id}",
            "user_id": "${observer.room_user_id}", "state": 2}),),
        "SELF_ADMIN_ON": (("operator", "set_role", {
            "user_id": "${operator.room_user_id}", "role": "admin",
            "enabled": True}),),
        "TARGET_ADMIN_ON": (("operator", "set_role", {
            "user_id": "${observer.room_user_id}", "role": "admin",
            "enabled": True}),),
        "SELF_PRESENTER_ON": (("operator", "set_role", {
            "user_id": "${operator.room_user_id}", "role": "presenter",
            "enabled": True}),),
        "TARGET_PRESENTER_ON": (("operator", "set_role", {
            "user_id": "${observer.room_user_id}", "role": "presenter",
            "enabled": True}),),
    }
    # An idempotent AV setter may legitimately produce no Notify when the
    # requested state is already current. The actual case operation is still
    # asserted normally.
    OPTIONAL_NOTIFY_PRECONDITIONS = {
        "AUDIO_ON", "AUDIO_OFF", "VIDEO_ON", "VIDEO_OFF",
        "TARGET_AUDIO_ON", "TARGET_AUDIO_OFF", "TARGET_VIDEO_ON", "TARGET_VIDEO_OFF",
        "ROOM_UNLOCKED", "RECORDING_STOPPED",
    }

    def execute(self, case: FunctionalCase, runtime) -> CaseExecutionResult:
        if case.scene_type == 'WAITING_REVIEW':
            from .waiting_review import execute_waiting_review
            return execute_waiting_review(case, runtime)
        if case.scene_type == 'AUX_FM_CONTENTION':
            from .aux_contention import execute_fm_aux_contention
            return execute_fm_aux_contention(runtime)
        if case.scene_type == 'REMOTE_AV_CONSENT':
            from .interaction_scenarios import execute_remote_av_consent
            return execute_remote_av_consent(case, runtime)
        if case.scene_type == 'WEB_NOTIFY_6654':
            from .interaction_scenarios import execute_web_notify
            return execute_web_notify(case, runtime)
        if case.scene_type == 'RECORD_ERROR_6637':
            from .interaction_scenarios import execute_record_error_6637
            return execute_record_error_6637(runtime)
        if case.scene_type == 'CHAT_REVIEW':
            from .interaction_scenarios import execute_chat_review
            return execute_chat_review(case, runtime)
        if case.scene_type == 'H323_LIFECYCLE':
            from .h323_scenario import execute_h323
            return execute_h323(case, runtime)
        if case.scene_type == 'RELAY_ROUNDTRIP':
            from .invitation_relay import relay_roundtrip
            return relay_roundtrip(case, runtime)
        if case.scene_type == 'PAAS_INVITED_JOIN':
            from .invitation_relay import invited_join
            return invited_join(case, runtime)
        if case.operation == 'configure_polling' and case.scene_type == 'POLLING_BOUNDARY':
            from .polling_boundaries import execute_polling_boundary
            return execute_polling_boundary(case, runtime)
        if case.operation == 'set_conf_state' and set(case.params) == {'mode'}:
            from .state_scenarios import ignored_room_mode
            return ignored_room_mode(case, runtime)
        from .state_scenarios import front_logout, unsubscribe, waiting_admission
        if case.operation == 'front_logout':
            return front_logout(runtime)
        if case.operation == 'unsubscribe_feature_users':
            return unsubscribe(runtime)
        if case.operation == 'allow_waiting_users' or (case.operation == 'set_conf_state' and 'enable_waiting_room' in case.params):
            return waiting_admission(runtime)
        if case.operation == 'reply_self_state_change':
            from .state_scenarios import remote_state_reply
            return remote_state_reply(runtime)
        if case.operation in {'enter_group', 'end_groups', 'bring_to_main',
                              'modify_group_config', 'change_group_state'}:
            self._prepare_group(case, runtime)
        self._prepare(case, runtime)
        if case.scene_type == 'AUDIO_APPLICATION_DIRECT':
            from .confirmed_contracts import prepare_direct_audio_application
            prepare_direct_audio_application(runtime, case.actor)
        if case.operation == 'create_group':
            from .confirmed_contracts import read_group_capacity, prepare_group_switches
            read_group_capacity(runtime, case.actor)
            prepare_group_switches(runtime, case.actor, case.scene_type)
        if case.operation == 'allow_knock' and case.expected_outcome is ExpectedOutcome.NO_SUCCESS_EVENT:
            from .state_scenarios import ignore_absent_knock
            return ignore_absent_knock(case, runtime)
        if (case.operation in ('request_right', 'release_right') and
                case.expected_outcome is ExpectedOutcome.NO_SUCCESS_EVENT):
            from .state_scenarios import ignored_right
            return ignored_right(case, runtime)
        if case.expected_outcome is ExpectedOutcome.EXPECTED_DISCONNECT:
            if not case.isolated or not runtime.is_healthy():
                raise AssertionError('预期断会必须使用初始健康的独立会议')
        marks = runtime.mark_all_events(); params = runtime.resolve_variables(case.params)
        try:
            if case.actor in getattr(runtime, "services", {}):
                response = runtime.call_service(case.actor, case.operation, params)
                response_actor = "operator" if "operator" in runtime.clients else next(iter(runtime.clients), case.actor)
            else:
                response = runtime.call(case.actor, case.operation, params)
                response_actor = case.actor
        except Exception as exc:
            return self._handle_error(case, runtime, marks, exc)

        if case.expected_outcome is ExpectedOutcome.EXPECTED_DISCONNECT:
            from .models import EventExpectation
            # 必须有其他端的真实离会通知，不能把本地调用异常当作预期断会。
            events = {}
            for alias, driver in runtime.clients.items():
                if alias == case.actor:
                    continue
                events[f'{alias}:notify:0'] = driver.wait_event(
                    EventExpectation(cmdid=6605, kind='notify', match={
                        'UserID': runtime.variables[f'{case.actor}.room_user_id']}),
                    marks[alias], runtime.event_timeout)
            deadline = time.monotonic() + runtime.event_timeout
            while runtime.clients[case.actor].is_healthy() and time.monotonic() < deadline:
                time.sleep(0.05)
            if runtime.clients[case.actor].is_healthy():
                raise AssertionError('非法命令后操作端未按约定断会')
            for alias, driver in runtime.clients.items():
                driver.expect_no_event(EventExpectation(cmdid=6633, kind='notify', match={
                    'VideoPolling.val': params['polling_id']}), marks[alias], runtime.quiet_timeout)
                if alias != case.actor and not driver.is_healthy():
                    raise AssertionError(f'非操作端不应断会: {alias}')
            runtime.dirty = True  # 预期断会也不能归还复用池。
            return CaseExecutionResult(response=response, events=events, expected_failure=True)
        if case.expected_outcome in {ExpectedOutcome.ERROR_RESPONSE,
                                     ExpectedOutcome.ERROR_AND_NO_EVENT,
                                     ExpectedOutcome.VALIDATION_ERROR}:
            raise AssertionError(f"预期业务失败但操作成功: {case.operation}")
        if case.expected_outcome is ExpectedOutcome.NO_SUCCESS_EVENT:
            if case.actor not in runtime.clients:
                raise AssertionError("NO_SUCCESS_EVENT only supports TestClient operations")
            if case.operation == 'configure_polling' and params.get('operation') == 4:
                from .polling_scenario import assert_ignored
                assert_ignored(runtime, params, marks)
                return CaseExecutionResult(response=response, events={}, expected_failure=True)
            runtime.assert_no_operation_event(
                case.operation, case.actor, case.target,
                case.expected_fields, marks)
            self._check_after_rejection(runtime)
            return CaseExecutionResult(response=response, events={}, expected_failure=True)
        if case.operation == "set_av_mix_params":
            # 6521 is fire-and-forget. Close its loop through the public
            # 6520/6620 query rather than treating send() as proof of success.
            response = runtime.call(case.actor, "get_av_mix_params", {})
            self._assert_fields(
                response, runtime.resolve_variables(case.expected_response))
        elif (case.expected_outcome is ExpectedOutcome.SUCCESS_RESPONSE and
              response is None):
            raise AssertionError(
                f"SUCCESS_RESPONSE operation returned no evidence: {case.operation}")
        self._assert_fields(response, runtime.resolve_variables(case.expected_response))
        from .assertion_contracts import validate_response_shape
        validate_response_shape(case.operation,response)
        if case.operation == 'conf_login':
            from .confirmed_contracts import verify_product_features, verify_role_permissions
            verify_product_features(runtime.clients[case.actor], marks[case.actor], runtime.event_timeout)
            verify_role_permissions(runtime.clients[case.actor], marks[case.actor], runtime.event_timeout)
        if case.operation=='create_group':
            from .group_assertions import verify_created_groups
            verify_created_groups(runtime,case.actor,params,response,marks[case.actor])

        if case.closure_type == ClosureType.HTTP_RESULT:
            from .http_result_assertions import validate_result
            validate_result(case.operation,response,params,runtime.variables.get('recording.expected_title',''),
                file_reference=runtime.variables.get('recording.file_reference'))
            return CaseExecutionResult(response=response, events={})
        if (case.operation == 'configure_polling' and case.case_type.value == 'NORMAL'
                and params.get('operation') == 1 and params.get('status') in (0, 1)):
            from .polling_scenario import resume_inactive_add
            marks = resume_inactive_add(runtime, case.actor, params, marks)
        events = runtime.assert_operation(
            case.operation, response_actor, case.target, case.expected_fields, marks,
            notify_overrides=case.expected_notifies,
            response_match=case.expected_response,
            expected_receivers=case.expected_receivers,
            forbidden_receivers=case.forbidden_receivers)
        if (not case.operation.startswith(('front_','entrance_')) and
                case.operation not in ('kick_user','conf_logout','close_room','remove_user','close_meeting')):
            assert runtime.is_healthy(), '业务协议断言完成后连接不健康'
        if case.operation == 'configure_polling' and params.get('screen') == -1:
            assert runtime.is_healthy(), '屏幕字段透传后A/B连接不健康'
        return CaseExecutionResult(
            response=response, events=events,
            expected_failure=case.expected_outcome is ExpectedOutcome.ERROR_EVENT)

    def _prepare_group(self, case, runtime):
        from .models import EventExpectation
        uid = runtime.variables['observer.room_user_id']
        runtime.call('operator', 'request_right', {'right_type': 11})
        result = runtime.call('operator', 'create_group', {'rooms': [
            {'Theme': 'E2E分组前置', 'User': [{'ID': uid, 'Right': 2}]}]})
        room = result['RoomInfo'][0]['RoomID']
        runtime.variables['group.room_id'] = room
        if case.operation == 'bring_to_main':
            marks = runtime.mark_all_events()
            runtime.call('operator', 'enter_group', {'room_id': room,
                                                   'users': [{'ID': uid, 'Right': 2}]})
            event = runtime.clients['observer'].wait_event(
                EventExpectation(cmdid=6624, kind='notify', match={'RoomID': room}),
                marks['observer'], runtime.event_timeout)
            runtime.join_related('observer', 'observer_group', event.fields)

    def _handle_error(self, case, runtime, marks, exc):
        code = getattr(exc, "code", None)
        if case.expected_outcome is ExpectedOutcome.VALIDATION_ERROR:
            if not isinstance(exc, (TypeError, ValueError)):
                raise AssertionError(
                    f"预期本地参数校验失败，实际异常不符合: {exc!r}") from exc
            return CaseExecutionResult(error_code=None, events={}, expected_failure=True)
        if case.expected_outcome not in {ExpectedOutcome.ERROR_RESPONSE,
                                         ExpectedOutcome.ERROR_AND_NO_EVENT}:
            raise exc
        if code is None:
            raise AssertionError(f"预期业务错误，实际为非业务异常: {exc!r}") from exc
        try: numeric_code = int(code)
        except (TypeError, ValueError): numeric_code = code
        if case.expected_error_codes and numeric_code not in case.expected_error_codes:
            raise AssertionError(
                f"业务错误码不符: actual={code!r}, expected={case.expected_error_codes}") from exc
        if case.expected_outcome is ExpectedOutcome.ERROR_AND_NO_EVENT and case.actor in runtime.clients:
            runtime.assert_no_operation_event(
                case.operation, case.actor, case.target, case.expected_fields, marks)
            self._check_after_rejection(runtime)
        elif (case.expected_outcome is ExpectedOutcome.ERROR_AND_NO_EVENT and
              case.actor in getattr(runtime, 'services', {})):
            runtime.assert_no_operation_event(
                case.operation, 'operator', case.target, case.expected_fields, marks)
            if not runtime.is_healthy():
                raise AssertionError('HTTP业务拒绝后模拟端不应断会')
        return CaseExecutionResult(
            error_code=numeric_code, events={}, expected_failure=True)

    @staticmethod
    def _check_after_rejection(runtime):
        assert runtime.is_healthy(), '业务拒绝后模拟端连接不健康'
        for alias, driver in runtime.clients.items():
            uid = driver.identity['room_user_id']
            result = runtime.call(alias, 'get_user_features', {'user_id': uid})
            assert str(result.get('UserID')) == str(uid), '后续合法请求未返回正确用户'
        assert runtime.is_healthy(), '后续合法请求后连接不健康'

    def _prepare(self, case: FunctionalCase, runtime) -> None:
        precondition = case.precondition
        if precondition == "NONE" and case.operation == "apply_host":
            precondition = "HOST_RELEASED"
        if precondition not in self.PRECONDITIONS:
            raise ValueError(f"unregistered precondition: {precondition}")
        for alias, operation, raw_params in self.PRECONDITIONS[precondition]:
            if (precondition == "HOST_HELD" and
                    int(runtime.variables.get(f"{alias}.user_right") or 0) == 3):
                continue
            params = dict(raw_params)
            if precondition in {"RIGHT_OCCUPIED", "RIGHT_HELD"}:
                params["right_type"] = case.params.get("right_type")
            marks = runtime.mark_all_events()
            from .assertion_contracts import notify_fields, response_fields
            prerequisite_fields=notify_fields(operation,params,alias)
            prerequisite_response=response_fields(operation,params,alias)
            if alias in getattr(runtime, "services", {}):
                runtime.call_service(alias, operation, runtime.resolve_variables(params))
                response_actor = "operator"
            else:
                runtime.call(alias, operation, runtime.resolve_variables(params))
                response_actor = alias
            if precondition in self.OPTIONAL_NOTIFY_PRECONDITIONS:
                # Idempotent setters may omit Notify, but when a transition is
                # emitted we must wait for it before issuing the inverse test
                # action. Otherwise the server can collapse both operations.
                target = ("observer" if response_actor == "operator" and
                          "observer" in runtime.clients else None)
                try:
                    runtime.assert_operation(
                        operation, response_actor, target, prerequisite_fields, marks, timeout=1.0)
                except AssertionError:
                    pass
                time.sleep(0.5)
                continue
            # State-building commands are asynchronous. Do not execute the
            # actual test step until the server has broadcast their Notify.
            target = ("observer" if response_actor == "operator" and
                      "observer" in runtime.clients else None)
            runtime.assert_operation(operation, response_actor, target, prerequisite_fields, marks,
                                     response_match=prerequisite_response)
            if precondition=='RECORDING':time.sleep(1.0)

    @classmethod
    def _assert_fields(cls, actual: Any, expected: Mapping[str, Any]) -> None:
        for path, expected_value in expected.items():
            value = cls._read_path(actual, path)
            if str(value) != str(expected_value):
                raise AssertionError(
                    f"response mismatch: path={path}, expected={expected_value!r}, actual={value!r}")

    @staticmethod
    def _read_path(value: Any, path: str) -> Any:
        current = value
        for part in path.split("."):
            if isinstance(current, Mapping):
                current = current.get(part)
            elif isinstance(current, (list, tuple)) and part.isdigit():
                index = int(part)
                current = current[index] if index < len(current) else None
            else:
                return None
        return current

    @staticmethod
    def _assert_http_result(response: Any, operation: str) -> None:
        """HTTP结果链路必须拿到结构化数据或非空文件，不能只看未抛异常。"""
        if isinstance(response, (bytes, bytearray)):
            if not response:
                raise AssertionError(f"HTTP结果文件为空: operation={operation}")
            if bytes(response).lstrip().lower().startswith((b'<html',b'<!doctype html')):
                raise AssertionError('HTTP结果为错误HTML页')
            return
        if not isinstance(response, Mapping):
            raise AssertionError(
                f"HTTP结果类型不正确: operation={operation}, actual={type(response).__name__}")
        if str(response.get('code'))!='0' or not response.get('data'):
            raise AssertionError('HTTP结果缺少业务成功码或业务数据')
