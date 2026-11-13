"""Test the four interactive protocol families in isolated temporary meetings.

No raw XML is assembled by these cases. Missing required environment data is
reported as BLOCKED (exit 2); server silence/business errors remain FAIL (exit 1).
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fm_client import conf as C
from fm_client import WebApiClient
from fm_client.testing import FmTestClient, connect_pair, load_config


class EnvironmentRequired(RuntimeError):
    pass


def received(client, mark, cmdid, timeout, match=None):
    event = client.events.wait_event(after=mark, source="conf", cmdid=cmdid,
        kind="notify", match=match, timeout=timeout)
    if event is None:
        recent = [(e.cmdid, e.kind) for e in client.events.snapshot_received(after=mark)]
        raise AssertionError(f"no {cmdid} matching {match}; recent cmdids={recent}")
    return event.fields


def health(a, b, timeout):
    for client in (a, b):
        result = client.get_user_features(timeout=timeout)
        assert str(result.get("UserID")) == str(client.room_user_id)


def remote_av(a, b, timeout, params, *, video=False, accept=True):
    key = "Video" if video else "Audio"
    dev_id = int(params.get("video_id", 0)) if video else 0
    devices = {"video_ids" if video else "audio_ids": [dev_id]}
    a.set_conf_state(enable_force_operate_user_av=0)
    # Ensure the control request is processed before the target reports devices.
    a.get_user_features(timeout=timeout)
    state = b.set_video_state if video else b.set_audio_state
    state(0, dev_id=dev_id)
    mark = a.events.mark()
    b.report_av_info(**{key.lower(): [C.ConfCmd.av_device(1, dev_id, "interaction-device")]})
    received(a, mark, 6608, timeout, {"UserID": b.room_user_id, key + ".0.ID": dev_id})
    am, bm = a.events.mark(), b.events.mark()
    a.request_remote_av(b.room_user_id, **devices)
    request = received(b, bm, 6642, timeout, {"SrcUserID": a.room_user_id,
        "DstUserID": b.room_user_id, "Type": 0, key + ".0.ID": dev_id})
    # Target decision is explicit, never automatic in the receive callback.
    b.respond_remote_av(request, accept=accept)
    result = received(a, am, 6643, timeout, {"SrcUserID": b.room_user_id,
        "DstUserID": a.room_user_id, "Type": 0, "Result": 0 if accept else 1,
        key + ".0.ID": dev_id})
    if accept:
        for client, mark in ((a, am), (b, bm)):
            received(client, mark, 6609, timeout, {"UserID": b.room_user_id,
                key + ".ID": dev_id, key + ".State": 2})
    else:
        for client, mark in ((a, am), (b, bm)):
            client.events.assert_no_event(after=mark, source="conf", cmdid=6609,
                match={"UserID": b.room_user_id, key + ".State": 2}, duration=0.3)
    return {"request": request, "result": result, "scope": "consent signaling; no media capture"}


def chat_review(a, b, timeout, params, *, approved=True):
    a.set_user_permissions(a.room_user_id, permissions={"CheckChat": 1, "ConfigChatCheck": 1})
    a.set_user_permissions(b.room_user_id, permissions={"CheckChat": 0, "OperatePublicChat": 1})
    a.set_chat_check(False)
    mark = a.events.mark()
    a.set_chat_check(True)
    received(a, mark, 6610, timeout, {"EnableChatMsgCheck": 1})
    am, bm = a.events.mark(), b.events.mark()
    text = f"approval-{time.time_ns()} 中文<&>"
    b.chat(text)
    pending = received(a, am, 6612, timeout, {"SrcUserID": b.room_user_id,
        "Text": text, "NeedCheck": 1})
    checked = a.review_chat_message_and_wait(pending, approved=approved, timeout=timeout)
    if not approved:
        b.events.assert_no_event(after=bm, source="conf", cmdid=6612,
            match={"SrcUserID": b.room_user_id, "Text": text}, duration=min(timeout, 1))
        return {"rejection": checked,
                "scope": "6629 rejection acknowledged; message not delivered during observation"}
    delivered = received(b, bm, 6612, timeout, {"SrcUserID": b.room_user_id, "Text": text})
    return {"approval": checked, "delivered": delivered}


def language(a, b, timeout, params):
    code = params.get("language")
    if not code:
        raise EnvironmentRequired("supply --language with a code enabled on this server, e.g. zh-CN")
    result = a.set_interactive_language_and_wait(
        code, user_id=b.room_user_id, observer=b, timeout=timeout)
    return {"notification": result}


def room_languages(a, b, timeout, params):
    # 6641 is pushed automatically, so include messages received at login.
    result = {}
    for label, client in (("A", a), ("B", b)):
        def pages():
            return [e for e in client.events.snapshot_received()
                    if e.source == "conf" and e.cmdid == 6641 and e.kind == "notify"]
        if not client.events.wait_until(lambda: any("End" in e.fields for e in pages()), timeout):
            raise AssertionError(label + " did not receive 6641 End; check multilingual deployment")
        end = next(e for e in pages() if "End" in e.fields)
        result[label] = [e.fields for e in client.events.snapshot_received()
                         if e.source == "conf" and e.cmdid == 6641 and e.event_id <= end.event_id]
    return result


def face_recognition(a, b, timeout, params):
    video_id = int(params.get("video_id", 0))
    mark = a.events.mark()
    b.report_av_info(video=[C.ConfCmd.av_device(1, video_id, "face-source")])
    received(a, mark, 6608, timeout, {"UserID": b.room_user_id, "Video.0.ID": video_id})
    result = a.request_face_recognition(b.room_user_id, video_id, timeout=timeout)
    assert str(result.get("Result")) == "0", result.get("Result")
    assert all(result.get(k) not in (None, "") for k in ("ServerDevID", "ServerAddr", "AppID", "CheckCode")), "incomplete face-service route"
    return {"Result": result["Result"], "scope": "face-service route allocated; recognition not performed"}


def face_rollcall(a, b, timeout, params, *, report=True):
    face_id = params.get("face_id")
    if report and face_id is None:
        raise EnvironmentRequired("supply --face-id: existing numeric ID from the face database, not room_user_id or a name")
    am, bm = a.events.mark(), b.events.mark()
    started = a.start_face_rollcall("SDK rollcall", countdown_seconds=max(60, int(timeout * 6)),
                                     timeout=timeout)
    assert str(started.get("Result")) == "0", started.get("Result")
    rollcall_id = started["RollcallID"]
    cancelled = False
    try:
        for client, mark in ((a, am), (b, bm)):
            received(client, mark, 6657, timeout, {"RollcallID": rollcall_id})
        if report:
            b.report_face_rollcall_result(rollcall_id, face_id)
            for client, mark in ((a, am), (b, bm)):
                received(client, mark, 6658, timeout, {"RollcallID": rollcall_id, "FaceID": face_id})
        a.cancel_face_rollcall(rollcall_id)
        cancelled = True
        for client, mark in ((a, am), (b, bm)):
            received(client, mark, 6659, timeout, {"RollcallID": rollcall_id})
        return {"RollcallID": rollcall_id, "FaceID": face_id, "scope": "signaling/database result; no biometric recognition"}
    finally:
        if not cancelled:
            a.cancel_face_rollcall(rollcall_id)


CASES = {
    "chat_approve": lambda a,b,t,p: chat_review(a,b,t,p,approved=True),
    "chat_reject": lambda a,b,t,p: chat_review(a,b,t,p,approved=False),
    "language": language, "room_languages": room_languages,
    "remote_audio_accept": lambda a,b,t,p: remote_av(a,b,t,p,accept=True),
    "remote_audio_reject": lambda a,b,t,p: remote_av(a,b,t,p,accept=False),
    "remote_video_accept": lambda a,b,t,p: remote_av(a,b,t,p,video=True,accept=True),
    "remote_video_reject": lambda a,b,t,p: remote_av(a,b,t,p,video=True,accept=False),
    "face_recognition": face_recognition, "face_rollcall": face_rollcall,
    "face_rollcall_start_cancel": lambda a,b,t,p: face_rollcall(a,b,t,p,report=False),
}


def run_case(name, cfg, timeout, params):
    a = b = None
    try:
        a, b = connect_pair(cfg)
        result = CASES[name](a, b, timeout, params)
        health(a, b, timeout)
        return result
    finally:
        for client in (b, a):
            if client is not None:
                client.close()


def http_ok(result):
    if str(result.get("code", 0)) != "0":
        raise AssertionError("HTTP operation failed")
    data = result.get("data")
    if isinstance(data, dict) and (str(data.get("result", 0)) != "0" or data.get("errors")):
        raise AssertionError("HTTP operation returned a business error")


def preflight(cfg, timeout):
    """Verify both Front identities before creating temporary meetings."""
    accounts = cfg.get("accounts") or []
    if len(accounts) < 2:
        raise ValueError("config.accounts requires an operator and an observer")
    for index, account in enumerate(accounts[:2]):
        client = FmTestClient(cfg["host"], int(cfg.get("port", 1089)), request_timeout=timeout)
        try:
            client.front_authenticate(account["username"], account.get("password", ""))
        except Exception as exc:
            raise EnvironmentRequired(f"Front account-index={index}: {type(exc).__name__}: {exc}") from exc
        finally:
            client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--http-config", required=True)
    parser.add_argument("--case", choices=["all", *CASES], default="all")
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--protocol-version", default="5.0", help="explicit test-session version; files are unchanged")
    parser.add_argument("--language")
    parser.add_argument("--video-id", type=int, default=0)
    parser.add_argument("--face-id", type=int)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    if not re.fullmatch(r"[0-9]+\.[0-9]+", args.protocol_version):
        parser.error("protocol-version must be a major.minor version")
    if int(args.protocol_version.split(".")[0]) < 4:
        parser.error("these consent cases require Conf protocol >=4")
    cfg = copy.deepcopy(load_config(args.config))
    api = WebApiClient.from_config(args.http_config)
    api.timeout = args.timeout
    if urlsplit(api.base_url).hostname != cfg["host"]:
        parser.error("HTTP and Front hosts must match")
    cfg.update(conf_protocol_version=args.protocol_version, request_timeout=args.timeout)
    cfg.setdefault("initialization_wait", 2)
    # Sender echo after approval is conditional on ChatRecall support.
    for options in [cfg.setdefault("conf_login_options", {}),
                    *(account.setdefault("conf_login_options", {}) for account in cfg.get("accounts", []))]:
        features = options.get("support_features") or []
        features = features.split(",") if isinstance(features, str) else list(features)
        options["support_features"] = list(dict.fromkeys([*features, "ChatRecall"]))
    names = list(CASES) if args.case == "all" else [args.case]
    failures, blocked, passed = [], [], []
    print(f"server={cfg['host']}, Conf={cfg['conf_protocol_version']}", flush=True)
    try:
        preflight(cfg, args.timeout)
    except Exception as exc:
        print(f"[BLOCKED] login preflight: {exc}; no temporary meeting created", flush=True)
        return 2
    for name in names:
        room_id = None
        ok = False
        try:
            if name == "language" and not args.language:
                raise EnvironmentRequired("--language is required for this case")
            if name == "face_rollcall" and args.face_id is None:
                raise EnvironmentRequired("--face-id from the actual face database is required")
            created = api.create_instant_meeting(meeting_name=f"interaction-{name}-{time.time_ns()}",
                user_name=cfg["accounts"][0]["username"], meeting_type=1, verify_mode=3)
            http_ok(created)
            room = created["data"]
            room_id = int(room["roomId"])
            current = {**cfg, "room_id": room_id, "room_node_id": str(room["nodeId"])}
            print(f"[RUN] {name} temporary_room={room_id}", flush=True)
            result = run_case(name, current, args.timeout, vars(args))
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            ok = True
        except EnvironmentRequired as exc:
            blocked.append(name)
            print(f"[BLOCKED] {name}: {exc}", flush=True)
        except Exception as exc:
            failures.append(name)
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}", flush=True)
        finally:
            if room_id is not None:
                try:
                    http_ok(api.close_meeting(room_id))
                    print(f"[CLEANUP] temporary_room={room_id} closed", flush=True)
                except Exception as exc:
                    failures.append(name + ":cleanup")
                    ok = False
                    print(f"[FAIL] cleanup room={room_id}: {exc}", flush=True)
            if ok:
                passed.append(name)
                print(f"[PASS] {name}", flush=True)
    print(f"summary: passed={passed}, failures={failures}, blocked={blocked}", flush=True)
    return 1 if failures else (2 if blocked else 0)


if __name__ == "__main__":
    raise SystemExit(main())
