import json

import pytest
from openpyxl import Workbook, load_workbook

from meeting_test_runtime.excel_loader import ExcelCaseLoader
from meeting_test_runtime.models import CaseStatus, CaseType, ClosureType, ExpectedOutcome


HEADERS = ["用例ID","模块","闭环类型","用例类型","场景类型","前置模板","操作角色","目标角色",
           "统一操作名","参数JSON","预期结果类型","预期业务键","预期Response","预期Notify序列JSON",
           "预期接收端","不应接收端","字段断言JSON","预期错误码JSON","独立执行","执行级别","是否执行"]


def _workbook(path):
    book = Workbook(); profiles = book.active; profiles.title = "终端配置"
    profiles.append(["配置ID","终端类型","账号引用","环境引用","可承担角色","启用"])
    profiles.append(["FM_01","fm","account_a","fm_env","operator","是"])
    book.create_sheet("参数域").append(["参数域ID"])
    cases = book.create_sheet("功能用例"); cases.append(HEADERS)
    cases.append(["FM-AUDIO-N-001","音频","FM_TO_FM","NORMAL","BASIC","NONE","operator","",
                  "mic_on","{}","SUCCESS_EVENT","change_user_state","{}",
                  '[{"source":"conf","cmdid":6609,"kind":"notify",'
                  '"receivers":["operator","observer"],"forbidden_receivers":"witness"}]',
                  "operator,observer","",
                  '{"Audio.State":2}',"[]","否","P0","是"])
    cases.append(["FM-LOGIN-E-001","登录","FM_TO_FM","BUSINESS_EXCEPTION","LOGIN","NONE","operator","",
                  "front_login",'{"password":"wrong"}',"ERROR_AND_NO_EVENT","login_error","{}","[]","","",
                  "{}",json.dumps(["0x2101",4102]),"是","P0","是"])
    signals = book.create_sheet("Conf信令检查"); signals.append(["cmdid","协议说明"]); signals.append([6509,"改变用户状态"])
    methods = book.create_sheet("业务方法检查"); methods.append(["公开方法"])
    flows = book.create_sheet("依赖流程")
    flows.append(["流程ID","流程名称","优先级","环境风险","步骤序号","执行端","统一操作名",
                  "参数JSON","输出变量JSON","预期Response","预期Notify序列JSON","预期接收端",
                  "不应接收端","步骤说明","失败是否终止","清理要求","实现状态"])
    flows.append(["FLOW-1","建会入会","P0","DESTRUCTIVE_ISOLATED",1,"http_admin",
                  "create_instant_meeting","{}",'{"meeting.room_id":"data.roomId"}',"{}","[]",
                  "","","创建会议","是","删除会议","DESIGNED"])
    book.save(path)


def test_excel_loader_reads_extended_cases(tmp_path):
    path=tmp_path/"cases.xlsx"; _workbook(path); loader=ExcelCaseLoader(path)
    profiles=loader.load_profiles({"account_a":{"username":"a","password":"p"}},
                                  {"fm_env":{"host":"127.0.0.1","port":1089}})
    cases=loader.load_cases()
    assert profiles[0].roles == frozenset({"operator"})
    assert cases[0].closure_type is ClosureType.FM_TO_FM
    assert cases[0].expected_notifies[0].cmdid == 6609
    assert cases[0].expected_notifies[0].receivers == ("operator", "observer")
    assert cases[0].expected_notifies[0].forbidden_receivers == ("witness",)
    assert cases[1].case_type is CaseType.BUSINESS_EXCEPTION
    assert cases[1].expected_outcome is ExpectedOutcome.ERROR_AND_NO_EVENT
    assert cases[1].expected_error_codes == (0x2101,4102)
    assert loader.load_signal_checklist()[0]["cmdid"] == 6509
    flows = loader.load_dependency_flows()
    assert flows[0].status is CaseStatus.DESIGNED
    assert flows[0].steps[0].output_variables == {"meeting.room_id": "data.roomId"}


def test_excel_loader_rejects_invalid_error_code_json(tmp_path):
    path=tmp_path/"cases.xlsx"; _workbook(path)
    book=load_workbook(path); book["功能用例"].cell(3,18).value='{"code":1}'; book.save(path)
    with pytest.raises(ValueError,match="预期错误码JSON"):
        ExcelCaseLoader(path).load_cases()


def test_excel_loader_can_explicitly_include_disabled_risky_cases(tmp_path):
    path = tmp_path / "cases.xlsx"
    _workbook(path)
    book = load_workbook(path)
    sheet = book["功能用例"]
    sheet.cell(2, 21).value = "否"
    book.save(path)

    loader = ExcelCaseLoader(path)
    enabled = loader.load_cases()
    assert len(enabled) == 1 and enabled[0].enabled is True
    cases = loader.load_cases(include_disabled=True)
    assert len(cases) == 2
    assert cases[0].enabled is False
