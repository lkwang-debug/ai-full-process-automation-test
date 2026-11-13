"""0914 SDK公开能力的真实双端链路适配。"""
import uuid
import socket
from pathlib import Path
from urllib.parse import urlsplit
from .models import EventExpectation
from .allure_reporting import attach_json, report_step


class InvitationAccountOnline(RuntimeError):
    """PaaS明确拒绝重复在线；不抢占用户当前设备。"""


def _resolved_addresses(host):
    try:
        return {item[4][0] for item in socket.getaddrinfo(host, None)}
    except OSError:
        return set()


def validate_invitation_target(invitation, expected_code):
    if invitation.invite_code != int(expected_code):
        raise AssertionError('邀请目标不是本次动态会议')


def invited_join(case, runtime):
    from fm_client import PaasOnlineClient, OnlineError
    from examples.fm_invited_join_auto import generate_config, read_config
    from .case_executor import CaseExecutionResult
    a, b = runtime.clients['operator'], runtime.clients['observer']
    endpoint, account = b.profile.endpoint, b.profile.account
    root = Path(__file__).resolve().parents[1]
    bootstrap = Path(endpoint['paas_bootstrap_file'])
    if not bootstrap.is_absolute(): bootstrap = root / bootstrap
    service = read_config(bootstrap)
    api = runtime.services['http_admin'].native_client
    api_host = urlsplit(api.base_url).hostname or ''
    front_host = endpoint['host']
    if (api_host != front_host and not (
            _resolved_addresses(api_host) & _resolved_addresses(front_host))):
        raise AssertionError(
            f'HTTP与Front不属于同一环境: http={api_host!r}, front={front_host!r}')
    # SDK discovery keeps a strict host check. Use the equivalent HTTP DNS
    # name for its temporary Front authentication; the runtime clients retain
    # their configured Front endpoint.
    config = {'host': api_host, 'port': endpoint['port'], 'accounts': [account]}
    online = None
    with report_step('按当前环境和B账号获取PaaS身份及CP地址（凭据不进入报告）'):
        generated = generate_config(config, api, service, account_index=0, timeout=runtime.event_timeout)
        generated['client_guid'] = str(uuid.uuid5(uuid.NAMESPACE_URL,
            f"meeting-test:{endpoint['host']}:{generated['app_id']}:{generated['user_id']}"))
    try:
        with report_step('B仅Front认证并登录独立PaaS连接，确认10001成功、尚未入会'):
            runtime.call('observer', 'front_authenticate', {})
            assert str(b.identity['local_user_id']) == generated['user_id']
            assert b.identity['room_id'] is None
            online = PaasOnlineClient(generated, timeout=runtime.event_timeout)
            try:
                logged = online.login()
            except OnlineError as exc:
                if str(exc) == 'PaaS cmd 10001 result=1000010':
                    attach_json({'cmdid': 10001, 'result': 1000010, 'user_id': generated['user_id'],
                                 'cp_address': generated['cp_address'], 'force_login': 0}, 'PaaS重复在线拒绝（未发送邀请）')
                    raise InvitationAccountOnline(
                        f"PaaS账号{generated['user_id']}重复在线(1000010)，请退出该账号其他终端；本轮未发送邀请") from exc
                raise
            assert str(logged['result']) == '0' and online.is_online
            attach_json({'user_id': generated['user_id'], 'cp_address': generated['cp_address'],
                         'response_id': 10001, 'result': logged['result']}, 'PaaS在线证据')
        expected_code = int(runtime.call('operator', 'conf_get_room_config', {})['InviteCode'])
        expected_room = a.identity['room_id']
        marks = runtime.mark_all_events()
        with report_step(f'A发起{case.operation}，B等待真实14002并核对邀请码'):
            response = runtime.call('operator', case.operation, {'user_ids': [int(generated['user_id'])]})
            attach_json(response, '实际邀请HTTP返回')
            assert str(response.get('code')) == '0', response
            assert response.get('data', {}).get('errors', []) == [], response
            invitation = online.wait_invitation(timeout=float(endpoint['paas_invitation_timeout']))
            validate_invitation_target(invitation, expected_code)
            attach_json(invitation.raw, '真实PaaS 14002邀请')
        with report_step('SDK接受14003并按邀请中的邀请码入会，验证30005/6601和A收到6602'):
            joined = b.accept_online_invitation(online, invitation)
            actual_room = joined['front'].get('RealRoomID') or joined['front'].get('RoomID')
            assert int(actual_room) == int(expected_room)
            assert str(joined['conf']['Result']) == '0'
            event = a.wait_event(EventExpectation(cmdid=6602, kind='notify', match={
                'User.0.UserID': b.identity['room_user_id']}), marks['operator'], runtime.event_timeout)
            for key, value in b.identity.items(): runtime.variables[f'observer.{key}'] = value
            check_features(runtime)
            assert online.is_online, '入会后PaaS不在线'
            attach_json({'front_cmdid': 30005, 'conf_cmdid': 6601, 'room_id': actual_room,
                         'conf_result': joined['conf']['Result'], 'user_id': b.identity['room_user_id']}, '邀请入会结果')
        return CaseExecutionResult(response=response, events={'operator:notify:0': event})
    finally:
        if online is not None: online.close()


def check_features(runtime):
    for alias, driver in runtime.clients.items():
        result = runtime.call(alias, 'get_user_features', {})
        assert str(result['UserID']) == str(driver.identity['room_user_id'])
    assert runtime.is_healthy(), '后续能力查询/Conf连接失败'


def relay_roundtrip(case, runtime):
    from .case_executor import CaseExecutionResult
    params = runtime.resolve_variables(case.params)
    probe = uuid.uuid4().hex
    params['messages'][0]['fields']['Probe'] = probe
    a, b = runtime.clients['operator'], runtime.clients['observer']
    au, bu = a.identity['room_user_id'], b.identity['room_user_id']
    marks = runtime.mark_all_events()
    events = {}
    with report_step('A通过公开接口发6700，B验证内层6718及中文特殊字符'):
        runtime.call('operator', 'send_relay_message', params)
        event = b.wait_event(EventExpectation(cmdid=6700, kind='notify', match={
            'SrcUserID': au, 'DstUserID': bu, 'msg.id': 6718, 'msg.Probe': probe}), marks['observer'], runtime.event_timeout)
        assert event.fields['msg'] == {'id': '6718', 'Text': '中文<&>"', 'Probe': probe,
                                       'Item': [{'ID': '1'}, {'ID': '2'}]}
        a.expect_no_event(EventExpectation(cmdid=6700, kind='notify', match={'msg.Probe': probe}), marks['operator'], runtime.quiet_timeout)
        events['observer:notify:0'] = event
    with report_step('B通过公开接口回6700/msg6719，A验证来源目标和结果'):
        marks = runtime.mark_all_events()
        runtime.call('observer', 'send_relay_message', {'dst_user_id': au,
            'messages': [{'id': 6719, 'fields': {'Probe': probe, 'Result': 0}}]})
        events['operator:notify:0'] = a.wait_event(EventExpectation(cmdid=6700, kind='notify', match={
            'SrcUserID': bu, 'DstUserID': au, 'msg.id': 6719, 'msg.Probe': probe, 'msg.Result': 0}), marks['operator'], runtime.event_timeout)
        b.expect_no_event(EventExpectation(cmdid=6700, kind='notify', match={'msg.Probe': probe}), marks['observer'], runtime.quiet_timeout)
        check_features(runtime)
    return CaseExecutionResult(events=events)
