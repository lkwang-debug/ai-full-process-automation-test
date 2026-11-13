"""真实服务器锁会敲门闭环：6617 -> 6604 -> allow -> 6601/6602。"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fm_client import conf as C  # noqa: E402
from fm_client.testing import FmTestClient, connect_client, load_config  # noqa: E402


def _close(client: Optional[FmTestClient]) -> None:
    if client is None:
        return
    try:
        client.conf_logout()
    except Exception:
        pass
    try:
        client.front_logout()
    except Exception:
        pass
    client.close()


def _require_event(client: FmTestClient, mark: int, cmdid: int, timeout: float,
                   match: Optional[dict] = None):
    event = client.events.wait_event(
        after=mark,
        source="conf",
        cmdid=cmdid,
        kind="notify",
        match=match,
        timeout=timeout,
    )
    if event is None:
        recent = [
            (item.cmdid, item.kind, item.fields)
            for item in client.events.snapshot_received(after=mark)
        ]
        raise AssertionError(
            f"event timeout: cmdid={cmdid}, match={match or {}}, recent={recent}")
    return event


def run(config: dict, timeout: float) -> None:
    operator = None
    waiting = None
    login_result = {}
    login_error = []
    try:
        operator = connect_client(config, account_index=0)
        waiting = connect_client(config, account_index=1, conf_login=False)
        time.sleep(float(config.get("initialization_wait", 0)))

        # 先恢复到未锁定，再制造一次确定的 0 -> 1 状态变化。
        operator.unlock_room()
        time.sleep(0.2)
        lock_mark = operator.events.mark()
        operator.lock_room()
        _require_event(operator, lock_mark, C.CONFSTATECHANGED, timeout, {"Lock": 1})

        knock_mark = operator.events.mark()
        wait_mark = waiting.events.mark()

        def login_waiting_client() -> None:
            try:
                login_result.update(waiting.conf_login(
                    user_name=config["accounts"][1]["username"], timeout=timeout))
            except Exception as exc:  # noqa: BLE001
                login_error.append(exc)

        thread = threading.Thread(target=login_waiting_client, daemon=True)
        thread.start()

        wait_event = _require_event(waiting, wait_mark, C.WAITOPENDOOR, timeout)
        knock_event = _require_event(
            operator,
            knock_mark,
            C.KNOCKNOTIFY,
            timeout,
            {"UserID": waiting.room_user_id_int},
        )
        operator.allow_knock(waiting.room_user_id_int, allow=1)

        thread.join(timeout + 1)
        if thread.is_alive():
            raise TimeoutError("waiting client did not complete 6601 login after allow_knock")
        if login_error:
            raise login_error[0]
        if str(login_result.get("Result", "0")) != "0":
            raise AssertionError(f"waiting client login failed: {login_result}")

        print(
            "[PASS] locked_join "
            f"waiting={wait_event.fields} knock={knock_event.fields} "
            f"login_result={login_result.get('Result', '0')}"
        )
    finally:
        if operator is not None:
            try:
                operator.unlock_room()
                time.sleep(0.2)
            except Exception:
                pass
        _close(waiting)
        _close(operator)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    try:
        run(load_config(args.config), args.timeout)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
