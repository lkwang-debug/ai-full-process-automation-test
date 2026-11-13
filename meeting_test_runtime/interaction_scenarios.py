"""Multi-step interactive protocol scenarios using only public SDK methods."""
from __future__ import annotations

import time

from .allure_reporting import attach_json, report_step
from .models import EventExpectation


def _event(runtime, alias, mark, cmdid, match):
    return runtime.clients[alias].wait_event(
        EventExpectation(cmdid=cmdid, kind="notify", match=match),
        mark, runtime.event_timeout)


def execute_remote_av_consent(case, runtime):
    from .case_executor import CaseExecutionResult

    kind = str(case.params["kind"])
    accept = bool(case.params["accept"])
    key = "Video" if kind == "video" else "Audio"
    dev_id = int(case.params.get("device_id", 1 if kind == "video" else 0))
    a_id = runtime.variables["operator.room_user_id"]
    b_id = runtime.variables["observer.room_user_id"]
    devices = {"video_ids" if kind == "video" else "audio_ids": [dev_id]}

    with report_step("步骤3.1：关闭强制音视频控制，并准备目标端设备和关闭状态"):
        runtime.call("operator", "set_conf_state", {
            "enable_force_operate_user_av": 0})
        runtime.call("operator", "get_user_features", {"user_id": a_id})
        runtime.call("observer", "set_video_state" if kind == "video" else
                     "set_audio_state", {"state": 0, "dev_id": dev_id})
        mark = runtime.clients["operator"].mark_events()
        runtime.call("observer", "report_av_info", {
            kind: [{"Operation": 1, "ID": dev_id,
                    "Name": f"e2e-remote-{kind}"}]})
        _event(runtime, "operator", mark, 6608, {
            "UserID": b_id, f"{key}.0.ID": dev_id})

    with report_step("步骤3.2：A发送6542，目标端B收到6642且字段匹配"):
        marks = runtime.mark_all_events()
        runtime.call("operator", "request_remote_av", {
            "dst_user_id": b_id, **devices})
        request = _event(runtime, "observer", marks["observer"], 6642, {
            "SrcUserID": a_id, "DstUserID": b_id, "Type": 0,
            f"{key}.0.ID": dev_id})

    with report_step("步骤3.3：B按实际6642请求同意或拒绝，A收到6643结果"):
        runtime.call("observer", "respond_remote_av", {
            "request": request.fields, "accept": accept})
        result = _event(runtime, "operator", marks["operator"], 6643, {
            "SrcUserID": b_id, "DstUserID": a_id, "Type": 0,
            "Result": 0 if accept else 1, f"{key}.0.ID": dev_id})

    state_expectation = EventExpectation(cmdid=6609, kind="notify", match={
        "UserID": b_id, f"{key}.ID": dev_id, f"{key}.State": 2})
    with report_step("步骤3.4：按同意或拒绝结果验证6609，并检查A/B连接健康"):
        states = {}
        if accept:
            for alias in ("operator", "observer"):
                states[alias] = runtime.clients[alias].wait_event(
                    state_expectation, marks[alias], runtime.event_timeout)
        else:
            for alias in ("operator", "observer"):
                runtime.clients[alias].expect_no_event(
                    state_expectation, marks[alias], runtime.quiet_timeout)
        assert runtime.is_healthy()
        attach_json({"kind": kind, "accepted": accept, "request": request.fields,
                     "result": result.fields}, "远程音视频确认链核心字段")
    return CaseExecutionResult(response={"Result": 0}, events={
        "target_request": request, "requester_result": result, **states})


def execute_web_notify(case, runtime):
    from .case_executor import CaseExecutionResult

    params = runtime.resolve_variables(case.params)
    marks = runtime.mark_all_events()
    with report_step("步骤3.1：后台调用公开HTTP接口向观察端B发送Web通知"):
        response = runtime.call_service("http_admin", "send_room_message", params)
        assert str(response.get("code", 0)) == "0", response
        data = response.get("data")
        if isinstance(data, dict):
            assert str(data.get("result", 0)) == "0" and not data.get("errors"), response
    match = {
        "UserID": runtime.variables["observer.room_user_id"],
        "operator": params["operator"],
        "appBackNotify": params["app_back_notify"],
    }
    with report_step("步骤3.2：B收到6654，A不收到同一条定向通知"):
        event = _event(runtime, "observer", marks["observer"], 6654, match)
        runtime.clients["operator"].expect_no_event(
            EventExpectation(cmdid=6654, kind="notify", match=match),
            marks["operator"], runtime.quiet_timeout)
        assert runtime.is_healthy()
    return CaseExecutionResult(response=response, events={"observer": event})


def execute_record_error_6637(runtime):
    """Start recording in per-user mode and verify directed 6637 then 6610."""
    from .case_executor import CaseExecutionResult

    marks = runtime.mark_all_events()
    with report_step("步骤3.1：A调用公开开始录制方法，触发按用户录制模式错误"):
        runtime.call("operator", "start_record", {})
    with report_step("步骤3.2：A先收到6637原始录制错误，再收到6610状态错误"):
        error = _event(runtime, "operator", marks["operator"], 6637, {
            "RecordType": 4, "Error": 12801})
        state = _event(runtime, "operator", marks["operator"], 6610, {
            "ServerRecordStatus": 0, "Error": 12801,
            "SrcUserID": runtime.variables["operator.room_user_id"]})
        assert error.event_id < state.event_id, "6637必须先于6610"
        assert error.fields.get("Description"), "6637必须包含错误描述"
    with report_step("步骤3.3：错误为定向消息，B不接收，并确认A/B连接健康"):
        for cmdid, match in (
                (6637, {"RecordType": 4, "Error": 12801}),
                (6610, {"Error": 12801})):
            runtime.clients["observer"].expect_no_event(
                EventExpectation(cmdid=cmdid, kind="notify", match=match),
                marks["observer"], runtime.quiet_timeout)
        assert runtime.is_healthy()
        attach_json({"6637": error.fields, "6610": state.fields,
                     "order": [error.cmdid, state.cmdid]},
                    "录制异常原始错误与状态通知")
    return CaseExecutionResult(response={"Result": 0}, events={
        "record_error": error, "record_state": state})


def execute_chat_review(case, runtime):
    """Reject one pending public chat and optionally retry the consumed item."""
    from .case_executor import CaseExecutionResult

    a_id = runtime.variables["operator.room_user_id"]
    b_id = runtime.variables["observer.room_user_id"]
    with report_step("步骤3.1：配置A为审批人并开启公共聊天审批"):
        runtime.call("operator", "set_user_permissions", {
            "user_id": a_id,
            "permissions": {"CheckChat": 1, "ConfigChatCheck": 1}})
        runtime.call("operator", "set_user_permissions", {
            "user_id": b_id,
            "permissions": {"CheckChat": 0, "OperatePublicChat": 1}})
        runtime.call("operator", "set_chat_check", {"enabled": False})
        runtime.call("operator", "get_user_features", {"user_id": a_id})
        mark = runtime.clients["operator"].mark_events()
        runtime.call("operator", "set_chat_check", {"enabled": True})
        _event(runtime, "operator", mark, 6610, {"EnableChatMsgCheck": 1})

    text = f"chat-review-{time.time_ns()} 中文<&>"
    marks = runtime.mark_all_events()
    with report_step("步骤3.2：B发送公共聊天，A收到带SerialNumber的待审批6612"):
        runtime.call("observer", "chat", {"text": text})
        pending = _event(runtime, "operator", marks["operator"], 6612, {
            "SrcUserID": b_id, "DstUserID": 0, "Text": text,
            "NeedCheck": 1})

    with report_step("步骤3.3：A拒绝审批并收到6629，B不收到聊天正文"):
        rejected = runtime.call("operator", "review_chat_message_and_wait", {
            "message": pending.fields, "approved": False,
            "timeout": runtime.event_timeout})
        assert str(rejected.get("SerialNumber")) == str(
            pending.fields["SerialNumber"])
        assert str(rejected.get("CheckResult")) == "0"
        assert str(rejected.get("CheckUserID")) == str(a_id)
        runtime.clients["observer"].expect_no_event(
            EventExpectation(cmdid=6612, kind="notify", match={
                "SrcUserID": b_id, "Text": text}),
            marks["observer"], runtime.quiet_timeout)

    events = {"pending": pending, "rejection": rejected}
    if case.params.get("repeat_after_reject"):
        with report_step("步骤3.4：对已消费SerialNumber再次同意，服务器静默忽略且不发布正文"):
            repeat_marks = runtime.mark_all_events()
            runtime.call("operator", "review_chat_message", {
                "message": pending.fields, "approved": True})
            runtime.clients["operator"].expect_no_event(
                EventExpectation(cmdid=6629, kind="notify", match={
                    "SerialNumber": pending.fields["SerialNumber"]}),
                repeat_marks["operator"], runtime.quiet_timeout)
            runtime.clients["observer"].expect_no_event(
                EventExpectation(cmdid=6612, kind="notify", match={
                    "SrcUserID": b_id, "Text": text}),
                repeat_marks["observer"], runtime.quiet_timeout)
    assert runtime.is_healthy()
    attach_json({"pending": pending.fields, "rejection": rejected,
                 "repeat_after_reject": bool(case.params.get(
                     "repeat_after_reject"))}, "聊天审批拒绝核心字段")
    return CaseExecutionResult(response=rejected, events=events)
