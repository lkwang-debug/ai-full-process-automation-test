"""Attribute frozen pytest phase durations without changing results or assertions."""
import argparse
from collections import defaultdict
import json
from pathlib import Path


def summarize(folder):
    name='web_key_acceptance_manifest.json' if (folder/'web_key_acceptance_manifest.json').exists() else 'acceptance_manifest.json'
    manifest=json.loads((folder/name).read_text(encoding='utf-8'))
    phases=defaultdict(float);nodes=defaultdict(float)
    for row in manifest['results']:
        phases[row['phase']]+=row['duration'];nodes[row['nodeid']]+=row['duration']
    names={};root_steps=defaultdict(float)
    for file in folder.glob('*-result.json'):
        result=json.loads(file.read_text(encoding='utf-8'))
        for param in result.get('parameters',[]):
            if param['name']=='用例ID':names[param['value'].strip("'")]=result['name']
        for step in result.get('steps',[]):
            root_steps[step['name'].split('：')[0]]+=(step.get('stop',0)-step.get('start',0))/1000
    mixed=[(n,t) for n,t in nodes.items() if 'MIXED_CONTROL-' in n]
    return {'folder':str(folder),'phase_seconds':{k:round(v,3) for k,v in phases.items()},
        'phase_total_seconds':round(sum(phases.values()),3),
        'root_allure_steps_seconds':{k:round(v,3) for k,v in root_steps.items()},
        'mixed_control_count':len(mixed),'mixed_control_total_seconds':round(sum(t for _,t in mixed),3),
        'slowest':[{'nodeid':n,'seconds':round(t,3)} for n,t in sorted(nodes.items(),key=lambda item:-item[1])[:10]]}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('folders',type=Path,nargs='+')
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result={'scope':'Frozen executed phases only; top-level Allure steps are supplementary, not added again to pytest phases',
            'runs':[summarize(folder) for folder in args.folders]}
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in result['runs']:
        print(json.dumps({k:row[k] for k in ('folder','phase_seconds','phase_total_seconds','mixed_control_count','mixed_control_total_seconds')},ensure_ascii=False))


if __name__=='__main__':main()
