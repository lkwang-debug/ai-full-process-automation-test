"""会后JSON结构、目标身份、分页以及文本导出内容校验。"""
import json
import math
from dataclasses import dataclass
import re
import unicodedata

SUMMARY_FILE_OPERATIONS = {'download_meeting_summary', 'export_meeting_summary'}


@dataclass(frozen=True)
class ExportReference:
    operation: str
    group_id: str
    room_id: int
    source_operation: str
    content: bytes


def _text_file(body):
    assert body, '下载内容为空'
    try:text=bytes(body).decode('utf-8-sig')
    except UnicodeDecodeError as exc:raise AssertionError('文件不是有效UTF-8文本') from exc
    assert text.strip() and '\x00' not in text, '不是有效的UTF-8纪要/字幕文本'
    assert not text.lstrip().lower().startswith(('<!doctype','<html','<head','<body')), 'HTTP成功返回错误页而非导出内容'
    try:value=json.loads(text)
    except ValueError:pass
    else:assert not isinstance(value,(dict,list)), '文件接口返回JSON而非Markdown文件'
    return text


def _pdf_content_key(text):
    # PDF may change whitespace, list bullets and compatibility characters.
    # Keep every letter/digit/Chinese character in order; do not use fuzzy ratios.
    text=unicodedata.normalize('NFKC',text)
    text=re.sub(r'^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)、]\s*)','',text,flags=re.MULTILINE)
    return re.sub(r'\W+','',text,flags=re.UNICODE).replace('_','')


def load_export_reference(api, operation, group_id, room_id):
    """Use a different public route for the same validated recording identity."""
    assert operation in SUMMARY_FILE_OPERATIONS
    detail=api.get_record_summary(group_id)
    validate_result('get_record_summary',detail,{'group_id':group_id,'room_id':room_id})
    source='export_meeting_summary' if operation=='download_meeting_summary' else 'download_meeting_summary'
    content=(api.export_meeting_summary(group_id,type=1) if source=='export_meeting_summary'
             else api.download_meeting_summary(group_id))
    _text_file(content)
    return ExportReference(operation,str(group_id),int(room_id),source,bytes(content))


def validate_result(operation, response, params, expected_title='', *, file_reference=None):
    if isinstance(response,(bytes,bytearray)):
        assert response, '下载内容为空'
        if int(params.get('type',1))==2:
            from io import BytesIO
            from pypdf import PdfReader
            assert bytes(response).startswith(b'%PDF-'), '请求PDF却返回其他格式'
            reader=PdfReader(BytesIO(bytes(response)),strict=True)
            assert len(reader.pages)>0, 'PDF无有效页面'
            text='\n'.join(page.extract_text() or '' for page in reader.pages)
            assert text.strip(), 'PDF没有可核对的正文'
        else:text=_text_file(response)
        if operation in SUMMARY_FILE_OPERATIONS:
            assert isinstance(file_reference,ExportReference), '纪要文件缺少独立公开接口的内容基准'
            assert file_reference.operation==operation and file_reference.group_id==str(params.get('group_id')), '文件基准操作或groupId与本次请求不符'
            if 'room_id' in params:assert file_reference.room_id==int(params['room_id']), '文件基准会议室与请求不符'
            expected_source='export_meeting_summary' if operation=='download_meeting_summary' else 'download_meeting_summary'
            assert file_reference.source_operation==expected_source, '不能用被测接口自己的返回自证文件内容'
            expected=_text_file(file_reference.content)
            if int(params.get('type',1))==2:
                key=_pdf_content_key(expected)
                assert key and key in _pdf_content_key(text), 'PDF正文与当前groupId的纪要内容不符'
            else:
                assert text.replace('\r\n','\n').strip()==expected.replace('\r\n','\n').strip(), '下载/导出正文与当前groupId的纪要内容不符'
        else:
            assert operation=='export_transcript', '未注册的文件响应操作'
            assert expected_title and expected_title in unicodedata.normalize('NFKC',text), '字幕文件缺少当前会议主题'
        return
    assert isinstance(response,dict) and str(response.get('code'))=='0', '缺少成功业务code'
    assert 'data' in response and isinstance(response['data'],dict), '缺少结构化data'
    data=response['data']
    if operation in ('get_record_summary','get_record_share'):
        assert str(data.get('groupId'))==str(params['group_id']), '返回了错误的groupId'
        if operation=='get_record_summary' and 'room_id' in params:
            assert str(data.get('roomId'))==str(params['room_id']), '录制摘要与目标会议室不符'
        for key in (('roomId','roomName','startTime','durationSeconds','participantCount') if operation=='get_record_summary' else ('shareEnabled','passwordEnabled')):
            assert key in data, f'缺少{key}'
        return
    assert isinstance(data.get('records'),list), '列表缺少records'
    rows=data['records']
    total,size,current,pages=[int(data[k]) for k in ('total','size','current','pages')]
    assert total>=0 and size>0 and current>0
    assert pages==math.ceil(total/size), '分页总页数不一致'
    assert len(rows)==max(0,min(size,total-(current-1)*size)), '返回条数与total/current/size不一致'
    for keys,actual in ((('page_size','pageSize'),size),(('page_number','pageNumber','page_num','pageNum'),current)):
        value=next((params[k] for k in keys if k in params),None)
        if value is not None: assert int(value)==actual, f'分页参数没有生效: {keys}'
    required={
        'list_record_participants':('userId','nickName','selected','creator','disabled'),
        'list_record_tasks':('recordId','status'),
        'list_records':('recordId','fileId','status','mediaType'),
        'list_record_summaries':('groupId','roomId'),
        'list_room_record_summaries':('groupId','roomId'),
        'get_meeting_summary':('groupId','name','createTime'),
    }.get(operation)
    assert required, f'未定义HTTP结果结构: {operation}'
    for row in rows:
        assert isinstance(row,dict) and all(k in row for k in required), '结果记录缺少业务字段'
        if operation=='list_room_record_summaries': assert str(row['roomId'])==str(params['room_id'])
        if operation=='get_meeting_summary':
            assert params['beginTime']<=row['createTime']<=params['endTime'], '纪要不在查询时间范围'
    primary=required[0]
    assert len({str(r[primary]) for r in rows})==len(rows), '同页出现重复业务记录'
