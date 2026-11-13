"""Extended Conf protocol regression against disposable real-server meetings.

Only signaling is simulated: no screen capture, H5 UI or ASR audio is produced.
Run --help for individual cases. Existing configured rooms are never modified.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import threading
import time
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fm_client import conf as C
from fm_client._helpers import _as_list
from fm_client.http_api import WebApiClient
from fm_client.testing import connect_client, connect_pair, load_config


FEATURES = ["AbilityRenegotiation", "NewVote", "GroupMeeting", "MoveInWaitingRoom", "VNCRemote"]


def diagnostics(client, mark):
    def redact(value):
        if isinstance(value, dict):
            return {key: "<redacted>" if key in ("Token", "SrvAuthCode", "RoomPassword", "ChairPwd")
                    else redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value
    return {"recent": [(item.cmdid, item.kind, redact(item.fields))
                       for item in client.events.snapshot_received(after=mark)],
            "disconnects": client.events.snapshot(client.events.disconnects)}


def event(client, mark, cmdid, timeout, match=None, kind="notify"):
    received = client.events.wait_event(after=mark, source="conf", cmdid=cmdid,
                                        kind=kind, match=match, timeout=timeout)
    if received is None:
        raise AssertionError(f"cmdid={cmdid} kind={kind} match={match}; {diagnostics(client, mark)}")
    return received.fields


def absent(client, mark, cmdid, match=None):
    client.events.assert_no_event(after=mark, source="conf", cmdid=cmdid,
                                  match=match, duration=0.3)


def both(a, b, cmdid, action, match, timeout):
    marks = [client.events.mark() for client in (a, b)]
    result = action()
    try:
        received = [event(client, mark, cmdid, timeout, match)
                    for client, mark in zip((a, b), marks)]
    except AssertionError as exc:
        raise AssertionError(f"{exc}; A={diagnostics(a, marks[0])}; B={diagnostics(b, marks[1])}") from exc
    return {"result": result, "A": received[0], "B": received[1]}


def healthy(a, b, timeout):
    for client in (a, b):
        result = client.get_user_features(timeout=timeout)
        assert str(result["UserID"]) == str(client.room_user_id), result


def relay(a, b, timeout):
    """Directional 6700, reply, content preservation, and independent health."""
    probe = "relay-" + str(time.time_ns())
    fields = {"Probe": probe, "Text": "中文<&>\"", "Item": [{"ID": 1}, {"ID": 2}]}
    am, bm = a.events.mark(), b.events.mark()
    a.send_relay_message(b.room_user_id, [{"id": 6718, "fields": fields}])
    incoming = event(b, bm, C.RELAYMESSAGE, timeout,
        {"SrcUserID": a.room_user_id, "DstUserID": b.room_user_id, "msg.id": 6718,
         "msg.Probe": probe})
    assert incoming["msg"] == {"id": "6718", "Probe": probe, "Text": fields["Text"],
                                "Item": [{"ID": "1"}, {"ID": "2"}]}, incoming
    absent(a, am, C.RELAYMESSAGE)
    mark = a.events.mark()
    b.send_relay_message(a.room_user_id, [{"id": 6719, "fields": {"Probe": probe, "Result": 0}}])
    response = event(a, mark, C.RELAYMESSAGE, timeout,
        {"SrcUserID": b.room_user_id, "DstUserID": a.room_user_id,
         "msg.id": 6719, "msg.Probe": probe, "msg.Result": 0})
    return {"B_received": incoming, "A_received": response}


def multi_share(a, b, timeout):
    a.set_multi_data_sharer(False, timeout=timeout)
    try:
        mark = b.events.mark()
        changed = a.set_multi_data_sharer(True, timeout=timeout)
        notified = event(b, mark, C.MULTIDATASHARERNOTIFY, timeout,
                         {"EnableMultiDataSharer": 1, "SrcUserID": a.room_user_id, "Result": 0})
        mark = b.events.mark()
        unchanged = a.set_multi_data_sharer(True, timeout=timeout)
        absent(b, mark, C.MULTIDATASHARERNOTIFY)
        return {"changed": changed, "observer": notified, "idempotent": unchanged}
    finally:
        a.set_multi_data_sharer(False, timeout=timeout)


def user_features(a, b, timeout):
    mark = b.events.mark()
    features = a.get_user_features(b.room_user_id, timeout=timeout)
    assert "NewVote" in features["SupportFeatures"].split(","), features
    absent(b, mark, C.USERFEATURESREP)
    users = a.query_feature_users("NewVote", subscribe=True, timeout=timeout)
    assert str(b.room_user_id) in users["UserIDs"], users
    mark = a.events.mark()
    a.unsubscribe_feature_users("NewVote")
    absent(a, mark, C.FEATUREUSERSREP)
    return {"features": features, "subscription_initial": users, "unsubscribe": "send-only"}


def extended_state(a, b, timeout):
    result = {}
    # Preserve the complete contact snapshot; server Info updates are not patches.
    info = dict(b.user_info or {})
    for user in a.events.snapshot(a.events.user_enters):
        if str(user.user_id) == str(b.room_user_id):
            info.update(user.raw)
    keys = ("Sex", "SeatList", "DisplayName", "Tel", "Mobile", "EMail")
    if any(key not in info for key in keys):
        raise ValueError("complete observer Info snapshot required before modifying contact information")
    saved = {key: info[key] for key in keys}
    saved_mute = int(info.get("AudioOutMute", 0))
    saved_mark = int(info.get("WBMarkState", 0))
    try:
        result["AudioOutMute"] = both(a, b, C.USERSTATECHANGED,
            lambda: a.set_user_state(b.room_user_id, audio_out_mute=1 - bool(saved_mute)),
            {"UserID": b.room_user_id, "AudioOutMute": 1 - bool(saved_mute)}, timeout)
        result["WBMarkState"] = both(a, b, C.USERSTATECHANGED,
            lambda: a.set_user_state(b.room_user_id, wb_mark_state=0 if saved_mark else 1),
            {"UserID": b.room_user_id, "WBMarkState": 0 if saved_mark else 1}, timeout)
        new_info = {**saved, "DisplayName": "extended-" + str(int(time.time()))}
        result["Info"] = both(a, b, C.USERSTATECHANGED,
            lambda: a.set_user_state(b.room_user_id, info=new_info),
            {"UserID": b.room_user_id, "Info.DisplayName": new_info["DisplayName"]}, timeout)
        # A asks B, then B accepts. The request's State=2 produces WAITING=1.
        result["PrivateTalk_request"] = both(a, b, C.USERSTATECHANGED,
            lambda: a.set_user_state(b.room_user_id,
                private_talk={"DstUserID": a.room_user_id, "State": 2}),
            {"UserID": b.room_user_id, "PrivateTalk.State": 1}, timeout)
        result["PrivateTalk_accept"] = both(a, b, C.USERSTATECHANGED,
            lambda: b.set_user_state(private_talk={"DstUserID": a.room_user_id, "State": 2}),
            {"UserID": b.room_user_id, "PrivateTalk.State": 2}, timeout)
        return result
    finally:
        b.set_user_state(private_talk={"DstUserID": a.room_user_id, "State": 0})
        both(a, b, C.USERSTATECHANGED, lambda: a.set_user_state(b.room_user_id, info=saved),
             {"UserID": b.room_user_id, "Info.DisplayName": saved["DisplayName"]}, timeout)
        a.set_user_state(b.room_user_id, audio_out_mute=saved_mute, wb_mark_state=saved_mark)


def self_state(a, b, timeout):
    am, bm = a.events.mark(), b.events.mark()
    a.set_user_state(b.room_user_id, vnc_state=2, vnc_audio_id=0)
    request = event(b, bm, C.MODIFYSELFSTATENOTIFY, timeout,
                    {"SrcUserID": a.room_user_id, "VNCState": 2})
    absent(a, am, C.MODIFYSELFSTATENOTIFY)
    absent(a, am, C.USERSTATECHANGED, {"UserID": b.room_user_id, "VNCState": 2})
    mark = a.events.mark()
    b.reply_self_state_change(request["SrcUserID"], vnc_state=0, error="simulated capture declined")
    absent(a, mark, C.MODIFYSELFSTATENOTIFY)
    # Success branch simulates local media startup and explicitly commits state.
    bm = b.events.mark()
    a.set_user_state(b.room_user_id, vnc_state=2, vnc_audio_id=0)
    event(b, bm, C.MODIFYSELFSTATENOTIFY, timeout, {"SrcUserID": a.room_user_id})
    b.request_right(C.RIGHT_TYPE_SCREEN_SHARE, timeout=timeout)
    try:
        committed = both(a, b, C.USERSTATECHANGED,
            lambda: b.set_user_state(vnc_state=2, vnc_audio_id=0),
            {"UserID": b.room_user_id, "VNCState": 2}, timeout)
        b.reply_self_state_change(a.room_user_id, vnc_state=2)
        return {"request": request, "committed": committed, "6546": "send-only acknowledgement"}
    finally:
        both(a, b, C.USERSTATECHANGED, lambda: b.set_user_state(vnc_state=0),
             {"UserID": b.room_user_id, "VNCState": 0}, timeout)
        b.release_right(C.RIGHT_TYPE_SCREEN_SHARE, timeout=timeout)


def renegotiate(a, b, timeout):
    mark = b.events.mark()
    result = a.renegotiate_layout(["W1", "WV4", "WV9"], screen_count=1, timeout=timeout)
    assert "Layout" in result and "Code" in result["Layout"], result
    absent(b, mark, C.ABILITYRENEGOTIATIONREP)
    return result


def h5_vote(a, b, timeout):
    result = {}
    for label, targets in (("broadcast", None), ("targeted", [b.room_user_id])):
        vote_id = (time.time_ns() // 1000000) % 0x7FFFFFFF
        am, bm = a.events.mark(), b.events.mark()
        a.send_h5_vote(vote_id, duration=1, user_ids=targets)
        try:
            result[label] = event(b, bm, C.H5VOTENOTIFY, timeout,
                                  {"ID": vote_id, "State": 2, "Creator": a.room_user_id})
            absent(a, am, C.H5VOTENOTIFY, {"ID": vote_id})
        finally:
            bm = b.events.mark()
            a.send_h5_vote(vote_id, state=0)
            result[label + "_end"] = event(b, bm, C.H5VOTENOTIFY, timeout,
                                            {"ID": vote_id, "State": 0})
    return result


def group_config(a, b, timeout):
    a.request_right(C.RIGHT_TYPE_GROUP, timeout=timeout)
    group_ids = []
    try:
        mark = a.events.mark()
        created = a.create_group([C.ConfCmd.group_room(theme="extended-group", users=[
            C.ConfCmd.group_user(id=b.room_user_id, right=2)])], timeout=timeout)
        group_ids = [room["RoomID"] for room in created["RoomInfo"]]
        assert group_ids, created
        state = event(a, mark, C.GROUPUSERSTATENOTIFY, timeout,
                      {"RoomUserInfo.0.RoomID": group_ids[0], "RoomUserInfo.0.User.0.ID": b.room_user_id})
        bm = b.events.mark()
        updated = a.modify_group_config([{"RoomID": group_ids[0], "Theme": "extended-renamed",
            "User": [{"ID": b.room_user_id, "Right": 3}]}], timeout=timeout)
        room = _as_list(updated.get("RoomInfo"))[0]
        assert room["Theme"] == "extended-renamed", updated
        assert str(_as_list(room["User"])[0]["Right"]) == "3", updated
        absent(b, bm, C.MODIFYGROUPCONFIGREP)
        return {"created": created, "manager_6666": state, "modified": updated}
    finally:
        if group_ids:
            a.change_group_state(group_ids, 2, timeout=timeout)
        a.release_right(C.RIGHT_TYPE_GROUP, timeout=timeout)


def asr_subtitle(a, b, timeout):
    mark = b.events.mark()
    try:
        enabled = a.set_asr_subtitle(True, timeout=timeout)
        assert str(enabled["EnableAsrSubtitle"]) == "1", enabled
        translated = a.set_asr_subtitle(True, translation=True,
                                       target_language="en", timeout=timeout)
        assert str(translated["EnableAsrSubtitleTranslation"]) == "1", translated
        absent(b, mark, C.SETASRSUBTITLEREP)
        return {"enabled": enabled, "translated": translated, "scope": "settings signaling only"}
    finally:
        a.set_asr_subtitle(False, timeout=timeout)


def custom_data(a, b, timeout):
    public = json.dumps({"probe": "extended-public", "text": "<&>"})
    return {"public": both(a, b, C.USERCUSTOMDATANOTIFY,
        lambda: a.set_user_custom_data(b.room_user_id, public_data=public),
        {"SrcUserID": a.room_user_id, "UserID": b.room_user_id, "PublicData": public}, timeout)}


def custom_private(a, b, timeout):
    private = json.dumps({"probe": "extended-private", "text": "<&>"})
    am, bm = a.events.mark(), b.events.mark()
    a.set_user_custom_data(b.room_user_id, private_data=private)
    result = event(b, bm, C.USERCUSTOMDATANOTIFY, timeout,
                   {"SrcUserID": a.room_user_id, "UserID": b.room_user_id, "PrivateData": private})
    absent(a, am, C.USERCUSTOMDATANOTIFY, {"PrivateData": private})
    return result


def waiting_room(config, timeout):
    a = b = manager = guest = None
    worker = None
    outcome = {}
    try:
        a = connect_client(config, account_index=0)
        b = connect_client(config, account_index=1, conf_login=False)
        time.sleep(float(config.get("initialization_wait", 0)))
        a.request_right(C.RIGHT_TYPE_WAITING_ROOM, timeout=timeout)
        mark = a.events.mark()
        a.set_conf_state(enable_waiting_room=1)
        route = event(a, mark, C.ENTERGROUPREP, timeout)
        manager = a.connect_related_room(route, timeout=timeout)
        bm = b.events.mark()

        def join():
            try:
                options = dict(config["conf_login_options"])
                options.update(config["accounts"][1].get("conf_login_options") or {})
                options.setdefault("user_name", config["accounts"][1]["username"])
                options["timeout"] = timeout * 5
                outcome["login"] = b.conf_login(**options)
            except Exception as exc:
                outcome["error"] = exc

        worker = threading.Thread(target=join, daemon=True)
        worker.start()
        waiting = event(b, bm, C.WAITINGROOMINFO, timeout)
        guest = b.connect_related_room(waiting, timeout=timeout)
        # Independent waiting-group session, not the main-room manager session.
        admitted = manager.allow_waiting_users([
            {"UserID": b.room_user_id, "Allow": 1}], timeout=timeout)
        results = _as_list(admitted.get("UserList", {}).get("UserInfo"))
        assert len(results) == 1 and str(results[0].get("Result")) == "0", admitted
        worker.join(timeout)
        assert not worker.is_alive(), "main-room 6601 missing after admission"
        if "error" in outcome:
            raise outcome["error"]
        assert str(outcome["login"].get("Result")) == "0", outcome
        healthy(a, b, timeout)
        return {"6667": {"RoomID": waiting["RoomID"]}, "6670": admitted, "main_6601": "0"}
    finally:
        for client in (guest, manager):
            if client is not None:
                client.close()
        try:
            if a is not None:
                a.set_conf_state(enable_waiting_room=0)
        finally:
            if a is not None:
                a.close()
            if b is not None:
                b.close()
            if worker is not None:
                worker.join(timeout + 1)


CASES = {name: globals()[name] for name in (
    "multi_share", "user_features", "extended_state", "self_state", "renegotiate",
    "h5_vote", "group_config", "waiting_room", "asr_subtitle", "custom_data", "custom_private", "relay")}


def run_case(name, config, timeout):
    config = copy.deepcopy(config)
    options = config.setdefault("conf_login_options", {})
    declared = options.get("support_features") or []
    if isinstance(declared, str):
        declared = declared.split(",")
    options["support_features"] = list(dict.fromkeys([*declared, *FEATURES]))
    options.setdefault("screen_count", 1)
    options.setdefault("layouts", ["W1", "WV4", "WV9"])
    if name == "waiting_room":
        return waiting_room(config, timeout)
    a, b = connect_pair(config)
    try:
        result = CASES[name](a, b, timeout)
        # Custom-data acceptance covers delivery only, not subsequent session health.
        if name not in ("custom_data", "custom_private"):
            healthy(a, b, timeout)
        return result
    finally:
        b.close()
        a.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--http-config", required=True, help="same server; creates/closes temporary meetings")
    parser.add_argument("--case", choices=["all", *CASES], default="all")
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    api = WebApiClient.from_config(args.http_config)
    if urlsplit(api.base_url).hostname != config["host"]:
        parser.error("HTTP and signaling config hosts differ; select matching environment configs")
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    config["request_timeout"] = args.timeout
    selected = list(CASES) if args.case == "all" else [args.case]
    failures = []
    print(f"server={config['host']}, Conf={config.get('conf_protocol_version', C.CONF_VER_CS)}", flush=True)
    for name in selected:
        room_id = None
        try:
            created = api.create_instant_meeting(meeting_name=f"protocol-{name}-{int(time.time())}",
                user_name=config["accounts"][0]["username"], meeting_type=1, verify_mode=3)
            room = created["data"]
            room_id = int(room["roomId"])
            current = {**config, "room_id": room_id, "room_node_id": str(room["nodeId"])}
            print(f"[RUN] {name} temporary_room={room_id}", flush=True)
            result = run_case(name, current, args.timeout)
            print(f"[PASS] {name}\n{json.dumps(result, ensure_ascii=False, indent=2)}", flush=True)
        except Exception as exc:
            failures.append(name)
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}", flush=True)
        finally:
            if room_id is not None:
                try:
                    closed = api.close_meeting(room_id)
                    data = closed.get("data")
                    if isinstance(data, dict) and data.get("result") not in (None, 0, "0"):
                        raise AssertionError(f"close failed: {closed}")
                    print(f"[CLEANUP] temporary_room={room_id} closed", flush=True)
                except Exception as exc:
                    failures.append(name + ":cleanup")
                    print(f"[FAIL] cleanup room={room_id}: {exc}", flush=True)
    print(f"summary: cases={len(selected)}, failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
