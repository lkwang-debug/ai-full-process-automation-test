"""Remove nested credentials from this stage's diagnostic artifacts; outcomes stay intact."""
from pathlib import Path
import sys
import json

ROOT=Path(__file__).resolve().parents[1]
for folder in (ROOT,ROOT/'sessionmanager_py'):
    sys.path.insert(0,str(folder))
from meeting_test_runtime.redaction import redact


def main():
    reports=ROOT/'reports'
    files=[reports/'web_sdk_discovery_20261009.json']
    for folder in reports.glob('allure-web-*20261009*'):
        if folder.is_dir(): files.extend(folder.glob('*.json'))
    changed=0
    for path in files:
        if not path.exists(): continue
        value=json.loads(path.read_text(encoding='utf-8'))
        cleaned=redact(value)
        if cleaned!=value:
            path.write_text(json.dumps(cleaned,ensure_ascii=False,indent=2),encoding='utf-8')
            changed+=1
    print(f'仅脱敏本阶段JSON报告：{changed}个文件；不修改业务结果。')


if __name__=='__main__':
    main()
