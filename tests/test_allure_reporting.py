from dataclasses import dataclass

from meeting_test_runtime.allure_reporting import (
    OPERATION_NAMES, actual_call_evidence, case_title, event_evidence,
    operation_name, protocol_notify_evidence, protocol_outbound_evidence,
    protocol_response_evidence, to_report_data,
)
from meeting_test_runtime.excel_loader import ExcelCaseLoader


EXCEL = "tests/e2e/data/fm_e2e_cases.xlsx"


def test_all_executable_excel_operations_have_chinese_report_names():
    cases = ExcelCaseLoader(EXCEL).load_cases()
    operations = {case.operation for case in cases}

    assert operations <= OPERATION_NAMES.keys()
    assert all(operation_name(operation) != operation for operation in operations)


def test_case_title_is_readable_and_does_not_expose_long_case_id():
    case = next(case for case in ExcelCaseLoader(EXCEL).load_cases()
                if case.operation == "send_subtitles")

    title = case_title(case)

    assert title.startswith("后台发送会议字幕｜正常场景")
    assert case.case_id not in title
    assert len(title) < 100


def test_report_titles_are_unique_inside_each_business_module():
    cases = ExcelCaseLoader(EXCEL).load_cases()
    values = [(case.module, case_title(case)) for case in cases]

    assert len(values) == len(set(values))


def test_event_evidence_converts_dataclass_and_bytes_to_structured_chinese_data():
    @dataclass
    class Event:
        cmdid: int
        name: str
        kind: str
        fields: dict
        raw_xml: bytes
        received_at: float

    values = event_evidence({
        "observer:notify:0": Event(
            6615, "MEETINGCHARNOTIFY", "notify", {"Data": "测试字幕"},
            b'<cmd id="6615"><Data val="test"/></cmd>', 1.0),
    })

    assert values == [{
        "接收端": "观察端B",
        "事件序号": "notify:0",
        "消息类型": "notify",
        "cmdid": 6615,
        "协议名称": "MEETINGCHARNOTIFY",
        "字段": {"Data": "测试字幕"},
        "原始XML": '<cmd id="6615"><Data val="test"/></cmd>',
        "接收时间": 1.0,
    }]


def test_notify_only_operation_explains_why_response_is_empty():
    value = protocol_response_evidence(None, {}, response_expected=False)

    assert value["是否定义同步Response"] is False
    assert value["公开方法实际返回值"] is None
    assert value["实际业务错误码"] is None
    assert "Notify型协议" in value["说明"]


def test_response_and_notify_are_split_for_report_readability():
    @dataclass
    class Event:
        cmdid: int
        name: str
        kind: str
        fields: dict
        raw_xml: bytes
        received_at: float

    events = {
        "operator:response": Event(6630, "OPERATIONRIGHTREP", "response",
                                   {"Result": 0}, b'<cmd id="6630"/>', 1.0),
        "observer:notify:0": Event(6609, "USERSTATECHANGED", "notify",
                                   {"State": 2}, b'<cmd id="6609"/>', 2.0),
    }

    response = protocol_response_evidence(
        {"Result": 0}, events, response_expected=True)
    notify = protocol_notify_evidence(events)

    assert response["客户端收到的Response事件"][0]["cmdid"] == 6630
    assert notify["实际Notify数量"] == 1
    assert notify["各端实际Notify"][0]["cmdid"] == 6609


def test_outbound_evidence_shows_actual_call_xml_and_http():
    outbound = {
        "operator": [
            {"trace_type": "public_call", "trace_id": 1,
             "operation": "chat", "method_name": "chat",
             "params": {"text": "hello", "room_password": "secret",
                        "ChairPwd": "chair-secret"},
             "called_at": 1.0, "sent_command_ids": [7],
             "http_request_ids": []},
            {"trace_type": "xml_command", "event_id": 7,
             "operation": "chat", "source": "conf", "cmdid": 6512,
             "name": "CHATMSG", "ver": "2.0", "seq": None,
             "fields": {"Text": "hello", "ChairPwd": "chair-secret"},
             "raw_xml": b'<cmd id="6512" ver="2.0"><RoomPassword val="secret"/><ChairPwd val="chair-secret"/></cmd>',
             "sent_at": 1.1},
            {"trace_type": "http_request", "trace_id": 2,
             "operation": None, "method": "POST",
             "url": "https://server/api/v1/message", "body": {"token": "secret"},
             "content_type": "application/json", "sent_at": 2.0},
        ],
        "http_admin": [
            {"trace_type": "http_request", "trace_id": 2,
             "operation": "send_room_message", "method": "POST",
             "url": "https://server/api/v1/message", "body": {"token": "secret"},
             "content_type": "application/json", "sent_at": 2.0},
        ],
    }
    call = actual_call_evidence(
        outbound, actor="operator", operation="chat", fallback_params={})
    protocol = protocol_outbound_evidence(
        outbound, actor="operator", operation="chat", request_cmdid=6512)
    rendered = to_report_data({"call": call, "protocol": protocol})

    assert rendered["call"]["主业务实际参数"]["text"] == "hello"
    assert rendered["call"]["主业务实际参数"]["room_password"] == "***REDACTED***"
    assert rendered["call"]["主业务实际参数"]["ChairPwd"] == "***REDACTED***"
    xml = rendered["protocol"]["实际发送协议"][0]
    assert xml["是否主业务协议"] is True and xml["cmdid"] == 6512
    assert "chair-secret" not in xml["原始XML"]
    assert xml["字段"]["ChairPwd"] == "***REDACTED***"
    assert "***REDACTED***" in xml["原始XML"]
    http = rendered["protocol"]["实际发送协议"][1]
    assert rendered["protocol"]["实际发送协议数量"] == 2
    assert http["HTTP方法"] == "POST"
    assert http["请求Body"]["token"] == "***REDACTED***"
