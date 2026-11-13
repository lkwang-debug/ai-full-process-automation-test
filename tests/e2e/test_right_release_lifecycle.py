"""真实服务器：扩展权限取得、释放及无人占用时再次释放。"""
from pathlib import Path
from dataclasses import replace
import pytest

from meeting_test_runtime import ExcelCaseLoader, EnvironmentRisk
from meeting_test_runtime.allure_reporting import (
    actual_call_evidence, allure, attach_json,
    protocol_outbound_evidence, report_step)
from meeting_test_runtime.expectations import get_expectation


@pytest.mark.real_server
@pytest.mark.protocol_e2e
@pytest.mark.p1
@pytest.mark.parametrize('right_type', [13, 99])
def test_release_unoccupied_right(right_type, fm_runtime_factory, meeting_session_pool):
    if allure is not None:
        allure.dynamic.parent_suite('角色、操作权及权限')
        allure.dynamic.suite('权限释放连续流程')
        allure.dynamic.title(f'权限{right_type}：申请→释放→无人占用时再次释放')
    cases = ExcelCaseLoader(Path(__file__).parent / 'data/fm_e2e_cases.xlsx').load_cases()
    case = next(c for c in cases if c.operation == 'request_right'
                and c.params.get('right_type') == right_type)
    _, factory = fm_runtime_factory
    # 同账号不与其他用例的缓存连接并存；本流程独立建会和清理。
    meeting_session_pool.close_all()
    runtime = factory(replace(case, case_id=f'RIGHT-RELEASE-REPEAT-{right_type}',
                              isolated=True, environment_risk=EnvironmentRisk.ISOLATED_STATEFUL))
    try:
        assert runtime.is_healthy(), '入会初始化后连接不健康'
        attach_json({'room_id': runtime.variables['operator.room_id'],
                     'operator': runtime.variables['operator.room_user_id'],
                     'versions': {a: d.conf_protocol_version for a, d in runtime.clients.items()}},
                    '独立会议、操作端与协议版本')
        steps = [
            ('A申请权限', 'request_right', 1, runtime.variables['operator.room_user_id']),
            ('A释放自己持有的权限', 'release_right', 0, 0),
            ('权限已无人占用，A再次释放', 'release_right', 0, 0),
        ]
        for label, operation, value, owner in steps:
            with report_step(label):
                marks = runtime.mark_all_events()
                outbound_marks = runtime.mark_all_outbound()
                expected = {'Result': 0, 'Operation': value, 'RightType': right_type,
                            'RightIndex': 0, 'OccupiedUserID': owner}
                params = {'right_type': right_type}
                try:
                    response = runtime.call('operator', operation, params)
                finally:
                    outbound = runtime.snapshot_all_outbound(outbound_marks)
                    attach_json(actual_call_evidence(
                        outbound, actor='operator', operation=operation,
                        fallback_params=params),
                        '实际公开方法传参（变量已替换）')
                    attach_json(protocol_outbound_evidence(
                        outbound, actor='operator', operation=operation,
                        request_cmdid=get_expectation(operation).request_cmdid),
                        '实际发送协议（请求报文）')
                attach_json(response, '实际公开方法返回')
                events = runtime.assert_operation(operation, 'operator', None, {}, marks,
                                                  response_match=expected)
                attach_json(events, '本步骤游标之后的真实6630及原始XML')
                assert runtime.is_healthy(), '权限操作后A/B连接不健康'
    finally:
        attach_json(runtime.snapshot_all_events({a: 0 for a in runtime.clients}), '流程完整接收证据')
        runtime.close_all()
        attach_json(runtime.cleanup_results, '独立会议资源清理结果')
