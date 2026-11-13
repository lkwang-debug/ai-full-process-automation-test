"""Run a full suite while recording the user's per-run H.323 exclusion."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'sessionmanager_py')]
import pytest

H323_OPERATIONS = {
    'conf_invite_call', 'invite_terminals', 'invite_terminals_http',
    'conf_hangup', 'conf_query_call_list',
}


class UserExclusions:
    def __init__(self, results):
        self.results = results

    def pytest_collection_modifyitems(self, items):
        excluded = []
        for item in items:
            callspec = getattr(item, 'callspec', None)
            case = callspec.params.get('fm_excel_case') if callspec else None
            if (getattr(case, 'operation', None) in H323_OPERATIONS
                    or 'h323' in item.nodeid.lower()):
                item.add_marker(pytest.mark.skip(reason='USER_EXCLUDED: 本轮不执行H.323'))
                excluded.append({'nodeid': item.nodeid,
                                 'operation': getattr(case, 'operation', None)})
        self.results.mkdir(parents=True, exist_ok=True)
        (self.results / 'user_exclusions.json').write_text(json.dumps({
            'reason': '本轮用户要求排除H.323，不改变其他用例及断言',
            'excluded': excluded,
        }, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', choices=('fm', 'web'), required=True)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--junit', type=Path, required=True)
    args = parser.parse_args()
    if args.results.exists():
        raise FileExistsError(f'Refusing to mix existing results: {args.results}')
    os.environ['FM_RUN_DESTRUCTIVE_CASES'] = '1'
    os.environ['FM_RECORDING_SCOPE'] = 'configured'
    os.environ['WEB_RUN_REAL'] = '1'
    code = pytest.main([
        'tests/e2e' if args.suite == 'fm' else 'tests/web_e2e', '-q', '-ra',
        f'--alluredir={args.results}', f'--junitxml={args.junit}',
    ], plugins=[UserExclusions(args.results)])
    raise SystemExit(code)


if __name__ == '__main__':
    main()
