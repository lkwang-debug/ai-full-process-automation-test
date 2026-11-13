from meeting_test_runtime.allure_reporting import attach_json, report_step, protocol_outbound_evidence, actual_call_evidence
from meeting_test_runtime.web_key_scenarios import execute_key_flow

try:
    import allure
except ImportError:
    allure=None


def test_web_key_flow(web_flow,web_runtime):
    if allure:
        allure.dynamic.parent_suite('Web SDK关键流程验收')
        modules={'login':'登录与成员','chat':'群聊','self_av':'本人音视频','mute':'全体静音接收',
                 'role':'角色申请与放弃','info':'用户资料','remote':'远程音视频确认','direct':'远程音视频直接开启',
                 'screen':'桌面共享','whiteboard':'白板共享','record':'云录制与权限',
                 'lock':'锁会提示接收','kick':'被请出','close':'会议关闭接收'}
        allure.dynamic.suite(modules[web_flow['scenario']])
        allure.dynamic.sub_suite(web_flow['combination'])
        params=web_flow['params']; details=[]
        if 'kind' in params: details.append('音频' if params['kind']=='audio' else '视频')
        if 'accept' in params: details.append('接受' if params['accept'] else '拒绝')
        if 'role' in params: details.append({'presenter':'仅主持人','manager':'仅管理员','attendee':'普通成员'}[params['role']])
        allure.dynamic.title(web_flow['name']+'（'+web_flow['combination']+('，'+'、'.join(details) if details else '')+'）')
        allure.dynamic.parameter('用例ID',web_flow['run_id'])
    runtime=web_runtime
    attach_json({alias:driver.snapshot_events(0) for alias,driver in runtime.clients.items()},'实际初始化消息及角色前置')
    marks=runtime.mark_all_events(); outgoing=runtime.mark_all_outbound()
    try:
        with report_step('执行真实公开方法并核对关键流程消息、字段和连接'):
            execute_key_flow(web_flow,runtime)
    finally:
        attach_json(runtime.snapshot_all_events(marks),'各端实际新增消息（Web JSON、FM XML及PaaS分开）')
        outbound=runtime.snapshot_all_outbound(outgoing)
        attach_json(outbound,'实际公开方法参数与发送轨迹')
        attach_json(actual_call_evidence(outbound,actor='operator',operation=web_flow['scenario'],
                    fallback_params=web_flow['params']),'实际Python与SDK公开方法传参')
        attach_json(protocol_outbound_evidence(outbound,actor='operator',operation=web_flow['scenario'],request_cmdid=None),
                    '实际发送协议')
