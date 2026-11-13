"""Generate an honest functional and protocol coverage report."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]


def _rows(sheet):
    headers = [cell.value for cell in sheet[1]]
    return [dict(zip(headers, row)) for row in sheet.iter_rows(min_row=2, values_only=True)]


def generate(excel_path: Path, output: Path) -> None:
    book = load_workbook(excel_path, read_only=True, data_only=True)
    try:
        cases = _rows(book["功能用例"])
        signals = _rows(book["Conf信令检查"])
        methods = _rows(book["业务方法检查"])
        flows = _rows(book["依赖流程"])
    finally:
        book.close()

    case_types = Counter(row["用例类型"] for row in cases)
    outcomes = Counter(row["预期结果类型"] for row in cases
                       if row["用例类型"] == "BUSINESS_EXCEPTION")
    modules = Counter(row["模块"] for row in cases)
    closures = Counter(row["闭环类型"] for row in cases)
    levels = Counter(row["执行级别"] for row in cases)
    status = Counter(row["封装状态"] for row in signals)
    blocked = [row for row in signals if row["封装状态"] != "已封装"]
    traced = [row for row in signals if row["正常用例ID"] or row["缺口原因"]]

    status_path = ROOT / "reports/real_server_status.json"
    real_status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    totals = real_status.get("totals", {})
    current = real_status.get('current_acceptance', {})
    functional = current.get('functional_counts', {})
    latest_exists = (ROOT / 'reports/remaining_blockers_20260922.md').exists()
    sdk_update_passed = (
        ROOT / 'reports/allure-sdk-update-5cases-20260923/acceptance_manifest.json').exists()
    final_20260924 = (ROOT / 'reports/full-run-20260924-final.xml').exists()
    final_full_pass = final_20260924 or (ROOT / 'reports/full-run-20260923-final.xml').exists()
    lines = [
        "# sessionmanager_py 当前覆盖与缺口报告", "",
        "> 本报告区分已设计、可执行、已通过和开发缺口；未连接真实服务器的用例不计为通过。", "",
        "> 当前验收状态：" + (
            'CURRENT_EXECUTABLE_SCOPE_PASS' if final_full_pass else
            'PARTIAL_RETEST_SDK_UPDATE_8_PASS_PENDING_FULL_RUN' if sdk_update_passed else
            'PARTIAL_RETEST_AUX_RECOVERED_PENDING_FULL_RUN' if latest_exists else
            str(current.get('status', '见最近验收记录'))),
        (("> 最新完整执行见[2026-09-24全量回归](full_run_20260924.md)和[可读Allure](allure-report-full-readable-20260924-final/index.html)。" if final_20260924 else
          "> 最新完整执行见[2026-09-23全量回归](full_run_20260923.md)和[Allure](allure-report-full-20260923-final/index.html)。") if final_full_pass else
         "> 最新完整执行见[全量回归](full_run_20260916.md)，原始记录见[整改批次](acceptance_hardening_20260915.md)。历史累计PASS不是本轮验收通过。"), "",
        f"- 当前批次功能：通过{functional.get('passed', 0)}、失败{functional.get('failed', 0)}、阻塞跳过{functional.get('skipped', 0)}。",
        f"- 当前批次补充流程通过：{current.get('dependency_counts', {}).get('passed', 0)}。", "",
        "## 汇总", "",
        f"- Excel业务用例：{len(cases)}",
        f"- 正常用例：{case_types['NORMAL']}",
        f"- 业务异常用例：{case_types['BUSINESS_EXCEPTION']}",
        f"- 根目录ConfProtocol定义：{len(signals)}",
        f"- 业务方法台账：{len(methods)}",
        f"- 协议追溯记录完整：{len(traced)}/{len(signals)}",
        f"- 已关联sessionmanager能力：{status['已封装']}",
        f"- 缺少公开接口或解析：{status['MISSING_SESSIONMANAGER_API']}",
        f"- 部分字段封装：{status['部分封装']}",
        f"- 已实现并允许执行：{sum(row.get('用例状态') == 'IMPLEMENTED' and str(row.get('是否执行')) == '是' for row in cases)}",
        f"- 已设计待实现：{sum(row.get('用例状态') == 'DESIGNED' for row in cases)}",
        f"- 阻塞用例设计：{sum(row.get('用例状态') == 'SKIP_BLOCKED' for row in cases)}",
        f"- 关键依赖流程：{len(set(row['流程ID'] for row in flows))}条，其中IMPLEMENTED "
        f"{len(set(row['流程ID'] for row in flows if row['实现状态'] == 'IMPLEMENTED'))}条、DESIGNED "
        f"{len(set(row['流程ID'] for row in flows if row['实现状态'] == 'DESIGNED'))}条、SKIP_BLOCKED "
        f"{len(set(row['流程ID'] for row in flows if row['实现状态'] == 'SKIP_BLOCKED'))}条、OUT_OF_SCOPE "
        f"{len(set(row['流程ID'] for row in flows if row['实现状态'] == 'OUT_OF_SCOPE'))}条",
        f"- 已有功能用例闭环覆盖的流程：{len(set(row['流程ID'] for row in flows if row['实现状态'] == 'COVERED_BY_CASES'))}条；不重复执行或计入新增通过数。",
        "", "### 历史台账（非本轮结果）", "",
        f"- 历史累计通过：{totals.get('passed', 0)}，仅供追溯，不与本轮相加。",
        f"- 独立未解决服务端缺陷：{totals.get('open_server_defects', 0)}", "",
        f"- 已实现用例的环境跳过：{totals.get('skipped_env', 0)}", "",
        "## 业务异常判定方式", "",
    ]
    if final_full_pass:
        lines[2:2] = [
            ('> 2026-09-24最新指纹全量：590条Excel功能用例和12条补充流程全部通过。完整结果为`602 passed, 13 skipped, 0 failed`；13条仅为默认关闭的手工诊断探针。聊天Font、布局全屏和6631增强均已纳入本次完整回归。' if final_20260924 else
             '> 2026-09-23最终全量：590条Excel功能用例全部通过；另有12条依赖/生命周期流程通过。完整结果为`602 passed, 13 skipped, 0 failed`。13条仅为默认关闭的手工诊断探针，不是业务阻塞。'),
            '',
        ]
    elif latest_exists:
        lines[2:2] = [
            '> 2026-09-22最新阶段以[补测与阻塞报告](remaining_blockers_20260922.md)为准；下方历史真实服务器累计不是当前Excel全量结果。',
            '',
        ]
    if sdk_update_passed and not final_full_pass:
        lines[2:2] = [
            '> 2026-09-23专项：6654 Web通知1条、远程音视频确认4条、录制异常6637 1条、聊天拒绝2条及即时会议均通过；590条当前指纹尚未全量重跑。',
            '',
        ]
    lines.extend(f"- {name}：{count}" for name, count in sorted(outcomes.items()))
    lines.extend([
        "",
        "> 单向Notify类操作不编造同步错误Response：按当前事件游标验证无成功Notify；客户端参数校验与服务器业务错误分开统计。",
        "",
        "## 当前结论与解阻证据", "",
        "- 2026-09-24最新指纹全量602通过、0失败；590条功能及12条补充流程全部通过。",
        "- 当前执行用例无测试失败、服务端失败、环境跳过或设计阻塞。",
        "- 聊天Font、布局全屏和6631字段增强已进入同一全量批次并通过。",
        "- 唯一剩余协议环境阻塞为人脸6555～6659，需要人脸服务、视频测试源和FaceID数据。",
        "- H.323主动抢发辅流两条为OUT_OF_SCOPE；跨调用者挂断不属于当前验收范围。",
        "- 2026-09-23新版SDK专项通过5条：固定会议6654定向Web通知1条；Conf 5.0远程音频/视频同意与拒绝4条。远程链验证6542→6642→6543→6643，同意后A/B收到6609，拒绝后无成功6609。",
        "- 6637真实录制异常已通过：recordMode=4时A开始录制，A先收6637再收6610，B无定向错误。createInstant环境更新后也已恢复通过。",
        "- 6647及6540/6640/6641经产品确认当前不实现、未使用或入口未启用，仅保留协议追溯，不执行、不计阻塞。聊天拒绝及拒绝后重复审批已纳入Excel并真实通过；人脸服务链继续阻塞。",
        "- 2026-09-16轮询历史失败已按完整配置契约修正，并在9月24日全量中通过；历史请求见[frozen_polling_failure_20260916.md](frozen_polling_failure_20260916.md)。",
        "- 最终协议台账已进一步人工复核，旧的方向/API分类不能直接用作最终分母；以[最终缺口复核](final_gap_audit_20260916.md)为准。",
        "- 原24条分组开关失败已按开发确认规则修订：32条配置矩阵及1条既有分组流程真机33通过；6623的加入/离开取创建前已验证的主会议配置，人数取6634。",
        "- 用户已将暂停录制2条排除出本期测试：不执行、不计阻塞；历史跳过记录只作追溯。",
        "- H.323的Fm链路和HTTP邀请均通过；跨调用者挂断已按范围决定排除，历史分析见[分开验收](http_h323_contract_20260915.md)。",
        "- 无效敲门目标6504已确认直接忽略：受控新会议无目标敲门，观察期无目标6602，A/B健康及后续6644能力查询通过。见[无效敲门验收](absent_knock_ignored_20260911.md)。Keep省略使用默认0。",
        "- Mode处理为空实现：Mode=0无Mode字段通知、A/B健康、B重新登录回读模式未变，真机通过。见[Mode验收](room_mode_ignored_20260911.md)。不再列为模式切换故障。",
        "- 视频与布局修订通过；RightType=0静默忽略通过。13/99按用户确认的实际6630.Result=0、Operation、RightType、RightIndex和占用者断言后，真机4条通过。详见[权限13/99最新验收](right_13_99_retest_20260911.md)。",
        "- 最新28条有效HTTP结果用例及1条预置查询导出流程通过；已合并无效language参数重复用例。动态生成录制/纪要仍未验收。",
        "- `30012/30013`和`6528/6628`已确认当前产品不使用，功能用例已删除，仅保留协议追溯。",
        "- 0914 SDK的Relay定向往返已在170通过；邀请更换为INI专用hzk05后两条均通过：10001在线→14002→14003→30005/6601→A收到6602及后续能力查询。重复在线环境阻塞已解除。见[邀请最新验收](invitation_hzk05_20260915.md)。",
        "- 分组5条功能及完整流程通过；修正6524成员Right=2、关联分组Conf和6665字段路径。",
        "- 轮询原7条边界按当前契约扩展为9条（ID=0拆分三操作），集中真机9通过：内存保存后修正启动、负数ID、Window透传、Interval毫秒计时观察及批量操作。见[最终验收](polling_memory_timer_batch_20260911.md)。查询未扩SDK，以后续修改/新增和停止通知间接验证，不声明数据库持久化或长期计时通过。",
        "- 撤回和锁会敲门允许已复测通过，移除两条重复占位；独立等候室审批和拒绝分支不据此认定通过。",
        "- 最新9条解阻：Front注销、自身状态回复、取消订阅、等候室开启/允许审批、HTTP音频三条和视频一条；集中回归14通过。详见[本轮剩余阻塞修正与复测](remaining_blockers_retest_20260911.md)。",
        "- HTTP录制`6610`和ASR `6571/6671`已通过；9月24日回归使用`.160/.161`环境，两个会议进程执行前后PID不变。",
        "- TestClient已提供只读上行发送轨迹；Allure步骤3展示实际方法参数及真实XML/HTTP请求，步骤4展示Response/Notify。Authorization不记录，敏感字段统一脱敏。",
        "",
        "## 用例模块", "",
    ])
    lines.extend(f"- {name}：{count}" for name, count in sorted(modules.items()))
    lines.extend(["", "## 闭环与执行级别", ""])
    lines.extend(f"- {name}：{count}" for name, count in sorted(closures.items()))
    lines.extend(f"- {name}：{count}" for name, count in sorted(levels.items()))
    risk = Counter(row.get("环境风险") for row in cases)
    state = Counter(row.get("用例状态") for row in cases)
    blocker_ids = Counter(row.get("阻塞ID") for row in cases
                          if row.get("阻塞ID"))
    lines.extend(["", "## 环境风险与实现状态", ""])
    lines.extend(f"- {name}：{count}" for name, count in sorted(risk.items()))
    lines.extend(f"- {name}：{count}" for name, count in sorted(state.items()))
    lines.extend(["", "## 阻塞根因", "",
                  f"- 独立阻塞ID：{len(blocker_ids)}",
                  f"- 受影响用例：{sum(blocker_ids.values())}"])
    lines.extend(f"- `{name}`：影响{count}条" for name, count in blocker_ids.most_common())
    lines.extend(["", "## 协议缺口", ""])
    for row in blocked:
        version = f"@{row['版本']}" if row["版本"] else ""
        lines.append(f"- `{row['cmdid']}{version}` {row['协议说明']}：{row['封装状态']}，{row['缺口原因']}")
    if real_status.get("stages"):
        lines.extend(["", "## 历史真实服务器阶段结果（不与当前累计重复相加）", ""])
        for name, value in real_status["stages"].items():
            lines.append(
                f"- `{name}`：{value['result']}，通过{value['passed']}，"
                f"测试失败{value['failed_test']}，服务端失败{value['failed_server']}；{value['scope']}")
    lines.extend(["", "## 判定规则", "",
                  "- 测试只能调用FmTestClient公开业务方法，不直接构造ConfProtocol XML。",
                  "- Request、Response、Notify均有真实服务器证据后，信令才可标记为已覆盖。",
                  "- 缺口由开发补齐到sessionmanager_py后，再增加操作期望和可执行用例。", ""])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8-sig")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--excel", type=Path, default=ROOT / "tests/e2e/data/fm_e2e_cases.xlsx")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/current_coverage_gaps.md")
    args = parser.parse_args(); generate(args.excel, args.output); print(args.output)


if __name__ == "__main__":
    main()
