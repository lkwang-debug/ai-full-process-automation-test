"""汇总验收manifest，按版本分组，不把不同版本历史PASS拼成一次全量。"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def node_verdict(phases):
    if any(p['outcome']=='failed' for p in phases):return 'failed'
    if any(p['outcome']=='skipped' for p in phases):return 'skipped'
    if (any(p['phase']=='call' and p['outcome']=='passed' for p in phases)
            and any(p['phase']=='teardown' and p['outcome']=='passed' for p in phases)):
        return 'passed'
    return 'not_completed'


def summarize(paths):
    groups=defaultdict(list)
    for path in paths:
        report=json.loads(path.read_text(encoding='utf-8'))
        by_node=defaultdict(list)
        for item in report['results']:by_node[item['nodeid']].append(item)
        cases=[{'nodeid':node,'case_id':next((p.get('case_id') for p in phases if p.get('case_id')),None),
                'status':node_verdict(phases)} for node,phases in by_node.items()]
        groups[report['fingerprint']].append({'manifest':str(path),'exitstatus':report['exitstatus'],
            'collected':report['collected'],'environment':report.get('environment',{}),
            'counts':dict(Counter(c['status'] for c in cases)),'cases':cases})
    return {'scope':'按SDK/代码/Excel指纹分组；批次有重叠，不直接相加', 'versions':dict(groups)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--reports',type=Path,default=Path(__file__).resolve().parents[1]/'reports')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=summarize(sorted(args.reports.glob('allure-acceptance-*/acceptance_manifest.json')))
    if args.output:
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    for fingerprint,runs in result['versions'].items():
        for run in runs:print(fingerprint[:12],Path(run['manifest']).parent.name,run['counts'])


if __name__=='__main__':main()
