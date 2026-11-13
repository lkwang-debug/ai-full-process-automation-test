"""Validate meeting-control APIs with two simulated clients.

Offline mode:
    python3 examples/fm_meeting_control_demo.py --mock

Real-server mode:
    python3 examples/fm_meeting_control_demo.py \
        --config examples/multi_client.json

HTTP user invitation and terminal calls are skipped in real-server mode unless
their explicit options are provided. These operations have external effects.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fm_client import conf as C  # noqa: E402
from fm_client.mockserver import MockFmServer  # noqa: E402
from fm_client.testing import FmTestClient, connect_pair, load_config  # noqa: E402


class _MockHttpApi:
    def __init__(self) -> None:
        self.calls = []

    def invite_users(self, room_id, user_ids, **kwargs):
        self.calls.append((room_id, list(user_ids), dict(kwargs)))
        return {"code": "0", "data": {"errors": []}}


def _wait_notify(
    client: FmTestClient,
    *,
    after: int,
    cmdid: int,
    match: dict,
    timeout: float,
):
    event = client.events.wait_event(
        after=after,
        source="conf",
        cmdid=cmdid,
        kind="notify",
        match=match,
        timeout=timeout,
    )
    if event is None:
        recent = [
            {"event_id": item.event_id, "cmdid": item.cmdid,
             "kind": item.kind, "fields": item.fields}
            for item in client.events.snapshot_received(after=after)[-10:]
        ]
        raise AssertionError(
            f"notify timeout: cmdid={cmdid}, match={match}, recent={recent}")
    return event


def _send_and_expect(
    observer: FmTestClient,
    action: Callable[[], object],
    *,
    cmdid: int,
    match: dict,
    timeout: float,
):
    mark = observer.events.mark()
    result = action()
    event = _wait_notify(
        observer, after=mark, cmdid=cmdid, match=match, timeout=timeout)
    return result, event


def _close_client(client: Optional[FmTestClient]) -> None:
    if client is None:
        return
    try:
        client.conf_logout()
    except Exception as exc:
        print(f"[WARN] conf_logout failed: {exc}")
    try:
        client.front_logout()
    except Exception as exc:
        print(f"[WARN] front_logout failed: {exc}")
    client.close()


def _build_mock_pair() -> Tuple[MockFmServer, FmTestClient, FmTestClient]:
    server = MockFmServer()
    config = {
        "host": server.host,
        "port": server.port,
        "room_id": 100001,
        "room_node_id": "1",
        "room_app_id": 11,
        "request_timeout": 5.0,
        "accounts": [
            {"username": "control-operator", "password": "pwd"},
            {"username": "control-observer", "password": "pwd"},
        ],
    }
    operator, observer = connect_pair(config)
    return server, operator, observer


def run_checks(
    operator: FmTestClient,
    observer: FmTestClient,
    *,
    timeout: float,
    http_config: Optional[str],
    invite_user_id: Optional[int],
    terminal_sns: List[str],
    mock_mode: bool,
    audio_share_id: int = 1,
    video_share_id: int = 1,
) -> Tuple[List[str], List[str], List[str]]:
    passed: List[str] = []
    failed: List[str] = []
    skipped: List[str] = []
    initial_observer_events = observer.events.snapshot_received()
    operator.events.clear()
    observer.events.clear()

    operator_uid = operator.room_user_id_int
    target_uid = observer.room_user_id_int
    if operator_uid is None or target_uid is None:
        raise RuntimeError("both clients must complete Conf login")

    def initial_user_state(field: str, default: int = 0) -> int:
        value = (observer.user_info or {}).get(field)
        for event in initial_observer_events:
            if event.cmdid == C.USERENTER:
                users = event.fields.get("User")
                if isinstance(users, dict):
                    users = [users]
                for user in users or []:
                    if str(user.get("UserID")) == str(target_uid) and user.get(field) is not None:
                        value = user[field]
            elif (event.cmdid == C.USERSTATECHANGED
                  and str(event.fields.get("UserID")) == str(target_uid)
                  and event.fields.get(field) is not None):
                value = event.fields[field]
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    right_names = {
        C.RIGHT_TYPE_LAYOUT: "layout",
        C.RIGHT_TYPE_VIDEO_POLLING: "video-polling",
        C.RIGHT_TYPE_SCREEN_SHARE: "screen-share",
        C.RIGHT_TYPE_MEDIA_SHARE: "media-share",
    }

    def acquire_right(right_type: int) -> None:
        response = operator.request_right(right_type, timeout=timeout)
        print(
            f"[INFO] acquired {right_names.get(right_type, right_type)} right: "
            f"Result={response.get('Result')}, "
            f"OccupiedUserID={response.get('OccupiedUserID', '')}")

    def release_right(right_type: int) -> None:
        try:
            operator.release_right(right_type, timeout=timeout)
        except Exception as exc:
            print(
                f"[WARN] release {right_names.get(right_type, right_type)} "
                f"right failed: {exc}")

    def run(name: str, check: Callable[[], None]) -> None:
        try:
            check()
        except Exception as exc:
            failed.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
        else:
            passed.append(name)
            print(f"[PASS] {name}")

    def check_user_audio() -> None:
        for state in (C.AUDIO_SPEAKING, C.AUDIO_OFF):
            _send_and_expect(
                observer,
                lambda state=state: operator.set_user_audio(target_uid, state),
                cmdid=C.USERSTATECHANGED,
                match={"UserID": target_uid, "Audio.State": state},
                timeout=timeout,
            )

    def check_user_video() -> None:
        for state in (C.VIDEO_BROADCAST, C.VIDEO_OFF):
            _send_and_expect(
                observer,
                lambda state=state: operator.set_user_video(target_uid, state),
                cmdid=C.USERSTATECHANGED,
                match={"UserID": target_uid, "Video.State": state},
                timeout=timeout,
            )

    def check_layout() -> None:
        acquire_right(C.RIGHT_TYPE_LAYOUT)
        try:
            _send_and_expect(
                observer,
                lambda: operator.set_layout(
                    layout_type=1001,
                    user_ids=[operator_uid, target_uid],
                    target_user_ids=[target_uid],
                ),
                cmdid=C.WNDSTATENOTIFY,
                match={"VideoRelayID": 1001, "Video.0.UserID": operator_uid,
                       "Video.1.UserID": target_uid},
                timeout=timeout,
            )
        finally:
            release_right(C.RIGHT_TYPE_LAYOUT)

    def check_role() -> None:
        role_fields = (
            (C.ROLE_PRESENTER, "DataState", C.DATASTATE_PRESENTER, C.DATASTATE_NONE),
            (C.ROLE_ADMIN, "ManagerState", C.MANAGERSTATE_ADMIN, C.MANAGERSTATE_NONE),
        )
        for role, field, enabled_value, disabled_value in role_fields:
            original = initial_user_state(field, disabled_value)
            try:
                # 真实服务器对幂等设置不广播。先归一化到关闭状态；如果本来已关闭，
                # 没有 Notify 也是合法的，随后开启/关闭必须各产生一次状态变化。
                mark = observer.events.mark()
                operator.set_role(target_uid, role, enabled=False)
                observer.events.wait_event(
                    after=mark,
                    source="conf",
                    cmdid=C.USERSTATECHANGED,
                    kind="notify",
                    match={"UserID": target_uid, field: disabled_value},
                    timeout=min(timeout, 2.0),
                )
                _send_and_expect(
                    observer,
                    lambda role=role: operator.set_role(target_uid, role, enabled=True),
                    cmdid=C.USERSTATECHANGED,
                    match={"UserID": target_uid, field: enabled_value},
                    timeout=timeout,
                )
                _send_and_expect(
                    observer,
                    lambda role=role: operator.set_role(target_uid, role, enabled=False),
                    cmdid=C.USERSTATECHANGED,
                    match={"UserID": target_uid, field: disabled_value},
                    timeout=timeout,
                )
            finally:
                if original == enabled_value:
                    _send_and_expect(
                        observer,
                        lambda role=role: operator.set_role(target_uid, role, enabled=True),
                        cmdid=C.USERSTATECHANGED,
                        match={"UserID": target_uid, field: enabled_value},
                        timeout=timeout,
                    )

    def check_aux_stream() -> None:
        changes = (
            (C.RIGHT_TYPE_MEDIA_SHARE, "media", "MediaShareState",
             C.MEDIASHARE_START, C.MEDIASHARE_STOP),
            (C.RIGHT_TYPE_SCREEN_SHARE, "vnc", "VNCState", C.VNC_ON, C.VNC_OFF),
        )
        for right_type, kind, field, start_state, stop_state in changes:
            acquire_right(right_type)
            try:
                for action, state in (
                    (lambda kind=kind: operator.start_aux_stream(
                        kind=kind,
                        audio_share_id=audio_share_id if kind == "media" else None,
                        video_share_id=video_share_id if kind == "media" else None), start_state),
                    (lambda kind=kind: operator.stop_aux_stream(kind=kind), stop_state),
                ):
                    _send_and_expect(
                        observer,
                        action,
                        cmdid=C.USERSTATECHANGED,
                        match={"UserID": operator_uid, field: state},
                        timeout=timeout,
                    )
            finally:
                release_right(right_type)

    def check_polling() -> None:
        polling_id = 7001
        acquire_right(C.RIGHT_TYPE_VIDEO_POLLING)
        try:
            _send_and_expect(
                observer,
                lambda: operator.configure_polling(
                    C.POLLING_OP_ADD,
                    polling_id,
                    "control-demo",
                    [operator_uid, target_uid],
                    interval=5,
                    screen=1,
                    window=1,
                    status=C.POLLING_STATUS_RUNNING,
                ),
                cmdid=C.VIDEOPOLLINGNOITFY,
                match={"VideoPolling.val": polling_id,
                       "VideoPolling.Operation": C.POLLING_NOTIFY_CHANGE},
                timeout=timeout,
            )
        finally:
            try:
                operator.configure_polling(
                    C.POLLING_OP_DEL, polling_id, "control-demo",
                    [operator_uid, target_uid])
            except Exception as exc:
                print(f"[WARN] polling cleanup failed: {exc}")
            release_right(C.RIGHT_TYPE_VIDEO_POLLING)

    def check_invite_users() -> None:
        if mock_mode:
            stub = _MockHttpApi()
            operator.attach_http(stub)
            user_id = observer.local_user_id_int
        else:
            if not http_config or invite_user_id is None:
                raise LookupError(
                    "requires --http-config and --invite-user-id in real-server mode")
            operator.attach_http(http_config)
            user_id = invite_user_id
        response = operator.invite_users([user_id])
        if str(response.get("code", "0")) != "0":
            raise AssertionError(f"invite_users failed: {response}")
        errors = response.get("data", {}).get("errors", [])
        if errors:
            raise AssertionError(f"invite_users returned errors: {errors}")

    def check_invite_terminals() -> None:
        sns_values = terminal_sns or (["8001"] if mock_mode else [])
        if not sns_values:
            raise LookupError("requires --terminal-sns in real-server mode")
        mark = operator.events.mark()
        response = operator.invite_terminals(sns_values)
        if str(response.get("OprResult", "0")) != "0":
            raise AssertionError(f"MAKECALL failed: {response}")
        event = operator.events.wait_event(
            after=mark,
            source="conf",
            cmdid=C.CALLSTATE_NOTIFY,
            kind="notify",
            match={"CalleeNum": sns_values[0]},
            timeout=timeout,
        )
        if event is None:
            raise AssertionError("no CALLSTATE_NOTIFY received for terminal invite")

    run("set_user_audio", check_user_audio)
    run("set_user_video", check_user_video)
    run("set_layout", check_layout)
    run("set_role presenter/admin", check_role)
    run("start/stop_aux_stream", check_aux_stream)
    run("configure_polling", check_polling)

    for name, check in (
        ("invite_users", check_invite_users),
        ("invite_terminals", check_invite_terminals),
    ):
        try:
            check()
        except LookupError as exc:
            skipped.append(f"{name}: {exc}")
            print(f"[SKIP] {name}: {exc}")
        except Exception as exc:
            failed.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
        else:
            passed.append(name)
            print(f"[PASS] {name}")

    return passed, failed, skipped


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Validate meeting-control wrappers with operator/observer clients.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true", help="use the built-in MockFmServer")
    mode.add_argument("--config", help="real-server multi_client.json path")
    parser.add_argument("--http-config", help="HTTP API JSON for invite_users")
    parser.add_argument("--invite-user-id", type=int,
                        help="platform user ID for the real HTTP invitation")
    parser.add_argument("--terminal-sns", action="append", default=[],
                        help="SNS/H323 number to call; repeat for multiple terminals")
    parser.add_argument("--timeout", type=float, default=5.0)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    server: Optional[MockFmServer] = None
    operator: Optional[FmTestClient] = None
    observer: Optional[FmTestClient] = None
    try:
        if args.mock:
            server, operator, observer = _build_mock_pair()
            case_params = {}
        else:
            config = load_config(args.config)
            operator, observer = connect_pair(config)
            case_params = config.get("case_params") or {}

        print(
            f"operator room_user_id={operator.room_user_id_int}, "
            f"observer room_user_id={observer.room_user_id_int}")
        passed, failed, skipped = run_checks(
            operator,
            observer,
            timeout=args.timeout,
            http_config=args.http_config,
            invite_user_id=args.invite_user_id,
            terminal_sns=args.terminal_sns,
            mock_mode=args.mock,
            audio_share_id=int(case_params.get("audio_share_id", 1)),
            video_share_id=int(case_params.get("video_share_id", 1)),
        )
        print(
            f"summary: passed={len(passed)}, failed={len(failed)}, "
            f"skipped={len(skipped)}")
        if failed:
            for failure in failed:
                print(f"  - {failure}")
            return 1
        return 0
    finally:
        _close_client(observer)
        _close_client(operator)
        if server is not None:
            server.stop()


if __name__ == "__main__":
    sys.exit(main())
