import json
import threading
import time

import pytest

from meeting_test_runtime.models import EventExpectation
from meeting_test_runtime.web_protocol import WebEventJournal, matches_fields
from meeting_test_runtime.allure_reporting import to_report_data, protocol_notify_evidence, protocol_outbound_evidence
from meeting_test_runtime.drivers import registered_client_types


def test_raw_web_event_preserves_fields_and_does_not_invent_xml_or_result():
    journal = WebEventJournal()
    wire = {"CmdID":6601,"ErrorCode":0,"Data":{"UserInfo":{"UserID":121}}}
    event = journal.append_frame(json.dumps(wire), 1)
    assert event.kind == "response" and event.error_code == 0
    assert event.raw_xml == b"" and "Result" not in event.fields
    assert json.loads(event.raw_json) == wire


def test_cursor_wrong_user_wrong_state_and_old_event_are_rejected():
    journal = WebEventJournal()
    expected = EventExpectation(cmdid=6609, kind="notify", match={"UserID":121,"Audio.ID":0,"Audio.State":2})
    journal.append_frame(json.dumps({"CmdID":6609,"Data":{"UserID":121,"Audio":{"ID":0,"State":2}}}),1)
    mark = journal.mark()
    for uid, device, state in [(122,0,2),(121,1,2),(121,0,0)]:
        journal.append_frame(json.dumps({"CmdID":6609,"Data":{"UserID":uid,"Audio":{"ID":device,"State":state}}}),1)
    assert journal.wait(expected,mark,0.01) is None
    journal.assert_no(expected,mark,0.01)


def test_wait_continues_while_another_thread_receives_and_negative_checks_full_window():
    journal = WebEventJournal()
    expected = EventExpectation(cmdid=6612, kind="notify",match={"Text":"中文<&>"})
    def receive():
        time.sleep(0.03)
        journal.append_frame(json.dumps({"CmdID":6612,"Data":{"Text":"中文<&>"}}),1)
    thread=threading.Thread(target=receive)
    thread.start()
    event=journal.wait(expected,0,0.3)
    thread.join()
    assert event is not None
    with pytest.raises(AssertionError,match="unexpected Web event"):
        journal.assert_no(expected,0,0.05)


def test_unknown_paas_and_decode_errors_stay_distinct():
    journal=WebEventJournal()
    journal.append_frame('{"id":19001,"business":"WBS","board_id":"board-1","result":0}',2)
    journal.append_frame('{"CmdID":6999,"Data":{"Value":""}}',1)
    journal.append_frame('invalid-json',3)
    events=journal.snapshot()
    assert [e.source for e in events]==["paas","conf","unknown"]
    assert events[2].kind=="decode_error"
    assert events[0].kind=="response" and events[0].error_code==0
    assert matches_fields(events[1].fields,{"Value":""})
    assert not matches_fields(events[1].fields,{"Missing":""})


def test_list_fields_match_actual_target_without_using_first_member_only():
    assert matches_fields({"User":[{"UserID":1},{"UserID":2}]},{"User.UserID":2})
    assert not matches_fields({"User":[{"UserID":1},{"UserID":2}]},{"User.0.UserID":2})


def test_web_report_json_is_real_and_secrets_are_redacted():
    journal=WebEventJournal()
    event=journal.append_frame('{"CmdID":6601,"ErrorCode":0,"Data":{"Token":"secret"}}',1)
    data=to_report_data(event)
    assert "secret" not in data["raw_json"]
    notify=protocol_notify_evidence({"observer:notify:0":event})
    assert notify["实际Notify数量"]==0
    wire=protocol_outbound_evidence({"operator":[{"trace_type":"json_command","event_id":1,
        "cmdid":6509,"raw_json":'{"Token":"secret"}',"fields":{"Token":"secret"},"sent_at":1}]},
        actor="operator",operation="mic_on",request_cmdid=6509)
    assert wire["实际发送协议数量"]==1
    assert "secret" not in json.dumps(to_report_data(wire))
    assert "web" in registered_client_types()


def test_json_inside_proxy_result_is_redacted_recursively_without_changing_business_text():
    from meeting_test_runtime.redaction import redact
    wire={'CmdID':1522,'Data':{'Result':json.dumps({'userInfo':{'password':'hash-secret','userName':'A'}})}}
    result=redact(wire)
    assert 'hash-secret' not in result['Data']['Result']
    assert json.loads(result['Data']['Result'])['userInfo']['userName']=='A'
    assert redact('{"Text": "业务<&>"}')=='{"Text": "业务<&>"}'


def test_sdk_positional_admin_password_is_redacted_in_both_report_shapes():
    from meeting_test_runtime.allure_reporting import actual_call_evidence
    record={'trace_type':'sdk_call','method_name':'HST.applyManagerRight',
            'params':['sensitive-admin-password','[callback]','[callback]']}
    assert 'sensitive-admin-password' not in json.dumps(to_report_data(record))
    evidence=actual_call_evidence({'operator':[record]},actor='operator',operation='request_manager',fallback_params={})
    assert 'sensitive-admin-password' not in json.dumps(to_report_data(evidence))
