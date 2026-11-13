from dataclasses import replace
from pathlib import Path
import pytest
from meeting_test_runtime.web_full_cases import validate_case, load_web_full_cases, WebFullCase
from meeting_test_runtime.web_protocol import matches_fields


def test_repeated_user_and_device_fields_must_come_from_same_nodes():
    fields={'User':[{'UserID':1,'Audio':[{'ID':0,'State':0},{'ID':1,'State':2}]},
                    {'UserID':2,'Audio':{'ID':0,'State':2}}]}
    assert not matches_fields(fields,{'User.UserID':1,'User.Audio.ID':0,'User.Audio.State':2})
    assert matches_fields(fields,{'User.UserID':2,'User.Audio.ID':0,'User.Audio.State':2})
    assert not matches_fields(fields,{'User.0.UserID':2})


@pytest.mark.parametrize('change',[{'combination':'live->web'}, {'identities':('guest','guest')},
    {'params':{'text':'hi','raw_xml':'<cmd/>'}}, {'params':{'text':1}}, {'template':'unknown'},
    {'status':'SKIP_BLOCKED'}])
def test_full_case_rejects_unregistered_configuration_before_mutation(change):
    case=WebFullCase('id','群聊','chat','fm->web',('account','account'),'BASE',{'text':'hi'})
    with pytest.raises(ValueError):validate_case(replace(case,**change))


def test_full_design_contains_remote_matrix_guest_modes_and_all_sources():
    from tools.create_web_full_workbook import design_cases
    cases,source=design_cases()
    core=[c for c in cases if c.scenario in ('remote','direct')]
    assert len(core)==36
    assert len({c.case_id for c in cases})==len(cases)
    assert len(source)==590
    guests=[c for c in cases if c.scenario=='guest_share']
    assert {c.template for c in guests}=={'discussion','training','control'}
    assert all(c.identities[0]=='guest' for c in guests)
    changed=[c for c in cases if c.scenario in ('fm_whiteboard','remote_target_left','remote_overlap')]
    assert len(changed)==6 and all(c.status=='IMPLEMENTED' and not c.reason for c in changed)
    assert all(c.evidence=='SDK_LOCAL' for c in changed if c.scenario=='remote_target_left')


def test_guest_join_has_no_account_authentication_call():
    from types import SimpleNamespace
    from meeting_test_runtime.web_client import WebTestClient
    calls=[]
    client=object.__new__(WebTestClient)
    client.profile=SimpleNamespace(account={'identity_type':'guest','display_name':'游客测试'},
        endpoint={'ces_addr':'https://example.invalid','join_settle_time':0,'event_timeout':0.001})
    client.events=SimpleNamespace(mark=lambda:0,wait=lambda *a:None)
    client.call=lambda op,p:calls.append((op,p))
    with pytest.raises(AssertionError,match='6601'):client.join(123)
    assert [op for op,_ in calls]==['init','join_guest']


def test_cors_http_failure_uses_actual_browser_status_not_invented_sdk_code():
    from meeting_test_runtime.web_client import WebTestClient
    from meeting_test_runtime.web_protocol import WebEventJournal
    from types import SimpleNamespace
    c=object.__new__(WebTestClient);c.events=WebEventJournal()
    c._network_urls={'id':'https://example.invalid/oauth2/token?password=secret'}
    c.worker=SimpleNamespace(server=SimpleNamespace(url='http://127.0.0.1:1234'))
    c._http_status_extra({'requestId':'id','statusCode':400})
    e=c.events.snapshot()[0]
    assert e.fields['status']==400 and 'body' not in e.fields
    assert 'Network.responseReceivedExtraInfo' in e.fields['evidence_source']


def test_factory_reuse_key_distinguishes_guest_modes_topology_and_accounts():
    from meeting_test_runtime.web_full_runtime import WebMeetingFactory
    from types import SimpleNamespace
    f=WebMeetingFactory(SimpleNamespace(get=lambda s,k:s))
    c=WebFullCase('id','名字','chat','web->web',('account','guest'),'discussion',{'text':'hi'})
    assert f._key(c)!=f._key(replace(c,template='training'))
    assert f._key(c)!=f._key(replace(c,identities=('guest','account')))


def test_second_stage_loader_round_trip_preserves_params_and_gaps():
    path=Path(__file__).resolve().parents[1]/'tests/web_e2e/data/web_e2e_cases.xlsx'
    from tools.create_web_full_workbook import design_cases
    expected,_=design_cases()
    assert load_web_full_cases(path)==expected


def test_whiteboard_position_matches_actual_request_instead_of_assuming_zero():
    from meeting_test_runtime.web_key_scenarios import _whiteboard_matches
    fields={'Screen':[{'DataArea':[{'ID':1,'Item':[{'Type':1,'Pos':0,'Data':99},
        {'Type':1,'Pos':1,'Data':2256296469}]}]}]}
    assert _whiteboard_matches(fields,'web',2256296469,expected_pos=1)
    assert not _whiteboard_matches(fields,'web',2256296469,expected_pos=0)


def test_unimplemented_contract_cannot_be_enabled_by_excel_status_flip():
    c=WebFullCase('id','本人申请拒绝','approval','web->web',('guest','account'),'training',{'kind':'audio','role':'manager','decision':'reject'})
    with pytest.raises(ValueError,match='缺口'):validate_case(c)


@pytest.mark.parametrize('case',[
    WebFullCase('id','本地拒绝','remote_target_left','web->web',('account','account'),'BASE',{'kind':'audio','role':'manager'},evidence='PROTOCOL'),
    WebFullCase('id','重叠邀请','remote_overlap','web->web+web',('account','account','account'),'DIRECT',{'kind':'audio'}),
    WebFullCase('id','白板视图','fm_whiteboard','web->web',('account','account'),'BASE'),
])
def test_confirmed_scope_cannot_be_relabelled_as_other_protocol_or_topology(case):
    with pytest.raises(ValueError):validate_case(case)


def test_whiteboard_view_checks_actual_web_shape_and_same_data_area():
    fields={'Screen':[{'ScreenID':0,'LayoutMode':2,'DataArea':[
        {'ID':1,'Item':[{'Type':1,'Data':0}]}, {'ID':2,'Item':[{'Type':6,'Data':0}]}]}]}
    expected={'Screen.ScreenID':0,'Screen.LayoutMode':2,'Screen.DataArea.ID':1,
              'Screen.DataArea.Item.Type':1,'Screen.DataArea.Item.Data':0}
    assert matches_fields(fields,expected)
    assert not matches_fields(fields,{**expected,'Screen.DataArea.Item.Type':6})
    assert not matches_fields(fields,{'Mode':2,'DataActive':1})


def test_excel_steps_are_closed_whitelist_and_really_change_order():
    from meeting_test_runtime.web_full_steps import validate_steps, reviewed_steps
    c=WebFullCase('id','双向聊天','chat_roundtrip','web->web',('account','guest'),'BASE',{'text':'hi','repeat':True})
    steps=reviewed_steps(c)
    assert [s['actor'] for s in steps[:4]]==['operator','observer','operator','observer']
    validate_steps(steps,['operator','observer'])
    with pytest.raises(ValueError):validate_steps([{'actor':'operator','operation':'_send','params':{'xml':'<cmd/>'}}],['operator'])


def test_resource_cleanup_requeries_exact_room_instead_of_trusting_delete_success():
    from meeting_test_runtime.web_full_runtime import WebMeetingFactory
    from types import SimpleNamespace
    calls=[]
    good=SimpleNamespace(list_rooms_http=lambda **p:(calls.append(p) or {'code':'0','data':{'records':[]}}))
    assert WebMeetingFactory.verify_deleted(good,123)['data']['records']==[]
    assert calls==[{'room_id':123,'page_number':1,'page_size':10}]
    bad=SimpleNamespace(list_rooms_http=lambda **p:{'code':'0','data':{'records':[{'roomId':123}]}})
    with pytest.raises(AssertionError,match='仍存在'):WebMeetingFactory.verify_deleted(bad,123)
