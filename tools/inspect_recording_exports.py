"""Read-only public API inspection; save file metadata, not full meeting content."""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'sessionmanager_py')]
from meeting_test_runtime.config import TestEnvironmentConfig
from meeting_test_runtime.http_driver import WebApiServiceDriver


def main():
    p=argparse.ArgumentParser();p.add_argument('--group-id',required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();cfg=TestEnvironmentConfig()
    driver=WebApiServiceDriver({'base_url':cfg.get('http','base_url'),'app_key':cfg.get('http','app_key'),
        'app_secret':cfg.get('http','app_secret'),'verify_tls':cfg.getbool('http','verify_tls',fallback=False)})
    report={'scope':'Read-only selected recording exports; no conference controls or data changes','group_id':args.group_id,'files':[]}
    try:
        api=driver.native_client;data=api.get_record_summary(args.group_id)['data']
        report['summary']={k:data.get(k) for k in ('groupId','roomId','roomName','startTime')}
        listed=api.list_room_record_summaries(data['roomId'],page_number=1,page_size=100)
        report['matching_list_rows']=[{k:r.get(k) for k in ('groupId','roomId','roomName','startTime')}
            for r in listed['data']['records'] if r.get('groupId')==args.group_id]
        for operation,params in [('download_meeting_summary',{}),('export_meeting_summary',{}),
                                 ('export_meeting_summary',{'type':2}),('export_transcript',{})]:
            body=getattr(api,operation)(args.group_id,**params);pdf=body.startswith(b'%PDF-')
            if pdf:
                from pypdf import PdfReader
                reader=PdfReader(BytesIO(body));text='\n'.join(page.extract_text() or '' for page in reader.pages)
            else:text=body.decode('utf-8-sig')
            report['files'].append({'operation':operation,'params':params,'size_bytes':len(body),
                'sha256':hashlib.sha256(body).hexdigest(),'format':'PDF' if pdf else 'UTF-8 text',
                'pdf_pages':len(reader.pages) if pdf else None,'title_present':bool(data.get('roomName') and data['roomName'] in text),
                'nonempty_text':bool(text.strip()),'prefix':text[:65]})
    finally:driver.close()
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
