"""Waiting-room rejection and mixed decisions through public TestClient calls."""
from .allure_reporting import attach_json, report_step
from .models import EventExpectation


def verify_waiting_route(fields):
    assert int(fields.get('RoomID') or 0) > 0, '6667缺少等候室RoomID'
    for key in ('RoomSrvNodeID', 'SrvAddr', 'SrvAuthCode'):
        assert fields.get(key), f'6667缺少可用的{key}'


def _users(response):
    values = response.get('UserList', {}).get('UserInfo', [])
    return [values] if isinstance(values, dict) else values


def execute_waiting_review(case, runtime):
    from .case_executor import CaseExecutionResult

    mixed = case.params['decision'] == 'mixed'
    aliases = ('observer', 'witness') if mixed else ('observer',)
    room_id = runtime.variables['operator.room_id']
    uids = {alias: runtime.variables[f'{alias}.room_user_id'] for alias in aliases}
    children = []
    waiting_enabled = False
    right_held = False
    results = {}
    try:
        with report_step('B和C退出主会场，重新完成Front登录，准备待审批入会'):
            for alias in aliases:
                runtime.call(alias, 'conf_logout', {})
                runtime.replace_client(alias)
                runtime.call(alias, 'front_login', {'room_id': room_id})

        with report_step('A取得等候室操作权，开启等候室并连接管理子会场'):
            right = runtime.call('operator', 'request_right', {'right_type': 12})
            assert str(right.get('Result')) == '0', right
            right_held = True
            mark = runtime.clients['operator'].mark_events()
            runtime.call('operator', 'set_conf_state', {'enable_waiting_room': 1})
            waiting_enabled = True
            route = runtime.clients['operator'].wait_event(
                EventExpectation(cmdid=6624, kind='notify'), mark, runtime.event_timeout)
            runtime.join_related('operator', 'waiting_manager', route.fields)
            children.append('waiting_manager')

        with report_step('待审批用户收到各自6667路由，并进入对应等候室子会场'):
            marks = runtime.mark_all_events()
            for alias in aliases:
                handle = f'waiting_{alias}'
                runtime.start_async_call(handle, alias, 'conf_login',
                                         {'timeout': runtime.event_timeout * 2})
                waiting = runtime.clients[alias].wait_event(
                    EventExpectation(cmdid=6667, kind='notify'),
                    runtime.async_call_state(handle)['marks'][alias], runtime.event_timeout)
                verify_waiting_route(waiting.fields)
                runtime.join_related(alias, f'{alias}_waiting', waiting.fields)
                children.append(f'{alias}_waiting')
                results[f'{alias}:route'] = {
                    'cmdid': 6667, 'RoomID': waiting.fields['RoomID'],
                    'RoomSrvNodeID': waiting.fields['RoomSrvNodeID']}
            runtime.clients['operator'].expect_no_event(
                EventExpectation(cmdid=6667, kind='notify'),
                marks['operator'], runtime.quiet_timeout)

        with report_step('管理端同批拒绝B、允许C，并逐用户核对6670结果'):
            decisions = [{'UserID': uids['observer'], 'Allow': 0}]
            if mixed:
                decisions.append({'UserID': uids['witness'], 'Allow': 1})
            mark = runtime.clients['waiting_manager'].mark_events()
            response = runtime.call('waiting_manager', 'allow_waiting_users',
                                    {'users': decisions})
            actual = _users(response)
            assert len(actual) == len(decisions), response
            assert {str(item['UserID']): str(item['Result']) for item in actual} == {
                str(item['UserID']): '0' for item in decisions}, response
            results['manager:response'] = runtime.clients['waiting_manager'].wait_event(
                EventExpectation(cmdid=6670, kind='response'), mark, runtime.event_timeout)

        with report_step('B不得成功入主会；允许C时A必须收到C的6602'):
            runtime.clients['operator'].expect_no_event(
                EventExpectation(cmdid=6602, kind='notify',
                                 match={'User.0.UserID': uids['observer']}),
                marks['operator'], runtime.quiet_timeout)
            runtime.clients['observer'].expect_no_event(
                EventExpectation(cmdid=6601, kind='response', match={'Result': 0}),
                marks['observer'], runtime.quiet_timeout)
            if mixed:
                login = runtime.wait_async_call('waiting_witness', runtime.event_timeout)
                assert str(login.get('Result')) == '0', login
                results['operator:allowed'] = runtime.clients['operator'].wait_event(
                    EventExpectation(cmdid=6602, kind='notify',
                                     match={'User.0.UserID': uids['witness']}),
                    marks['operator'], runtime.event_timeout)
                assert runtime.clients['witness'].is_healthy()
            assert runtime.clients['operator'].is_healthy()
            attach_json({'decisions': decisions, '6670': response,
                         'observer_6602': 'absent',
                         'witness_6602': 'received' if mixed else 'not applicable'},
                        '等候室拒绝和混合审批结果')
        return CaseExecutionResult(response=response, events=results, expected_failure=True)
    finally:
        # Close the rejected pending login before disabling the waiting room.
        runtime.clients['observer'].close()
        runtime.dirty = True
        for alias in reversed(children):
            child = runtime.clients.pop(alias, None)
            if child is not None:
                child.close()
        if waiting_enabled:
            runtime.call('operator', 'set_conf_state', {'enable_waiting_room': 0})
        if right_held:
            runtime.call('operator', 'release_right', {'right_type': 12})
