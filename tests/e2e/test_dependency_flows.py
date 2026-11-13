"""Real-server execution for reviewed multi-step protocol state machines."""
from __future__ import annotations

import pytest

from meeting_test_runtime import (
    CaseType, ClosureType, DependencyFlowExecutor, EnvironmentRisk,
    ExpectedOutcome, FunctionalCase, RecordingDataUnavailable, SessionKey,
)
from meeting_test_runtime.allure_reporting import (
    actual_call_evidence, attach_json, configure_flow,
    protocol_outbound_evidence, report_step, allure,
)


def _attach_direct_http(api, mark, operation, params):
    records = []
    for item in api.snapshot_requests(after=mark):
        records.append({**item, "trace_type": "http_request", "operation": operation})
    outbound = {"http_admin": records}
    attach_json(actual_call_evidence(
        outbound, actor="http_admin", operation=operation,
        fallback_params=params), "实际公开方法传参（变量已替换）")
    attach_json(protocol_outbound_evidence(
        outbound, actor="http_admin", operation=operation,
        request_cmdid=None), "实际发送协议（请求报文）")


def _runtime_case(flow):
    """Build the minimal case descriptor required by the shared runtime factory."""
    return FunctionalCase(
        case_id=flow.flow_id, module=flow.name, case_type=CaseType.NORMAL,
        scene_type="DEPENDENCY_FLOW", precondition="NONE", actor="operator",
        target="observer", operation=flow.steps[0].operation, params={},
        expected_business=flow.name, expected_fields={},
        expected_outcome=ExpectedOutcome.EVENT_SEQUENCE, isolated=False,
        priority=flow.priority, closure_type=ClosureType.FM_TO_FM,
        environment_risk=flow.environment_risk,
    )


def _best_effort_cleanup(flow_id, runtime):
    """Failure cleanup uses public methods and never hides the original failure."""
    if "operator" not in runtime.clients:
        return
    if flow_id == "FLOW-LAYOUT-SWITCH":
        operations = [
            ("set_window_state", {"mode": 3, "video_relay_id": 0}),
            ("release_right", {"right_type": 1}),
        ]
    elif flow_id == "FLOW-POLLING-LIFECYCLE":
        operations = [
            ("configure_polling", {"operation": 3, "polling_id": 1001,
                                   "name": "E2E状态机轮询", "user_ids": []}),
            ("release_right", {"right_type": 2}),
        ]
    elif flow_id == "FLOW-DEVICE-AV":
        operations = [
            ("mic_off", {}), ("camera_off", {}),
            ("report_av_info", {
                "audio": [{"Operation": 3, "ID": 0, "Name": "flow-mic"}],
                "video": [{"Operation": 3, "ID": 1,
                           "Name": "flow-camera-main", "Type": 0}],
            }),
        ]
    elif flow_id == "FLOW-GROUP-LIFECYCLE":
        room_id = runtime.variables.get("group.room_id")
        operations = []
        if room_id is not None:
            operations.append(("end_groups", {"room_ids": [room_id]}))
        operations.append(("release_right", {"right_type": 11}))
    elif flow_id == "FLOW-RIGHT-TRANSFER":
        operations = [
            ("release_right", {"right_type": 1}),
        ]
        if "observer" in runtime.clients:
            try:
                runtime.call("observer", "release_right", {"right_type": 1})
            except Exception:
                pass
    elif flow_id == "FLOW-AUX-CONTENTION":
        operations = [
            ("stop_aux_stream", {"kind": "media"}),
            ("release_right", {"right_type": 5}),
        ]
    else:
        operations = []
    for operation, params in operations:
        try:
            runtime.call("operator", operation, params)
        except Exception:
            runtime.dirty = True


@pytest.mark.real_server
@pytest.mark.protocol_e2e
def test_dependency_flow(
    fm_dependency_flow, meeting_session_pool, fm_runtime_factory, test_config,
    recording_dataset_provider,
):
    configure_flow(fm_dependency_flow)
    if (fm_dependency_flow.environment_risk is EnvironmentRisk.RISKY_MANUAL and
            not test_config.getbool(
                "execution", "run_risky_protocol_probes", fallback=False)):
        pytest.skip("高风险流程默认禁用，需要独立会议和人工批准")
    if fm_dependency_flow.flow_id == "FLOW-RECORD-RESULT":
        if recording_dataset_provider is None:
            pytest.skip("动态录制数据未启用或HTTP配置不完整")
        try:
            prepared = recording_dataset_provider.data_source == "prepared"
            if prepared and allure is not None:
                allure.dynamic.title("预置录制：查询纪要并导出字幕（不含录制生成验证）")
                allure.dynamic.description("复用预置会议记录验证会后结果；本次不验证录制、转写和纪要生成状态机。")
            with report_step("步骤1：查询预置会议录制记录" if prepared else
                             "步骤1：动态创建会议并完成真实录制"):
                api_mark = recording_dataset_provider.api.mark_requests()
                try:
                    dataset = recording_dataset_provider.get()
                finally:
                    _attach_direct_http(
                        recording_dataset_provider.api, api_mark,
                        "prepare_recording_dataset",
                        {"data_source": recording_dataset_provider.data_source})
        except RecordingDataUnavailable as exc:
            pytest.skip(f"当前环境未生成录制纪要数据: {exc}")
        if not prepared:
            assert dataset.state_transitions == (2, 1, 2, 0)
        with report_step("步骤2：使用查询取得的groupId读取录制摘要"):
            api_mark = recording_dataset_provider.api.mark_requests()
            try:
                detail = recording_dataset_provider.api.get_record_summary(dataset.group_id)
            finally:
                _attach_direct_http(
                    recording_dataset_provider.api, api_mark,
                    "get_record_summary", {"group_id": dataset.group_id})
        from meeting_test_runtime.http_result_assertions import validate_result,load_export_reference
        title=dataset.summary.get('roomName','')
        validate_result('get_record_summary',detail,{'group_id':dataset.group_id},title)
        with report_step('建立同groupId、roomId的独立纪要内容基准'):
            api_mark=recording_dataset_provider.api.mark_requests()
            try:
                file_reference=load_export_reference(recording_dataset_provider.api,
                    'export_meeting_summary',dataset.group_id,dataset.room_id)
                import hashlib
                attach_json({'group_id':file_reference.group_id,'room_id':file_reference.room_id,
                    'reference_method':file_reference.source_operation,'size_bytes':len(file_reference.content),
                    'sha256':hashlib.sha256(file_reference.content).hexdigest()},'纪要文件独立基准')
            finally:
                _attach_direct_http(recording_dataset_provider.api,api_mark,'load_export_reference',
                    {'group_id':dataset.group_id,'room_id':dataset.room_id})
        with report_step("步骤3：导出会议纪要文件"):
            api_mark = recording_dataset_provider.api.mark_requests()
            try:
                summary_file = recording_dataset_provider.api.export_meeting_summary(dataset.group_id)
            finally:
                _attach_direct_http(
                    recording_dataset_provider.api, api_mark,
                    "export_meeting_summary", {"group_id": dataset.group_id})
        with report_step("步骤4：导出会议字幕文件"):
            api_mark = recording_dataset_provider.api.mark_requests()
            try:
                transcript_file = recording_dataset_provider.api.export_transcript(dataset.group_id)
            finally:
                _attach_direct_http(
                    recording_dataset_provider.api, api_mark,
                    "export_transcript", {"group_id": dataset.group_id})
        validate_result('export_meeting_summary',summary_file,{'group_id':dataset.group_id,'room_id':dataset.room_id},title,
                        file_reference=file_reference)
        validate_result('export_transcript',transcript_file,{},title)
        attach_json({"录制数据": dataset, "摘要": detail}, "录制与纪要验证结果")
        return
    room_id, build_for_case = fm_runtime_factory
    runtime_case = _runtime_case(fm_dependency_flow)
    key = SessionKey(
        f"DEPENDENCY_FLOW:{fm_dependency_flow.flow_id}", "fm->fm", room_id,
        ("operator", "observer"),
    )
    isolated = fm_dependency_flow.environment_risk in {
        EnvironmentRisk.ISOLATED_STATEFUL,
        EnvironmentRisk.DESTRUCTIVE_ISOLATED, EnvironmentRisk.RISKY_MANUAL}
    invalidated = meeting_session_pool.invalidate_conflicts(key, include_exact=False)
    if invalidated:
        import time
        time.sleep(test_config.getfloat(
            "execution", "session_recycle_time", fallback=2.0))
    with report_step("准备流程场景：创建客户端并登录动态测试会议"):
        runtime = meeting_session_pool.acquire(
            key, lambda: build_for_case(runtime_case), isolated=isolated)
    try:
        results = DependencyFlowExecutor().execute(fm_dependency_flow, runtime)
        assert runtime.is_healthy(), f"依赖流程结束后连接不健康: {fm_dependency_flow.flow_id}"
        attach_json(results, "连续流程实际执行结果")
    except Exception:
        attach_json(
            runtime.snapshot_all_events({alias: 0 for alias in runtime.clients}),
            "流程失败后各客户端全部事件")
        _best_effort_cleanup(fm_dependency_flow.flow_id, runtime)
        runtime.dirty = True
        raise
    finally:
        if fm_dependency_flow.flow_id in {"FLOW-HTTP-CREATE-JOIN", "FLOW-LOCK-JOIN"}:
            room_id = runtime.variables.get("meeting.room_id")
            service = runtime.services.get("http_admin")
            if room_id is not None and service is not None:
                runtime.register_cleanup(service.native_client.delete_room, room_id)
                runtime.register_cleanup(service.native_client.close_meeting, room_id)
        with report_step("完成流程：释放会话并清理动态会议资源"):
            try: meeting_session_pool.release(key, runtime, isolated=isolated)
            finally: attach_json(runtime.cleanup_results, '流程清理逐项结果')
