"""Summarize one frozen batch, preserving failures and separately counting gates."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'sessionmanager_py')]
from meeting_test_runtime.web_full_cases import load_web_full_cases, MODULES
from tools.summarize_acceptance import node_verdict
from tools.sanitize_allure_attachments import sanitize_folder


def main():
    parser=argparse.ArgumentParser();parser.add_argument('results',type=Path);parser.add_argument('--view',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/web_phase2_execution_summary.json')
    args=parser.parse_args();folder=args.results
    manifest=json.loads((folder/'web_key_acceptance_manifest.json').read_text(encoding='utf-8'))
    cases=load_web_full_cases(ROOT/'tests/web_e2e/data/web_e2e_cases.xlsx');by_id={c.case_id:c for c in cases}
    grouped=defaultdict(list)
    for row in manifest['results']:grouped[row['nodeid']].append(row)
    totals=Counter(node_verdict(rows) for rows in grouped.values())
    full=Counter();gate=Counter();seen_ids=set();modules=defaultdict(Counter)
    for node,rows in grouped.items():
        identity=next((r.get('full_case_id') for r in rows if r.get('full_case_id')),None)
        status=node_verdict(rows)
        if identity:
            seen_ids.add(identity);full[status]+=1;modules[MODULES[by_id[identity].scenario]][status]+=1
        else:gate[status]+=1
    missing=sorted(set(by_id)-seen_ids)
    summary={'scope':'One controlled Web expansion + independent first-stage gates; no historical PASS union',
        'fingerprint':manifest['fingerprint'],'source_unchanged':manifest['source_unchanged'],'sdk_sha256':manifest['sdk_sha256'],
        'environment':manifest['environment'],'collected':manifest['collected'],'pytest_exitstatus':manifest['exitstatus'],
        'recorded':dict(totals),'full_cases':dict(full),'initial_gate':dict(gate),'modules':{k:dict(v) for k,v in modules.items()},
        'designed':len(cases),'missing_full_case_ids':missing,
        'contract_gaps':[{'case_id':c.case_id,'name':c.name,'reason':c.reason,'template':c.template} for c in cases if c.status!='IMPLEMENTED'],
        'final_acceptance':'NOT_COMPLETE' if missing or totals['failed'] or not manifest['source_unchanged'] or any(c.status!='IMPLEMENTED' for c in cases) else 'PASS'}
    args.output.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:summary[k] for k in ('collected','recorded','full_cases','initial_gate','final_acceptance','source_unchanged')},ensure_ascii=False))
    if args.view:
        if args.view.exists():raise FileExistsError('报告展示视图已存在，禁止混入旧结果')
        args.view.mkdir(parents=True)
        # Display-only copies: statuses, timestamps, UUID/historyId and raw original files are preserved.
        for source in folder.iterdir():
            if not source.is_file():continue
            if source.name.endswith('-result.json'):
                result=json.loads(source.read_text(encoding='utf-8'))
                identity=next((label['value'] for label in result.get('labels',[]) if label['name']=='as_id'),None)
                if not identity:
                    for parameter in result.get('parameters',[]):
                        match=re.search(r"case_id='([^']+)'",str(parameter.get('value','')))
                        if match:identity=match[1];break
                if identity in by_id:
                    case=by_id[identity]
                    result['name']=case.name+'（'+case.combination+'，'+case.template+'，'+','.join(case.identities)+'）'
                    labels=[x for x in result.get('labels',[]) if x['name'] not in {'parentSuite','suite','subSuite'}]
                    labels.extend([{'name':'parentSuite','value':'Web SDK第二阶段全量'},
                        {'name':'suite','value':MODULES[case.scenario]},{'name':'subSuite','value':case.combination}])
                    result['labels']=labels
                    result['parameters']=[x for x in result.get('parameters',[]) if x['name']!='web_full_case']
                    if not any(x['name']=='用例ID' for x in result['parameters']):result['parameters'].append({'name':'用例ID','value':case.case_id})
                else:
                    result['parameters']=[x for x in result.get('parameters',[]) if x['name']!='web_flow']
                (args.view/source.name).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
            else:shutil.copy2(source,args.view/source.name)
        sanitize_folder(args.view)
        (args.view/'display_provenance.json').write_text(json.dumps({'raw_results':str(folder.resolve()),
            'changes':'Chinese names/module labels and compact parameter display only; outcomes unchanged'},ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
