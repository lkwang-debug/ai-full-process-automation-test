"""Business postconditions shared by Web gates and expansion cases."""
import json
import time
from collections.abc import Mapping
from urllib.parse import urlsplit

from .allure_reporting import attach_json, alias_name
from .web_protocol import matches_fields, field_values

RECORD_PERMISSION_DENIED = 0x3201  # Confirmed manual recording permission branch.
SHARE_START_OCCUPIED = 8486       # Web createScreenSharing occupied branch, not arbitrary nonzero.
PERMISSION_KEYS = ('BroadcastOthersAudio', 'BroadcastOthersVideo', 'ConfigOthersApplyBroadcastAudio',
                   'ConfigOthersPresenter', 'CreateAppshare', 'CreateWhiteboard', 'ServerRecord',
                   'OperatePublicChat', 'ApplyAdminPermissions', 'ApplyPresenter')


def assert_sent(runtime, alias, marks, cmdid, fields):
    rows = [r for r in runtime.clients[alias].snapshot_outbound(marks[alias])
            if r.get('cmdid') == cmdid and matches_fields(r.get('fields', {}), fields)]
    assert rows, f'{alias}没有本次真实上行{cmdid}及指定业务字段: {fields}'
    attach_json(rows, alias_name(alias) + '本次真实业务请求关联')
    return rows[-1]


def assert_av_state(runtime, alias, kind, dev, expected):
    from .web_key_scenarios import user
    key = 'Audio' if kind == 'audio' else 'Video'
    value = user(runtime, alias)
    assert matches_fields(value, {key + '.ID': dev, key + '.State': expected}), \
        f'{alias}最终{kind}设备{dev}状态未达到{expected}'
    attach_json({'user_id': runtime.variables[alias + '.room_user_id'], 'device_id': dev,
                 'state': expected, 'actual_user': value}, alias_name(alias) + '最终音视频状态')


def av_state(runtime, alias, kind, dev):
    from .web_key_scenarios import user, _nodes
    key = 'Audio' if kind == 'audio' else 'Video'
    nodes = [n for n in _nodes([user(runtime, alias).get(key)]) if str(n.get('ID')) == str(dev)]
    assert len(nodes) == 1, '目标设备必须来自同一个明确用户节点'
    return int(nodes[0].get('State') or 0)


def observe_no(runtime, marks, forbidden, description):
    """One fixed quiet window per receiver; inspect all relevant forbidden messages."""
    for receiver, checks in forbidden.items():
        deadline = time.monotonic() + runtime.quiet_timeout
        while True:
            for row in runtime.clients[receiver].snapshot_events(marks[receiver]):
                if any(check(row) for check in checks):
                    attach_json(row, '反向断言失败的实际消息')
                    raise AssertionError(f'{receiver}: {description}: {row.get("cmdid")}')
            if time.monotonic() >= deadline:
                break
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))
    attach_json({'receivers': list(forbidden), 'after': marks,
                 'window_seconds_per_receiver': runtime.quiet_timeout,
                 'assertion': description}, '逐端反向观察结果')


def assert_non_target(runtime, marks, target, kind, dev, baseline, actor):
    key = 'Audio' if kind == 'audio' else 'Video'
    uid = runtime.variables[target + '.room_user_id']
    sender = runtime.variables[actor + '.room_user_id']
    changes = lambda r: r.get('cmdid') == 6609 and matches_fields(r.get('fields', {}), {'UserID': uid, key + '.ID': dev})
    invites = lambda r: r.get('cmdid') == 6642 and matches_fields(r.get('fields', {}), {'SrcUserID': sender, 'DstUserID': uid, key + '.ID': dev})
    observe_no(runtime, marks, {a: [changes, invites] for a in runtime.clients}, '非目标不得出现设备变更或本次确认邀请')
    assert av_state(runtime, target, kind, dev) == baseline, '非目标最终设备状态被误改变'


def permission_snapshot(runtime, alias):
    value = runtime.call(alias, 'get_effective_permissions', {})
    assert isinstance(value, Mapping) and value, '必须取得实际有效权限，不能用空字典证明恢复'
    return {key: str(value.get(key, 0)) for key in PERMISSION_KEYS}


def role_restored(runtime, alias, baseline):
    from .web_key_scenarios import user
    info = user(runtime, alias)
    assert int(info.get('DataState') or 0) == 0 and int(info.get('ManagerState') or 0) == 0
    actual = permission_snapshot(runtime, alias)
    assert actual == baseline, f'角色放弃后有效权限未恢复: before={baseline}, after={actual}'
    attach_json({'before': baseline, 'after': actual, 'user': info}, '参会人权限恢复精确对照')


def assert_departed(runtime, alias, uid, marks):
    from .web_key_scenarios import wait
    peers = [a for a in runtime.clients if a != alias]
    for peer in peers:
        wait(runtime, peer, marks, 6605, {'UserID': uid})
        if runtime.clients[peer].client_type == 'web':
            members = runtime.call(peer, 'get_users', {})
            assert all(str(m.get('UserID')) != str(uid) for m in members), '退出用户仍在观察端成员缓存'
        assert runtime.clients[peer].is_healthy(), '目标退出不应断开其他客户端'
    deadline = time.monotonic() + runtime.event_timeout
    if runtime.clients[alias].client_type == 'web':
        while True:
            identity = runtime.call(alias, 'get_identity', {})
            if identity.get('joined') is False:
                break
            assert time.monotonic() < deadline, '退出后SDK仍处于已入会状态'
            time.sleep(0.02)
        attach_json(identity, alias_name(alias) + 'SDK已退出会议（代理可保持在线）')
    else:
        while runtime.clients[alias].is_healthy() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not runtime.clients[alias].is_healthy(), 'FM退出后Conf会话仍在会'


def assert_old_whiteboards_absent(runtime, alias, after, new_id, old_ids):
    from .web_key_scenarios import _nodes, _board_id32
    old = {_board_id32(str(value).rsplit(';', 1)[-1]) for value in old_ids}
    new = _board_id32(str(new_id).rsplit(';', 1)[-1])
    requests = []
    for r in runtime.clients[alias].snapshot_outbound(after[alias]):
        if r.get('cmdid') != 6513:
            continue
        ids = {_board_id32(n['Data']) for n in _nodes(field_values(r.get('fields', {}), 'Screen.DataArea.Item'))
               if str(n.get('Type')) == '1' and n.get('Data') not in (None, 0, '0')}
        if new in ids:
            assert not ids.intersection(old), '新白板实际6513仍携带已关闭旧资源'
            requests.append(r)
    assert requests, '新白板必须有对应实际6513'
    for peer in runtime.clients:
        if peer == alias:
            continue
        prefix = 'Screen' if runtime.clients[peer].client_type == 'web' else 'V3'
        for r in runtime.clients[peer].snapshot_events(after.get('events', {}).get(peer, 0)):
            if r.get('cmdid') != 6613:
                continue
            ids = {_board_id32(n['Data']) for n in _nodes(field_values(r.get('fields', {}), prefix + '.DataArea.Item'))
                   if str(n.get('Type')) == '1' and n.get('Data') not in (None, 0, '0')}
            if new in ids:
                assert not ids.intersection(old), '对端新白板6613仍包含已关闭旧资源'
    attach_json({'new': new_id, 'excluded': old_ids, 'requests': requests}, '新旧白板资源隔离断言')


def validate_initialization(roles, features):
    nodes = roles.get('Role')
    if isinstance(nodes, dict): nodes = [nodes]
    assert isinstance(nodes, list) and nodes, 'Web实际角色列表必须非空'
    codes = []
    for node in nodes:
        assert isinstance(node, dict) and isinstance(node.get('RoleCode'), str) and node['RoleCode']
        assert isinstance(node.get('Permissions'), dict) and node['Permissions'], '角色权限结构缺失'
        codes.append(node['RoleCode'])
    assert len(set(codes)) == len(codes), '角色代码重复'
    assert isinstance(features, dict) and features, 'Web实际产品能力必须非空'
    for name, value in features.items():
        assert isinstance(name, str) and name and isinstance(value, dict), '产品能力结构不合法'
        attr = value.get('Attr', {})
        assert isinstance(attr, dict), '能力属性结构不合法'
        if 'Renders' in attr:
            assert isinstance(attr['Renders'], str) and attr['Renders'], '产品能力展示属性缺失'
    # Proxy JSON omits empty XML RoleEnd/End; never synthesize them or require absent wire fields.


def validate_lock_source(event, operator_id):
    """Lock notices may omit source; an explicitly present source must be correct."""
    if 'SrcUserID' in event.fields:
        assert str(event.fields['SrcUserID'])==str(operator_id), '锁会通知携带了错误来源用户'


def validate_latest_remote_reply(event, latest_actor_id, target_id, kind, device_id, accepted):
    key='Audio' if kind=='audio' else 'Video'
    assert matches_fields(event.fields,{'SrcUserID':target_id,'DstUserID':latest_actor_id,key+'.ID':device_id}), '重叠邀请答复来源、最新接收人或设备不符'
    assert 'Result' in event.fields and (int(event.fields['Result'])==0)==accepted, '重叠邀请答复与接受/拒绝不符'


def oauth_security_rejection(row, ces_addr):
    """Confirmed security contract: OAuth rejects bad credentials with HTTP 400.

    A generic 400 on a different endpoint/server is not authentication evidence.
    The caller must additionally prove failed login and no identity/token/join.
    """
    fields = row.get('fields', {})
    actual, expected = urlsplit(fields.get('url', '')), urlsplit(ces_addr)
    return (row.get('source') == 'http' and row.get('kind') == 'response'
            and fields.get('status') == 400
            and actual.scheme == expected.scheme and actual.netloc == expected.netloc
            and actual.path == '/meeting-resource-server/oauth2/token')
