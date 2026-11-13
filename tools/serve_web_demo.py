"""Start the standalone Demo without signing in or joining any meeting."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
for folder in (ROOT,ROOT/'sessionmanager_py'):
    sys.path.insert(0,str(folder))
from meeting_test_runtime.web_client import DemoServer


def main():
    server=DemoServer()
    print('Web SDK测试Demo：'+server.url,flush=True)
    print('账号/会议由页面输入；Ctrl+C关闭本地服务。',flush=True)
    try:
        while server.thread.is_alive():
            server.thread.join(timeout=1)
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


if __name__=='__main__':
    main()
