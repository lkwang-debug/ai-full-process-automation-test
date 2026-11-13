"""Read-only SSH diagnostics for missing initialization messages in a frozen batch."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys

import paramiko

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.sanitize_allure_attachments import sanitize


def safe_line(line):
    line=sanitize(line)
    line=re.sub(r'(?i)\b(Token|Password|RoomPassword|Authorization|Secret)\s*:\s*[^,\s]+',r'\1:***REDACTED***',line)
    # Log prefixes contain JSON/XML fragments, not a complete JSON document.
    return re.sub(r'(?i)(["\\]*(?:password|roomPassword|token|svrAuthCode|secret|authorization)["\\]*\s*:\s*["\\]*)([^",\\]*)(["\\]+)',
                  r'\1***REDACTED***\3',line)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--host',required=True)
    p.add_argument('--date',required=True)
    p.add_argument('--results',type=Path,nargs='+',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--max-cases',type=int,default=0)
    p.add_argument('--quiet',action='store_true',help='只输出摘要，完整脱敏日志写入证据文件')
    args=p.parse_args()
    tz=timezone(timedelta(hours=8));cases=[]
    for folder in args.results:
        for path in folder.glob('*-result.json'):
            result=json.loads(path.read_text(encoding='utf-8'))
            if result['status'] not in ('failed','broken'):continue
            cases.append({'folder':str(folder),'uuid':result['uuid'],'name':result['name'],
                'start':datetime.fromtimestamp((result['start']-30000)/1000,tz).strftime('%H:%M:%S'),
                'stop':datetime.fromtimestamp((result['stop']+30000)/1000,tz).strftime('%H:%M:%S')})
    assert cases,'No failed cases to correlate'
    cases.sort(key=lambda c:c['start'])
    if args.max_cases:cases=cases[:args.max_cases]
    ssh=paramiko.SSHClient();ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(args.host,port=22,username='root',password=os.environ['WEB_LOG_SSH_PASSWORD'],timeout=10,auth_timeout=10)
    report={'host':args.host,'date':args.date,'read_only':True,'cases':cases,'files':[],'rows':[],'global_feature_mentions':[]}
    try:
        with ssh.open_sftp() as sftp:
            for base,prefixes in (('/hst/meeting/FMServer/log',('FMRoomSrv-','FMAsyncDBSrv-','FMFront-')),
                                  ('/hst/meeting/FMWebProxy/log',('FMWebProxy-',))):
                directory=base+'/'+args.date
                try:entries=sftp.listdir_attr(directory)
                except FileNotFoundError:
                    report.setdefault('missing_directories',[]).append(directory)
                    continue
                for e in sorted(entries,key=lambda e:e.filename):
                    if not e.filename.startswith(prefixes) or not e.filename.endswith('.log'):continue
                    path=directory+'/'+e.filename;report['files'].append({'path':path,'size':e.st_size})
                    with sftp.open(path,'rb') as stream:
                        stream.prefetch(e.st_size);lines=stream.read().decode('utf-8','replace').splitlines()
                    for n,line in enumerate(lines,1):
                        if re.search(r'<cmd id="6639"|"CmdID"\s*:\s*6639\b|ProductFeature|Get.*Feature|Load.*Feature|On.*Feature|license|License',line):
                            report['global_feature_mentions'].append({'path':path,'line':n,'text':safe_line(line)})
                        clock=re.search(r'\b(\d{2}:\d{2}:\d{2})(?:\.\d+)?\b',line)
                        if not clock:continue
                        relevant=[c['uuid'] for c in cases if c['start']<=clock[1]<=c['stop']]
                        if relevant:
                            report['rows'].append({'cases':relevant,'path':path,'line':n,'text':safe_line(line)})
    finally:ssh.close()
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'cases':cases,'files':report['files'],'window_lines':len(report['rows']),
        'feature_mentions':len(report['global_feature_mentions']),'output':str(args.output)},ensure_ascii=False))
    if args.quiet:return
    select=re.compile(r'6501|6517|6631|6639|OnLogin|OnInit|ProductFeature|error|Error|fail|Fail|Warn|\b[EW]\b|ClientVer')
    rows=[r for r in report['rows'] if select.search(r['text'])]
    for r in rows[:45]:print(json.dumps(r,ensure_ascii=False))
    for r in report['global_feature_mentions'][:15]:print(json.dumps({'global_feature':r},ensure_ascii=False))


if __name__=='__main__':main()
