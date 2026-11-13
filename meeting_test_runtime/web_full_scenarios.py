"""Expanded protocol workflows; requests only use native public client methods."""
import time
from .models import EventExpectation
from .web_key_scenarios import execute_key_flow, wait, no_event, user, device, av, role, wait_whiteboard, _board_id32, _nodes
from .web_full_runtime import clear_roles, establish_role
from .web_protocol import matches_fields, field_values
from .allure_reporting import attach_json, report_step, alias_name


def assert_av(runtime, marks, alias, kind, dev, state):
    key="Audio" if kind=="audio" else "Video"
    uid=runtime.variables[alias+".room_user_id"]
    return {receiver:wait(runtime,receiver,marks,6609,{"UserID":uid,key+".ID":dev,key+".State":state})
            for receiver in runtime.clients}


def no_av(runtime, marks, alias, kind, state=2):
    key="Audio" if kind=="audio" else "Video"
    uid=runtime.variables[alias+".room_user_id"]
    for receiver in runtime.clients:
        no_event(runtime,receiver,marks,6609,{"UserID":uid,key+".State":state})


def public_chat(runtime, actor, text):
    marks=runtime.mark_all_events()
    runtime.call(actor,"chat",{"text":text})
    peers=[alias for alias in runtime.clients if alias!=actor]
    ids=[]
    for peer in peers:
        event=wait(runtime,peer,marks,6612,{"SrcUserID":runtime.variables[actor+".room_user_id"],"DstUserID":0,"Text":text})
        message=event.fields.get("MsgID")
        assert message is not None and str(message),"必须有服务器消息标识，不能用SDK本地时间戳"
        ids.append(str(message))
    assert len(set(ids))==1,"广播消息各端MsgID不一致"
    return ids[0]


def start_share(runtime, alias, kind):
    marks=runtime.mark_all_events();out=runtime.mark_all_outbound()
    if runtime.clients[alias].client_type=="fm":
        assert kind=="screen","FM白板没有公开入口"
        runtime.call(alias,"request_right",{"right_type":4})
        runtime.call(alias,"start_aux_stream",{"kind":"vnc"})
        result=None
    else:
        result=runtime.call(alias,"start_screen_share" if kind=="screen" else "start_whiteboard",{})
    for peer in runtime.clients:
        if peer==alias:continue
        if kind=="screen":wait(runtime,peer,marks,6609,{"UserID":runtime.variables[alias+".room_user_id"],"VNCState":2})
        else:
            expected_id=_board_id32(str(result["mediaId"]).rsplit(';',1)[-1])
            positions=[];deadline=time.monotonic()+runtime.event_timeout
            while not positions and time.monotonic()<deadline:
                for request in runtime.clients[alias].snapshot_outbound(out[alias]):
                    if request.get('cmdid')!=6513:continue
                    for item in _nodes(field_values(request['fields'],'Screen.DataArea.Item')):
                        if str(item.get('Type'))=='1' and _board_id32(item['Data'])==expected_id:
                            positions.append(int(item['Pos']))
                if not positions:time.sleep(0.02)
            assert positions,'实际6513没有本次白板资源及位置'
            wait_whiteboard(runtime,peer,marks,result["mediaId"],expected_pos=positions[-1])
    if kind=="whiteboard":
        events=runtime.clients[alias].native_client.events
        created=events.wait(EventExpectation(source="paas",cmdid=19001,kind="response",match={"board_id":result["mediaId"],"result":0}),marks[alias],runtime.event_timeout)
        assert created,"缺少本次白板创建Response"
    return result


def stop_share(runtime, alias, kind, result=None):
    marks=runtime.mark_all_events();out=runtime.mark_all_outbound()
    if runtime.clients[alias].client_type=="fm":
        runtime.call(alias,"stop_aux_stream",{"kind":"vnc"})
        runtime.call(alias,"release_right",{"right_type":4})
    else:runtime.call(alias,"stop_screen_share" if kind=="screen" else "stop_whiteboard",{})
    if kind=="screen":
        for peer in runtime.clients:
            if peer!=alias:wait(runtime,peer,marks,6609,{"UserID":runtime.variables[alias+".room_user_id"],"VNCState":0})
    else:
        journal=runtime.clients[alias].native_client.events
        event=journal.wait(EventExpectation(source="paas",cmdid=19003,kind="response",match={"result":0}),marks[alias],runtime.event_timeout)
        assert event,"白板关闭Response未收到"
        requests=[r for r in runtime.clients[alias].snapshot_outbound(out[alias]) if r.get("cmdid")==19002]
        assert len(requests)==1 and str(requests[0]["fields"].get("board_id"))==str(result["mediaId"])
        assert event.connection_id==requests[0]["connection_id"],"白板关闭回复连接不符"
        attach_json({"request":requests[0],"response":event},"本次白板关闭请求与Response关联")
        releases=[r for r in runtime.clients[alias].snapshot_outbound(out[alias]) if r.get('cmdid')==6530 and
                  str(r.get('fields',{}).get('Operation'))=='0']
        for request in releases:
            f=request['fields']
            released=runtime.clients[alias].wait_event(EventExpectation(cmdid=6630,kind='response',
                match={'Operation':0,'RightType':f['RightType'],'RightIndex':f.get('RightIndex',0),'OccupiedUserID':0}),
                marks[alias],runtime.event_timeout)
            assert str(getattr(released,'error_code',0))=='0','白板关闭后的操作权释放失败'
            attach_json({'request':request,'response':released},'白板关闭操作权释放屏障')
        runtime.call(alias,'get_identity',{})
        for peer in runtime.clients:
            if peer==alias:continue
            if runtime.clients[peer].client_type=='web':
                event=runtime.clients[peer].wait_event(EventExpectation(source='paas',cmdid=14106,kind=None,
                    match={'user_id':runtime.variables[alias+'.room_user_id'],'media_id':result['mediaId'],'operation':0}),marks[peer],runtime.event_timeout)
                attach_json(event,alias_name(peer)+'收到本次白板资源取消发布')
            else:
                wait(runtime,peer,marks,6609,{'UserID':runtime.variables[alias+'.room_user_id'],'DataSharerState':0})


def remote_open(runtime, actor, target, kind, path, marks, dev):
    uid_a=runtime.variables[actor+".room_user_id"];uid_b=runtime.variables[target+".room_user_id"]
    key="Audio" if kind=="audio" else "Video"
    runtime.call(actor,"request_remote_av",{"dst_user_id":uid_b,"kind":kind,"dev_id":dev})
    if path!="direct":
        request=wait(runtime,target,marks,6642,{"SrcUserID":uid_a,"DstUserID":uid_b,key+".ID":dev})
        no_av(runtime,marks,target,kind)
        if runtime.clients[target].client_type=="web":
            runtime.call(target,"respond_remote_av",{"kind":kind,"accept":path=="accept"})
        else:runtime.call(target,"respond_remote_av",{"request":request.fields,"accept":path=="accept"})
        response=wait(runtime,actor,marks,6643,{"SrcUserID":uid_b,"DstUserID":uid_a,key+".ID":dev})
        assert "Result" in response.fields and (int(response.fields["Result"])==0)==(path=="accept")
    if path=="reject":no_av(runtime,marks,target,kind)
    else:assert_av(runtime,marks,target,kind,dev,2)
    if path=="direct":no_event(runtime,target,marks,6642,{"SrcUserID":uid_a,"DstUserID":uid_b,key+".ID":dev})


def verify_remote_template(runtime, alias, direct):
    logins=[e for e in runtime.clients[alias].snapshot_events(0) if e.get("cmdid")==6601]
    assert logins,"缺少实际会议配置"
    values=field_values(logins[-1]["fields"],"RoomInfo.RoomConfig.EnableForceOperateUserAV")
    if not values:values=field_values(logins[-1]["fields"],"RoomInfo.EnableForceOperateUserAV")
    assert values and str(values[0])==("1" if direct else "0"),f"远程确认配置前置不符: {values}"


def execute_full_case(case, runtime):
    if case.steps:
        from .web_full_steps import execute_steps
        execute_steps(case,runtime);return
    scenario=case.scenario;params=case.params
    a,b="operator","observer"
    if scenario in {"login","chat","self_av","mute","role","info","remote","direct","screen","whiteboard","record","lock","kick","close"}:
        execute_key_flow(case.spec(),runtime);return
    if scenario=="guest_login":
        execute_key_flow({**case.spec(),"scenario":"login"},runtime);return
    if scenario=="guest_av":
        clear_roles(runtime,a)
        dev=device(runtime,a,params["kind"]);marks=runtime.mark_all_events()
        av(runtime,a,params["kind"],True,dev);assert_av(runtime,marks,a,params["kind"],dev,2)
        marks=runtime.mark_all_events();av(runtime,a,params["kind"],False,dev);assert_av(runtime,marks,a,params["kind"],dev,0)
    elif scenario=="guest_role":
        from .web_assertions import permission_snapshot,role_restored
        clear_roles(runtime,a);baseline=permission_snapshot(runtime,a)
        establish_role(runtime,a,params["role"])
        role(runtime,a,params["role"],False)
        role_restored(runtime,a,baseline)
    elif scenario in {"guest_share","share_cycle","whiteboard_cycle"}:
        kind=params.get("kind","whiteboard")
        if scenario=='guest_share':clear_roles(runtime,a)
        else:establish_role(runtime,a,'presenter')
        first=start_share(runtime,a,kind);stop_share(runtime,a,kind,first)
        second=start_share(runtime,a,kind);stop_share(runtime,a,kind,second)
        if kind=="whiteboard":
            assert first["mediaId"]!=second["mediaId"],"两次新建白板不能关联同一个资源"
            requests=[r for r in runtime.clients[a].snapshot_outbound(0) if r.get('cmdid')==6513 and
                      any(str(i.get('Data'))==str(second['mediaId']).rsplit(';',1)[-1] for i in _nodes(field_values(r['fields'],'Screen.DataArea.Item')))]
            assert requests,'新白板缺少实际广播'
            old=_board_id32(str(first['mediaId']).rsplit(';',1)[-1])
            assert all(_board_id32(i['Data'])!=old for i in _nodes(field_values(requests[-1]['fields'],'Screen.DataArea.Item'))
                       if str(i.get('Type'))=='1' and i.get('Data') not in (None,0,'0')),'新建广播仍携带已关闭的旧白板'
    elif scenario in {"approval","av_cancel"}:
        kind=params["kind"]
        establish_role(runtime,b,params.get("role","manager"))
        clear_roles(runtime,a)
        dev=device(runtime,a,kind);marks=runtime.mark_all_events()
        av(runtime,a,kind,True,dev);assert_av(runtime,marks,a,kind,dev,1)
        no_av(runtime,marks,a,kind)
        marks=runtime.mark_all_events()
        if scenario=="av_cancel" or params.get("decision")=="cancel":av(runtime,a,kind,False,dev);desired=0
        else:
            runtime.call(b,"approve_self_av",{"user_id":runtime.variables[a+".room_user_id"],"kind":kind,"dev_id":dev});desired=2
        assert_av(runtime,marks,a,kind,dev,desired)
        if desired==2:
            marks=runtime.mark_all_events();av(runtime,a,kind,False,dev);assert_av(runtime,marks,a,kind,dev,0)
    elif scenario=="chat_roundtrip":
        previous=[]
        for actor in runtime.clients:
            with report_step(alias_name(actor)+"发送公共消息，全部对端校验真实消息"):
                previous.append(public_chat(runtime,actor,params["text"]))
        if params["repeat"]:
            for actor in runtime.clients:previous.append(public_chat(runtime,actor,params["text"]))
        assert len(set(previous))==len(previous),"重复发送不能由旧消息或同一MsgID重复证明"
    elif scenario=="av_cycle":
        kind=params["kind"]
        for actor in runtime.clients:
            clear_roles(runtime,actor)
            dev=device(runtime,actor,kind)
            for state in (True,False,True,False):
                marks=runtime.mark_all_events();av(runtime,actor,kind,state,dev);assert_av(runtime,marks,actor,kind,dev,2 if state else 0)
    elif scenario=="rejoin":
        old=runtime.variables[b+".room_user_id"];marks=runtime.mark_all_events()
        runtime.call(b,"leave" if runtime.clients[b].client_type=="web" else "conf_logout",{})
        wait(runtime,a,marks,6605,{"UserID":old})
        runtime.replace_client(b);marks=runtime.mark_all_events()
        runtime.join(b,runtime.variables["meeting.room_id"])
        wait(runtime,a,marks,6602,{"User.UserID":runtime.variables[b+".room_user_id"]})
    elif scenario=="mixed_control":
        kind=params["kind"];path=params["path"]
        establish_role(runtime,a,params["role"])
        verify_remote_template(runtime,a,path=="direct")
        for target in (b,"witness"):
            clear_roles(runtime,target)
            dev=device(runtime,target,kind);marks=runtime.mark_all_events()
            remote_open(runtime,a,target,kind,path,marks,dev)
            untouched="witness" if target==b else b
            no_av(runtime,marks,untouched,kind)
            if path!="reject":
                marks=runtime.mark_all_events()
                runtime.call(a,"close_remote_av",{"user_id":runtime.variables[target+".room_user_id"],"kind":kind,"dev_id":dev})
                assert_av(runtime,marks,target,kind,dev,0)
    elif scenario=="manager_wrong_password":
        clear_roles(runtime,a);marks=runtime.mark_all_events()
        try:runtime.call(a,"request_manager",{"password":"wrong-e2e-chair"})
        except Exception as exc:attach_json({"type":type(exc).__name__,"detail":getattr(exc,"detail",None)},"SDK管理员失败回调")
        event=runtime.clients[a].wait_event(EventExpectation(cmdid=6611,kind="response"),marks[a],runtime.event_timeout)
        from fm_client.errcodes import CHAIR_PWDERR
        assert int(event.fields['Result'])==CHAIR_PWDERR and int(event.fields['ChairRight'])==2,'必须是本次管理员申请密码错误'
        attach_json(event,'本次管理员申请实际密码错误Response')
        for receiver in runtime.clients:no_event(runtime,receiver,marks,6609,{"UserID":runtime.variables[a+".room_user_id"],"ManagerState":2})
    elif scenario=="role_repeated":
        establish_role(runtime,a,params["role"])
        assert params["role"]=="manager","重复主持申请为toggle，须独立定义产品语义"
        marks=runtime.mark_all_events();out=runtime.mark_all_outbound()
        runtime.call(a,"request_manager",{"password":runtime.variables["env.chair_password"]})
        assert int(user(runtime,a).get("ManagerState") or 0)==2
        assert not any(r.get("cmdid")==6511 for r in runtime.snapshot_all_outbound(out).get(a,[])),"已取得管理员应走SDK本地幂等分支"
        attach_json({"evidence":"SDK_LOCAL","not_server_response":True},"重复管理员申请证据类型")
    elif scenario=='chat_denied':
        clear_roles(runtime,a);permissions=runtime.call(a,'get_effective_permissions',{})
        assert str(permissions.get('OperatePublicChat','0'))!='1','禁止群聊前置未生效'
        marks=runtime.mark_all_events();out=runtime.mark_all_outbound();text='E2E-禁止群聊-'+case.case_id
        rejected=False
        try:runtime.call(a,'chat',{'text':text})
        except Exception as exc:
            assert getattr(exc,'origin',None)=='sdk' and isinstance(getattr(exc,'detail',None),dict) and exc.detail.get('msg')
            assert exc.detail.get('msg')=='您暂无权限操作！','SDK错误必须明确为群聊权限拒绝，不能以发送失败替代'
            attach_json({'source':'SDK_LOCAL','detail':exc.detail},'真实SDK群聊权限拒绝');rejected=True
        assert rejected,'无权限时SDK没有拒绝'
        assert not any(r.get('cmdid')==6512 for r in runtime.snapshot_all_outbound(out).get(a,[]))
        for peer in runtime.clients:no_event(runtime,peer,marks,6612,{'Text':text,'SrcUserID':runtime.variables[a+'.room_user_id']})
    elif scenario=='remote_denied':
        clear_roles(runtime,a);kind=params['kind'];key='Audio' if kind=='audio' else 'Video'
        permissions=runtime.call(a,'get_effective_permissions',{})
        assert str(permissions.get('BroadcastOthers'+key,'0'))!='1','普通成员必须没有远程广播权限'
        dev=device(runtime,b,kind);marks=runtime.mark_all_events()
        out=runtime.mark_all_outbound()
        runtime.call(a,'request_remote_av',{'dst_user_id':runtime.variables[b+'.room_user_id'],'kind':kind,'dev_id':dev})
        from .web_assertions import assert_sent,assert_non_target
        if case.template=='DIRECT':assert_sent(runtime,a,out,6509,{'UserID':runtime.variables[b+'.room_user_id'],key+'.ID':dev,key+'.State':2})
        else:assert_sent(runtime,a,out,6542,{'SrcUserID':runtime.variables[a+'.room_user_id'],'DstUserID':runtime.variables[b+'.room_user_id'],key+'.ID':dev,'Type':0})
        assert_non_target(runtime,marks,b,kind,dev,0,a)
        no_av(runtime,marks,b,kind)
        no_event(runtime,b,marks,6642,{'SrcUserID':runtime.variables[a+'.room_user_id'],'DstUserID':runtime.variables[b+'.room_user_id'],key+'.ID':dev})
        public_chat(runtime,a,'E2E-普通成员无权限会控-'+case.case_id)
        attach_json({'permission':permissions,'contract':'仅主持/管理员可控制他人；无成功状态/确认邀请及后续连接可用，不等待不存在的6509同步Response'},'普通成员会控依据')
    elif scenario=='share_takeover':
        for alias in runtime.clients:clear_roles(runtime,alias)
        first=start_share(runtime,a,'screen');stop_share(runtime,a,'screen',first)
        second=start_share(runtime,b,'screen');stop_share(runtime,b,'screen',second)
    elif scenario=='share_contention':
        clear_roles(runtime,a);clear_roles(runtime,b)
        first=start_share(runtime,a,'screen');marks=runtime.mark_all_events()
        try:runtime.call(b,'start_screen_share',{})
        except Exception as exc:attach_json({'origin':getattr(exc,'origin',None),'detail':getattr(exc,'detail',None)},'共享争抢的真实SDK拒绝')
        event=runtime.clients[b].wait_event(EventExpectation(cmdid=6630,kind='response',match={'RightType':4,'Operation':1}),marks[b],runtime.event_timeout)
        code=event.error_code if getattr(event,'error_code',None) is not None else event.fields.get('Result')
        from .web_assertions import SHARE_START_OCCUPIED
        assert code is not None and int(code)==SHARE_START_OCCUPIED,'必须收到Web被占用辅流的正式失败码'
        assert str(event.fields.get('OccupiedUserID'))==str(runtime.variables[a+'.room_user_id']),'争抢失败必须关联当前共享者'
        for receiver in runtime.clients:no_event(runtime,receiver,marks,6609,{'UserID':runtime.variables[b+'.room_user_id'],'VNCState':2})
        stop_share(runtime,a,'screen',first)
        next_share=start_share(runtime,b,'screen');stop_share(runtime,b,'screen',next_share)
    elif scenario=="fm_screen":
        role(runtime,a,"presenter",True)
        result=start_share(runtime,a,"screen");stop_share(runtime,a,"screen",result)
    elif scenario=='fm_whiteboard':
        from .web_assertions import assert_sent
        # Protocol view only, explicitly approved scope. No invented board ID/resource.
        acquired=runtime.call(a,'request_right',{'right_type':1})
        assert str(acquired.get('Result'))=='0','白板视图前置布局权未取得'
        assert str(acquired.get('OccupiedUserID'))==str(runtime.variables[a+'.room_user_id']) and str(acquired.get('RightType'))=='1' and str(acquired.get('Operation'))=='1'
        for mode,active in ((2,1),(3,6)):
            marks=runtime.mark_all_events();out=runtime.mark_all_outbound()
            runtime.call(a,'set_window_state',{'mode':mode,'data_active':active})
            request=assert_sent(runtime,a,out,6513,{'Mode':mode,'DataActive':active})
            # Web proxy converts legacy Mode/DataActive into Screen layout/items.
            # Assert the corresponding current packet, not absent legacy keys.
            event=wait(runtime,b,marks,6613,{'Screen.ScreenID':0,'Screen.LayoutMode':mode,
                'Screen.DataArea.ID':1,'Screen.DataArea.Item.Type':active,'Screen.DataArea.Item.Data':0})
            attach_json({'request':request,'notify':event,'scope':'白板视图协议及恢复，不创建资源、不验证白板内容或页面布局跟随'},'FM白板视图协议闭环')
        released=runtime.call(a,'release_right',{'right_type':1})
        assert str(released.get('Result'))=='0','白板视图布局权未释放'
        assert str(released.get('RightType'))=='1' and str(released.get('Operation'))=='0'
    elif scenario=='remote_target_left':
        from .web_assertions import assert_departed,assert_av_state,observe_no
        establish_role(runtime,a,params['role'])
        kind=params['kind'];key='Audio' if kind=='audio' else 'Video'
        target_uid=runtime.variables[b+'.room_user_id'];dev=device(runtime,b,kind)
        actor_dev=device(runtime,a,kind)
        from .web_assertions import av_state
        baseline=av_state(runtime,a,kind,actor_dev)
        marks=runtime.mark_all_events();runtime.call(b,'leave',{})
        assert_departed(runtime,b,target_uid,marks)
        marks=runtime.mark_all_events();out=runtime.mark_all_outbound()
        try:runtime.call(a,'request_remote_av',{'user_id':target_uid,'kind':kind,'dev_id':dev})
        except Exception as exc:
            assert getattr(exc,'origin',None)=='adapter' and getattr(exc,'detail',{}).get('code')=='TARGET_NOT_IN_MEETING', '不是公开测试门面的明确本地拒绝'
            assert '目标不在当前成员缓存' in str(getattr(exc,'detail',exc)), '不是明确的离会目标本地拒绝'
            attach_json({'target_user_id':target_uid,'device_id':dev,'exception_type':type(exc).__name__,
                'detail':getattr(exc,'detail',str(exc)),'evidence':'SDK_LOCAL'},'已离会目标本地拒绝')
        else:raise AssertionError('离会目标远控没有在本地拒绝')
        assert not any(r.get('cmdid') in (6509,6542) for r in runtime.clients[a].snapshot_outbound(out[a])), '本地拒绝仍发送了音视频远控请求'
        assert not any(r.get('trace_type')=='sdk_call' and r.get('method_name')=='HST.boradOthersMedia'
                       for r in runtime.clients[a].snapshot_outbound(out[a])), '目标不存在仍调用了SDK远程开启'
        observe_no(runtime,marks,{a:[lambda r:r.get('cmdid') in (6609,6642)]},'离会目标请求不得产生音视频变更或邀请')
        assert_av_state(runtime,a,kind,actor_dev,baseline)
        assert runtime.clients[a].is_healthy(),'离会目标请求破坏操作端连接'
        assert runtime.call(b,'get_identity',{}).get('joined') is False
        return
    elif scenario=='remote_overlap':
        from .web_assertions import assert_sent,assert_av_state,validate_latest_remote_reply
        c='witness';kind=params['kind'];key='Audio' if kind=='audio' else 'Video'
        for controller in (a,b):establish_role(runtime,controller,'manager')
        verify_remote_template(runtime,a,False)
        dev=device(runtime,c,kind)
        uid_a,uid_b,uid_c=(runtime.variables[x+'.room_user_id'] for x in (a,b,c))
        for accepted in (True,False):
            assert_av_state(runtime,c,kind,dev,0)
            marks=runtime.mark_all_events();out=runtime.mark_all_outbound()
            runtime.call(a,'request_remote_av',{'user_id':uid_c,'kind':kind,'dev_id':dev})
            assert_sent(runtime,a,out,6542,{'SrcUserID':uid_a,'DstUserID':uid_c,key+'.ID':dev,'Type':0})
            first=wait(runtime,c,marks,6642,{'SrcUserID':uid_a,'DstUserID':uid_c,key+'.ID':dev})
            second=runtime.mark_all_events();out=runtime.mark_all_outbound()
            runtime.call(b,'request_remote_av',{'user_id':uid_c,'kind':kind,'dev_id':dev})
            assert_sent(runtime,b,out,6542,{'SrcUserID':uid_b,'DstUserID':uid_c,key+'.ID':dev,'Type':0})
            latest=wait(runtime,c,second,6642,{'SrcUserID':uid_b,'DstUserID':uid_c,key+'.ID':dev})
            no_av(runtime,second,c,kind)
            reply=runtime.mark_all_events();out=runtime.mark_all_outbound()
            runtime.call(c,'respond_remote_av',{'kind':kind,'accept':accepted})
            assert_sent(runtime,c,out,6543,{'SrcUserID':uid_c,'DstUserID':uid_b,key+'.ID':dev,'Result':0 if accepted else -1})
            result=wait(runtime,b,reply,6643,{'SrcUserID':uid_c,'DstUserID':uid_b,key+'.ID':dev})
            validate_latest_remote_reply(result,uid_b,uid_c,kind,dev,accepted)
            no_event(runtime,a,reply,6643,{'SrcUserID':uid_c,'DstUserID':uid_a,key+'.ID':dev})
            if accepted:
                assert_av(runtime,reply,c,kind,dev,2)
                marks=runtime.mark_all_events();runtime.call(b,'close_remote_av',{'user_id':uid_c,'kind':kind,'dev_id':dev})
                assert_av(runtime,marks,c,kind,dev,0)
            else:no_av(runtime,reply,c,kind)
            assert_av_state(runtime,c,kind,dev,0)
            attach_json({'first_request':first,'latest_request':latest,'actual_reply':result,'accepted':accepted,
                'contract':'仅答复最新发起人B；A不得收到本次答复；等待及拒绝不打开设备，接受后开启并恢复'},'重叠邀请最新请求及答复归属')
    elif scenario=="role_then_record":
        execute_key_flow({**case.spec(),"scenario":"record"},runtime)
        role(runtime,a,params["role"],False)
        execute_key_flow({**case.spec(),"scenario":"record","params":{"role":"attendee"}},runtime)
    elif scenario=="info_membership":
        uid=runtime.variables[b+".room_user_id"]
        marks=runtime.mark_all_events();runtime.call(b,"leave" if runtime.clients[b].client_type=="web" else "conf_logout",{})
        from .web_assertions import assert_departed
        assert_departed(runtime,b,uid,marks)
        users=runtime.call(a,"get_users",{})
        assert all(str(u.get("UserID"))!=str(uid) for u in users),"真实离会后SDK成员缓存仍有目标"
        assert runtime.clients[a].is_healthy();return
    elif scenario=="login_invalid":
        driver=runtime.clients[a];client=driver.native_client;account=runtime.profiles[a].account
        if params['failure']!='room':
            # Valid control for the same account/environment, then a fresh context:
            # no old SDK token may make the invalid attempt appear authenticated.
            client.call('init',{'ces_addr':runtime.profiles[a].endpoint['ces_addr'],'timeout_ms':30000})
            valid=client.call('login',{'username':account['username'],'password':account['password']})
            assert valid['profile'].get('userName',valid['profile'].get('username'))==account['username']
            runtime.replace_client(a);driver=runtime.clients[a];client=driver.native_client
        client.call("init",{"ces_addr":runtime.profiles[a].endpoint["ces_addr"],"timeout_ms":30000})
        marks=client.events.mark();out=client.mark_outbound();rejected=False
        try:
            if params["failure"]=="room":
                client.call("login",{"username":account["username"],"password":account["password"]})
                client.call("join",{"username":account["username"],"room_id":2147483646})
            else:client.call("login",{"username":"e2e-account-does-not-exist" if params["failure"]=="account" else account["username"],
                                      "password":"invalid-e2e-password"})
        except Exception as exc:
            rejected=True
            attach_json({"type":type(exc).__name__,"detail":getattr(exc,"detail",None)},"实际入会失败")
        assert rejected,'非法凭据/会议调用未拒绝'
        from .web_assertions import oauth_security_rejection
        ces_addr=runtime.profiles[a].endpoint['ces_addr']
        deadline=time.monotonic()+runtime.event_timeout
        # HTTP拒绝可能在SDK失败回调后才到观察器；等真实事件，不补造错误码。
        while time.monotonic()<deadline:
            rows=driver.snapshot_events(marks)
            if any(oauth_security_rejection(r,ces_addr) or
                   r.get('cmdid')==6601 and str(r.get('error_code')) not in ('0','None') for r in rows):break
            time.sleep(0.02)
        for row in driver.snapshot_events(marks):
            if params['failure']!='room' and oauth_security_rejection(row,ces_addr):
                from urllib.parse import urlsplit
                assert any(r.get('trace_type')=='http_request' and r.get('method')=='POST'
                           and urlsplit(r.get('url',''))._replace(query='',fragment='')==urlsplit(row['fields']['url'])._replace(query='',fragment='')
                           for r in client.snapshot_outbound(out)), '必须关联本次真实OAuth POST，不能以其他HTTP400代替'
                assert client.call('get_auth_state',{})=={'has_auth_token':False,'authenticated':False},'非法鉴权留下成功身份/token'
                assert not any(e.get('cmdid')==6601 and str(e.get('error_code'))=='0' for e in driver.snapshot_events(marks))
                assert not any(r.get('cmdid') in (1000,6501) for r in client.snapshot_outbound(out)), '错误凭据不得继续发送入会'
                attach_json({'actual_response':row,'contract':'安全修复正式约定：本次OAuth HTTP400拒绝，不要求业务JSON',
                    'login_rejected':rejected,'auth_state':client.call('get_auth_state',{})},'实际OAuth HTTP400及无成功身份/入会断言')
                assert runtime.clients[b].is_healthy();return
            if row.get("cmdid")==6601 and int(row.get("error_code",0))!=0:
                from fm_client.errcodes import INVITECODE_ERROR
                assert int(row['error_code'])==INVITECODE_ERROR,'无效RoomID必须收到当前入口已定义的明确拒绝，不能接受任意服务器错误'
                attach_json({'requested_room_id':2147483646,'actual_error_code':row['error_code'],
                    'sdk_error_name':'INVITECODE_ERROR','note':'验证非法RoomID不能入会；当前入口返回码名称是邀请码错误，不把它改写为会议不存在'},'非法会议真实入口拒绝')
                assert runtime.clients[b].is_healthy();return
        raise AssertionError("未收到正式鉴权/入会错误，不能以任意超时当拒绝")
    else:raise ValueError(f"场景尚未执行实现: {scenario}")
    assert runtime.is_healthy(),"全量业务完成但连接不健康"
