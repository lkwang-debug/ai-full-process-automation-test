"""真实终端CallType=2生命周期；不发送电话请求。"""
from .models import EventExpectation
from .allure_reporting import attach_json, report_step


def execute_h323(case, runtime):
    from .case_executor import CaseExecutionResult
    if case.operation == 'invite_terminals_http':
        return http_h323_invitation(case, runtime)
    driver = runtime.clients['operator']
    target = runtime.variables['env.h323']
    events = {}
    if case.case_type.value == 'BUSINESS_EXCEPTION':
        with report_step('挂断未建立呼叫的无效H.323目标，验证真实非零6744'):
            params = runtime.resolve_variables(case.params)
            marks = runtime.mark_all_events()
            result = runtime.call('operator', 'conf_hangup', params)
            attach_json(result, '无效挂断实际返回')
            assert 'OprResult' in result and int(result['OprResult']) != 0
            events['operator:response'] = driver.wait_event(EventExpectation(cmdid=6744, kind='response',
                match={'CallType': 2, 'OprResult': result['OprResult']}), marks['operator'], runtime.event_timeout)
            for alias, d in runtime.clients.items():
                d.expect_no_event(EventExpectation(cmdid=6745, kind='notify', match={
                    'CalleeNum': params['callee_nums'][0], 'CallState': 5}), marks[alias], runtime.quiet_timeout)
            assert runtime.is_healthy()
        return CaseExecutionResult(response=result, events=events, expected_failure=True)
    attempted = False
    hungup = False
    try:
        with report_step('发起H.323呼叫（挂断用例先建立真实呼叫）'):
            marks = runtime.mark_all_events()
            attempted = True
            operation = case.operation
            params = runtime.resolve_variables(case.params)
            if operation == 'conf_hangup':
                operation = 'conf_invite_call'
                params = {'call_type': 2, 'callees': [{'CalleeNum': target, 'CalleeUserID': 0, 'CalleeNickName': 'E2E-H323'}]}
            result = (runtime.call_service(case.actor, operation, params) if case.actor in runtime.services
                      else runtime.call(case.actor, operation, params))
            attach_json(result, '呼叫入口实际返回')
            if operation == 'invite_terminals_http':
                assert str(result.get('code')) == '0', result
            else:
                assert str(result.get('OprResult')) == '0', result
                assert any(str(c.get('CalleeNum')) == target for c in result['ReqCallInfo'])
                events['operator:response'] = driver.wait_event(EventExpectation(cmdid=6742, kind='response',
                    match={'CallType': 2, 'OprResult': 0}), marks['operator'], runtime.event_timeout)
        with report_step('等待实际接通6745并查询呼叫列表'):
            events['operator:notify'] = driver.wait_event(EventExpectation(cmdid=6745, kind='notify',
                match={'CalleeNum': target, 'CallState': 5}), marks['operator'], 30)
            entered = driver.wait_event(EventExpectation(cmdid=6602, kind='notify',
                match={'User.0.UserIP': target, 'User.0.TerminalType': 14}), marks['operator'], runtime.event_timeout)
            users = entered.fields['User']
            if isinstance(users, dict): users = [users]
            terminal_id = next(u['UserID'] for u in users if u.get('UserIP') == target)
            listed = runtime.call('operator', 'conf_query_call_list', {'call_type': 2})
            attach_json(listed, '实际6747呼叫列表')
            assert str(listed.get('OprResult')) == '0'
            calls = listed.get('CallInfo') or listed.get('CalleeInfo') or []
            if isinstance(calls, dict): calls = [calls]
            assert any(str(c.get('CalleeNum')) == target for c in calls), listed
        with report_step('挂断已接通终端，验证6744和真实终端离会6605'):
            marks = runtime.mark_all_events()
            ended = runtime.call('operator', 'conf_hangup', {'call_type': 2, 'callee_nums': [target]})
            attach_json(ended, '实际6744挂断结果')
            assert str(ended.get('OprResult')) == '0', ended
            hungup = True
            events['operator:hangup'] = driver.wait_event(EventExpectation(cmdid=6744, kind='response',
                match={'CallType': 2, 'OprResult': 0}), marks['operator'], runtime.event_timeout)
            events['operator:disconnected'] = driver.wait_event(EventExpectation(cmdid=6605, kind='notify',
                match={'UserID': terminal_id}), marks['operator'], 15)
            assert runtime.is_healthy()
        return CaseExecutionResult(response=result, events=events, expected_failure=True)
    finally:
        if attempted and not hungup and driver.is_healthy():
            try:
                result = runtime.call('operator', 'conf_hangup', {'call_type': 2, 'callee_nums': [target]})
                attach_json(result, '失败路径释放H.323呼叫')
            except Exception as exc:
                attach_json({'error': type(exc).__name__, 'message': str(exc)}, '呼叫清理异常（保留原始断言失败）')


def http_h323_invitation(case, runtime):
    from .case_executor import CaseExecutionResult
    target = runtime.variables['env.h323']
    marks = runtime.mark_all_events()
    with report_step('HTTP邀请H.323终端：验证业务结果'):
        result = runtime.call_service(case.actor, case.operation, runtime.resolve_variables(case.params))
        attach_json(result, '实际HTTP邀请返回')
        assert str(result.get('code')) == '0', result
        data = result.get('data')
        if isinstance(data, dict) and 'result' in data:
            assert str(data['result']) == '0', result
    events = {}
    with report_step('观察端A/B收到真实H.323终端6602；不要求原呼叫者的6745投递到A'):
        ids = []
        for alias in ('operator', 'observer'):
            event = runtime.clients[alias].wait_event(EventExpectation(cmdid=6602, kind='notify',
                match={'User.0.UserIP': target, 'User.0.TerminalType': 14,
                       'User.0.Name': case.params['call_info'][0]['calleeNickname']}), marks[alias], 30)
            users = event.fields['User']
            if isinstance(users, dict): users = [users]
            ids.append(str(next(u['UserID'] for u in users if u.get('UserIP') == target)))
            events[f'{alias}:notify'] = event
        assert ids[0] == ids[1], '两端看到的H.323用户ID不一致'
        assert runtime.is_healthy()
    attach_json({'说明': 'HTTP呼叫记录归后台发起者；清理由独立会议退出/关会完成，不从A调用6743冒充该用例收尾',
                 '独立缺陷': 'H323-CROSS-CALLER-HANGUP：A挂断后台呼叫，实际挂断成功但6744回给原后台用户'}, '验收边界')
    return CaseExecutionResult(response=result, events=events, expected_failure=True)
