from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
import pytest
from meeting_test_runtime.http_result_assertions import validate_result,ExportReference,load_export_reference


def test_http_result_rejects_empty_business_payload_and_html_success_body():
    with pytest.raises(AssertionError): validate_result('list_records',{'code':0,'data':{}},{})
    with pytest.raises(AssertionError): validate_result('export_transcript',b'<html>error</html>',{},'meeting')


def test_summary_checks_requested_group_not_just_success_code():
    value={'code':0,'data':{'groupId':'wrong','roomId':1,'roomName':'meeting','startTime':'x','durationSeconds':1,'participantCount':2}}
    with pytest.raises(AssertionError): validate_result('get_record_summary',value,{'group_id':'correct'})


def test_pagination_checks_parameter_and_record_count():
    value={'code':0,'data':{'records':[{'recordId':'a','status':6}], 'total':1,'size':10,'current':1,'pages':1}}
    validate_result('list_record_tasks',value,{'page_size':10})
    with pytest.raises(AssertionError): validate_result('list_record_tasks',value,{'page_size':20})
    value['data']['total']=2
    with pytest.raises(AssertionError): validate_result('list_record_tasks',value,{})


def reference(content='本次讨论确定周五发布，并由项目组跟进。',operation='export_meeting_summary'):
    return ExportReference(operation,'group-1',10012,
        'download_meeting_summary' if operation=='export_meeting_summary' else 'export_meeting_summary',content.encode())


def test_summary_body_need_not_repeat_title_but_must_match_target_record():
    expected=reference()
    validate_result('export_meeting_summary',expected.content,{'group_id':'group-1'},'测试会议',file_reference=expected)
    with pytest.raises(AssertionError,match='正文'):
        validate_result('export_meeting_summary','测试会议：另一场会议的内容'.encode(),{'group_id':'group-1'},'测试会议',file_reference=expected)


@pytest.mark.parametrize('mutation',[{'group_id':'another-group'},{'source_operation':'export_meeting_summary'},{'operation':'download_meeting_summary'}])
def test_file_cannot_use_wrong_record_or_same_route_as_reference(mutation):
    expected=replace(reference(),**mutation)
    with pytest.raises(AssertionError):
        validate_result('export_meeting_summary',expected.content,{'group_id':'group-1'},file_reference=expected)


def test_nonempty_file_without_reference_does_not_pass():
    with pytest.raises(AssertionError,match='基准'):
        validate_result('export_meeting_summary',b'nonempty but unrelated',{'group_id':'group-1'})


def test_download_uses_export_route_and_checks_complete_body():
    expected=reference(operation='download_meeting_summary')
    validate_result('download_meeting_summary',expected.content,{'group_id':'group-1'},file_reference=expected)
    with pytest.raises(AssertionError):
        validate_result('download_meeting_summary',expected.content[:12],{'group_id':'group-1'},file_reference=expected)


@pytest.mark.parametrize('body',[b'',b'<html>failure</html>',b' \n{"code": 9999,"msg":"error"}',b'\xff\xfe',b'\x00'])
def test_error_pages_json_and_invalid_text_cannot_pass_as_minutes(body):
    with pytest.raises(AssertionError):
        validate_result('export_meeting_summary',body,{'group_id':'group-1'},file_reference=reference())


def test_reference_query_rejects_wrong_room_before_loading_any_file():
    data={'groupId':'group-1','roomId':999,'roomName':'meeting','startTime':'x','durationSeconds':1,'participantCount':2}
    api=SimpleNamespace(get_record_summary=lambda group_id:{'code':0,'data':data})
    with pytest.raises(AssertionError,match='会议室'):load_export_reference(api,'export_meeting_summary','group-1',10012)


def pdf(text):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=300,height=300)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(('BT /F1 12 Tf 20 240 Td ('+text+') Tj ET').encode('ascii'))
    page[NameObject('/Contents')]=writer._add_object(stream)
    output=BytesIO();writer.write(output);return output.getvalue()


def test_pdf_requires_complete_same_record_body_not_a_title_or_blank_page():
    expected=reference('Release on Friday. Owner Alice.')
    validate_result('export_meeting_summary',pdf('Release on Friday. Owner Alice.'),{'group_id':'group-1','type':2},'Meeting title',file_reference=expected)
    for content in ('Meeting title', 'Release on Friday.', 'Release on Monday. Owner Alice.', ''):
        with pytest.raises(AssertionError):
            validate_result('export_meeting_summary',pdf(content),{'group_id':'group-1','type':2},'Meeting title',file_reference=expected)


def test_pdf_request_cannot_pass_with_markdown_or_error_page():
    for body in (b'<html>error</html>','会议主题：测试会议'.encode()):
        with pytest.raises(AssertionError):
            validate_result('export_meeting_summary',body,{'type':2},'测试会议')
