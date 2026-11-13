"""Read-only case-to-assertion review; no real-server calls or workbook edits."""
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'sessionmanager_py')]
from meeting_test_runtime.web_full_cases import load_web_full_cases, validate_case
from meeting_test_runtime.web_key_loader import load_web_key_flows
from meeting_test_runtime import web_key_scenarios as key_code
from meeting_test_runtime import web_full_steps as steps_code


def fingerprint():
    digest = hashlib.sha256()
    for base in (ROOT / 'meeting_test_runtime', ROOT / 'web_sdk_demo', ROOT / 'tests/web_e2e'):
        for path in sorted(base.rglob('*')):
            if path.is_file() and path.suffix in {'.py', '.js', '.html', '.json', '.xlsx'}:
                digest.update(str(path.relative_to(ROOT)).encode()); digest.update(path.read_bytes())
    digest.update((ROOT / 'hst-rtc-sdk-1.8.18.es.unmin.js').read_bytes())
    return digest.hexdigest()


class InMemoryRuntime:
    """Counterexamples only. This is never a TestClient or a server PASS result."""
    def __init__(self):
        self.variables = {'operator.room_user_id': 101, 'observer.room_user_id': 102, 'env.chair_password': 'test-only'}
        self.calls = []
        self.expectations = []
        self.event_timeout = 0.01
        self.profiles = {a: SimpleNamespace(account={'username': a}) for a in ('operator', 'observer')}
        self.clients = {a: SimpleNamespace(client_type='web', is_healthy=lambda: True,
            snapshot_events=lambda after: [{'cmdid': 6601, 'fields': {'RoomInfo': {'EnableForceOperateUserAV': 1}}},
                {'cmdid': 6602, 'fields': {'User': [{'UserID': 102, 'Name': 'observer', 'DisplayName': 'correct nickname'}]}}],
            wait_event=lambda *args: SimpleNamespace(fields={'Error': 12811})) for a in self.profiles}

    def mark_all_events(self): return {'operator': 0, 'observer': 0}
    def is_healthy(self): return True

    def call(self, alias, operation, params=None):
        self.calls.append((alias, operation, params))
        if operation == 'get_authenticated_profile':
            return {'userName': 'operator', 'displayName': 'correct operator nickname'}
        if operation == 'get_user_info':
            if params['user_id'] == 101:
                return {'UserID': 101, 'Name': 'operator', 'DisplayName': 'correct operator nickname'}
            return {'UserID': 999, 'Name': 'observer', 'DisplayName': 'WRONG nickname'}

    def resolve_variables(self, value):
        if isinstance(value, dict): return {k: self.resolve_variables(v) for k, v in value.items()}
        if isinstance(value, str) and value.startswith('${'): return self.variables[value[2:-1]]
        return value


def counterexamples(cases):
    findings = []
    runtime = InMemoryRuntime()
    def wait(rt, alias, marks, cmdid, fields):
        rt.expectations.append((alias, cmdid, fields))
        return SimpleNamespace(fields=fields)
    with patch.object(key_code, 'role'), patch.object(key_code, 'device', return_value=0), \
            patch.object(key_code, 'wait', side_effect=wait), patch.object(key_code, 'no_event'):
        key_code.execute_key_flow({'scenario': 'direct', 'params': {'kind': 'audio'}}, runtime)
    assert any(call[1] == 'mic_off' for call in runtime.calls)
    assert not any(fields.get('Audio.State') == 0 for _, _, fields in runtime.expectations)
    findings.append({'id': 'C01', 'counterexample': '远程开启后关闭命令无任何关闭通知',
                     'current_assertion_accepts': True, 'proof': 'mic_off被调用，但执行链没有等待State=0'})

    runtime = InMemoryRuntime()
    with patch.object(key_code, 'user', return_value={'DataState': 0, 'ManagerState': 0}), patch.object(key_code, 'no_event'):
        key_code.execute_key_flow({'scenario': 'record', 'params': {'role': 'attendee'}}, runtime)
    findings.append({'id': 'C02', 'counterexample': '普通成员录制收到存储错误12811，而非权限拒绝',
                     'current_assertion_accepts': True, 'proof': '代码仅判断Error非零；原批次实际为12801'})

    runtime = InMemoryRuntime()
    key_code.execute_key_flow({'scenario': 'info', 'params': {}}, runtime)
    findings.append({'id': 'C03', 'counterexample': '查询B返回UserID=999、错误但非空昵称，用户名恰好正确',
                     'current_assertion_accepts': True, 'proof': 'B的UserID及昵称未与独立来源精确比较'})

    case = next(c for c in cases if c.scenario == 'mixed_control')
    truncated = replace(case, steps=(case.steps[0],))
    validate_case(truncated)
    runtime = InMemoryRuntime()
    with patch.object(steps_code, 'establish_role'), patch(
            'meeting_test_runtime.web_full_scenarios.verify_remote_template'):
        steps_code.execute_steps(truncated, runtime)
    findings.append({'id': 'C04', 'counterexample': '混合会控Excel步骤只留下取得角色，没有控制任何目标',
                     'current_assertion_accepts': True, 'proof': 'validate_case允许截短，执行器只做剩余步骤和健康检查；当前Excel未截短'})
    return findings


def main():
    baseline=json.loads((ROOT/'reports/allure-web-phase2-full-20261013-r4/web_key_acceptance_manifest.json').read_text(encoding='utf-8'))
    if fingerprint()!=baseline['fingerprint']:
        raise RuntimeError('该工具仅重现2026-10-13原断言评审；代码已补强，请查历史评审JSON，不用新代码覆盖历史反例结论')
    full = load_web_full_cases(ROOT / 'tests/web_e2e/data/web_e2e_cases.xlsx')
    key = load_web_key_flows(ROOT / 'tests/web_e2e/data/web_key_flows.xlsx')
    rows = [{'id': c.case_id, 'name': c.name, 'scope': 'expansion', 'scenario': c.scenario,
             'params': c.params, 'steps': len(c.steps)} for c in full if c.status == 'IMPLEMENTED']
    rows.extend({'id': c['run_id'], 'name': c['name'], 'scope': 'initial_gate', 'scenario': c['scenario'],
                 'params': c['params'], 'steps': 0} for c in key)
    folder = ROOT / 'reports/allure-web-phase2-full-20261013-r4'
    manifest = json.loads((folder / 'web_key_acceptance_manifest.json').read_text(encoding='utf-8'))
    passed = {r.get('full_case_id') or r.get('run_id') for r in manifest['results'] if r['phase'] == 'call' and r['outcome'] == 'passed'}
    assert len(rows) == 254 and {r['id'] for r in rows} == passed
    rules = {
        'A01': lambda r: r['scenario'] == 'direct' or r['scenario'] == 'remote' and r['params']['accept'],
        'A02': lambda r: r['scenario'] == 'login_invalid' and r['params']['failure'] in {'account', 'password'},
        'A03': lambda r: r['scenario'] in {'guest_role', 'role'},
        'A04': lambda r: r['scenario'] == 'info',
        'A05': lambda r: r['scenario'] == 'whiteboard' and r['scope'] == 'initial_gate',
        'A06': lambda r: r['scenario'] == 'whiteboard_cycle',
        'A07': lambda r: r['scenario'] in {'role_then_record', 'manager_wrong_password', 'share_contention', 'chat_denied'} or
            r['scenario'] == 'record' and r['params']['role'] == 'attendee',
        'A08': lambda r: r['scenario'] == 'role_then_record' or r['scenario'] == 'record' and r['params']['role'] != 'attendee',
        'A09': lambda r: r['scenario'] in {'kick', 'close'},
        'A10': lambda r: r['scenario'] == 'lock',
        'A11': lambda r: r['scenario'] in {'remote_denied', 'mixed_control'},
        'A12': lambda r: r['scenario'] == 'chat',
        'A13': lambda r: bool(r['steps']),
        'A14': lambda r: r['scenario'] in {'guest_login', 'login', 'rejoin', 'info_membership'},
        'A15': lambda r: r['scenario'] == 'guest_login' and r['id'] in {
            c.case_id for c in full if c.identities[0] == 'guest' and c.identities[1] == 'account'},
    }
    affected = {name: [r for r in rows if rule(r)] for name, rule in rules.items()}
    priority_ids = {'A01', 'A02', 'A03', 'A04', 'A05', 'A06', 'A07', 'A09', 'A10', 'A11', 'A14', 'A15'}
    candidate_union = sorted({r['id'] for name, values in affected.items() if name in priority_ids for r in values})
    report = {'date': '2026-10-14', 'scope': '254 PASS Web cases from one frozen batch; FM 590 not reviewed here',
              'current_matches_batch': fingerprint() == manifest['fingerprint'],
              'historical_passes_unchanged': True, 'rules_are_code_review_findings_not_server_failures': True,
              'candidate_counts_overlap': True, 'priority_candidates_unique': len(candidate_union),
              'priority_candidate_ids': candidate_union, 'findings': {}, 'in_memory_counterexamples': counterexamples(full)}
    for name, values in affected.items():
        report['findings'][name] = {'count': len(values), 'scopes': dict(Counter(r['scope'] for r in values)),
                                   'cases': [{k: r[k] for k in ('id', 'name', 'scope', 'scenario')} for r in values]}
    output = ROOT / 'reports/web_assertion_review_20261014.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'reviewed': len(rows), 'fingerprint_matches': report['current_matches_batch'],
                     'counts': {k: v['count'] for k, v in report['findings'].items()},
                     'priority_candidates_unique': len(candidate_union), 'counterexamples': report['in_memory_counterexamples']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
