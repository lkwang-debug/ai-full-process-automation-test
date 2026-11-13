"""Second-stage Excel entry; initial key cases remain a separate gate."""
from meeting_test_runtime.web_full_cases import MODULES
from meeting_test_runtime.web_full_scenarios import execute_full_case
from meeting_test_runtime.allure_reporting import attach_json, report_step
import allure


def test_web_full_case(web_full_case,web_full_runtime,request):
    case=web_full_case;runtime=web_full_runtime
    allure.dynamic.parent_suite('Web SDK全量协议闭环')
    allure.dynamic.suite(MODULES[case.scenario])
    allure.dynamic.sub_suite(case.combination+' / '+case.template+' / '+','.join(case.identities))
    allure.dynamic.title(case.name+'（'+case.combination+'，'+','.join(case.identities)+'）')
    allure.dynamic.id(case.case_id)
    allure.dynamic.parameter('用例ID',case.case_id)
    attach_json({'case_id':case.case_id,'scenario':case.scenario,'identities':case.identities,'template':case.template,
        'role':case.params.get('role'),'parameters':case.params,'evidence':case.evidence,
        'source_fm_cases':case.source_ids,'connection_state':{a:c.is_healthy() for a,c in runtime.clients.items()}},'用例目的、角色、身份与证据口径')
    attach_json(runtime.snapshot_all_events({}),'实际初始化和权限前置（未改写原始字段）')
    marks=runtime.mark_all_events();out=runtime.mark_all_outbound()
    request.node.web_execution_failed=True
    try:
        with report_step('执行公开业务链，核对本次消息、字段、接收端和非目标端'):
            execute_full_case(case,runtime)
        request.node.web_execution_failed=False
    finally:
        attach_json(runtime.snapshot_all_outbound(out),'全部实际公开方法与发送轨迹')
        attach_json(runtime.snapshot_all_events(marks),'全部新增Response、Notify及PaaS原始消息')
