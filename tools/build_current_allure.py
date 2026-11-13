"""按已核对的业务ID生成最新证据视图；原始Allure批次保持不变。"""
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'sessionmanager_py')]
from meeting_test_runtime.excel_loader import ExcelCaseLoader


def main():
    reports = ROOT / 'reports'
    runs = ['allure-acceptance-full-20260916', 'allure-acceptance-full-20260916-followup',
            'allure-group-config-20260916']
    cases = ExcelCaseLoader(ROOT / 'tests/e2e/data/fm_e2e_cases.xlsx').load_cases(include_disabled=True)
    allowed = {c.case_id for c in cases}
    latest = {}
    for run in runs:
        for path in (reports / run).glob('*-result.json'):
            result = json.loads(path.read_text(encoding='utf-8'))
            identity = next((label['value'] for label in result['labels'] if label['name'] == 'as_id'), None)
            if identity is None:
                # collection阶段跳过的用例尚未执行configure_case，只有pytest参数。
                value = next(p['value'] for p in result.get('parameters', []) if p['name'] == 'fm_excel_case')
                match = re.search(r"case_id='([^']+)'", value)
                identity = match.group(1) if match else value.strip("'\"")
            if identity in allowed or identity.startswith('FLOW-'):
                previous = latest.get(identity)
                if previous is None or result['stop'] > previous[1]['stop']:
                    latest[identity] = path, result
    assert allowed <= latest.keys(), '当前Excel必须都有结果'
    target = reports / 'allure-current-scope-20260916'
    target.mkdir(exist_ok=False)  # 避免混入上一次视图的旧结果。
    uuids = {result['uuid'] for _, result in latest.values()}
    for path, _ in latest.values():
        shutil.copy2(path, target / path.name)
    for run in runs:
        for path in (reports / run).glob('*'):
            if 'attachment' in path.name:
                shutil.copy2(path, target / path.name)
            elif path.name.endswith('-container.json'):
                container = json.loads(path.read_text(encoding='utf-8'))
                container['children'] = [child for child in container.get('children', []) if child in uuids]
                if container['children']:
                    (target / path.name).write_text(json.dumps(container, ensure_ascii=False), encoding='utf-8')
    manifest = {'scope': '当前用例ID最新证据；全量加定向补测，不是单次全量；暂停两条经用户批准排除',
                'source_runs': runs,
                'selected': {key: str(path.relative_to(reports)) for key, (path, _) in latest.items()}}
    (target / 'current_scope_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print('当前范围结果数:', len(latest))


if __name__ == '__main__':
    main()
