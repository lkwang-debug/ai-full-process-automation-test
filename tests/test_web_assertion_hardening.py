"""Counterexamples must fail before a real-server result can be called PASS."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from meeting_test_runtime.web_assertions import assert_sent,observe_no,validate_initialization,oauth_security_rejection
from meeting_test_runtime.web_assertions import validate_lock_source,validate_latest_remote_reply
from meeting_test_runtime.web_full_cases import validate_case
from meeting_test_runtime.web_key_scenarios import execute_key_flow
from tools.create_web_full_workbook import design_cases


@pytest.mark.parametrize('scenario',['mixed_control','approval','whiteboard_cycle','rejoin','guest_rejoin','role_then_record','chat_roundtrip'])
def test_every_core_flow_step_is_required(scenario):
    case=next(c for c in design_cases()[0] if c.scenario==scenario and c.steps)
    for index in range(len(case.steps)):
        with pytest.raises(ValueError,match='步骤'):
            validate_case(replace(case,steps=case.steps[:index]+case.steps[index+1:]))


def test_flow_rejects_wrong_target_wrong_media_reordered_or_unrelated_steps():
    case=next(c for c in design_cases()[0] if c.scenario=='mixed_control')
    for index,field,value in ((1,'target','operator'),(1,'kind','video' if case.params['kind']=='audio' else 'audio')):
        rows=deepcopy(list(case.steps));rows[index]['params'][field]=value
        with pytest.raises(ValueError):validate_case(replace(case,steps=tuple(rows)))
    with pytest.raises(ValueError):validate_case(replace(case,steps=tuple(reversed(case.steps))))
    basic=next(c for c in design_cases()[0] if c.scenario=='guest_av')
    with pytest.raises(ValueError):validate_case(replace(basic,steps=(case.steps[0],)))


def test_empty_or_repeated_output_validation_cannot_pass():
    case=next(c for c in design_cases()[0] if c.scenario=='whiteboard_cycle')
    for values in ([],['flow.board.0','flow.board.0']):
        rows=deepcopy(list(case.steps));rows[-1]['params']['variables']=values
        with pytest.raises(ValueError):validate_case(replace(case,steps=tuple(rows)))


def test_guest_rejoin_covers_real_guest_and_all_modes_topologies():
    cases=[c for c in design_cases()[0] if c.scenario=='guest_rejoin']
    assert len(cases)==9
    assert {(c.template,c.combination) for c in cases}=={(m,t) for m in ('discussion','training','control') for t in ('fm->web','web->fm','web->web')}
    for c in cases:
        i=('operator','observer').index(c.params['guest_alias'])
        assert c.identities[i]=='guest' and c.types[i]=='web'


def test_non_target_waiting_state_is_a_failure_even_without_opening():
    row={'cmdid':6609,'fields':{'UserID':2,'Audio':{'ID':0,'State':1}}}
    rt=SimpleNamespace(quiet_timeout=0,clients={'observer':SimpleNamespace(snapshot_events=lambda after:[row])})
    with pytest.raises(AssertionError,match='非目标'):
        observe_no(rt,{'observer':0},{'observer':[lambda e:e['cmdid']==6609]},'非目标被误申请')


@pytest.mark.parametrize('packet',[None,{'UserID':1,'Video':{'ID':99,'State':2}}, {'UserID':99,'Video':{'ID':0,'State':2}}])
def test_negative_case_requires_real_current_target_request(packet):
    rows=[] if packet is None else [{'cmdid':6509,'fields':packet}]
    rt=SimpleNamespace(clients={'operator':SimpleNamespace(snapshot_outbound=lambda after:rows)})
    with pytest.raises(AssertionError):assert_sent(rt,'operator',{'operator':0},6509,{'UserID':1,'Video.ID':0,'Video.State':2})


@pytest.mark.parametrize('mutation',['duplicate','empty_permissions','empty_features'])
def test_initialization_rejects_incomplete_or_conflicting_structures(mutation):
    roles={'Role':[{'RoleCode':'mentee','Permissions':{'ServerRecord':'0'}}]}
    features={'ServerRecord':{'Attr':{'Renders':'highlight'}}}
    if mutation=='duplicate':roles['Role']*=2
    if mutation=='empty_permissions':roles['Role'][0]['Permissions']={}
    if mutation=='empty_features':features={}
    with pytest.raises(AssertionError):validate_initialization(roles,features)


def test_web_initialization_does_not_invent_xml_end_markers():
    validate_initialization({'Role':[{'RoleCode':'mentee','Permissions':{'ServerRecord':'0'}}]},
                            {'ServerRecord':{'Attr':{'Renders':'highlight'}}})


@pytest.mark.parametrize('change',[
    {'source':'sdk'}, {'kind':'notify'},
    {'fields':{'url':'https://example.test/meeting-resource-server/oauth2/token','status':401}},
    {'fields':{'url':'https://example.test/meeting-resource-server/oauth2/token','status':500}},
    {'fields':{'url':'https://other.test/meeting-resource-server/oauth2/token','status':400}},
    {'fields':{'url':'https://example.test/api/v1/room/createInstant','status':400}},
])
def test_other_http_errors_cannot_replace_current_oauth_security_rejection(change):
    row={'source':'http','kind':'response','fields':{'url':'https://example.test/meeting-resource-server/oauth2/token','status':400}}
    row.update(change)
    assert not oauth_security_rejection(row,'https://example.test')


@pytest.mark.parametrize('body',[None,{'text':'<html>request rejected</html>'},{'error':'invalid_grant'}])
def test_confirmed_oauth_security_contract_does_not_require_business_json(body):
    row={'source':'http','kind':'response','fields':{'url':'https://example.test/meeting-resource-server/oauth2/token?grant_type=password','status':400,'body':body}}
    assert oauth_security_rejection(row,'https://example.test')


def test_remote_close_without_notification_now_fails():
    rt=SimpleNamespace(variables={'operator.room_user_id':1,'observer.room_user_id':2},
        clients={a:SimpleNamespace(client_type='web',snapshot_events=lambda after:[{'cmdid':6601,'fields':{'RoomInfo':{'EnableForceOperateUserAV':1}}}]) for a in ('operator','observer')},
        mark_all_events=lambda:{'operator':0,'observer':0},call=lambda *args:None,is_healthy=lambda:True)
    def receive(rt,alias,marks,cmdid,fields):
        if fields.get('Audio.State')==0:raise AssertionError('没有实际关闭通知')
        return SimpleNamespace(fields=fields)
    with patch('meeting_test_runtime.web_key_scenarios.role'),patch('meeting_test_runtime.web_key_scenarios.device',return_value=0),patch('meeting_test_runtime.web_key_scenarios.wait',side_effect=receive):
        with pytest.raises(AssertionError,match='关闭通知'):execute_key_flow({'scenario':'direct','params':{'kind':'audio'}},rt)


def test_guest_login_exits_guest_operator_not_account_observer():
    rt=SimpleNamespace(variables={'operator.room_user_id':1,'observer.room_user_id':2},
        clients={a:SimpleNamespace(client_type='web') for a in ('operator','observer')},
        profiles={'operator':SimpleNamespace(account={'identity_type':'guest'}),'observer':SimpleNamespace(account={'identity_type':'account'})},
        mark_all_events=lambda:{'operator':0,'observer':0},call=lambda *args:None)
    with patch('meeting_test_runtime.web_key_scenarios.wait'),patch('meeting_test_runtime.web_assertions.assert_departed') as checked:
        execute_key_flow({'scenario':'login','params':{}},rt)
    assert checked.call_args.args[1:3]==('operator',1)


def test_fm_registered_device_default_inactive_preserves_observed_state():
    from meeting_test_runtime.web_key_scenarios import user
    rows=[{'cmdid':6601,'fields':{'UserInfo':{'Name':'B','Video':{'ID':1,'State':2}}}},
          {'cmdid':6608,'fields':{'UserID':2,'Video':[{'Operation':1,'ID':1,'Name':'same'},
                                                     {'Operation':1,'ID':3,'Name':'new'}]}}]
    rt=SimpleNamespace(clients={'observer':SimpleNamespace(client_type='fm',snapshot_events=lambda after:rows)},variables={'observer.room_user_id':2})
    nodes={v['ID']:v for v in user(rt,'observer')['Video']}
    assert nodes[1]['State']==2 and nodes[3]['State']==0


def test_record_storage_error_cannot_pass_permission_rejection():
    rt=SimpleNamespace(variables={'operator.room_user_id':1,'observer.room_user_id':2},event_timeout=0.01,
        clients={'operator':SimpleNamespace(wait_event=lambda *args:SimpleNamespace(fields={'Error':12811,'SrcUserID':1,'ServerRecordStatus':0}),
            snapshot_outbound=lambda after:[{'cmdid':6510,'fields':{'ServerRecordStatus':2}}])},
        mark_all_events=lambda:{'operator':0,'observer':0},mark_all_outbound=lambda:{'operator':0},
        call=lambda alias,op,p:{'ServerRecord':'0'} if op=='get_effective_permissions' else None)
    with patch('meeting_test_runtime.web_key_scenarios.user',return_value={'DataState':0,'ManagerState':0}):
        with pytest.raises(AssertionError,match='权限拒绝'):execute_key_flow({'scenario':'record','params':{'role':'attendee'}},rt)


@pytest.mark.parametrize('bad', [{'UserID':999,'Name':'observer','DisplayName':'obsnick'},
                                {'UserID':2,'Name':'observer','DisplayName':'wrong but nonempty'}])
def test_wrong_identity_or_nonempty_wrong_nickname_cannot_pass(bad):
    def call(alias,op,p):
        if op=='get_authenticated_profile':return {'userName':'operator','displayName':'opnick'}
        return {'UserID':1,'Name':'operator','DisplayName':'opnick'} if p['user_id']==1 else bad
    rt=SimpleNamespace(variables={'operator.room_user_id':1,'observer.room_user_id':2},call=call,
        profiles={a:SimpleNamespace(account={'username':a}) for a in ('operator','observer')},
        clients={'operator':SimpleNamespace(snapshot_events=lambda after:[{'cmdid':6601,'fields':{'UserInfo':{'DisplayName':'opnick'}}},
            {'cmdid':6602,'fields':{'User':{'UserID':2,'Name':'observer','DisplayName':'obsnick'}}}])})
    with pytest.raises(AssertionError):execute_key_flow({'scenario':'info','params':{}},rt)


def test_lock_source_may_be_absent_but_not_wrong():
    validate_lock_source(SimpleNamespace(fields={'Lock':1}),1)
    validate_lock_source(SimpleNamespace(fields={'Lock':0,'SrcUserID':'1'}),1)
    with pytest.raises(AssertionError):validate_lock_source(SimpleNamespace(fields={'Lock':1,'SrcUserID':2}),1)


@pytest.mark.parametrize('change',[{'DstUserID':1},{'SrcUserID':999},{'Audio':{'ID':5}},{'Result':-1}])
def test_overlap_reply_cannot_use_old_actor_wrong_target_device_or_result(change):
    fields={'SrcUserID':3,'DstUserID':2,'Audio':{'ID':0},'Result':0}
    fields.update(change)
    with pytest.raises(AssertionError):validate_latest_remote_reply(SimpleNamespace(fields=fields),2,3,'audio',0,True)
