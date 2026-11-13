"""Run only unexecuted FM items independent of a failed group dependency."""
import argparse
from contextlib import redirect_stdout,redirect_stderr
import io
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'sessionmanager_py')]
import pytest


class Selection:
    def __init__(self,completed,include_inactive=False):
        self.completed=completed;self.selected=[];self.dependent=[];self.include_inactive=include_inactive
    def pytest_collection_finish(self,session):
        for item in session.items:
            if item.nodeid in self.completed:continue
            case=getattr(getattr(item,'callspec',None),'params',{}).get('fm_excel_case')
            safe=self.include_inactive and case is not None and (
                (case.operation=='create_group' and case.params.get('rooms') and all(
                    room.get('State')==0 for room in case.params['rooms'])) or
                (case.operation=='allow_knock' and case.params.get('user_id')==999999999))
            if case and ('分组' in case.module or 'GROUP' in case.precondition or case.operation in {
                    'create_group','enter_group','end_groups','bring_to_main','modify_group_config','change_group_state'}) and not safe:
                self.dependent.append({'nodeid':item.nodeid,'reason':'NOT_RUN_DEPENDENCY: 本阶段6523连续超时后停止，未继续分组相关探测'})
            else:self.selected.append(item.nodeid)


def main():
    p=argparse.ArgumentParser();p.add_argument('--prior',type=Path,nargs='+',required=True)
    p.add_argument('--results',type=Path,required=True);p.add_argument('--junit',type=Path,required=True)
    p.add_argument('--selection',type=Path,required=True);args=p.parse_args()
    completed=set()
    for path in args.prior:
        prior=json.loads(path.read_text(encoding='utf-8'))
        completed.update(x['nodeid'] for x in prior['results'])
    selection=Selection(completed,include_inactive=len(args.prior)>1)
    with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
        code=pytest.main(['tests/e2e','--collect-only','-q'],plugins=[selection])
    assert code==0
    args.selection.write_text(json.dumps({'prior':[str(path) for path in args.prior],'already_executed':len(completed),
        'selected':selection.selected,'not_run_dependencies':selection.dependent},ensure_ascii=False,indent=2),encoding='utf-8')
    print('REMAINDER',len(selection.selected),'DEPENDENCY_NOT_RUN',len(selection.dependent),flush=True)
    if not selection.selected:return
    raise SystemExit(pytest.main([*selection.selected,'-q','--tb=line',
        '--alluredir='+str(args.results),'--junitxml='+str(args.junit)]))


if __name__=='__main__':main()
