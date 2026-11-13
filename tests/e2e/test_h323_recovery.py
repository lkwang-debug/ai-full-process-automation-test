"""用户确认环境恢复后的H.323最小真实链路。"""
from pathlib import Path
from dataclasses import replace
import pytest
from meeting_test_runtime import ExcelCaseLoader, EnvironmentRisk, EventExpectation
from meeting_test_runtime.allure_reporting import (
    actual_call_evidence, attach_json, protocol_outbound_evidence, report_step)
from meeting_test_runtime.expectations import get_expectation


def _attach_outbound(runtime, marks, operation, params):
    outbound = runtime.snapshot_all_outbound(marks)
    attach_json(actual_call_evidence(
        outbound, actor='operator', operation=operation,
        fallback_params=params), '实际公开方法传参（变量已替换）')
    attach_json(protocol_outbound_evidence(
        outbound, actor='operator', operation=operation,
        request_cmdid=get_expectation(operation).request_cmdid),
        '实际发送协议（请求报文）')


@pytest.mark.real_server
def test_h323_recovered_call(fm_runtime_factory):
    case = next(c for c in ExcelCaseLoader(Path(__file__).parent/'data/fm_e2e_cases.xlsx').load_cases(include_disabled=True)
                if c.operation == 'conf_invite_call')
    _, factory = fm_runtime_factory
    runtime = factory(replace(case, isolated=True, environment_risk=EnvironmentRisk.ISOLATED_STATEFUL))
    target = runtime.variables['env.h323']
    marks = runtime.mark_all_events()
    try:
        with report_step('A发起真实H.323呼叫，验证6742成功及目标号码'):
            params = {'call_type': 2, 'callees': [
                {'CalleeNum': target, 'CalleeNickName': 'E2E-H323', 'CalleeUserID': 0}]}
            outbound_marks = runtime.mark_all_outbound()
            try:
                response = runtime.call('operator', 'conf_invite_call', params)
            finally:
                _attach_outbound(runtime, outbound_marks, 'conf_invite_call', params)
            attach_json(response, '实际6742')
            assert str(response.get('OprResult')) == '0', response
            assert str(response.get('CallType')) == '2'
            assert any(str(c.get('CalleeNum')) == target for c in response.get('ReqCallInfo', []))
        with report_step('等待H.323接通状态6745.CallState=5'):
            event = runtime.clients['operator'].wait_event(EventExpectation(cmdid=6745, kind='notify',
                match={'CalleeNum': target, 'CallState': 5}), marks['operator'], 30)
            attach_json(event, '实际接通通知')
        with report_step('挂断并验证6744及连接健康'):
            params = {'call_type': 2, 'callee_nums': [target]}
            outbound_marks = runtime.mark_all_outbound()
            try:
                response = runtime.call('operator', 'conf_hangup', params)
            finally:
                _attach_outbound(runtime, outbound_marks, 'conf_hangup', params)
            attach_json(response, '实际6744')
            assert str(response.get('OprResult')) == '0', response
            assert runtime.is_healthy()
    finally:
        attach_json(runtime.snapshot_all_events(marks), '完整呼叫新增事件')
        runtime.close_all()
