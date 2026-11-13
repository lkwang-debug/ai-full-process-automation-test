"""最终验收的只读缺口复核；静态关联不代表真实协议已执行。"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'sessionmanager_py')]
from meeting_test_runtime.conf_audit import scan_conf_protocol
from meeting_test_runtime.excel_loader import ExcelCaseLoader
from meeting_test_runtime.expectations import OPERATION_EXPECTATIONS
from meeting_test_runtime.blockers import blocker_for_cmdid


# 手工复核的正式方向，避免“未登记上行方法=仅下行”的错误推导。
UPLINK_MISSING = {}
OBSERVED_ASSERTED = {
    6512: ('tests/e2e/data/fm_e2e_cases.xlsx', '公开chat(font=...)按XML属性发送，群聊和私聊真机通过'),
    6612: ('tests/e2e/data/fm_e2e_cases.xlsx', '接收端断言Font.Name/Height/Weight/Underline/CharSet/Italic等字段'),
    6513: ('tests/e2e/data/fm_e2e_cases.xlsx', 'set_layout/set_window_state公开全屏字段，媒体ID 0/1及退出全屏真机通过'),
    6613: ('tests/e2e/data/fm_e2e_cases.xlsx', '目标端断言Full/FullVideoID/FullVideoMediaID/ValidFullMode，发送端反向断言'),
    6631: ('meeting_test_runtime/confirmed_contracts.py', 'Conf登录初始化断言Role列表、RoleCode、Permissions和RoleEnd'),
    6529: ('meeting_test_runtime/interaction_scenarios.py', 'A拒绝待审批消息；拒绝后重复同意同一SerialNumber静默忽略'),
    6629: ('meeting_test_runtime/interaction_scenarios.py', '首次拒绝返回CheckResult=0/CheckUserID=A；正文不发布'),
    6637: ('meeting_test_runtime/interaction_scenarios.py', 'recordMode=4真实触发：A先收6637原始错误，再收6610；B无定向错误'),
    6639: ('meeting_test_runtime/confirmed_contracts.py', 'Conf登录初始化断言产品能力列表、MeetingNow、ServerRecord和End'),
    6604: ('tools/create_e2e_template.py', 'FLOW-LOCK-JOIN：敲门UserID和正确主持端'),
    6611: ('meeting_test_runtime/case_executor.py', '主持申请失败按ERROR_EVENT验证6611及错误码；成功另验6609'),
    6617: ('tools/create_e2e_template.py', 'FLOW-LOCK-JOIN：敲门端等待开门通知，后续真实入会'),
    6646: ('meeting_test_runtime/state_scenarios.py', 'remote_state_reply：目标端来源/VNC字段与发送端反向断言'),
    6542: ('meeting_test_runtime/interaction_scenarios.py', 'Conf 5.0管理员向目标端发起远程音视频确认请求'),
    6642: ('meeting_test_runtime/interaction_scenarios.py', '目标端定向收到请求，断言来源、目标、设备及Type'),
    6543: ('meeting_test_runtime/interaction_scenarios.py', '目标端基于实际6642字段显式同意或拒绝'),
    6643: ('meeting_test_runtime/interaction_scenarios.py', '原请求端收到同意/拒绝结果；同意与6609关联，拒绝反向断言无成功状态'),
    6654: ('meeting_test_runtime/interaction_scenarios.py', '固定会议HTTP定向通知：B收到、A不收到、特殊字符及连接健康'),
    6666: ('meeting_test_runtime/group_assertions.py', 'verify_created_groups：分组RoomID及成员ID/Right'),
    6667: ('meeting_test_runtime/state_scenarios.py', 'waiting_admission：真实等候室路由，后续关联子连接及入会；路由逐字段覆盖仍需增强'),
}
DOWNLINK_REVIEW = {
}


def main():
    definitions = scan_conf_protocol(ROOT / 'ConfProtocol.xml')
    loader = ExcelCaseLoader(ROOT / 'tests/e2e/data/fm_e2e_cases.xlsx')
    cases = loader.load_cases(include_disabled=True)
    flows = loader.load_dependency_flows()
    records = []
    for definition in definitions:
        cmdid = definition.cmdid
        senders = [op for op, e in OPERATION_EXPECTATIONS.items() if e.request_cmdid == cmdid]
        receivers = [op for op, e in OPERATION_EXPECTATIONS.items()
                     if (e.response and e.response.cmdid == cmdid) or any(n.cmdid == cmdid for n in e.notifies)]
        methods = sorted(set(senders + receivers))
        related = [c.case_id for c in cases if c.operation in methods]
        direction = '上行' if senders or cmdid in UPLINK_MISSING else '下行/方向需人工复核'
        state, detail = 'HAS_TEST_MAPPING_NOT_FULL_COVERAGE', '有关联业务；不能仅凭映射判全部字段已验收'
        blocker = blocker_for_cmdid(cmdid)
        if blocker:
            state, detail = blocker.kind.value, blocker.reason
        if cmdid in UPLINK_MISSING:
            state, detail = 'MISSING_PUBLIC_UPLINK_API', UPLINK_MISSING[cmdid][1]
        if cmdid in DOWNLINK_REVIEW:
            state, detail = 'NEEDS_TRIGGER_OR_FIELD_ASSERTION', DOWNLINK_REVIEW[cmdid]
        if cmdid in OBSERVED_ASSERTED:
            state, detail = 'HAS_SPECIAL_SCENARIO_ASSERTION', '；'.join(OBSERVED_ASSERTED[cmdid])
        if cmdid == 6517:
            direction, state = '上行', 'SDK_LIFECYCLE_SEND'
            detail = '由conf_login(init_complete=True)内部公开流程发送；完整发送轨迹缺口独立保留，不需测试侧拼XML'
        if not methods and cmdid not in OBSERVED_ASSERTED and state == 'HAS_TEST_MAPPING_NOT_FULL_COVERAGE':
            state, detail = 'NEEDS_MANUAL_MAPPING_REVIEW', '尚无操作映射；不能自动判为仅下行或SDK不存在'
        records.append({'ordinal': definition.ordinal, 'cmdid': cmdid, 'version': definition.version,
                        'description': definition.description, 'direction': direction, 'state': state,
                        'detail': detail, 'methods': methods, 'case_ids': related})
    result = {'scope': '只读人工复核补充；协议追溯不等于全量可执行或字段覆盖',
              'definitions': len(records), 'unique_cmdids': len({d.cmdid for d in definitions}),
              'states': dict(Counter(r['state'] for r in records)), 'protocols': records,
              'flows': [{'id': f.flow_id, 'name': f.name, 'status': f.status.value,
                         'operations': [s.operation for s in f.steps]} for f in flows]}
    target = ROOT / 'reports/final_gap_audit_20260916.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 最终验收缺口复核（2026-09-16）', '',
             '不能用当前581条全部通过推导127条协议定义所有字段及所有流程都已验收。'
             '本清单修正旧台账误分类；只读核对，未向服务器发送缺失协议。', '',
             '## 已纠正的台账问题', '',
             '- 6529、6540、6542、6543是上行；新版SDK均已有公开方法，服务端未完成项与SDK缺口分开记录。',
             '- 6542/6642/6543/6643远程音视频同意/拒绝及6654 Web定向通知已在6.160专项通过。',
             '- 6646、6666、6667已有专用场景证据，不再标成完全没有触发；尚需逐字段评审的部分仍保留。',
             '- 6509扩展状态已有set_user_state入口；API可调用与字段全覆盖分开。',
             '- 暂停录制已正式排除；FLOW-RECORD-RESULT的Excel步骤仍含暂停，而prepared执行只查预置结果。应改为明确的预置纪要流程，不能保留错误的流程能力描述。', '',
             '## 真正需要开发/产品确认', '',
             '| 协议/事项 | 当前缺口 | 处理责任 |', '|---|---|---|']
    for cmdid, (title, detail) in UPLINK_MISSING.items():
        lines.append(f'| {cmdid} {title} | {detail} | 开发提供公开SDK封装/正式契约，测试接入 |')
    lines += ['| 人脸6555/6556/6558/6559及对应下行 | SDK已有公开方法；缺人脸服务、真实视频设备、FaceID数据库及服务端更新 | 开发提供可用隔离环境与测试数据，或产品正式调整范围 |',
              '| H323跨调用者挂断 | 异步6744仍按旧呼叫者路由 | 开发修复部署后，测试独立会议复测；不能改成不等Response |',
              '| 6637等服务异常下行 | 需要安全可重复触发及正式错误码 | 开发提供测试入口，不停止共享服务制造异常 |', '',
              '## 测试侧可继续完善', '',
              '- 等候室拒绝/混合审批、稳定音频等待审批、分组运行后加入/离开等流程：先按正式协议和权限契约设计，逐步真机验证。',
              '- 6639产品功能等下行的字段、分包终止和权限前置；已有事件不等于断言完整。',
              '- 流程台账中已由功能场景覆盖的邀请应关联现有用例，不重复计数；未实现行不能静默过滤后宣称12条全通过。',
              '- 固定版本回归必须检查SDK/框架/Excel运行前后指纹一致；上行轨迹和下载元数据是独立可观测性改造，当前记录不冒称完整。', '',
              '## 当前流程台账', '', '| ID | 名称 | 台账状态 |', '|---|---|---|']
    lines += [f'| {f.flow_id} | {f.name} | {f.status.value} |' for f in flows]
    lines += ['', '逐协议复核：[JSON台账](final_gap_audit_20260916.json)。本清单不自动调整验收分母，不删除开发待办。', '']
    (ROOT / 'reports/final_gap_audit_20260916.md').write_text('\n'.join(lines), encoding='utf-8')
    print(result['states'])
    print([(f.flow_id, f.status.value) for f in flows if f.status.value != 'IMPLEMENTED'])


if __name__ == '__main__':
    main()
