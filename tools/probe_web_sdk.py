"""Read-only Web discovery probe. Does not authenticate a user or create a room."""
from pathlib import Path
import sys
import json

ROOT=Path(__file__).resolve().parents[1]
for folder in (ROOT,ROOT/'sessionmanager_py'):
    sys.path.insert(0,str(folder))

from meeting_test_runtime.config import TestEnvironmentConfig
from meeting_test_runtime.models import ClientProfile
from meeting_test_runtime.web_client import WebTestClient
from meeting_test_runtime.allure_reporting import to_report_data


def main():
    cfg=TestEnvironmentConfig()
    profile=ClientProfile('WEB_DISCOVERY','web',{}, {
        'ces_addr':cfg.get('web_sdk','ces_addr') or cfg.get('http','base_url'),
        'headless':True,'browser_channel':cfg.get('web_sdk','browser_channel',fallback='chrome'),
        'ignore_https_errors':cfg.getbool('web_sdk','ignore_https_errors',fallback=False)})
    client=WebTestClient(profile)
    error=None
    try:
        client.call('init',{'ces_addr':profile.endpoint['ces_addr'],'timeout_ms':20000})
    except Exception as exc:
        error=str(exc)
    report=to_report_data({'purpose':'只读服务发现，未登录用户/未建会',
        'entry':profile.endpoint['ces_addr'],'error':error,'diagnostics':client.diagnostics()})
    try: client.close()
    except Exception as exc: report['cleanup_error']=str(exc)
    destination=ROOT/'reports/web_sdk_discovery_20261009.json'
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'entry':report['entry'],'init_error':error,
        'events':len(report['diagnostics']['events']),'report':str(destination)},ensure_ascii=False))
    return 1 if error else 0


if __name__=='__main__':
    raise SystemExit(main())
