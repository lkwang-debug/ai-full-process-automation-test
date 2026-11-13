"""汇总本次完整执行和有明确范围的补测，不覆盖历史证据。"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'sessionmanager_py')]
from meeting_test_runtime.excel_loader import ExcelCaseLoader
from summarize_acceptance import node_verdict


def main():
    reports = ROOT / 'reports'
    runs = ['allure-acceptance-full-20260916', 'allure-acceptance-full-20260916-followup',
            'allure-group-config-20260916', 'allure-final-frozen-20260916']
    cases = ExcelCaseLoader(ROOT / 'tests/e2e/data/fm_e2e_cases.xlsx').load_cases(include_disabled=True)
    latest = {}
    flows = {}
    versions = {}
    for run in runs:
        manifest = json.loads((reports / run / 'acceptance_manifest.json').read_text(encoding='utf-8'))
        versions[run] = manifest['fingerprint']
        grouped = defaultdict(list)
        for row in manifest['results']:
            grouped[row['nodeid']].append(row)
        for node, phases in grouped.items():
            result = {'status': node_verdict(phases), 'source': run}
            case_id = next((p['case_id'] for p in phases if p.get('case_id')), None)
            if case_id:
                latest[case_id] = result
            else:
                flows[node] = result
    active_ids = {c.case_id for c in cases}
    excluded = {k: v for k, v in latest.items() if k not in active_ids}
    assert all('-pause_record-' in k for k in excluded), '范围外历史结果必须有明确排除依据'
    latest = {k: v for k, v in latest.items() if k in active_ids}
    assert set(latest) == active_ids, '必须覆盖Excel全部用例'
    totals = Counter(r['status'] for r in latest.values())
    modules = defaultdict(Counter)
    for case in cases:
        modules[case.module][latest[case.case_id]['status']] += 1
    summary = {'status': 'FUNCTIONAL_SCOPE_PASSED_WITH_PROTOCOL_GAPS' if not totals['failed'] else 'FULL_RUN_WITH_FAILURES',
               'date': '2026-09-16', 'functional_counts': dict(totals),
               'dependency_counts': dict(Counter(r['status'] for r in flows.values())),
               'fingerprints': versions, 'cases': latest, 'excluded_by_user': excluded,
               'scope': '最新固定版本批次覆盖全部581功能及9流程，运行前后指纹一致；589通过1失败。历史批次只作追溯。'}
    (reports / 'acceptance_result_20260916.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    status_file = reports / 'real_server_status.json'
    historical = json.loads(status_file.read_text(encoding='utf-8'))
    historical['current_acceptance'] = {k: v for k, v in summary.items() if k != 'cases'}
    historical['current_acceptance']['report'] = 'full_run_20260916.md'
    historical['current_acceptance']['pending'] = ['轮询1105省略Interval修改后5变1', '独立H323跨调用者6744响应路由缺陷', '其他未实现协议字段及依赖流程仍需独立验收']
    status_file.write_text(json.dumps(historical, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 2026-09-16 全量真机回归结果', '',
        '状态：最新固定版本全量批次出现1条轮询字段失败，最终验收未通过；此前累计全绿不替代最新失败。', '',
        f'- 当前范围功能用例{len(cases)}条：{totals["passed"]}通过、{totals["failed"]}失败、{totals["skipped"]}阻塞跳过、0未执行。用户已明确排除暂停录制2条，不再计为阻塞。',
        '- 已实现依赖流程9条全部通过；其余未实现流程及协议缺口不因此算通过。',
        '- 最新冻结批次：589通过、1失败，耗时40分21秒；SDK/框架/Excel运行前后指纹一致，全部当前用例和已实现流程均收集执行，含独立破坏性场景。',
        '- 完整批次：565通过、25失败，耗时36分56秒；通讯录修正后补测2通过、暂停2跳过。重复通过不重复计数。',
        '- 分组规则确认后复测32条配置组合及1条既有分组流程，33条全部通过；主会议4种开关组合×请求4种组合均有覆盖。',
        '- 外部框架165条单测通过；SDK291条通过、1条跳过。',
        '- 本次包含三个版本指纹：全量、通讯录修复、分组覆盖规则修订。补测覆盖实际修改范围，并非一次最终代码全量全通过。', '',
        '[最新冻结批次Allure](allure-report-final-frozen-20260916/index.html) · [冻结门禁](frozen_acceptance_20260916.json) · [逐用例结果与证据来源](acceptance_result_20260916.json)',
        '最新链接为单个冻结批次590条真实结果，不是历史最新PASS合并视图。旧current-scope报告与早期失败/补测仅保留为历史证据。', '',
        '## 模块结果', '', '| 模块 | 通过 | 失败 | 阻塞跳过 |', '|---|---:|---:|---:|']
    for module, counts in modules.items():
        lines.append(f'| {module} | {counts["passed"]} | {counts["failed"]} | {counts["skipped"]} |')
    lines += ['', '## 剩余问题', '',
        '0. 最新失败：轮询1105新增Interval=5/Status=0，后续Operation=2只设Status=2、省略Interval，6633持续返回Interval=1。真实请求已由170日志确认；需要明确省略字段保留语义并分析配置/默认值处理，不改为接受1。详见[frozen_polling_failure_20260916.md](frozen_polling_failure_20260916.md)。',
        '1. 原24条分组开关失败已解决：开发确认新版直接取主会议配置覆盖请求，不是总开关。32条创建用例先用公开set_conf_state设置主会议开关，逐字段验证A/B的6610，再按该独立配置断言6623；全部通过。创建配置与实际主动加入/离开仍分层验收，不将State=0的配置通过当成可实际加入。',
        '2. 暂停录制正常/异常2条已按用户要求移出本期用例，不再执行或计入阻塞。历史Allure中的2条跳过保留为原执行证据，不改写为通过。',
        '3. 独立H323-CROSS-CALLER-HANGUP仍开放：A挂断HTTP创建的呼叫，异步6744路由到原后台用户。5条邀请/本端呼叫用例通过不等于此缺陷修复。', '',
        '## 分组日志证据', '',
        '170，2026-09-16，FMRoomSrv日志，10:43:27.941～10:43:27.943，会议1000001169：', '',
        '```xml', '<!-- 创建前读取：独立预期人数10 -->',
        '<cmd id="6634"><GroupRoomConfig><GroupRoomMaxCount val="30"/><GroupRoomMaxUserCount val="10"/></GroupRoomConfig></cmd>',
        '<!-- 6523请求核心字段 -->', '<RoomInfo><Theme val="E2E分组-10-0"/><State val="0"/><MaxUserCount val="16"/><EnableJoin val="1"/><EnableLeave val="1"/></RoomInfo>',
        '<!-- 6623实际核心字段 -->', '<RoomInfo><RoomID val="1879048213"/><MaxUserCount val="10"/><EnableJoin val="0"/><EnableLeave val="0"/></RoomInfo>', '```', '',
        '日志入口为LocalRoom.cpp:7293 OnCreateGroupRoom。上面仅摘录核心字段，完整请求含User/Right，以Allure及服务日志为准。', '',
        '## 已修复及边界', '',
        '- 本人申请发言：明确设置ApplyBroadcastOwnAudio/BroadcastOwnAudio，并逐项验证6635；确认音频0后申请1，A/B收到状态2。此用例不冒充等待审批场景。',
        '- SDK只新增conf_get_room_config(group_room_config=True)查询能力；默认邀请码查询不变。只发GroupRoomConfig，避免邀请码分条响应提前结束请求。测试仍只调用公开方法。',
        '- 通讯录快照正确字段是offline；原始conf_get_contacts为offline_users，已分别断言并真机复测。',
        '- 录制纪要使用预置数据，不能代表模拟端产生媒体后自动生成纪要。上行完整XML轨迹、仅下行触发和其他SDK缺口仍保留。',
        '- 所有实际执行用例均完成teardown；未出现未预期断会、清理失败或阶段中断。已确认非法协议的预期断会在独立会议中验证。', '',
        '## 指纹', '']
    for run, fingerprint in versions.items():
        lines.append(f'- [{run}]({run}/acceptance_manifest.json)：`{fingerprint}`')
    (reports / 'full_run_20260916.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(dict(totals), summary['dependency_counts'])


if __name__ == '__main__':
    main()
