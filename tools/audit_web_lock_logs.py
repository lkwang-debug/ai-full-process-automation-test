"""Read-only correlation of failed lock cases, original messages and cluster logs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import sys

import paramiko

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.sanitize_allure_attachments import sanitize


def capture(folder):
    cases = []
    for path in folder.glob('*-result.json'):
        result = json.loads(path.read_text(encoding='utf-8'))
        params = result.get('parameters', [])
        ids = [p['value'].strip("'") for p in params if p['name'] == '用例ID']
        if result.get('status') != 'failed' or not ids or 'LOCK' not in ids[0]:
            continue
        rows = []
        def scan(attachments):
            for a in attachments:
                if a.get('type') != 'application/json':
                    continue
                value = json.loads((folder / a['source']).read_text(encoding='utf-8'))
                def walk(v, alias=''):
                    if isinstance(v, dict):
                        if v.get('cmdid') in (6510, 6610) and 'Lock' in v.get('fields', {}):
                            rows.append({'alias':alias, **v})
                        else:
                            for k, child in v.items():
                                walk(child, k if k in ('operator','observer','witness') else alias)
                    elif isinstance(v,list):
                        for child in v:walk(child,alias)
                walk(value)
        scan(result.get('attachments', []))
        # Root attachments already include complete traffic, so nested steps need not be duplicated.
        cases.append({'case_id':ids[0], 'name':result['name'], 'start_ms':result['start'],
                      'stop_ms':result['stop'], 'traffic':sanitize(rows)})
    return cases


def inspect(host, date, cases):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, port=22, username='root', password=os.environ['WEB_LOG_SSH_PASSWORD'],
                timeout=10, auth_timeout=10, banner_timeout=10)
    zones = timezone(timedelta(hours=8))
    windows = [(c['case_id'], datetime.fromtimestamp(c['start_ms']/1000,zones).strftime('%H:%M:%S'),
                datetime.fromtimestamp((c['stop_ms']+3000)/1000,zones).strftime('%H:%M:%S')) for c in cases]
    found = []
    files = []
    try:
        with ssh.open_sftp() as sftp:
            for base,prefix in (('/hst/meeting/FMServer/log','FMRoomSrv-'),
                                ('/hst/meeting/FMWebProxy/log','FMWebProxy-')):
                directory=base+'/'+date
                try:entries=sftp.listdir_attr(directory)
                except OSError:continue
                for entry in sorted(entries,key=lambda e:e.filename):
                    if not entry.filename.startswith(prefix) or not entry.filename.endswith('.log'):continue
                    path=directory+'/'+entry.filename
                    files.append({'path':path,'size':entry.st_size})
                    with sftp.open(path,'rb') as stream:
                        stream.prefetch(entry.st_size)
                        content=stream.read().decode('utf-8','replace')
                    for number,line in enumerate(content.splitlines(),1):
                        clock=re.search(r'\b(\d{2}:\d{2}:\d{2})(?:\.\d+)?\b',line)
                        if not clock:continue
                        matched=[case for case,start,end in windows if start<=clock[1]<=end]
                        if not matched:continue
                        if not ((re.search(r'Lock',line,re.I) and re.search(r'6510|6610|BroadcastData|SendMsgToUser',line))
                                or (prefix=='FMWebProxy-' and '6610' in line)
                                or re.search(r'OnSetRoomLock|OnSetLock',line)):continue
                        found.append({'cases':matched,'path':path,'line':number,'text':sanitize(line)})
    finally:ssh.close()
    return {'host':host,'files':files,'rows':found}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('results',type=Path)
    p.add_argument('--date',required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    cases=capture(args.results)
    assert len(cases)==3, 'Expected the three original failed lock cases'
    with ThreadPoolExecutor(max_workers=2) as pool:
        servers=list(pool.map(lambda host:inspect(host,args.date,cases),('192.168.7.185','192.168.7.186')))
    report={'date':args.date,'read_only':True,'cases':cases,'servers':servers}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(args.output),'cases':len(cases),
          'servers':[{'host':s['host'],'files':len(s['files']),'matched_lines':len(s['rows'])} for s in servers]}))
    for s in servers:
        for row in s['rows'][:24]:
            print(json.dumps({'host':s['host'],**row},ensure_ascii=False))


if __name__=='__main__':main()
