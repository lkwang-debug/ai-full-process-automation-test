"""需要实际服务器前置消息的协议链；仅调用Driver公开业务方法。"""
from .models import EventExpectation
from .allure_reporting import report_step, attach_json


def assert_no_target_enter(snapshots, target):
    for alias, events in snapshots.items():
        for event in events:
            if event['cmdid'] != 6602: continue
            users = event.get('fields', {}).get('User', [])
            if isinstance(users, dict): users = [users]
            if any(str(user.get('UserID')) == str(target) for user in users):
                raise AssertionError(f'{alias}收到无效目标{target}的入会6602')


def ignore_absent_knock(case, runtime):
    from .case_executor import CaseExecutionResult
    assert case.isolated and runtime.is_healthy(), '必须使用健康的独立会议'
    params = runtime.resolve_variables(case.params)
    target = str(params['user_id'])
    assert target not in {str(d.identity['room_user_id']) for d in runtime.clients.values()}
    initial = runtime.snapshot_all_events({a: 0 for a in runtime.clients})
    assert_no_target_enter(initial, target)
    # SDK无待审批集合查询；在新建受控会议中确认未产生任何敲门请求。
    assert not any(e['cmdid'] == 6604 for es in initial.values() for e in es), '存在敲门事件，空队列前置不成立'
    attach_json({'目标用户': target, '前置': '动态新会议，测试只创建A/B，不为目标发起登录或敲门',
                 '历史6604数量': 0, '集合确认方式': '受控场景构造与完整接收历史；非服务器集合查询'}, '无待审批目标的前置证据')
    marks = runtime.mark_all_events()
    with report_step('发送6504允许不存在的待审批用户，不等待同步回复'):
        runtime.call(case.actor, 'allow_knock', params)
        for alias, driver in runtime.clients.items():
            driver.expect_no_event(EventExpectation(cmdid=6602, kind='notify',
                match={'User.0.UserID': target}), marks[alias], runtime.event_timeout)
        assert runtime.is_healthy(), '忽略无效敲门对象后连接不健康'
        assert_no_target_enter(runtime.snapshot_all_events(marks), target)
        attach_json(runtime.snapshot_all_events(marks), '6504后观察期内的全部消息')
    events = {}
    with report_step('A/B分别查询自身能力，验证后续正常请求仍可用'):
        for alias, driver in runtime.clients.items():
            uid = driver.identity['room_user_id']
            mark = driver.mark_events()
            response = runtime.call(alias, 'get_user_features', {'user_id': uid})
            assert str(response['UserID']) == str(uid)
            events[f'{alias}:response'] = driver.wait_event(EventExpectation(cmdid=6644, kind='response',
                match={'UserID': uid}), mark, runtime.event_timeout)
        assert runtime.is_healthy(), '能力查询后连接不健康'
        assert_no_target_enter(runtime.snapshot_all_events(marks), target)
    return CaseExecutionResult(events=events, expected_failure=True)


def assert_no_mode_notification(snapshots):
    # 只限制Mode字段，其他字段产生的6610不属于此断言的失败。
    for alias, events in snapshots.items():
        for event in events:
            if event.get('cmdid') == 6610 and 'Mode' in event.get('fields', {}):
                raise AssertionError(f'{alias}收到不应产生的Mode通知: {event["fields"]}')


def ignored_room_mode(case, runtime):
    import time
    from .case_executor import CaseExecutionResult
    before = {}
    for alias in ('operator', 'observer'):
        logins = [e for e in runtime.clients[alias].snapshot_events(0)
                  if e['cmdid'] == 6601 and 'Mode' in e['fields'].get('RoomInfo', {})]
        assert logins, f'{alias}初始化缺少6601.RoomInfo.Mode'
        before[alias] = logins[-1]['fields']['RoomInfo']['Mode']
    assert str(before['operator']) == str(before['observer']), 'A/B初始模式不一致'
    room_id = runtime.variables['observer.room_id']
    marks = runtime.mark_all_events()
    with report_step('发送仅含Mode的6510，验证观察期内没有Mode字段通知'):
        runtime.call(case.actor, 'set_conf_state', runtime.resolve_variables(case.params))
        deadline = time.monotonic() + runtime.event_timeout
        while True:
            assert_no_mode_notification(runtime.snapshot_all_events(marks))
            assert runtime.is_healthy(), 'Mode操作后A/B连接不健康'
            if time.monotonic() >= deadline: break
            time.sleep(0.1)
        attach_json(runtime.snapshot_all_events(marks), '观察期新增事件（允许其他状态通知）')
    with report_step('B重新登录同一独立会议，通过新的6601核对模式未改变'):
        runtime.call('observer', 'conf_logout', {})
        runtime.replace_client('observer')
        login = runtime.join('observer', room_id)
        after = login['conf']['RoomInfo']['Mode']
        assert str(after) == str(before['operator']), f'会议Mode发生变化: {before} -> {after}'
        assert runtime.is_healthy(), '回读模式后A/B连接不健康'
        attach_json({'发送Mode': case.params['mode'], '初始Mode': before,
                     '重新登录6601的Mode': after}, '真实模式回读（不是6510同步Response）')
    return CaseExecutionResult(events={}, expected_failure=True)


def ignored_right(case, runtime):
    from .case_executor import CaseExecutionResult
    marks = runtime.mark_all_events()
    assert runtime.is_healthy(), '操作前连接不健康'
    params = runtime.resolve_variables(case.params)
    params['timeout'] = runtime.event_timeout
    with report_step('发送不支持的RightType；按Operation和RightType匹配无回复断言'):
        try:
            response = runtime.call(case.actor, case.operation, params)
        except TimeoutError:
            # SDK公开请求方法等待到期；只有同时证实无匹配消息和连接健康才通过。
            response = None
        else:
            raise AssertionError(f'应忽略RightType，但公开方法收到了响应: {response!r}')
        expected = EventExpectation(cmdid=6630, kind=None,
                                    match=runtime.resolve_variables(case.expected_fields))
        for alias, driver in runtime.clients.items():
            driver.expect_no_event(expected, marks[alias], runtime.quiet_timeout)
        assert runtime.is_healthy(), '忽略权限类型后A/B连接不健康'
        attach_json(runtime.snapshot_all_events(marks), '权限请求后的全部事件（排除初始化旧消息）')
    return CaseExecutionResult(response=response, events={}, expected_failure=True)


def front_logout(runtime):
    from .case_executor import CaseExecutionResult
    mark = runtime.clients['operator'].mark_events()
    with report_step('调用Front注销，逐条验证真实30009和30011，而非方法返回None'):
        runtime.call('operator', 'front_logout', {})
        events = {}
        for cmdid in (30009, 30011):
            events[f'operator:response:{cmdid}'] = runtime.clients['operator'].wait_event(
                EventExpectation(source='front', cmdid=cmdid, kind='response', match={'Result': 0}),
                mark, runtime.event_timeout)
        return CaseExecutionResult(events=events, expected_failure=True)


def unsubscribe(runtime):
    from .case_executor import CaseExecutionResult
    a = runtime.clients['operator']
    room_id = runtime.variables['observer.room_id']
    feature = 'NewVote'
    with report_step('先订阅NewVote并验证成员重新入会确实触发订阅更新'):
        initial = runtime.call('operator', 'query_feature_users', {'feature': feature, 'subscribe': True})
        assert str(runtime.variables['observer.room_user_id']) in initial['UserIDs']
        mark = a.mark_events()
        runtime.call('observer', 'conf_logout', {})
        runtime.replace_client('observer')
        runtime.join('observer', room_id)
        update = a.wait_event(EventExpectation(cmdid=6645, kind=None, match={'Feature': feature}), mark, runtime.event_timeout)
    with report_step('取消订阅，再让成员离会；6605存在但不能再有6645更新'):
        runtime.call('operator', 'unsubscribe_feature_users', {'feature': feature})
        # 同连接的查询用于确认取消命令之后服务器已完成一次应答。
        runtime.call('operator', 'get_user_features', {})
        marks = runtime.mark_all_events()
        uid = runtime.variables['observer.room_user_id']
        runtime.call('observer', 'conf_logout', {})
        left = a.wait_event(EventExpectation(cmdid=6605, kind='notify', match={'UserID': uid}), marks['operator'], runtime.event_timeout)
        runtime.replace_client('observer')
        runtime.join('observer', room_id)
        a.expect_no_event(EventExpectation(cmdid=6645, match={'Feature': feature}, kind=None), marks['operator'], runtime.quiet_timeout)
        assert runtime.is_healthy()
    return CaseExecutionResult(response=initial, events={'operator:notify:0': update, 'operator:notify:1': left}, expected_failure=True)


def waiting_admission(runtime):
    from .case_executor import CaseExecutionResult
    room = runtime.variables['observer.room_id']
    runtime.call('observer', 'conf_logout', {})
    runtime.replace_client('observer')
    runtime.call('observer', 'front_login', {'room_id': room})
    runtime.call('operator', 'request_right', {'right_type': 12})
    try:
        with report_step('开启等候室，使用6624路由创建管理端子连接'):
            mark = runtime.clients['operator'].mark_events()
            runtime.call('operator', 'set_conf_state', {'enable_waiting_room': 1})
            route = runtime.clients['operator'].wait_event(EventExpectation(cmdid=6624, kind='notify'), mark, runtime.event_timeout)
            runtime.join_related('operator', 'waiting_manager', route.fields)
        with report_step('B异步登录主会场，收到6667后进入等候室'):
            runtime.start_async_call('waiting_join', 'observer', 'conf_login', {'timeout': runtime.event_timeout * 5})
            marks = runtime.async_call_state('waiting_join')['marks']
            waiting = runtime.clients['observer'].wait_event(EventExpectation(cmdid=6667, kind='notify'), marks['observer'], runtime.event_timeout)
            from .waiting_review import verify_waiting_route
            verify_waiting_route(waiting.fields)
            runtime.clients['operator'].expect_no_event(
                EventExpectation(cmdid=6667, kind='notify'), marks['operator'], runtime.quiet_timeout)
            runtime.join_related('observer', 'waiting_guest', waiting.fields)
        with report_step('在等候室管理连接审批，验证6670逐用户结果及主会场6601/6602'):
            uid = runtime.variables['observer.room_user_id']
            admitted = runtime.call('waiting_manager', 'allow_waiting_users', {'users': [{'UserID': uid, 'Allow': 1}]})
            users = admitted.get('UserList', {}).get('UserInfo', [])
            if isinstance(users, dict): users = [users]
            assert len(users) == 1 and str(users[0]['Result']) == '0', admitted
            assert str(users[0]['UserID']) == str(uid), admitted
            login = runtime.wait_async_call('waiting_join', runtime.event_timeout)
            assert str(login['Result']) == '0', login
            entered = runtime.clients['operator'].wait_event(EventExpectation(cmdid=6602, kind='notify',
                match={'User.0.UserID': uid}), marks['operator'], runtime.event_timeout)
            assert runtime.is_healthy()
            return CaseExecutionResult(response=admitted, events={'operator:notify:0': route,
                'observer:notify:0': waiting, 'operator:notify:1': entered}, expected_failure=True)
    finally:
        for alias in ('waiting_guest', 'waiting_manager'):
            driver = runtime.clients.pop(alias, None)
            if driver: driver.close()
        runtime.call('operator', 'set_conf_state', {'enable_waiting_room': 0})
        runtime.call('operator', 'release_right', {'right_type': 12})


def remote_state_reply(runtime):
    from .case_executor import CaseExecutionResult
    a, b = runtime.clients['operator'], runtime.clients['observer']
    uid = runtime.variables['observer.room_user_id']
    source = runtime.variables['operator.room_user_id']
    marks = runtime.mark_all_events()
    with report_step('A请求B开启VNC，B接收6646并核对来源'):
        runtime.call('operator', 'set_user_state', {'user_id': uid, 'vnc_state': 2, 'vnc_audio_id': 0})
        request = b.wait_event(EventExpectation(cmdid=6646, kind='notify',
            match={'SrcUserID': source, 'VNCState': 2, 'VNCAudioID': 0}), marks['observer'], runtime.event_timeout)
        a.expect_no_event(EventExpectation(cmdid=6646, kind='notify'), marks['operator'], runtime.quiet_timeout)
    with report_step('B根据6646来源发送拒绝确认6546；验证不产生开启成功状态'):
        reply_marks = runtime.mark_all_events()
        runtime.call('observer', 'reply_self_state_change', {
            'dst_user_id': request.fields['SrcUserID'], 'vnc_state': 0,
            'error': 'E2E拒绝远程共享'})
        for alias, driver in runtime.clients.items():
            driver.expect_no_event(EventExpectation(cmdid=6609, kind='notify',
                match={'UserID': uid, 'VNCState': 2}), marks[alias], runtime.quiet_timeout)
        assert runtime.is_healthy(), '状态回复后会议连接异常'
        attach_json(runtime.snapshot_all_events(reply_marks), '6546之后实际事件（无同步Response）')
    return CaseExecutionResult(events={'observer:notify:0': request}, expected_failure=True)
