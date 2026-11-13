from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook

from meeting_test_runtime.expectations import get_expectation
from meeting_test_runtime.models import DeliveryPolicy


ROOT = Path(__file__).resolve().parents[1]
EXCEL = ROOT / "tests/e2e/data/fm_e2e_cases.xlsx"


def _rows(sheet):
    headers = [cell.value for cell in sheet[1]]
    return [dict(zip(headers, row)) for row in sheet.iter_rows(min_row=2, values_only=True)]


def test_product_unused_waiting_list_is_not_an_execution_blocker():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["Conf信令检查"])
    finally:
        book.close()
    selected = {row["cmdid"]: row for row in rows
                if row["cmdid"] in {6647}}
    assert set(selected) == {6647}
    assert all(row["封装状态"] == "UNSUPPORTED_PROTOCOL" and
               row["执行状态"] == "不执行（产品未使用）" and row["缺口原因"]
               for row in selected.values())


def test_record_error_6637_has_real_chain_case():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        cases = _rows(book["功能用例"])
        signals = _rows(book["Conf信令检查"])
    finally:
        book.close()
    case = next(row for row in cases
                if row["用例ID"] == "FM-RECORD-N-ERROR-6637")
    assert case["场景类型"] == "RECORD_ERROR_6637"
    assert case["用例状态"] == "IMPLEMENTED" and case["是否执行"] == "是"
    signal = next(row for row in signals if row["cmdid"] == 6637)
    assert signal["执行状态"] != "BLOCKED"
    assert case["用例ID"] in signal["正常用例ID"]


def test_chat_review_rejection_and_consumed_replay_cases_exist():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        cases = _rows(book["功能用例"])
    finally:
        book.close()
    selected = [row for row in cases if row["场景类型"] == "CHAT_REVIEW"]
    assert {row["用例ID"] for row in selected} == {
        "FM-CHAT-REVIEW-N-REJECT",
        "FM-CHAT-REVIEW-N-REJECT-THEN-APPROVE"}
    assert {json.loads(row["参数JSON"])["repeat_after_reject"]
            for row in selected} == {False, True}
    assert all(row["用例状态"] == "IMPLEMENTED" and
               row["环境风险"] == "ISOLATED_STATEFUL" and
               row["执行级别"] == "P1" for row in selected)


def test_remote_av_and_web_notify_cases_are_executable():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        cases = _rows(book["功能用例"])
        signals = _rows(book["Conf信令检查"])
    finally:
        book.close()
    remote = [row for row in cases if row["场景类型"] == "REMOTE_AV_CONSENT"]
    assert len(remote) == 4
    assert {(json.loads(row["参数JSON"])["kind"],
             json.loads(row["参数JSON"])["accept"]) for row in remote} == {
        ("audio", True), ("audio", False), ("video", True), ("video", False)}
    web = [row for row in cases if row["场景类型"] == "WEB_NOTIFY_6654"]
    assert len(web) == 1
    assert all(row["用例状态"] == "IMPLEMENTED" and row["是否执行"] == "是"
               for row in remote + web)
    signal = {row["cmdid"]: row for row in signals}
    assert all(signal[cmdid]["执行状态"] != "BLOCKED"
               for cmdid in (6542, 6642, 6543, 6643, 6654))


def test_full_plan_contains_only_unique_business_cases():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        cases = _rows(book["功能用例"])
    finally:
        book.close()
    assert 500 <= len(cases) < 600
    priorities = Counter(case["执行级别"] for case in cases)
    assert priorities["P0"] == 58
    assert sum(priorities.values()) == len(cases)
    assert all(case["环境风险"] and case["用例状态"] for case in cases)
    designed = [case for case in cases if case["用例状态"] == "DESIGNED"]
    assert all(case["是否执行"] == "否" and case["设计说明"]
               for case in designed)
    assert not any(case["执行级别"] == "P0" and
                   case["用例类型"] == "BUSINESS_EXCEPTION" for case in cases)
    assert not any(case["执行级别"] == "P0" and
                   case["环境风险"] in {"DESTRUCTIVE_ISOLATED", "BLOCKED"}
                   for case in cases)
    assert not any(case["执行级别"] == "P0" and
                   case["统一操作名"] == "configure_polling" for case in cases)
    implemented = [case for case in cases if case["用例状态"] == "IMPLEMENTED"]
    keys = [(
        case["统一操作名"],
        json.dumps(json.loads(case["参数JSON"]), sort_keys=True,
                   ensure_ascii=False),
        case["用例类型"],
        case["前置模板"],
    ) for case in implemented]
    assert len(keys) == len(set(keys))
    all_keys = [(
        case["统一操作名"], case["参数JSON"], case["用例类型"],
        case["前置模板"], case["预期结果类型"], case["闭环类型"],
    ) for case in cases]
    assert len(all_keys) == len(set(all_keys))
    blocked = [case for case in cases if case["用例状态"] == "SKIP_BLOCKED"]
    assert all(case["阻塞ID"] for case in blocked)
    assert all(not case["阻塞ID"] for case in implemented)
    exceptions = [case for case in cases
                  if case["用例类型"] == "BUSINESS_EXCEPTION"]
    # 已确认的合法边界应改为正常场景，不为维持历史数量虚构异常。
    assert exceptions
    assert {case["预期结果类型"] for case in exceptions} >= {
        "ERROR_AND_NO_EVENT", "ERROR_EVENT", "NO_SUCCESS_EVENT",
        "VALIDATION_ERROR"}
    assert all(case["前置模板"] == "LAYOUT_RIGHT" for case in exceptions
               if case["统一操作名"] == "set_layout")
    assert all(
        case["前置模板"] == (
            "NONE" if case["预期结果类型"] == "VALIDATION_ERROR" else "POLLING_RIGHT")
        for case in exceptions if case["统一操作名"] == "configure_polling")


def test_chat_font_and_layout_fullscreen_cases_are_executable():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        cases = _rows(book["功能用例"])
    finally:
        book.close()
    font_cases = [case for case in cases if "font-" in case["用例ID"]]
    full_cases = [case for case in cases if any(token in case["用例ID"] for token in (
        "full-observer-media0", "full-observer-media1", "exit-fullscreen-zero"))]
    assert len(font_cases) == 2 and len(full_cases) == 3
    assert all(case["用例状态"] == "IMPLEMENTED" and case["是否执行"] == "是"
               for case in font_cases + full_cases)
    assert {json.loads(case["参数JSON"])["font"]["Name"] for case in font_cases} == {"宋体"}
    assert {json.loads(case["参数JSON"])["full_video_media_id"]
            for case in full_cases} == {0, 1}


def test_product_unused_operations_have_no_functional_cases():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        cases = _rows(book["功能用例"])
    finally:
        book.close()
    unused = {
        "front_roomlist", "front_roomlist_classified", "list_rooms",
        "get_av_mix_params", "set_av_mix_params",
    }
    assert not [row for row in cases if row["统一操作名"] in unused]


def test_http_mute_and_close_follow_current_product_cmdids():
    mute = get_expectation("mute_all_http")
    close = get_expectation("close_meeting")
    assert mute.notifies[0].cmdid == 6614
    assert mute.notifies[0].kind == "notify"
    assert close.notifies[0].cmdid == 6607


def test_layout_plan_covers_formal_ids_and_targeted_delivery():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        cases = [row for row in _rows(book["功能用例"])
                 if row["模块"] == "布局、窗口及定向投递"]
    finally:
        book.close()
    assert len(cases) == 55
    layout_ids = {
        json.loads(case["参数JSON"]).get("layout_type")
        for case in cases if case["统一操作名"] == "set_layout"
    }
    assert {0, 1, 2, 3, 4, 6, 9, 12, 16, 25, 36, 49, 64, 1001, 1002} <= layout_ids
    assert any(case["预期接收端"] == "observer" and
               case["不应接收端"] == "witness" for case in cases)
    notes = " ".join(str(case["设计说明"] or "") for case in cases)
    assert "空余窗口允许为空" in notes
    formal = [case for case in cases if "formal-" in case["用例ID"]]
    assert len(formal) == 15
    assert all(case["预期接收端"] == "observer,witness" and
               case["不应接收端"] == "operator" for case in formal)


def test_layout_plan_covers_order_capacity_and_delivery_boundaries():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        cases = [row for row in _rows(book["功能用例"])
                 if row["统一操作名"] == "set_layout"]
    finally:
        book.close()
    by_id = {row["用例ID"]: row for row in cases}

    def find(token):
        return next(row for case_id, row in by_id.items() if token in case_id)

    for layout in (4, 9, 1001):
        target = find(f"target-b-{layout}")
        exclude = find(f"exclude-c-{layout}")
        assert target["预期接收端"] == "observer"
        assert target["不应接收端"] == "witness"
        assert exclude["预期接收端"] == "observer"
        assert exclude["不应接收端"] == "witness"

    abc = json.loads(find("order-abc")["参数JSON"])["videos"]
    cba = json.loads(find("order-cba")["参数JSON"])["videos"]
    assert [item["UserID"] for item in abc] == [
        "${operator.room_user_id}", "${observer.room_user_id}",
        "${witness.room_user_id}"]
    assert [item["UserID"] for item in cba] == [
        "${witness.room_user_id}", "${observer.room_user_id}",
        "${operator.room_user_id}"]
    assert [item["Pos"] for item in abc] == [0, 1, 2]
    assert [item["MediaID"] for item in abc] == [10, 11, 12]

    # formal-4 already is the reviewed "4 windows with 2 active users" case;
    # the generator intentionally de-duplicates the identical four-empty row.
    empty_users = json.loads(find("formal-4-")["参数JSON"])["user_ids"]
    four_sync = find("four-ab-sync")
    large_sync = find("large-64-ab-sync")
    assert len(empty_users) == 2
    assert len(json.loads(four_sync["参数JSON"])["user_ids"]) == 2
    assert len(json.loads(large_sync["参数JSON"])["user_ids"]) == 2
    assert json.loads(four_sync["参数JSON"])["layout_type"] == 4
    assert json.loads(large_sync["参数JSON"])["layout_type"] == 64
    assert four_sync["用例状态"] == large_sync["用例状态"] == "IMPLEMENTED"


def test_layout_state_machine_has_exact_switch_sequence_and_fields():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = [row for row in _rows(book["依赖流程"])
                if row["流程ID"] == "FLOW-LAYOUT-SWITCH"]
    finally:
        book.close()
    assert len(rows) == 9
    assert [row["统一操作名"] for row in rows] == [
        "request_right", "set_layout", "set_layout", "set_layout", "set_layout",
        "set_layout", "set_layout", "set_layout", "release_right"]
    layouts = [json.loads(row["参数JSON"])["layout_type"] for row in rows
               if row["统一操作名"] == "set_layout"]
    assert layouts == [1, 4, 4, 4, 9, 1001, 0]
    reorder = json.loads(rows[4]["预期Notify序列JSON"])[0]["match"]
    assert reorder == {
        "VideoRelayID": 4,
        "Video.0.UserID": "${observer.room_user_id}",
        "Video.0.MediaID": 21,
        "Video.0.Pos": 0,
        "Video.1.UserID": "${operator.room_user_id}",
        "Video.1.MediaID": 20,
        "Video.1.Pos": 1,
    }
    assert all(row["预期接收端"] == "observer" and
               row["不应接收端"] == "operator"
               for row in rows[1:8])


def test_dependency_flow_ledger_contains_required_flows():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["依赖流程"])
    finally:
        book.close()
    assert {row["流程ID"] for row in rows} == {
        "FLOW-HTTP-CREATE-JOIN", "FLOW-LOCK-JOIN", "FLOW-HTTP-INVITE",
        "FLOW-WAITING-ROOM", "FLOW-RIGHT-TRANSFER", "FLOW-LAYOUT-SWITCH",
        "FLOW-DEVICE-AV", "FLOW-RECORD-RESULT", "FLOW-CHAT-RECALL",
        "FLOW-GROUP-LIFECYCLE", "FLOW-AUX-CONTENTION",
        "FLOW-H323-AUX-AUTO", "FLOW-H323-AUX-APPROVAL",
        "FLOW-POLLING-LIFECYCLE",
    }


def test_chat_recall_flow_uses_new_msg_id_contract_and_is_executable():
    from meeting_test_runtime import ExcelCaseLoader

    flow = next(flow for flow in ExcelCaseLoader(EXCEL).load_dependency_flows()
                if flow.flow_id == "FLOW-CHAT-RECALL")
    assert flow.status.value == "IMPLEMENTED"
    assert flow.environment_risk.value == "DESTRUCTIVE_ISOLATED"
    recalls = [step for step in flow.steps if step.operation == "recall_msg"]
    assert len(recalls) == 2
    assert all(step.params == {"msg_id": "${message.id}"} for step in recalls)
    assert recalls[0].expected_response == {
        "MsgID": "${message.id}", "Result": 0}
    assert recalls[0].expected_notifies[0].match == {
        "MsgID": "${message.id}",
        "SrcUserID": "${operator.room_user_id}",
    }
    assert recalls[1].expected_nonzero_result is True
    assert recalls[1].expected_response == {"MsgID": "${message.id}"}
    assert recalls[1].forbidden_receivers == ("observer",)


def test_custom_data_cases_cover_public_private_combined_and_clear_routing():
    from meeting_test_runtime import ExcelCaseLoader

    cases = [case for case in ExcelCaseLoader(EXCEL).load_cases()
             if case.operation == "set_user_custom_data"]
    assert len(cases) == 4
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = [row for row in _rows(book["功能用例"])
                if row["统一操作名"] == "set_user_custom_data"]
    finally:
        book.close()
    assert len(rows) == 4
    params = [json.loads(row["参数JSON"]) for row in rows]
    assert any("public_data" in item and "private_data" not in item
               for item in params)
    assert any("private_data" in item and "public_data" not in item
               for item in params)
    assert any(item.get("public_data") and item.get("private_data")
               for item in params)
    assert any(item.get("public_data") == "" and item.get("private_data") == ""
               for item in params)
    for row in rows:
        assert row["预期Response"] == "{}"
        assert row["用例状态"] == "IMPLEMENTED"
        assert row["是否执行"] == "是"
        notifies = json.loads(row["预期Notify序列JSON"])
        assert all(item["cmdid"] == 6672 for item in notifies)
        for item in notifies:
            assert {"SrcUserID", "UserID"} <= set(item["match"])
            if "PrivateData" in item["match"]:
                assert item["receivers"] == ["observer"]
                assert item["forbidden_receivers"] == ["operator"]


def test_lock_join_flow_uses_async_login_and_three_endpoint_events():
    from meeting_test_runtime import ExcelCaseLoader

    flow = next(flow for flow in ExcelCaseLoader(EXCEL).load_dependency_flows()
                if flow.flow_id == "FLOW-LOCK-JOIN")
    assert flow.status.value == "IMPLEMENTED"
    assert [step.operation for step in flow.steps][5:9] == [
        "start_async_call", "wait_async_events", "allow_knock",
        "wait_async_call",
    ]
    waiting = flow.steps[6].expected_notifies
    assert [(event.cmdid, event.receivers) for event in waiting] == [
        (6617, ("observer",)), (6604, ("operator",)),
    ]
    joined = flow.steps[8].expected_notifies
    assert [(event.cmdid, event.receivers) for event in joined] == [
        (6602, ("operator",)),
    ]


def test_aux_and_polling_flow_use_protocol_state_enums():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["依赖流程"])
    finally:
        book.close()
    aux = [row for row in rows if row["流程ID"] == "FLOW-AUX-CONTENTION"]
    start = next(row for row in aux if row["统一操作名"] == "start_aux_stream")
    assert start["实现状态"] == "COVERED_BY_CASES"
    assert "FM-AUX-N-FM-CONTENTION" in start["步骤说明"]
    polling = [row for row in rows if row["流程ID"] == "FLOW-POLLING-LIFECYCLE"]
    statuses = [json.loads(row["参数JSON"]).get("status") for row in polling
                if row["统一操作名"] == "configure_polling"]
    assert {0, 1, 2} <= set(statuses)


def test_audio_normal_domain_uses_only_server_supported_device_zero():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = [row for row in _rows(book["功能用例"])
                if row["统一操作名"] == "set_audio_state"]
    finally:
        book.close()
    normal = [json.loads(row["参数JSON"]) for row in rows
              if row["用例类型"] == "NORMAL"]
    rejected = [json.loads(row["参数JSON"]) for row in rows
                if row["用例类型"] == "BUSINESS_EXCEPTION"]
    assert normal and all(params.get("dev_id", 0) == 0 for params in normal)
    assert {1, 2} <= {params.get("dev_id") for params in rejected}


def test_video_standalone_normal_domain_uses_initialized_device_one():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    normal = [json.loads(row["参数JSON"]) for row in rows
              if row["统一操作名"] == "set_video_state" and
              row["用例类型"] == "NORMAL" and
              row["用例状态"] == "IMPLEMENTED"]
    assert normal and all(params.get("dev_id", 1) == 1 for params in normal)


def test_room_data_and_video_subtitle_use_protocol_field_shapes():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    room_data = next(json.loads(row["参数JSON"])["items"] for row in rows
                     if row["统一操作名"] == "set_room_data" and
                     row["用例状态"] == "IMPLEMENTED")
    assert room_data and {"ItemID", "RoomData"} <= set(room_data[0])
    styles = [json.loads(row["参数JSON"])["date_time"] for row in rows
              if row["统一操作名"] == "set_video_subtitle_params" and
              row["用例状态"] == "IMPLEMENTED"]
    assert styles and all({"Pos", "Size", "Color"} <= set(style)
                          for style in styles)
    assert {style["Pos"] for style in styles} <= {1, 2, 3, 4}


def test_http_record_normal_domain_uses_documented_states():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    cases = [row for row in rows if row["统一操作名"] == "operate_record" and
             row["用例类型"] == "NORMAL"]
    assert cases
    assert {row["用例状态"] for row in cases} == {"IMPLEMENTED"}
    assert all(row["是否执行"] == "是" for row in cases)
    assert {json.loads(row["参数JSON"])["state"] for row in cases} <= {0, 2}
    params = [json.loads(row["参数JSON"]) for row in cases]
    assert {item["state"] for item in params} <= {0, 2}
    assert all(row["前置模板"] == "RECORDING" for row, item in zip(cases, params)
               if item["state"] == 0)


def test_http_subtitle_normal_color_is_int32():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    params = [json.loads(row["参数JSON"]) for row in rows
              if row["统一操作名"] == "send_subtitles" and
              row["用例状态"] == "IMPLEMENTED" and
              row["用例类型"] == "NORMAL"]
    assert params and all(isinstance(item["font_color"], int) for item in params)
    assert {item["subtitle_type"] for item in params} <= {0, 2}


def test_http_layout_normal_cases_include_server_required_mode():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    layouts = [json.loads(row["参数JSON"])["layout"] for row in rows
               if row["统一操作名"] == "set_layout_http" and
               row["用例状态"] == "IMPLEMENTED" and
               row["用例类型"] == "NORMAL"]
    assert layouts
    assert all(layout.get("mode") in {1, 2, 3} for layout in layouts)
    assert all(layout.get("dataActive") in {1, 2, 3, 4, 5, 6}
               for layout in layouts)
    assert all("videoRelayId" in layout for layout in layouts)


def test_chairman_role_alias_uses_host_protocol_in_password_enabled_room():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    chairman = [row for row in rows if row["统一操作名"] == "set_role" and
                json.loads(row["参数JSON"]).get("role") == "chairman"]
    assert chairman
    assert all(row["用例状态"] == "IMPLEMENTED" and
               row["是否执行"] == "是" for row in chairman)
    for row in chairman:
        params = json.loads(row["参数JSON"])
        expected_precondition = "HOST_RELEASED" if params["enabled"] else "HOST_HELD"
        assert row["前置模板"] == expected_precondition
        if params["enabled"]:
            assert params["chair_pwd"] == "${env.chair_password}"


def test_role_disable_cases_first_build_the_enabled_state():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    cases = [row for row in rows if row["统一操作名"] == "set_role" and
             row["用例状态"] == "IMPLEMENTED" and
             row["用例类型"] == "NORMAL" and
             not json.loads(row["参数JSON"]).get("enabled", True)]
    assert cases
    for row in cases:
        params = json.loads(row["参数JSON"])
        if params["user_id"] == "${operator.room_user_id}":
            expected = "HOST_HELD" if params.get("role") == "chairman" else "NONE"
            assert row["前置模板"] == expected
        else:
            assert row["前置模板"] == f"TARGET_{params['role'].upper()}_ON"

    self_roles = [row for row in rows if row["统一操作名"] == "set_role" and
                  json.loads(row["参数JSON"]).get("role") in {
                      "admin", "presenter"} and
                  json.loads(row["参数JSON"]).get("user_id") ==
                  "${operator.room_user_id}"]
    assert self_roles
    for row in self_roles:
        params = json.loads(row["参数JSON"])
        if params["enabled"]:
            assert row["用例类型"] == "BUSINESS_EXCEPTION"
            assert row["预期结果类型"] == "NO_SUCCESS_EVENT"
        else:
            assert row["用例类型"] == "NORMAL"
            assert row["预期结果类型"] == "SUCCESS_EVENT"


def test_release_right_uses_server_no_owner_value_zero():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    releases = [json.loads(row["预期Response"]) for row in rows
                if row["统一操作名"] == "release_right" and
                row["用例状态"] == "IMPLEMENTED" and
                row["用例类型"] == "NORMAL"]
    assert releases
    assert all(response.get("OccupiedUserID") == 0 for response in releases)


def test_permission_changes_use_documented_role_and_server_delivery():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    role_cases = [row for row in rows
                  if row["统一操作名"] == "set_role_permissions" and
                  row["用例状态"] == "IMPLEMENTED"]
    assert role_cases
    assert all(set(json.loads(row["参数JSON"])["role_codes"]) <= {"mentee", "audit"}
               for row in role_cases)

    permission_cases = [row for row in rows
                        if row["统一操作名"] in {
                            "set_role_permissions", "set_user_permissions"} and
                        row["用例状态"] == "IMPLEMENTED"]
    assert permission_cases and all(row["独立执行"] == "是"
                                    for row in permission_cases)

    for operation in ("set_role_permissions", "set_user_permissions"):
        metadata = get_expectation(operation)
        assert metadata.delivery is DeliveryPolicy.ALL_PARTICIPANTS


def test_polling_normal_cases_always_acquire_polling_right():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    cases = [row for row in rows if row["统一操作名"] == "configure_polling" and
             row["用例类型"] == "NORMAL"]
    assert cases
    assert all(row["用例状态"] == "IMPLEMENTED" for row in cases)
    assert all(row["独立执行"] == "是" for row in cases)
    assert all(row["前置模板"] == "POLLING_RIGHT" for row in cases)


def test_aux_normal_cases_always_hold_the_matching_right():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    managed = [row for row in rows if row["场景类型"] == "AUX_FM_CONTENTION"]
    assert len(managed) == 1 and managed[0]["用例状态"] == "IMPLEMENTED"
    assert managed[0]["是否执行"] == "是"
    assert managed[0]["独立执行"] == "是" and managed[0]["阻塞ID"] is None
    cases = [row for row in rows if row["统一操作名"] in {
        "start_aux_stream", "stop_aux_stream"} and
        row["场景类型"] != "AUX_FM_CONTENTION" and
        row["用例状态"] == "IMPLEMENTED" and
        row["用例类型"] == "NORMAL"]
    allowed = {
        "start_aux_stream": {"MEDIA_RIGHT", "VNC_RIGHT"},
        "stop_aux_stream": {"MEDIA_STARTED_WITH_RIGHT", "VNC_STARTED_WITH_RIGHT"},
    }
    assert cases and all(row["前置模板"] in allowed[row["统一操作名"]]
                         for row in cases)
    assert all(row["独立执行"] == "是" for row in cases
               if row["统一操作名"] == "start_aux_stream")


def test_http_av_control_uses_target_state_precondition():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    for row in rows:
        if row["用例状态"] != "IMPLEMENTED" or row["用例类型"] != "NORMAL":
            continue
        params = json.loads(row["参数JSON"])
        if row["统一操作名"] == "control_user_audio":
            expected = "TARGET_AUDIO_ON" if params["state"] == 0 else "TARGET_AUDIO_OFF"
            assert row["前置模板"] == expected
        elif row["统一操作名"] == "control_user_video":
            expected = "TARGET_VIDEO_ON" if params["state"] == 0 else "TARGET_VIDEO_OFF"
            assert row["前置模板"] == expected


def test_http_role_control_uses_documented_zero_or_two_states():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    params = [json.loads(row["参数JSON"]) for row in rows
              if row["统一操作名"] in {
                  "set_user_manager", "set_user_presenter"} and
              row["用例状态"] == "IMPLEMENTED" and
              row["用例类型"] == "NORMAL"]
    assert params and {item["state"] for item in params} <= {0, 2}
    for row in rows:
        if row["统一操作名"] not in {"set_user_manager", "set_user_presenter"}:
            continue
        if row["用例状态"] != "IMPLEMENTED" or row["用例类型"] != "NORMAL":
            continue
        if json.loads(row["参数JSON"])["state"] == 0:
            expected = ("HTTP_MANAGER_ON" if row["统一操作名"] == "set_user_manager"
                        else "HTTP_PRESENTER_ON")
            assert row["前置模板"] == expected


def test_dynamic_id_operations_are_not_standalone_normal_cases():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    dynamic = {"enter_group", "end_groups", "bring_to_main"}
    assert not any(row['统一操作名'] == 'recall_msg' for row in rows)
    assert all(row["用例状态"] == "IMPLEMENTED" and row["独立执行"] == "是"
               for row in rows if row["统一操作名"] in dynamic)
    releases = [row for row in rows
                if row["统一操作名"] == "release_right" and
                row["用例类型"] == "NORMAL"]
    assert releases and all(row["前置模板"] == "RIGHT_HELD" for row in releases)


def test_empty_public_chat_is_a_real_server_supported_boundary():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    cases = [row for row in rows if row["统一操作名"] == "chat" and
             json.loads(row["参数JSON"]).get("text") == "" and
             json.loads(row["参数JSON"]).get("dst_user_id") == 0]
    assert cases
    assert all(row["用例类型"] == "NORMAL" and
               row["预期结果类型"] == "SUCCESS_EVENT" and
               row["预期接收端"] == "operator,observer" for row in cases)


def test_all_root_protocol_definitions_are_traceable():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["Conf信令检查"])
    finally:
        book.close()
    assert len(rows) == 127
    assert all(row["正常用例ID"] or row["异常用例ID"] or row["缺口原因"]
               for row in rows)


def test_other_user_av_cases_prepare_the_observer_state():
    from meeting_test_runtime import ExcelCaseLoader

    cases = ExcelCaseLoader(EXCEL).load_cases()
    other_audio = [
        case for case in cases
        if case.operation == "set_audio_state"
        and "${observer.room_user_id}" in str(case.params.get("user_id", ""))
        and case.case_type.value == "NORMAL"
    ]
    other_video = [
        case for case in cases
        if case.operation == "set_video_state"
        and "${observer.room_user_id}" in str(case.params.get("user_id", ""))
        and case.case_type.value == "NORMAL"
    ]
    assert other_audio and other_video
    assert all(
        case.precondition == (
            "TARGET_AUDIO_ON" if case.params["state"] == 0
            else "TARGET_AUDIO_OFF")
        for case in other_audio
    )
    assert all(
        case.precondition == (
            "TARGET_VIDEO_ON" if case.params["state"] == 0
            else "TARGET_VIDEO_OFF")
        for case in other_video
    )


def test_video_state_one_is_parameter_blocked_not_globally_hidden():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    video_cases = [row for row in rows if row["统一操作名"] == "set_video_state"]
    state_one = [row for row in video_cases
                 if json.loads(row["参数JSON"]).get("state") == 1]
    assert len(state_one) == 1
    assert all(row["用例状态"] == "IMPLEMENTED" and row["前置模板"] == "VIDEO_OFF"
               for row in state_one)
    assert any(json.loads(row["参数JSON"]).get("state") == 0 and
               row["用例状态"] == "IMPLEMENTED" for row in video_cases)
    assert any(json.loads(row["参数JSON"]).get("state") == 2 and
               row["用例状态"] == "IMPLEMENTED" for row in video_cases)


def test_invalid_layout_ids_are_parameter_blocked_only():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    layout_cases = [row for row in rows if row["统一操作名"] == "set_layout"]
    invalid = [row for row in layout_cases
               if json.loads(row["参数JSON"]).get("layout_type") in {-1, 5, 9999}]
    assert len(invalid) == 3
    assert all(row["用例状态"] == "IMPLEMENTED" and row["用例类型"] == "NORMAL"
               for row in invalid)
    supported = {0, 1, 2, 3, 4, 6, 9, 12, 16, 25, 36, 49, 64, 1001, 1002}
    implemented = {
        json.loads(row["参数JSON"]).get("layout_type")
        for row in layout_cases if row["用例状态"] == "IMPLEMENTED"
    }
    assert supported <= implemented


def test_invalid_right_types_are_parameter_blocked_only():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    right_cases = [row for row in rows
                   if row["统一操作名"] in {"request_right", "release_right"}]
    invalid = [row for row in right_cases
               if json.loads(row["参数JSON"]).get("right_type") in {0, 13, 99}]
    assert len(invalid) == 6
    assert all(row["用例状态"] == "IMPLEMENTED" for row in invalid)
    for row in invalid:
        right_type = json.loads(row['参数JSON'])['right_type']
        if right_type == 0:
            assert row['预期结果类型'] == 'NO_SUCCESS_EVENT'
        else:
            assert row['预期结果类型'] == 'SUCCESS_RESPONSE'
            expected = json.loads(row['预期Response'])
            assert expected['Result'] == 0 and expected['RightType'] == right_type
            assert expected['Operation'] == (1 if row['统一操作名'] == 'request_right' else 0)
            assert expected['OccupiedUserID'] == ('${operator.room_user_id}' if row['统一操作名'] == 'request_right' else 0)
    for operation in ("request_right", "release_right"):
        implemented = {
            json.loads(row["参数JSON"]).get("right_type")
            for row in right_cases
            if row["统一操作名"] == operation and row["用例状态"] == "IMPLEMENTED"
        }
        assert set(range(1, 13)) <= implemented


def test_self_role_enable_and_disable_have_distinct_semantics():
    from meeting_test_runtime import ExcelCaseLoader

    cases = ExcelCaseLoader(EXCEL).load_cases()
    self_roles = [
        case for case in cases
        if case.operation == "set_role"
        and case.params.get("role") in {"admin", "presenter"}
        and case.params.get("user_id") == "${operator.room_user_id}"
    ]
    assert len(self_roles) == 4
    assert all(case.expected_outcome.value == "NO_SUCCESS_EVENT"
               for case in self_roles if case.params.get("enabled") is True)
    assert all(case.expected_outcome.value == "SUCCESS_EVENT"
               for case in self_roles if case.params.get("enabled") is False)


def test_record_state_cases_are_isolated_from_the_shared_meeting():
    from meeting_test_runtime import ExcelCaseLoader

    cases = ExcelCaseLoader(EXCEL).load_cases()
    records = [case for case in cases
               if case.operation in {"start_record", "pause_record", "stop_record"}
               and case.case_type.value == "NORMAL"]
    assert records
    assert all(case.isolated and case.environment_risk.value == "ISOLATED_STATEFUL"
               for case in records)


def test_conference_state_matrix_forces_real_transitions_in_isolated_rooms():
    from meeting_test_runtime import ExcelCaseLoader

    cases = ExcelCaseLoader(EXCEL).load_cases()
    states = [case for case in cases if case.operation == "set_conf_state"
              and case.case_id.find("-conf-") >= 0]
    assert states and all(case.isolated for case in states)
    assert all(case.expected_outcome.value == 'EVENT_SEQUENCE'
               for case in states if 'mode' in case.params)

    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    waiting = [row for row in rows
               if row["统一操作名"] == "set_conf_state"
               and "enable_waiting_room" in json.loads(row["参数JSON"])]
    assert waiting and all(row["用例状态"] == "IMPLEMENTED" and row['独立执行'] == '是'
                           for row in waiting)
    modes = [row for row in rows
             if row["统一操作名"] == "set_conf_state"
             and "mode" in json.loads(row["参数JSON"])]
    assert modes and all(row["用例状态"] == "IMPLEMENTED" and row['预期结果类型'] == 'EVENT_SEQUENCE'
                         for row in modes)


def test_known_blocked_dependency_flows_are_not_executable():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["依赖流程"])
    finally:
        book.close()
    blocked = {
        "FLOW-AUX-CONTENTION", "FLOW-POLLING-LIFECYCLE",
        "FLOW-GROUP-LIFECYCLE"}
    matching = [row for row in rows if row["流程ID"] in blocked]
    assert matching
    assert {row["流程ID"] for row in matching} == blocked
    assert all(row["实现状态"] == ('COVERED_BY_CASES' if row['流程ID'] == 'FLOW-AUX-CONTENTION'
                                 else 'IMPLEMENTED') for row in matching)

    group = [row for row in matching if row["流程ID"] == "FLOW-GROUP-LIFECYCLE"]
    enter = next(row for row in group if row["统一操作名"] == "enter_group")
    enter_notify = json.loads(enter["预期Notify序列JSON"])[0]
    assert enter_notify["cmdid"] == 6624
    assert enter_notify["receivers"] == ["observer"]
    assert enter_notify["forbidden_receivers"] == ["operator"]
    assert json.loads(enter['参数JSON'])['users'][0]['Right'] == 2
    assert any(row['统一操作名'] == 'join_related' for row in group)

    polling = [row for row in matching if row['流程ID'] == 'FLOW-POLLING-LIFECYCLE'
               and row['统一操作名'] == 'configure_polling']
    for row in polling:
        params = json.loads(row['参数JSON'])
        expected = json.loads(row['预期Notify序列JSON'])
        if params['status'] == 1:
            assert expected == []  # 暂停观察无切换，不编造即时Notify。
        else:
            assert expected[0]['match']['VideoPolling.Screen'] == params['screen']


def test_device_flow_models_server_split_av_notifications():
    from meeting_test_runtime import ExcelCaseLoader

    flow = next(flow for flow in ExcelCaseLoader(EXCEL).load_dependency_flows()
                if flow.flow_id == "FLOW-DEVICE-AV")
    av_steps = [step for step in flow.steps if step.operation == "report_av_info"
                and step.params.get("audio") and step.params.get("video")]
    assert av_steps
    assert all(len(step.expected_notifies) == 2 for step in av_steps)
    assert all(step.expected_notifies[0].match.get("Audio.ID") == 0
               for step in av_steps)
    assert all(step.expected_notifies[1].match.get("Video.ID") == 1
               for step in av_steps)


def test_vnc_and_media_start_are_executable_after_protocol_parameter_fix():
    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        rows = _rows(book["功能用例"])
    finally:
        book.close()
    starts = [row for row in rows if row["统一操作名"] == "start_aux_stream"]
    vnc = [row for row in starts
           if json.loads(row["参数JSON"]).get("kind") == "vnc"]
    media = [row for row in starts
             if json.loads(row["参数JSON"]).get("kind") == "media"]
    assert vnc and any(row["用例状态"] == "IMPLEMENTED" for row in vnc)
    assert all(row["用例状态"] == "IMPLEMENTED" for row in vnc
               if row["场景类型"] != "AUX_FM_CONTENTION")
    assert media and any(row["用例状态"] == "IMPLEMENTED" for row in media)
