"""Validate 6637/6647/6654 against disposable real-server meetings.

PASS requires a real matching notification. BLOCKED means the selected record
action or waiting-room scenario did not produce the unspecified server push;
it is not a pass. No XML injection, service disruption or load exhaustion.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fm_client import WebApiClient, conf as C
from fm_client._helpers import _as_list
from fm_client.testing import FmTestClient, connect_client, connect_pair, load_config, wait_for


class TriggerUnavailable(RuntimeError):
    pass


def http_ok(result):
    data = result.get("data")
    if str(result.get("code", 0)) != "0" or (isinstance(data, dict) and (
            str(data.get("result", 0)) != "0" or data.get("errors"))):
        raise AssertionError("HTTP business operation failed")


def received(client, mark, cmdid, timeout, match=None):
    event = client.events.wait_event(after=mark, source="conf", cmdid=cmdid,
        kind="notify", match=match, timeout=timeout)
    if event is None:
        recent = [(e.cmdid, e.kind) for e in client.events.snapshot_received(after=mark)]
        raise AssertionError(f"no {cmdid} matching {match}; recent={recent}")
    return event


def health(*clients, timeout):
    for client in clients:
        result = client.get_user_features(timeout=timeout)
        assert str(result.get("UserID")) == str(client.room_user_id), result


def web_notify(cfg, api, timeout):
    a, b = connect_pair(cfg)
    try:
        mark, am = b.events.mark(), a.events.mark()
        text = f"web-notify-{time.time_ns()} 中文<&>\""
        result = api.send_room_message(room_id=cfg["room_id"], user_field_type=1,
            user_field_value=str(b.room_user_id), operator="SDK-test", app_back_notify=text)
        http_ok(result)
        event = received(b, mark, C.WEBSENDNOTIFY, timeout, {
            "UserID": b.room_user_id, "operator": "SDK-test", "appBackNotify": text})
        assert wait_for(lambda: any(e == event.fields for e in b.events.snapshot(
            b.events.web_notifies)), timeout=timeout), "6654 callback missing"
        a.events.assert_no_event(after=am, source="conf", cmdid=C.WEBSENDNOTIFY,
            match={"appBackNotify": text}, duration=0.3)
        health(a, b, timeout=timeout)
        return {"HTTP": result, "B_6654": event.fields,
                "scope": "directed delivery, callback, XML escaping and A/B health"}
    finally:
        b.close()
        a.close()


def record_error(cfg, api, timeout, action):
    a, b = connect_pair(cfg)
    try:
        marks = {"A": a.events.mark(), "B": b.events.mark()}
        clients = {"A": a, "B": b}
        if action == "start":
            a.start_record()
        elif action == "stop":
            a.stop_record()
        print(f"[OBSERVE] record action={action}; waiting for real 6637", flush=True)

        def errors():
            return [(name, e) for name, client in clients.items()
                    for e in client.events.snapshot_received(after=marks[name])
                    if e.source == "conf" and e.cmdid == C.SERVERRECORDERRORNOTIFY]

        wait_for(errors, timeout=timeout)
        found = errors()
        if not found:
            states = {name: [e.fields for e in client.events.snapshot_received(after=marks[name])
                      if e.cmdid == C.CONFSTATECHANGED] for name, client in clients.items()}
            print("[EVIDENCE] 6610=" + json.dumps(states, ensure_ascii=False), flush=True)
            raise TriggerUnavailable(
                "no 6637 from this ordinary action; requires a documented, isolated record-error trigger; "
                "6610 success/error is not a substitute for 6637")
        result = []
        for name, event in found:
            assert event.kind == "notify", event.kind
            fields = event.fields
            error = fields.get("Error")
            if error is None and isinstance(fields.get("ServerRecord"), dict):
                error = fields["ServerRecord"].get("Error")
            assert error is not None, "6637 missing Error"
            assert clients[name].wait_for_server_record_error(error=int(error), timeout=timeout), "callback missing"
            result.append({"client": name, "fields": fields})
        health(a, b, timeout=timeout)
        return {"6637": result, "scope": "real error receipt and callback; recording did not succeed"}
    finally:
        # The outer runner closes this disposable meeting even on errors.
        b.close()
        a.close()


def waiting_snapshot(client, uid):
    pages = [e.fields for e in client.events.snapshot_received(after=0)
             if e.source == "conf" and e.cmdid == C.WAITINGUSERLISTNOTIFY and e.kind == "notify"]
    users, complete = [], False
    for page in pages:
        body = page.get("UserList")
        if isinstance(body, dict):
            users.extend(_as_list(body.get("UserInfo")))
            if "End" in body:
                complete = complete or any(str(user.get("UserID")) == str(uid) for user in users)
                users = []  # Do not borrow End from an earlier, empty snapshot.
    return {"complete": complete, "pages": pages}


def waiting_users(cfg, api, timeout):
    cfg = copy.deepcopy(cfg)
    for options in [cfg.setdefault("conf_login_options", {}),
                    *(account.setdefault("conf_login_options", {}) for account in cfg["accounts"][:2])]:
        features = options.get("support_features") or []
        features = features.split(",") if isinstance(features, str) else list(features)
        options["support_features"] = list(dict.fromkeys([*features, "MoveInWaitingRoom"]))
    a = b = manager = guest = None
    worker = None
    outcome = {}
    try:
        a = connect_client(cfg, account_index=0)
        b = connect_client(cfg, account_index=1, conf_login=False)
        time.sleep(float(cfg.get("initialization_wait", 2)))
        a.request_right(C.RIGHT_TYPE_WAITING_ROOM, timeout=timeout)
        mark = a.events.mark()
        a.set_conf_state(enable_waiting_room=1)
        route = received(a, mark, C.ENTERGROUPREP, timeout).fields
        manager = a.connect_related_room(route, timeout=timeout)
        bm = b.events.mark()

        def join():
            try:
                options = dict(cfg.get("conf_login_options") or {})
                options.update(cfg["accounts"][1].get("conf_login_options") or {})
                options.setdefault("user_name", cfg["accounts"][1]["username"])
                options["timeout"] = timeout * 4
                outcome["login"] = b.conf_login(**options)
            except Exception as exc:
                outcome["error"] = type(exc).__name__

        worker = threading.Thread(target=join, daemon=True)
        worker.start()
        waiting = received(b, bm, C.WAITINGROOMINFO, timeout).fields
        guest = b.connect_related_room(waiting, timeout=timeout)
        clients = {"A.main": a, "B.main": b, "A.waiting": manager, "B.waiting": guest}
        print("[OBSERVE] B received 6667 and joined waiting room; observing four Conf connections", flush=True)
        wait_for(lambda: any(waiting_snapshot(c, b.room_user_id)["complete"] for c in clients.values()),
                 timeout=timeout)
        snapshots = {name: waiting_snapshot(c, b.room_user_id) for name, c in clients.items()}
        evidence = {name: {"cmdids": sorted({e.cmdid for e in c.events.snapshot_received(after=0)
                                            if e.cmdid is not None}),
                           "6647_pages": len(snapshots[name]["pages"])} for name, c in clients.items()}
        print("[EVIDENCE] " + json.dumps(evidence, ensure_ascii=False), flush=True)
        complete = {name: value for name, value in snapshots.items() if value["complete"]}
        if not complete:
            if any(value["pages"] for value in snapshots.values()):
                raise AssertionError("6647 received but no complete list containing B plus UserList.End")
            raise TriggerUnavailable(
                "6667/waiting subroom succeeded, but no 6647 on main or waiting connections; "
                "server push trigger/implementation still unconfirmed")
        for name, snapshot in complete.items():
            assert wait_for(lambda: len(clients[name].events.snapshot(clients[name].events.waiting_user_lists))
                >= len(snapshot["pages"]), timeout=timeout), "6647 callback missing"
        return {"6647": complete}
    finally:
        for client in (guest, manager, b, a):
            if client is not None:
                client.close()
        if worker is not None:
            worker.join(timeout + 1)
            if worker.is_alive():
                raise RuntimeError("waiting login worker failed to stop")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="examples/multi_client.json")
    parser.add_argument("--http-config", default="examples/http_api.json")
    parser.add_argument("--case", choices=("all", "web_notify", "record_error", "waiting_users"), default="all")
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--protocol-version", help="override test sessions only; otherwise use config")
    parser.add_argument("--record-action", choices=("start", "stop", "observe"), default="start",
                        help="ordinary action only; observe allows a separately coordinated server trigger")
    parser.add_argument("--web-room-type", choices=("fixed", "dynamic"), default="fixed",
                        help="6654: create a disposable fixed room by default; dynamic probes API support separately")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be finite and positive")
    cfg = copy.deepcopy(load_config(args.config))
    cfg["request_timeout"] = args.timeout
    if args.protocol_version:
        cfg["conf_protocol_version"] = args.protocol_version
    api = WebApiClient.from_config(args.http_config)
    api.timeout = args.timeout
    if urlsplit(api.base_url).hostname != cfg["host"]:
        parser.error("HTTP and signaling hosts must match")
    print(f"server={cfg['host']}, Conf={cfg.get('conf_protocol_version', C.CONF_VER_CS)}", flush=True)
    accounts = cfg.get("accounts") or []
    if len(accounts) < 2:
        parser.error("two accounts required")
    # Authenticate before creating any room. Never print passwords or tokens.
    try:
        for index, account in enumerate(accounts[:2]):
            client = FmTestClient(cfg["host"], cfg.get("port", 1089), request_timeout=args.timeout)
            try:
                client.front_authenticate(account["username"], account.get("password", ""))
            finally:
                client.close()
    except Exception as exc:
        print(f"[BLOCKED] Front preflight account-index={index}: {type(exc).__name__}: {exc}", flush=True)
        return 2
    selected = ["web_notify", "record_error", "waiting_users"] if args.case == "all" else [args.case]
    passed, failed, blocked = [], [], []
    for name in selected:
        room_id, ok = None, False
        fixed = name == "web_notify" and args.web_room_type == "fixed"
        try:
            title = f"notify-{name}-{time.time_ns()}"
            if fixed:
                created = api.add_room(room_name=title, verify_mode="3")
            else:
                created = api.create_instant_meeting(meeting_name=title,
                    user_name=accounts[0]["username"], meeting_type=1, verify_mode=3)
            http_ok(created)
            room = created["data"]
            room_id = int(room["roomId"])
            if fixed:
                http_ok(api.make_room_fixed(room_id=room_id))
            current = {**cfg, "room_id": room_id, "room_node_id": str(room.get("nodeId") or cfg["room_node_id"])}
            print(f"[RUN] {name} temporary_room={room_id}; type={'fixed' if fixed else 'dynamic'}", flush=True)
            if name == "record_error":
                result = record_error(current, api, args.timeout, args.record_action)
            else:
                result = {"web_notify": web_notify, "waiting_users": waiting_users}[name](current, api, args.timeout)
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            ok = True
        except TriggerUnavailable as exc:
            blocked.append(name)
            print(f"[BLOCKED] {name}: {exc}", flush=True)
        except Exception as exc:
            failed.append(name)
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}", flush=True)
        finally:
            if room_id is not None:
                try:
                    http_ok(api.close_meeting(room_id))
                    print(f"[CLEANUP] temporary_room={room_id} closed", flush=True)
                except Exception as exc:
                    failed.append(name + ":cleanup")
                    ok = False
                    print(f"[FAIL] cleanup room={room_id}: {type(exc).__name__}: {exc}", flush=True)
                if fixed:
                    try:
                        http_ok(api.delete_room(room_id))
                        print(f"[CLEANUP] temporary fixed room={room_id} deleted", flush=True)
                    except Exception as exc:
                        failed.append(name + ":delete")
                        ok = False
                        print(f"[FAIL] delete room={room_id}: {type(exc).__name__}: {exc}", flush=True)
            if ok:
                passed.append(name)
                print(f"[PASS] {name}", flush=True)
    print(f"summary: passed={passed}, failures={failed}, blocked={blocked}", flush=True)
    return 1 if failed else (2 if blocked else 0)


if __name__ == "__main__":
    raise SystemExit(main())
