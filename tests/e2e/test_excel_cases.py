"""Real-server execution of the external Excel business cases."""
from __future__ import annotations

import json
import time

import pytest

from meeting_test_runtime import (
    EnvironmentRisk, FunctionalCaseExecutor, MissingSessionManagerApi, SessionKey,
)
from meeting_test_runtime.expectations import get_expectation
from meeting_test_runtime.invitation_relay import InvitationAccountOnline
from meeting_test_runtime.allure_reporting import (
    actual_call_evidence, alias_name, attach_json, configure_case, operation_name,
    protocol_notify_evidence, protocol_outbound_evidence,
    protocol_response_evidence, report_step,
)

try:
    import allure
except ImportError:  # pragma: no cover - optional outside report runs
    allure = None


@pytest.mark.real_server
@pytest.mark.protocol_e2e
def test_excel_case(
    fm_excel_case, meeting_session_pool, fm_runtime_factory, test_config, request,
):
    configure_case(fm_excel_case)
    room_id, build_for_case = fm_runtime_factory
    builder = lambda: build_for_case(fm_excel_case)
    lifecycle_isolated = (
        fm_excel_case.operation == "entrance_get_config"
        or fm_excel_case.operation.startswith("front_")
        or fm_excel_case.operation == "conf_login"
        or fm_excel_case.closure_type.value in {"HTTP_TO_FM_JOIN", "HTTP_RESULT"}
    )
    isolated = (
        fm_excel_case.isolated or lifecycle_isolated or
        fm_excel_case.environment_risk is EnvironmentRisk.ISOLATED_STATEFUL
    )
    needs_witness = (
        "${witness." in str(fm_excel_case.params) or
        "witness" in fm_excel_case.expected_receivers or
        "witness" in fm_excel_case.forbidden_receivers)
    session_roles = ("operator", "observer", "witness") if needs_witness else (
        "operator", "observer")
    scene_key = f"{fm_excel_case.scene_type}:{fm_excel_case.closure_type.value}"
    if lifecycle_isolated:
        scene_key = f"{scene_key}:{fm_excel_case.operation}"
    key = SessionKey(
        scene_key, "fm->fm", room_id,
        session_roles,
    )
    recycle_started = time.monotonic()
    if isolated:
        # Destructive/lifecycle cases must not log the same account into a
        # meeting while a reusable A/B runtime still owns that session.
        invalidated = meeting_session_pool.invalidate_conflicts(key)
    else:
        # A/B与A/B/C拓扑不能并存：两者复用同一账号，服务端会返回USEREXISTED。
        # 双向切换时关闭其他冲突键，当前精确键不关闭，因此同一拓扑仍复用连接。
        invalidated = meeting_session_pool.invalidate_conflicts(key, include_exact=False)
    if invalidated:
        # Conf/Front服务端释放账号存在短暂异步窗口，避免下一拓扑立刻登录触发USEREXISTED/0x1002。
        time.sleep(test_config.getfloat(
            "execution", "session_recycle_time", fallback=2.0))
    recycle_seconds = time.monotonic() - recycle_started
    setup_started = time.monotonic()
    with report_step("步骤1：准备测试场景，创建或复用客户端并登录会议"):
        runtime = meeting_session_pool.acquire(
            key, builder, isolated=isolated)
    setup_seconds = time.monotonic() - setup_started
    operation_assertion_seconds = None
    with report_step("步骤2：记录各客户端事件游标和协议版本"):
        marks = runtime.mark_all_events()
        outbound_marks = runtime.mark_all_outbound()
        versions = {
            alias: getattr(driver, "conf_protocol_version", None)
            for alias, driver in runtime.clients.items()
        }
    protocol_expectation = get_expectation(fm_excel_case.operation)
    if allure is not None:
        allure.dynamic.parameter(
            "Conf协议版本",
            ", ".join(f"{alias}={version}" for alias, version in versions.items()))
        attach_json(versions, "各客户端Conf协议版本")
        attach_json({
            "用例ID": fm_excel_case.case_id,
            "所属模块": fm_excel_case.module,
            "业务操作": operation_name(fm_excel_case.operation),
            "公开方法": fm_excel_case.operation,
            "操作端": alias_name(fm_excel_case.actor),
            "目标端": alias_name(fm_excel_case.target),
            "请求cmdid": protocol_expectation.request_cmdid,
            "闭环类型": fm_excel_case.closure_type.value,
            "操作参数": fm_excel_case.params,
            "预期Response": fm_excel_case.expected_response,
            "预期接收端": list(fm_excel_case.expected_receivers),
            "不应接收端": list(fm_excel_case.forbidden_receivers),
            "字段断言": fm_excel_case.expected_fields,
            "上行协议取证": "步骤3展示实际公开方法传参及真实XML/HTTP请求",
        }, "用例与协议预期")
    try:
        with report_step(
                f"步骤3：{alias_name(fm_excel_case.actor)}执行"
                f"【{operation_name(fm_excel_case.operation)}】并断言协议结果"):
            try:
                operation_started = time.monotonic()
                try:
                    result = FunctionalCaseExecutor().execute(fm_excel_case, runtime)
                finally:
                    operation_assertion_seconds = time.monotonic() - operation_started
            finally:
                try:
                    resolved_params = runtime.resolve_variables(fm_excel_case.params)
                except KeyError:
                    # Some scenarios create dynamic variables inside their
                    # precondition. If setup fails before creation, preserve
                    # the original placeholder while still attaching evidence.
                    resolved_params = fm_excel_case.params
                outbound = runtime.snapshot_all_outbound(outbound_marks)
                attach_json(
                    actual_call_evidence(
                        outbound, actor=fm_excel_case.actor,
                        operation=fm_excel_case.operation,
                        fallback_params=resolved_params),
                    "实际公开方法传参（变量已替换）")
                attach_json(
                    protocol_outbound_evidence(
                        outbound, actor=fm_excel_case.actor,
                        operation=fm_excel_case.operation,
                        request_cmdid=protocol_expectation.request_cmdid),
                    "实际发送协议（请求报文）")
            if fm_excel_case.operation == "set_user_custom_data":
                time.sleep(test_config.getfloat(
                    "execution", "state_settle_time", fallback=1.0))
                unhealthy = [
                    alias for alias, driver in runtime.clients.items()
                    if not driver.is_healthy()]
                if unhealthy:
                    names = ", ".join(alias_name(alias) for alias in unhealthy)
                    raise AssertionError(
                        f"6572/6672字段投递完成，但Conf连接不健康: {names}")
        if fm_excel_case.operation in {"create_instant_meeting",
                                       "create_reserved_meeting"}:
            response = result.response if isinstance(result.response, dict) else {}
            if fm_excel_case.operation == "create_instant_meeting":
                data = response.get("data")
                created_room_id = data.get("roomId") if isinstance(data, dict) else None
            else:
                created_room_id = response.get("roomId")
            if created_room_id is None:
                raise AssertionError(
                    f"{fm_excel_case.operation}未返回动态roomId: {response!r}")
            service = runtime.services.get("http_admin")
            if service is None:
                raise AssertionError("HTTP建会成功但Runtime没有http_admin清理服务")
            runtime.register_cleanup(
                service.native_client.delete_room, int(created_room_id))
            runtime.register_cleanup(
                service.native_client.close_meeting, int(created_room_id))
        if allure is not None:
            with report_step("步骤4：展示实际Response和各端实际Notify"):
                if fm_excel_case.closure_type.value == "HTTP_RESULT":
                    attach_json({
                        "数据来源": test_config.get("recording", "data_source", fallback="dynamic"),
                        "会议室ID": runtime.variables.get("operator.room_id"),
                        "groupId": runtime.variables.get("env.record_group_id"),
                        "说明": "预置数据不代表本轮完成录制生成",
                    }, "录制数据来源")
                attach_json(
                    protocol_response_evidence(
                        result.response, result.events,
                        response_expected=(
                            protocol_expectation.response is not None or
                            fm_excel_case.actor in runtime.services or
                            fm_excel_case.closure_type.value == "HTTP_RESULT"),
                        error_code=result.error_code),
                    "实际Response（服务器返回）")
                attach_json(
                    protocol_notify_evidence(result.events),
                    "实际Notify（各端接收）")
            if result.error_code is not None:
                allure.attach(f"0x{result.error_code:04X}", "预期业务错误码")
        if (not result.expected_failure and
                fm_excel_case.actor in runtime.clients and not isolated):
            # 6523返回的分组RoomID由服务器动态分配，必须使用真实返回值结束分组。
            if fm_excel_case.operation == "create_group" and isinstance(result.response, dict):
                room_infos = result.response.get("RoomInfo") or []
                if isinstance(room_infos, dict):
                    room_infos = [room_infos]
                room_ids = [item.get("RoomID") for item in room_infos
                            if isinstance(item, dict) and item.get("RoomID") is not None]
                if not room_ids:
                    raise AssertionError(f"创建分组成功但6623未返回RoomID: {result.response!r}")
                cleanup_marks = runtime.mark_all_events()
                runtime.call(fm_excel_case.actor, "end_groups", {"room_ids": room_ids})
                runtime.assert_operation(
                    "end_groups", fm_excel_case.actor, None, {}, cleanup_marks)
            reset_marks = runtime.mark_all_events()
            if fm_excel_case.operation in {
                    "start_record", "start_aux_stream",
                    "set_user_audio", "set_user_video"}:
                # The Notify can arrive before the meeting state machine has
                # committed the transition. Avoid racing the inverse command.
                time.sleep(test_config.getfloat(
                    "execution", "state_settle_time", fallback=1.0))
            with report_step(
                    f"恢复状态：撤销【{operation_name(fm_excel_case.operation)}】产生的影响"):
                reset_result = runtime.reset_operation(
                    fm_excel_case.operation, fm_excel_case.actor, fm_excel_case.params)
            if reset_result is not None:
                reset_operation, _ = reset_result
                reset_match = {}
                if fm_excel_case.operation == "start_record":
                    reset_match = {"ServerRecordStatus": 0}
                elif fm_excel_case.operation == "start_aux_stream":
                    reset_match = ({"MediaShareState": 0}
                                   if fm_excel_case.params.get("kind", "media") == "media"
                                   else {"VNCState": 0})
                elif fm_excel_case.operation == "set_user_audio":
                    reset_match = {
                        "UserID": runtime.resolve_variables(
                            fm_excel_case.params["user_id"]),
                        "Audio.State": 0,
                    }
                elif fm_excel_case.operation == "set_user_video":
                    reset_match = {
                        "UserID": runtime.resolve_variables(
                            fm_excel_case.params["user_id"]),
                        "Video.State": 0,
                    }
                runtime.assert_operation(
                    reset_operation, fm_excel_case.actor, fm_excel_case.target,
                    reset_match, reset_marks)
        elif (not result.expected_failure and
              fm_excel_case.actor in runtime.services and
              fm_excel_case.operation == "operate_record" and
              fm_excel_case.params.get("state") == 2):
            reset_marks = runtime.mark_all_events()
            reset_params = runtime.resolve_variables({
                "room_id": fm_excel_case.params["room_id"], "state": 0})
            runtime.call_service(
                fm_excel_case.actor, "operate_record", reset_params)
            runtime.assert_operation(
                "operate_record", "operator", fm_excel_case.target,
                {"ServerRecordStatus": 0}, reset_marks)
        # Rights belong to the precondition rather than the main operation,
        # so they must be released for both successful and expected-failure cases.
        if (fm_excel_case.actor in runtime.clients and
                fm_excel_case.expected_outcome.value != 'EXPECTED_DISCONNECT' and
                fm_excel_case.precondition in {"LAYOUT_RIGHT", "POLLING_RIGHT",
                                                "MEDIA_RIGHT", "VNC_RIGHT", "GROUP_RIGHT",
                                                "MEDIA_STARTED_WITH_RIGHT",
                                                "VNC_STARTED_WITH_RIGHT"}):
            right_type = {"LAYOUT_RIGHT": 1, "POLLING_RIGHT": 2,
                          "VNC_RIGHT": 4, "MEDIA_RIGHT": 5,
                          "GROUP_RIGHT": 11,
                          "MEDIA_STARTED_WITH_RIGHT": 5,
                          "VNC_STARTED_WITH_RIGHT": 4}[fm_excel_case.precondition]
            release_marks = runtime.mark_all_events()
            runtime.call(fm_excel_case.actor, "release_right",
                         {"right_type": right_type})
            runtime.assert_operation(
                "release_right", fm_excel_case.actor, None, {}, release_marks,
                response_match={"Result": 0, "Operation": 0,
                                "RightType": right_type})
    except InvitationAccountOnline as exc:
        runtime.dirty = True
        pytest.skip(f'SKIP_ENV: {exc}')
    except MissingSessionManagerApi:
        runtime.dirty = True
        pytest.fail(f"MISSING_SESSIONMANAGER_API: {fm_excel_case.operation}")
    except Exception:
        # A successful HTTP mutation may fail later at the Notify assertion.
        # Always undo record-start before destroying the runtime, otherwise
        # the shared meeting remains recording and contaminates later cases.
        if (fm_excel_case.operation == "operate_record" and
                fm_excel_case.params.get("state") == 2 and
                fm_excel_case.actor in runtime.services):
            try:
                cleanup_params = runtime.resolve_variables({
                    "room_id": fm_excel_case.params["room_id"], "state": 0})
                runtime.call_service(
                    fm_excel_case.actor, "operate_record", cleanup_params)
            except Exception:
                pass
        if fm_excel_case.operation in {"conf_invite_call", "invite_terminals"}:
            try:
                runtime.reset_operation(
                    fm_excel_case.operation, fm_excel_case.actor,
                    runtime.resolve_variables(fm_excel_case.params))
            except Exception:
                pass
        if allure is not None:
            attach_json(
                runtime.snapshot_all_events(marks),
                "失败后各客户端新增事件")
        runtime.dirty = True
        raise
    finally:
        cleanup_started = time.monotonic()
        with report_step("步骤5：释放或归还客户端会话并清理测试资源"):
            try:
                meeting_session_pool.release(key, runtime, isolated=isolated)
            finally:
                attach_json(runtime.cleanup_results, '资源清理逐项结果')
                timing = {
                    "case_id": fm_excel_case.case_id,
                    "recycle_seconds": round(recycle_seconds, 3),
                    "setup_seconds": round(setup_seconds, 3),
                    "operation_assertion_seconds": (
                        round(operation_assertion_seconds, 3)
                        if operation_assertion_seconds is not None else None),
                    "cleanup_seconds": round(time.monotonic() - cleanup_started, 3),
                    "new_initialization": getattr(
                        runtime, "initialization_timing", {}).get("case_id")
                        == fm_excel_case.case_id,
                }
                request.config._case_timings.append(timing)
                attach_json(timing, "本用例各阶段耗时")
