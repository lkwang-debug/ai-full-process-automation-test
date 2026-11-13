"""Receive a real PaaS invitation and enter its meeting using Front/Conf.

Default: invited terminal only, waiting for a manual invitation from the backend.
With --http-config: create a temporary meeting and exercise invite_users or
invite_contacts end to end. HTTP success alone never passes this demo.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fm_client import PaasOnlineClient, WebApiClient
from fm_client.testing import FmTestClient, connect_client, load_config, wait_for


def http_success(result):
    if str(result.get("code", "0")) != "0":
        raise AssertionError("HTTP operation returned an error")
    data = result.get("data")
    if isinstance(data, dict) and (str(data.get("result", "0")) != "0" or data.get("errors")):
        raise AssertionError("HTTP operation reported unsuccessful result or users")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--online-config", required=True, help="PaaS CP address and invitee credentials")
    parser.add_argument("--account-index", type=int, default=1, help="invited user, default accounts[1]")
    parser.add_argument("--http-config", help="optional: create meeting and invite automatically")
    parser.add_argument("--invite-method", choices=("invite_users", "invite_contacts"), default="invite_users")
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--wait", type=float, default=60, help="seconds to wait for a real 14002")
    parser.add_argument("--stay", type=float, default=0, help="seconds to remain in the joined meeting")
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.wait <= 0 or args.stay < 0:
        parser.error("timeout/wait must be positive; stay must be nonnegative")
    cfg = load_config(args.config)
    cfg["request_timeout"] = args.timeout
    accounts = cfg.get("accounts") or []
    if not 0 <= args.account_index < len(accounts):
        parser.error("account-index is not in config.accounts")
    if args.http_config and args.account_index == 0:
        parser.error("automatic inviter uses accounts[0]; select a different invitee")
    account = accounts[args.account_index]
    online = PaasOnlineClient.from_config(args.online_config, timeout=args.timeout)
    api = WebApiClient.from_config(args.http_config) if args.http_config else None
    if api and urlsplit(api.base_url).hostname != cfg["host"]:
        parser.error("HTTP and Front config hosts must match")
    invited = FmTestClient(cfg["host"], int(cfg.get("port", 1089)), request_timeout=args.timeout,
                           conf_protocol_version=cfg.get("conf_protocol_version", "2.0"))
    operator = room_id = None
    exit_code = 0
    try:
        invited.front_authenticate(account["username"], account.get("password", ""), timeout=args.timeout)
        if str(invited.local_user_id) != online.user_id:
            raise AssertionError("PaaS user_id does not match Front invitee identity")
        online.login()
        print(f"[ONLINE] user={invited.local_user_id}; PaaS 10001 result=0; Conf not joined", flush=True)
        expected_code = None
        if api:
            created = api.create_instant_meeting(meeting_name=f"invited-{time.time_ns()}",
                user_name=accounts[0]["username"], meeting_type=1, verify_mode=3)
            http_success(created)
            room = created["data"]
            room_id = int(room["roomId"])
            current = {**cfg, "room_id": room_id, "room_node_id": str(room["nodeId"])}
            operator = connect_client(current, account_index=0)
            operator.attach_http(api)
            expected_code = int(operator.conf_get_room_config(timeout=args.timeout)["InviteCode"])
            operator_mark = operator.events.mark()
            http_success(getattr(operator, args.invite_method)([int(invited.local_user_id)]))
            print(f"[INVITE_SENT] {args.invite_method}; temporary_room={room_id}; waiting for B", flush=True)
        else:
            print("[WAIT] 请在后台邀请此用户，接收端将使用通知中的邀请码自动入会。", flush=True)
        invitation = online.wait_invitation(timeout=args.wait)
        print(f"[RECEIVED] 14002 seq_id={invitation.seq_id}; inviter={invitation.inviter_user_id}; "
              "target from extend_info.params.inviteCode", flush=True)
        if expected_code is not None and invitation.invite_code != expected_code:
            raise AssertionError("received invitation targets a different meeting")
        options = dict(cfg.get("conf_login_options") or {})
        options.update(account.get("conf_login_options") or {})
        options.setdefault("user_name", account["username"])
        online.accept_and_join(invitation, invited, room_node_id=cfg.get("room_node_id"),
            room_app_id=int(cfg.get("room_app_id", 11)), timeout=args.timeout, **options)
        time.sleep(float(cfg.get("initialization_wait", 2)))
        features = invited.get_user_features(timeout=args.timeout)
        if str(features.get("UserID")) != str(invited.room_user_id) or not online.is_online:
            raise AssertionError("joined user identity/online state check failed")
        if operator:
            def saw_invitee():
                for received in operator.events.snapshot_received(after=operator_mark):
                    if received.source != "conf" or received.cmdid != 6602:
                        continue
                    users = received.fields.get("User") or []
                    users = users if isinstance(users, list) else [users]
                    if any(str(user.get("UserID")) == str(invited.room_user_id) for user in users):
                        return True
                return False
            if not wait_for(saw_invitee, args.timeout):
                raise AssertionError("A did not receive B's actual 6602 join notification")
            actual_room = int(invited.real_room_id or invited.room_id)
            if actual_room != room_id:
                raise AssertionError("B joined a different meeting")
        print(f"[PASS] invited_join: received 14002 -> sent 14003 -> 30005/6601 -> "
              f"user={invited.room_user_id}, room={invited.real_room_id or invited.room_id}", flush=True)
        deadline = time.monotonic() + args.stay
        while time.monotonic() < deadline:
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    except Exception as exc:
        print(f"[FAIL] invited_join: {type(exc).__name__}: {exc}", flush=True)
        exit_code = 1
    finally:
        online.close()
        for client in (invited, operator):
            if client is not None:
                try:
                    client.conf_logout()
                except Exception:
                    pass
                try:
                    client.front_logout(timeout=args.timeout)
                finally:
                    client.close()
        if room_id is not None:
            try:
                http_success(api.close_meeting(room_id))
                print(f"[CLEANUP] temporary_room={room_id} closed", flush=True)
            except Exception as exc:
                print(f"[FAIL] cleanup room={room_id}: {exc}", flush=True)
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
