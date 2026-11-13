"""分组创建的配置数量和管理端成员权限通知校验。"""
import time
from .allure_reporting import attach_json


def member_pairs(users):
    if isinstance(users, dict): users=[users]
    pairs={}
    for user in users or []:
        ids=user.get('ID',[]); rights=user.get('Right',[])
        if not isinstance(ids,list): ids=[ids]
        if not isinstance(rights,list): rights=[rights]
        assert len(ids)==len(rights), '6666用户ID与权限列表长度不一致'
        pairs.update({str(uid):str(right) for uid,right in zip(ids,rights)})
    return pairs


def member_states(users):
    if isinstance(users, dict): users = [users]
    states = {}
    for user in users or []:
        ids = user.get('ID', [])
        values = user.get('State', [])
        if not isinstance(ids, list): ids = [ids]
        if not isinstance(values, list): values = [values]
        assert len(ids) == len(values), '6666成员ID与State数量不一致'
        for uid, state in zip(ids, values):
            assert str(state) in {'0', '1', '2'}, f'6666非法成员状态: {state!r}'
            states[str(uid)] = str(state)
    return states


def verify_created_groups(runtime, actor, params, response, mark):
    rooms=response.get('RoomInfo',[])
    if isinstance(rooms,dict):rooms=[rooms]
    assert len(rooms)==len(params['rooms']), '6623返回分组数与请求不符'
    ids=[str(r['RoomID']) for r in rooms]
    assert len(set(ids))==len(ids) and all(int(i)>0 for i in ids), '分组ID不合法或重复'
    required={rid:member_pairs(req.get('User',[])) for rid,req in zip(ids,params['rooms'])}
    required={k:v for k,v in required.items() if v}
    deadline=time.monotonic()+runtime.event_timeout
    while required:
        events=runtime.clients[actor].snapshot_events(mark)
        for event in events:
            if event['cmdid']!=6666:continue
            values=event['fields'].get('RoomUserInfo',[])
            if isinstance(values,dict):values=[values]
            for room in values:
                rid=str(room.get('RoomID'))
                if rid not in required:continue
                actual=member_pairs(room.get('User',[]))
                if all(actual.get(uid)==right for uid,right in required[rid].items()):
                    states = member_states(room.get('User', []))
                    assert all(uid in states for uid in required[rid]), '6666缺少目标成员State'
                    attach_json(event,f'分组{rid}成员与权限的真实6666')
                    required.pop(rid)
        if not required:break
        if time.monotonic()>=deadline:raise AssertionError(f'缺少匹配分组成员/权限的6666: {required}')
        time.sleep(.05)
