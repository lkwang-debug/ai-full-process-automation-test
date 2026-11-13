"""Summarize an interrupted environment run without inventing test outcomes."""
import argparse
from collections import Counter
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'sessionmanager_py')]
import pytest
from tools.sanitize_allure_attachments import sanitize_folder, sanitize


class Inventory:
    def pytest_collection_finish(self,session):
        self.items=[{'nodeid':item.nodeid,'planned_skip_reasons':[str(m.kwargs.get('reason','')) for m in item.iter_markers('skip')]} for item in session.items]


def main():
    p=argparse.ArgumentParser();p.add_argument('--suite',choices=['fm','web'],required=True)
    p.add_argument('--results',type=Path,required=True);p.add_argument('--view',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--server-release',default='未核实')
    p.add_argument('--additional-results',type=Path,nargs='*',default=[],
                   help='互不重叠的后续阶段；保留每阶段来源，不覆盖历史结果')
    p.add_argument('--targeted',action='store_true',help='报告仅统计有阶段证据的定向选中范围，不补造全量未执行')
    args=p.parse_args()
    inventory=Inventory()
    with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
        code=pytest.main(['tests/e2e' if args.suite=='fm' else 'tests/web_e2e','--collect-only','-q'],plugins=[inventory])
    assert code==0
    manifest_name='acceptance_manifest.json' if args.suite=='fm' else 'web_key_acceptance_manifest.json'
    manifest=json.loads((args.results/manifest_name).read_text(encoding='utf-8'))
    if not args.targeted:assert len(inventory.items)==manifest['collected']
    grouped={}
    source_folders=[args.results,*args.additional_results]
    sources=[]
    for folder in source_folders:
        stage=json.loads((folder/manifest_name).read_text(encoding='utf-8'))
        sources.append({'folder':str(folder),'fingerprint':stage['fingerprint'],
                        'collected':stage['collected'],'exitstatus':stage['exitstatus']})
        nodes={row['nodeid'] for row in stage['results']}
        if nodes.intersection(grouped):raise ValueError('阶段用例重叠，不能覆盖失败或重复计数')
        for row in stage['results']:grouped.setdefault(row['nodeid'],[]).append(row)
    if args.targeted:
        assert set(grouped)<={item['nodeid'] for item in inventory.items}, '定向结果存在当前配置未收集的用例'
        inventory.items=[item for item in inventory.items if item['nodeid'] in grouped]
    actual=[]
    for item in inventory.items:
        rows=grouped.get(item['nodeid'],[])
        if not rows:status='NOT_RUN'
        elif any(r['outcome']=='failed' for r in rows):status='FAIL'
        elif any(r['outcome']=='skipped' for r in rows):status='SKIP'
        elif any(r['phase']=='call' and r['outcome']=='passed' for r in rows):status='PASS'
        else:status='INCOMPLETE'
        actual.append({**item,'actual_status':status,'phases':rows})
    recording_scope=manifest['environment'].get('recording_scope','configured')
    scope=('本轮录制启停、结果查询、下载/导出均按INI执行' if recording_scope=='configured'
           else '本轮录制启停及结果查询保留；下载/导出排除')
    verdict='TARGET_SCOPE_PASS' if args.targeted and all(item['actual_status']=='PASS' for item in actual) else 'NOT_COMPLETE'
    result={'suite':args.suite,'server_release_user_reported':args.server_release,'scope':('定向选中范围，未选中不计为本批次未执行；' if args.targeted else '')+scope+'；未执行项不是失败或跳过',
            'environment':manifest['environment'],'fingerprint':manifest['fingerprint'],'collected':len(inventory.items),
            'source_stages':sources,
            'counts':dict(Counter(x['actual_status'] for x in actual)),'inventory':actual,'final_acceptance':verdict}
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    args.view.mkdir(parents=True,exist_ok=False)
    for path in (path for folder in source_folders for path in folder.iterdir()):
        if not path.is_file():continue
        if (args.view/path.name).exists():
            if path.name.endswith(('-result.json','-container.json')) or '-attachment.' in path.name:
                raise ValueError('阶段证据文件重名，禁止覆盖')
            continue
        if path.name.endswith('-result.json'):
            row=json.loads(path.read_text(encoding='utf-8'))
            if args.suite=='web':
                parameter=next((v['value'] for v in row.get('parameters',[]) if v['name']=='web_full_case'),None)
                if parameter:
                    from meeting_test_runtime.web_full_cases import load_web_full_cases,MODULES
                    cases=load_web_full_cases(ROOT/'tests/web_e2e/data/web_e2e_cases.xlsx')
                    selected=next((c for c in cases if c.case_id in parameter),None)
                    if selected:
                        row['name']=selected.name+'（'+selected.combination+'，'+','.join(selected.identities)+'）'
                        row['labels']=[v for v in row.get('labels',[]) if v['name'] not in ('parentSuite','suite','subSuite')]
                        row['labels'] += [{'name':'parentSuite','value':'Web SDK全量协议闭环'},
                            {'name':'suite','value':MODULES[selected.scenario]}, {'name':'subSuite','value':selected.combination+' / '+selected.template}]
                        row['parameters']=[{'name':'用例ID','value':selected.case_id}]
            row=sanitize(row)
            (args.view/path.name).write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
        else:shutil.copy2(path,args.view/path.name)
    sanitize_folder(args.view)
    (args.view/'environment.properties').write_text(
        'server_release='+args.server_release+'\nfront_host='+manifest['environment']['front_host']+'\nrecording_scope='+recording_scope+'\n'
        +'collected='+str(len(inventory.items))+'\nnot_run='+str(result['counts'].get('NOT_RUN',0))+'\nfinal_acceptance='+verdict+'\n',encoding='utf-8')
    print(json.dumps({'suite':args.suite,'counts':result['counts'],'output':str(args.output),'view':str(args.view)},ensure_ascii=False))


if __name__=='__main__':main()
