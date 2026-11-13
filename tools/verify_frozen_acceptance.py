"""固定版本真实回归门禁；不把功能全绿当作产品协议全部验收。"""
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'sessionmanager_py')]
from meeting_test_runtime.excel_loader import ExcelCaseLoader
from summarize_acceptance import node_verdict


def fingerprint():
    digest = hashlib.sha256()
    for folder in ('meeting_test_runtime', 'tests/e2e', 'sessionmanager_py/fm_client', 'sessionmanager_py/framecore_session'):
        for path in sorted((ROOT / folder).rglob('*.py')):
            if path.name.startswith('._'):
                continue
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    digest.update((ROOT / 'tests/e2e/data/fm_e2e_cases.xlsx').read_bytes())
    return digest.hexdigest()


def main():
    folder = ROOT / 'reports/allure-final-frozen-20260916'
    manifest = json.loads((folder / 'acceptance_manifest.json').read_text(encoding='utf-8'))
    loader = ExcelCaseLoader(ROOT / 'tests/e2e/data/fm_e2e_cases.xlsx')
    cases = loader.load_cases(include_disabled=True)
    expected_ids = {c.case_id for c in cases if c.enabled}
    flows = loader.load_dependency_flows()
    expected_flows = {f.flow_id for f in flows if f.status.value == 'IMPLEMENTED'}
    grouped = defaultdict(list)
    for phase in manifest['results']:
        grouped[phase['nodeid']].append(phase)
    statuses = Counter(node_verdict(phases) for phases in grouped.values())
    actual_ids = {p['case_id'] for p in manifest['results'] if p.get('case_id')}
    actual_flows = {node.split('[')[-1].rstrip(']') for node in grouped if 'test_dependency_flow[' in node}
    checks = {
        'fingerprint_unchanged': fingerprint() == manifest['fingerprint'],
        'all_enabled_case_ids_present': actual_ids == expected_ids,
        'all_implemented_flow_ids_present': actual_flows == expected_flows,
        'all_calls_and_cleanup_passed': statuses == Counter({'passed': len(expected_ids) + len(expected_flows)}),
        'pytest_exit_zero': manifest['exitstatus'] == 0,
        'destructive_included': manifest['environment']['run_destructive_cases'] is True,
    }
    gate = 'PASS' if all(checks.values()) else 'FAIL'
    result = {'functional_gate': gate, 'checks': checks, 'counts': dict(statuses),
              'fingerprint': manifest['fingerprint'], 'case_count': len(expected_ids),
              'implemented_flow_count': len(expected_flows),
              'product_final_acceptance': 'NOT_ACCEPTED_PENDING_PROTOCOL_GAPS_AND_H323_DEFECT',
              'excluded_flows': [{'id': f.flow_id, 'status': f.status.value} for f in flows if f.status.value != 'IMPLEMENTED'],
              'notes': ['已排除暂停录制；排除依据由用户确认。',
                        '当前指纹覆盖SDK业务/传输源码、外部运行框架、e2e代码和Excel；不含远端服务器二进制版本。',
                        'root ConfProtocol字节摘要另外保存，不冒充服务端部署版本证明。']}
    result['root_protocol_sha256'] = hashlib.sha256((ROOT / 'ConfProtocol.xml').read_bytes()).hexdigest()
    (ROOT / 'reports/frozen_acceptance_20260916.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if gate != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
