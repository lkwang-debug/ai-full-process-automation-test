"""Reviewed key workflows. All mutations use public SDK or HTTP methods."""
from __future__ import annotations

import time
import re

from .allure_reporting import attach_json, report_step, alias_name, operation_name, actual_call_evidence, protocol_outbound_evidence
from .models import EventExpectation
from .web_protocol import matches_fields, field_values
from .runtime import MeetingTestRuntime


class WebKeyRuntime(MeetingTestRuntime):
    """Record every public call, without interpreting its return as a Response."""
    def call(self,alias,operation,params=None):
        marks=self.mark_all_outbound()
        with report_step(f'{alias_name(alias)}调用【{operation_name(operation)}】'):
            try:
                result=super().call(alias,operation,params)
                attach_json({'返回来源':'SDK公开方法/缓存查询；不等同服务器Response','实际返回':result},'公开方法实际返回')
                return result
            finally:
                outgoing=self.snapshot_all_outbound(marks)
                attach_json(actual_call_evidence(outgoing,actor=alias,operation=operation,fallback_params=params or {}),'实际Python与SDK公开方法参数')
                attach_json(protocol_outbound_evidence(outgoing,actor=alias,operation=operation,request_cmdid=None),'实际发送协议')


def wait(runtime, alias, marks, cmdid, fields):
    deadline=time.monotonic()+runtime.event_timeout
    cursor=marks[alias]
    while time.monotonic()<deadline:
        event=runtime.clients[alias].wait_event(EventExpectation(cmdid=cmdid,kind='notify'),
            cursor,max(0.01,deadline-time.monotonic()))
        if matches_fields(event.fields,fields):
            attach_json(event, f'{alias_name(alias)}实际{cmdid}通知')
            return event
        cursor=event.event_id
    raise AssertionError(f'{alias}未收到匹配本次业务字段的{cmdid}: {fields}')


def no_event(runtime, alias, marks, cmdid, fields):
    runtime.clients[alias].expect_no_event(EventExpectation(cmdid=cmdid,kind='notify',match=fields),
                                          marks[alias],runtime.quiet_timeout)


def _board_id32(value):
    """仅对白板资源ID比较32位表示；拒绝截断越界值或把空窗口当白板。"""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("白板ID必须是十进制整数")
    text = str(value)
    if not re.fullmatch(r"-?\d+", text):
        raise ValueError("白板ID必须是十进制整数")
    number = int(text)
    if not -(1 << 31) <= number <= (1 << 32) - 1 or number == 0:
        raise ValueError("白板ID必须是非零32位整数")
    return number & 0xFFFFFFFF


def _nodes(values):
    for value in values:
        if isinstance(value, list):
            yield from _nodes(value)
        elif isinstance(value, dict):
            yield value


def _whiteboard_matches(fields, client_type, expected_id, expected_pos=0):
    """同一个数据区域Item同时满足白板类型、位置和本次ID，避免跨节点拼凑。"""
    prefix = "Screen" if client_type == "web" else "V3"
    for area in _nodes(field_values(fields, prefix + ".DataArea")):
        if str(area.get("ID")) != "1":
            continue
        for item in _nodes([area.get("Item")]):
            if str(item.get("Type")) != "1" or str(item.get("Pos")) != str(expected_pos):
                continue
            try:
                if _board_id32(item.get("Data")) == expected_id:
                    return True
            except ValueError:
                continue
    return False


def wait_whiteboard(runtime, alias, marks, media_id, *, expected_pos=0):
    # SDK创建ID保留完整值；只把会议通知中的数值ID统一成同一32位表示。
    expected = _board_id32(str(media_id).rsplit(";", 1)[-1])
    driver = runtime.clients[alias]
    deadline = time.monotonic() + runtime.event_timeout
    cursor = marks[alias]
    try:
        while time.monotonic() < deadline:
            event = driver.wait_event(EventExpectation(cmdid=6613, kind="notify"),
                                      cursor, max(0.01, deadline - time.monotonic()))
            if _whiteboard_matches(event.fields, driver.client_type, expected, expected_pos):
                attach_json({"SDK实际白板ID": media_id, "本次ID的无符号32位表示": expected,
                             "本次实际请求位置":expected_pos,
                             "比较范围": "同一DataArea.ID=1/Item.Type=1，本次ID和实际请求位置；实际消息不修改"},
                            "白板资源ID关联及32位表示规则")
                attach_json(event, f"{alias_name(alias)}实际6613白板通知")
                return event
            cursor = event.event_id
    except AssertionError as exc:
        attach_json(driver.snapshot_events(marks[alias]), "白板等待失败时游标后的全部实际消息")
        raise AssertionError(f"{alias}未收到匹配本次白板{media_id}的6613（32位ID={expected}）") from exc
    raise AssertionError(f"{alias}未收到匹配本次白板{media_id}的6613（32位ID={expected}）")


def user(runtime, alias):
    driver=runtime.clients[alias]
    if driver.client_type=='web':
        return driver.call('get_identity')['user']
    # FM身份/设备前置取真实6601/6602/6608，不凭空指定媒体ID。
    uid=runtime.variables[alias+'.room_user_id']
    value={}
    events=driver.snapshot_events(0)
    # Device reports are delivered to other participants, not necessarily the sender.
    # Read the peer's real 6608 for this exact UID rather than inventing a device.
    for peer,receiver in runtime.clients.items():
        if peer!=alias:
            events.extend(e for e in receiver.snapshot_events(0)
                          if e.get('cmdid')==6608 and str(e.get('fields',{}).get('UserID'))==str(uid))
    for event in events:
        fields=event.get('fields',{})
        own=fields.get('UserInfo')
        if event.get('cmdid')==6601 and isinstance(own,dict):
            value.update({**own,'UserID':uid})
        members=(fields.get('User') or []) if event.get('cmdid')==6602 else []
        if isinstance(members,dict): members=[members]
        for member in members:
            if isinstance(member,dict) and str(member.get('UserID'))==str(uid): value.update(member)
        if event.get('cmdid')==6608 and str(fields.get('UserID'))==str(uid):
            # A newly registered device is inactive until a state notification says otherwise.
            # Preserve an already observed state on repeated registration/modification.
            for key in ('Audio','Video'):
                if key not in fields:continue
                existing={str(n['ID']):dict(n) for n in _nodes([value.get(key)]) if 'ID' in n}
                for item in _nodes([fields[key]]):
                    dev=str(item['ID'])
                    if str(item.get('Operation'))=='3':existing.pop(dev,None)
                    else:
                        merged=existing.get(dev,{'ID':item['ID'],'State':0})
                        merged.update({k:v for k,v in item.items() if k!='Operation'});existing[dev]=merged
                devices=list(existing.values());value[key]=devices[0] if len(devices)==1 else devices
        if event.get('cmdid')==6609 and str(fields.get('UserID'))==str(uid):
            value.update(fields)
    if not value: raise AssertionError('FM未取得真实登录身份')
    return value


def device(runtime, alias, kind):
    if runtime.clients[alias].client_type=='fm':
        key='Audio' if kind=='audio' else 'Video'
        dev=0 if kind=='audio' else 1
        marks=runtime.mark_all_events()
        runtime.call(alias,'report_av_info',{kind:[{'Operation':1,'ID':dev,'Name':'key-'+kind}]})
        peer=next(a for a in runtime.clients if a!=alias)
        wait(runtime,peer,marks,6608,{'UserID':runtime.variables[alias+'.room_user_id'],f'{key}.ID':dev})
        return dev
    info=user(runtime,alias)
    if kind=='audio':
        assert info.get('Audio'),'Web没有实际上报的音频设备'
        return int(info['Audio']['ID'])
    videos=info.get('Video') or []
    if isinstance(videos,dict): videos=[videos]
    choices=[v for v in videos if int(v['ID'])!=5]
    assert choices,'Web没有实际上报的摄像头'
    return int(choices[0]['ID'])


def av(runtime, alias, kind, on, dev):
    if runtime.clients[alias].client_type=='web':
        op=('mic_' if kind=='audio' else 'camera_')+('on' if on else 'off')
        return runtime.call(alias,op,{'dev_id':dev})
    return runtime.call(alias,'set_audio_state' if kind=='audio' else 'set_video_state',{'state':2 if on else 0,'dev_id':dev})


def role(runtime, alias, state_role, enabled):
    uid=runtime.variables[alias+'.room_user_id']
    key='DataState' if state_role=='presenter' else 'ManagerState'
    if enabled and runtime.clients[alias].client_type=='fm' and int(user(runtime,alias).get(key) or 0)==2:
        return  # 前置已由真实初始化消息证明；不要求同值设置产生新通知。
    if enabled and runtime.clients[alias].client_type=='web':
        current=user(runtime,alias)
        if int(current.get(key) or 0)!=0:
            role(runtime,alias,state_role,False)
    marks=runtime.mark_all_events()
    if runtime.clients[alias].client_type=='web':
        op=('request_' if enabled else 'release_')+state_role
        runtime.call(alias,op,{'password':runtime.variables['env.chair_password']} if state_role=='manager' else {})
    else:
        if state_role=='manager' and enabled:
            runtime.call(alias,'apply_host',{'chair_pwd':runtime.variables['env.chair_password']})
        else:
            # 本人主讲申请使用1；2是指定/授予，不能在普通身份时借用管理权限。
            runtime.call(alias,'set_user_state',{('data_state' if state_role=='presenter' else 'manager_state'):1 if enabled and state_role=='presenter' else 0})
    for receiver in runtime.clients:
        if receiver in runtime.variables.get('flow.unjoined',[]): continue
        wait(runtime,receiver,marks,6609,{'UserID':uid,key:2 if enabled else 0})


def execute_key_flow(case,runtime):
    scenario=case['scenario']; params=case['params']
    a,b='operator','observer'
    uid_a=runtime.variables[a+'.room_user_id']
    uid_b=runtime.variables.get(b+'.room_user_id')
    if scenario=='login':
        for alias,other in ((a,b),(b,a)):
            wait(runtime,alias,{alias:0},6602,{'User.UserID':runtime.variables[other+'.room_user_id']})
        from .web_assertions import assert_departed
        guests=[alias for alias in (a,b) if runtime.profiles[alias].account.get('identity_type')=='guest']
        departing=guests[0] if guests else b
        uid=runtime.variables[departing+'.room_user_id'];marks=runtime.mark_all_events()
        runtime.call(departing,'leave' if runtime.clients[departing].client_type=='web' else 'conf_logout',{})
        assert_departed(runtime,departing,uid,marks)
        return
    if scenario=='chat':
        marks=runtime.mark_all_events()
        runtime.call(a,'chat',{'text':params['text']})
        message=wait(runtime,b,marks,6612,{'SrcUserID':uid_a,'DstUserID':0,'Text':params['text']})
        assert message.fields.get('MsgID') is not None and str(message.fields['MsgID']), '必须取得实际服务器消息标识'
    elif scenario in {'self_av','mute'}:
        actor=b if scenario=='mute' else a
        kind=params.get('kind','audio'); dev=device(runtime,actor,kind); key='Audio' if kind=='audio' else 'Video'
        marks=runtime.mark_all_events(); av(runtime,actor,kind,True,dev)
        peer=a if actor==b else b
        wait(runtime,peer,marks,6609,{'UserID':runtime.variables[actor+'.room_user_id'],key+'.ID':dev,key+'.State':2})
        marks=runtime.mark_all_events()
        if scenario=='mute': runtime.call(a,'mute_all',{})
        else: av(runtime,actor,kind,False,dev)
        for receiver in (a,b):
            wait(runtime,receiver,marks,6609,{'UserID':runtime.variables[actor+'.room_user_id'],key+'.ID':dev,key+'.State':0})
    elif scenario=='role':
        from .web_assertions import permission_snapshot,role_restored
        from .web_full_runtime import clear_roles
        clear_roles(runtime,a)
        baseline=permission_snapshot(runtime,a)
        role(runtime,a,params['role'],True)
        role(runtime,a,params['role'],False)
        role_restored(runtime,a,baseline)
    elif scenario=='info':
        for alias in (a,b):
            value=runtime.call(a,'get_user_info',{'user_id':runtime.variables[alias+'.room_user_id']})
            expected=runtime.profiles[alias].account['username']
            assert str(value.get('UserID'))==str(runtime.variables[alias+'.room_user_id']), '查询资料用户ID与目标不符'
            if alias==a:
                profile=runtime.call(a,'get_authenticated_profile',{})
                actual=profile.get('userName',profile.get('username'))
                attach_json(profile,'SDK公开账号查询的实际资料')
            else:
                actual=value.get('Name')
            assert str(actual)==expected, f'实际用户名与独立账号不符: {actual}'
            nodes=[]
            for event in runtime.clients[a].snapshot_events(0):
                if event.get('cmdid')==6602:
                    nodes.extend(n for n in _nodes([event.get('fields',{}).get('User')])
                                 if str(n.get('UserID'))==str(runtime.variables[alias+'.room_user_id']))
                elif alias==a and event.get('cmdid')==6601:
                    node=event.get('fields',{}).get('UserInfo')
                    if isinstance(node,dict):nodes.append(node)
            assert nodes and nodes[-1].get('DisplayName') is not None,'缺少目标用户的实际昵称源'
            assert value.get('DisplayName')==nodes[-1]['DisplayName'],'昵称与同一目标实际登录/成员节点不符'
            if alias==a:assert value['DisplayName']==profile['displayName'],'本人昵称与独立账户资料不符'
            snapshot=[e for e in runtime.clients[a].snapshot_events(0) if e.get('cmdid') in (6601,6602)]
            assert alias==a or any(expected in field_values(e.get('fields',{}),'User.Name') or
                       expected in field_values(e.get('fields',{}),'UserInfo.Name') for e in snapshot), '资料没有真实成员消息来源'
    elif scenario in {'remote','direct'}:
        kind=params['kind']; key='Audio' if kind=='audio' else 'Video'; dev=device(runtime,b,kind)
        acting_role=params.get('role','manager')
        if 'role' in params:
            other='presenter' if acting_role=='manager' else 'manager'
            if int(user(runtime,a).get('DataState' if other=='presenter' else 'ManagerState') or 0):
                role(runtime,a,other,False)
        role(runtime,a,acting_role,True)
        if 'role' in params:
            state=user(runtime,a)
            assert int(state.get('DataState' if acting_role=='presenter' else 'ManagerState') or 0)==2
            assert int(state.get('ManagerState' if acting_role=='presenter' else 'DataState') or 0)==0
        # 查询实际会议配置；不从会议名称推测确认路径。
        logins=[e for e in runtime.clients[b].snapshot_events(0) if e.get('cmdid')==6601]
        assert logins, '缺少实际会议登录配置'
        values=field_values(logins[-1]['fields'],'RoomInfo.RoomConfig.EnableForceOperateUserAV')
        if not values: values=field_values(logins[-1]['fields'],'RoomInfo.EnableForceOperateUserAV')
        assert values and str(values[0])==('1' if scenario=='direct' else '0'), f'会议配置前置不符: {values}'
        marks=runtime.mark_all_events()
        if runtime.clients[a].client_type=='web':
            runtime.call(a,'request_remote_av',{'kind':kind,'dst_user_id':uid_b,'dev_id':dev})
        elif scenario=='direct':
            runtime.call(a,'set_user_audio' if kind=='audio' else 'set_user_video',{'user_id':uid_b,'state':2,'dev_id':dev})
        else:
            runtime.call(a,'request_remote_av',{'dst_user_id':uid_b,'audio_ids' if kind=='audio' else 'video_ids':[dev]})
        if scenario=='remote':
            request=wait(runtime,b,marks,6642,{'SrcUserID':uid_a,'DstUserID':uid_b,key+'.ID':dev})
            for receiver in (a,b): no_event(runtime,receiver,marks,6609,{'UserID':uid_b,key+'.State':2})
            if runtime.clients[b].client_type=='web':
                runtime.call(b,'respond_remote_av',{'kind':kind,'accept':params['accept']})
            else:
                runtime.call(b,'respond_remote_av',{'request':request.fields,'accept':params['accept']})
            response=wait(runtime,a,marks,6643,{'SrcUserID':uid_b,'DstUserID':uid_a,key+'.ID':dev})
            assert (int(response.fields['Result'])==0)==params['accept']
        if scenario=='direct' or params['accept']:
            for receiver in (a,b): wait(runtime,receiver,marks,6609,{'UserID':uid_b,key+'.ID':dev,key+'.State':2})
            closed=runtime.mark_all_events()
            av(runtime,b,kind,False,dev)
            for receiver in (a,b):wait(runtime,receiver,closed,6609,{'UserID':uid_b,key+'.ID':dev,key+'.State':0})
            from .web_assertions import assert_av_state
            assert_av_state(runtime,b,kind,dev,0)
        else:
            for receiver in (a,b): no_event(runtime,receiver,marks,6609,{'UserID':uid_b,key+'.State':2})
        if scenario=='direct': no_event(runtime,b,marks,6642,{'SrcUserID':uid_a,'DstUserID':uid_b})
    elif scenario in {'screen','whiteboard'}:
        if not params.get('as_attendee'):
            role(runtime,a,'presenter',True)
        else:
            assert int(user(runtime,a).get('DataState') or 0)==0 and int(user(runtime,a).get('ManagerState') or 0)==0, '共享必须保持普通参会人身份'
        marks=runtime.mark_all_events()
        result=runtime.call(a,'start_screen_share' if scenario=='screen' else 'start_whiteboard',{})
        if scenario=='screen':
            wait(runtime,b,marks,6609,{'UserID':uid_a,'VNCState':2})
        else:
            assert result and result.get('mediaId'), 'SDK未返回本次白板ID'
            event=wait_whiteboard(runtime,b,marks,result['mediaId'])
            paas=[e for e in runtime.clients[a].snapshot_events(marks[a]) if e.get('source')=='paas' and e.get('cmdid')==19001]
            assert paas and any(str(e['fields'].get('board_id'))==str(result['mediaId']) and int(e['fields'].get('result',-1))==0 for e in paas), '缺少真实白板创建结果'
        from .web_full_scenarios import stop_share
        stop_share(runtime,a,'screen' if scenario=='screen' else 'whiteboard',result)
    elif scenario=='record':
        current=user(runtime,a)
        if params['role']=='attendee':
            for existing,key in (('presenter','DataState'),('manager','ManagerState')):
                if int(current.get(key) or 0)==2:
                    role(runtime,a,existing,False)
                    current=user(runtime,a)
            assert int(current.get('DataState') or 0)==0 and int(current.get('ManagerState') or 0)==0, '普通成员角色前置失败'
        else:
            other='manager' if params['role']=='presenter' else 'presenter'
            other_key='ManagerState' if other=='manager' else 'DataState'
            if int(current.get(other_key) or 0)==2: role(runtime,a,other,False)
            role(runtime,a,params['role'],True)
        from .web_assertions import assert_sent,RECORD_PERMISSION_DENIED
        if params['role']=='attendee':
            permissions=runtime.call(a,'get_effective_permissions',{})
            assert str(permissions.get('ServerRecord',0))=='0','普通成员必须没有云录制权限'
        marks=runtime.mark_all_events();out=runtime.mark_all_outbound(); runtime.call(a,'start_record',{})
        assert_sent(runtime,a,out,6510,{'ServerRecordStatus':2})
        if params['role']=='attendee':
            # 只认实际失败消息，既不把本地成功回调当成功，也不把任意超时当拒绝。
            event=runtime.clients[a].wait_event(EventExpectation(cmdid=6610,kind='notify'),marks[a],runtime.event_timeout)
            assert int(event.fields.get('Error',0))==RECORD_PERMISSION_DENIED, '收到的并非云录制权限拒绝'
            assert str(event.fields.get('SrcUserID'))==str(uid_a) and str(event.fields.get('ServerRecordStatus'))=='0'
            attach_json(event,'普通成员实际权限拒绝（排除其他录制错误）')
            for receiver in (a,b): no_event(runtime,receiver,marks,6610,{'ServerRecordStatus':2})
        else:
            for receiver in (a,b):
                event=wait(runtime,receiver,marks,6610,{'ServerRecordStatus':2,'SrcUserID':uid_a,'ServerRecordType':0})
                assert int(event.fields.get('Error',0))==0,'录制开启含失败错误'
            marks=runtime.mark_all_events();out=runtime.mark_all_outbound();runtime.call(a,'stop_record',{})
            assert_sent(runtime,a,out,6510,{'ServerRecordStatus':0})
            for receiver in (a,b):
                event=wait(runtime,receiver,marks,6610,{'ServerRecordStatus':0,'SrcUserID':uid_a,'ServerRecordType':0})
                assert int(event.fields.get('Error',0))==0,'录制停止含失败错误'
    elif scenario in {'lock','kick','close'}:
        if scenario=='lock':
            runtime.variables['flow.unjoined']=[b]
            role(runtime,a,'manager',True)
            locked=runtime.mark_all_events();runtime.call(a,'lock_room',{})
            locked_event=wait(runtime,a,locked,6610,{'Lock':1})
            handle='web_locked_join'
            # 真实SDK正常入会方法；等待服务器拒绝/排队证据。
            import threading
            error=[]
            def joining():
                try: runtime.join(b,runtime.variables['meeting.room_id'])
                except Exception as exc: error.append(exc)
            joining_marks=runtime.mark_all_events()
            thread=threading.Thread(target=joining,daemon=True); thread.start()
            def finish_join():
                thread.join(timeout=runtime.event_timeout)
                if thread.is_alive():raise RuntimeError('锁会登录异步任务清理后仍未结束')
            runtime.register_cleanup(finish_join)
            event=runtime.clients[b].native_client.events.wait(EventExpectation(cmdid=6617,kind='notify'),joining_marks[b],runtime.event_timeout)
            assert event, '未收到Web实际锁会等待通知；不能以登录超时代替'
            from .web_assertions import observe_no
            observe_no(runtime,joining_marks,{
                a:[lambda r:r.get('cmdid')==6602],
                b:[lambda r:r.get('cmdid')==6601 and str(r.get('error_code'))=='0']},'锁定期间不得完成入会')
            assert thread.is_alive() and not error,'锁定期间入会调用不应成功或异常退出'
            unlocked=runtime.mark_all_events();runtime.call(a,'unlock_room',{})
            unlocked_event=wait(runtime,a,unlocked,6610,{'Lock':0})
            thread.join(timeout=runtime.event_timeout)
            assert not thread.is_alive() and not error, f'解锁后入会失败: {error}'
            wait(runtime,a,unlocked,6602,{'User.UserID':runtime.variables[b+'.room_user_id']})
            assert runtime.is_healthy(),'解锁入会后连接不健康'
            from .web_assertions import validate_lock_source
            for notice in (locked_event,unlocked_event):validate_lock_source(notice,uid_a)
            attach_json({'locked':locked_event,'unlocked':unlocked_event,'operator_user_id':uid_a,
                'confirmed_contract':'锁会路径允许省略SrcUserID；如提供则必须正确；Lock、等待、解锁入会、成员及健康均已检查'},'完整锁会业务及可选来源协议证据')
            return
        role(runtime,a,'manager',True)
        marks=runtime.mark_all_events()
        runtime.call(a,'kick_user',{'user_id':uid_b}) if scenario=='kick' else runtime.call(a,'close_room',{})
        if scenario=='kick':
            event=wait(runtime,b,marks,6606,{'UserID':uid_b,'KickID':uid_a})
            assert int(event.fields.get('KickType') or 0)==0,'不是正常请出路径'
            from .web_assertions import assert_departed
            assert_departed(runtime,b,uid_b,marks)
        else:
            for receiver in runtime.clients:
                wait(runtime,receiver,marks,6607,{'Reason':0})
        deadline=time.monotonic()+runtime.event_timeout
        while runtime.clients[b].is_healthy() and time.monotonic()<deadline: time.sleep(0.05)
        assert not runtime.clients[b].is_healthy(), '收到退出通知但连接未结束'
        if scenario=='close':
            for receiver in runtime.clients:
                deadline=time.monotonic()+runtime.event_timeout
                while runtime.clients[receiver].is_healthy() and time.monotonic()<deadline:time.sleep(0.02)
                assert not runtime.clients[receiver].is_healthy(),'关会必须结束所有在会端'
        return
    else:
        raise ValueError(scenario)
    assert runtime.is_healthy(), '关键流程业务完成但连接不健康'
