"""开发确认的业务规则：先取得独立前置证据，再执行主操作。"""
from .models import EventExpectation
from .allure_reporting import attach_json, report_step


def verify_product_features(driver, mark, timeout):
    with report_step('确认会议初始化下发6639产品能力列表和结束标记'):
        event = driver.wait_event(EventExpectation(cmdid=6639, kind='notify'), mark, timeout)
        fields = event.fields
        assert 'End' in fields, '6639缺少能力列表结束标记'
        for name in ('MeetingNow', 'ServerRecord'):
            feature = fields.get(name)
            assert isinstance(feature, dict) and str(feature.get('val')) == '1', (
                f'验收账号缺少预期产品能力{name}: {feature!r}')
            assert feature.get('Attr', {}).get('Renders') in ('highlight', 'show'), (
                f'6639产品能力{name}缺少有效显示状态: {feature!r}')
        attach_json({'cmdid': event.cmdid, 'MeetingNow': fields['MeetingNow'],
                     'ServerRecord': fields['ServerRecord'], 'has_end': True},
                    '实际6639产品能力字段')


def verify_role_permissions(driver, mark, timeout):
    with report_step('确认会议初始化下发6631角色权限列表和结束标记'):
        event = driver.wait_event(EventExpectation(cmdid=6631, kind='notify'), mark, timeout)
        fields = event.fields
        roles = fields.get('Role')
        if isinstance(roles, dict):
            roles = [roles]
        assert isinstance(roles, list) and roles, '6631缺少非空Role角色列表'
        role_codes = []
        permission_counts = {}
        for index, role in enumerate(roles):
            assert isinstance(role, dict), f'6631 Role.{index}不是对象: {role!r}'
            role_code = role.get('RoleCode')
            assert role_code not in (None, ''), f'6631 Role.{index}缺少RoleCode'
            role_code = str(role_code)
            permissions = role.get('Permissions')
            assert isinstance(permissions, dict) and permissions, (
                f'6631角色{role_code}缺少非空Permissions')
            role_codes.append(role_code)
            permission_counts[role_code] = len(permissions)
        assert len(role_codes) == len(set(role_codes)), f'6631存在重复RoleCode: {role_codes}'
        assert 'RoleEnd' in fields, '6631缺少RoleEnd结束标记'
        attach_json({'cmdid': event.cmdid, 'role_codes': role_codes,
                     'permission_counts': permission_counts, 'has_role_end': True},
                    '实际6631角色权限初始化字段')


def group_switch_values(scene):
    suffix = scene.removeprefix('GROUP_CONFIG_')
    if not scene.startswith('GROUP_CONFIG_') or suffix not in ('00', '01', '10', '11'):
        raise ValueError('分组创建必须显式指定主会议加入/离开开关前置')
    return {'EnableJoinGroupMeeting': int(suffix[0]),
            'EnableLeaveGroupMeeting': int(suffix[1])}


def prepare_group_switches(runtime, actor, scene):
    values = group_switch_values(scene)
    with report_step('设置主会议分组开关，验证A/B收到6610后再创建分组'):
        # 先设置相反值，再设目标值，确保目标步骤是真实变化而非幂等静默。
        runtime.call(actor, 'set_conf_state', {key: 1 - value for key, value in values.items()})
        marks = runtime.mark_all_events()
        runtime.call(actor, 'set_conf_state', values)
        for alias in ('operator', 'observer'):
            for field, value in values.items():
                runtime.clients[alias].wait_event(EventExpectation(
                    cmdid=6610, kind='notify', match={field: value}),
                    marks[alias], runtime.event_timeout)
        runtime.variables['group.enable_join'] = values['EnableJoinGroupMeeting']
        runtime.variables['group.enable_leave'] = values['EnableLeaveGroupMeeting']
        attach_json(values, '主会议配置与6623预期的独立来源（6610已确认）')


def read_group_capacity(runtime, actor):
    with report_step('读取新版分组人数配置，作为创建响应的独立预期'):
        driver = runtime.clients[actor]
        mark = driver.mark_events()
        response = runtime.call(actor, 'conf_get_room_config', {'group_room_config': True})
        attach_json(response, '创建前6634分组配置（预期值来源）')
        config = response.get('GroupRoomConfig', {})
        value = config.get('GroupRoomMaxUserCount')
        assert value is not None and int(value) > 0, '6634缺少有效GroupRoomMaxUserCount，不能用6623反推预期'
        driver.wait_event(EventExpectation(cmdid=6634, kind='response', match={
            'GroupRoomConfig.GroupRoomMaxUserCount': value}), mark, runtime.event_timeout)
        runtime.variables['group.max_user_count'] = int(value)


def prepare_direct_audio_application(runtime, actor):
    with report_step('配置本人申请和直接广播权限，并确认音频关闭后再申请'):
        uid = runtime.variables[f'{actor}.room_user_id']
        driver = runtime.clients[actor]
        permissions = {'ApplyBroadcastOwnAudio': 1, 'BroadcastOwnAudio': 1}
        mark = driver.mark_events()
        runtime.call(actor, 'set_user_permissions', {'user_id': uid, 'permissions': permissions})
        # 服务端逐项下发6635，不要求多个权限在同一包内回显。
        for field, value in permissions.items():
            driver.wait_event(EventExpectation(cmdid=6635, kind='notify', match={
                'UserID': uid, field: value}), mark, runtime.event_timeout)
        # 先开再关，取得明确的0状态通知，避免靠幂等静默推断前置成功。
        runtime.call(actor, 'mic_on', {})
        mark = driver.mark_events()
        runtime.call(actor, 'mic_off', {})
        driver.wait_event(EventExpectation(cmdid=6609, kind='notify', match={
            'UserID': uid, 'Audio.ID': 0, 'Audio.State': 0}), mark, runtime.event_timeout)
        attach_json(permissions, '直接获准申请发言的权限前置')
