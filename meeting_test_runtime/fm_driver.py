"""Generic driver around the development-owned FmTestClient."""
from __future__ import annotations

import copy
from dataclasses import asdict, is_dataclass
import threading
import time
from typing import Any, Dict, Mapping, Optional, Set

from fm_client.testing import FmTestClient
from fm_client.errcodes import FmError

from .drivers import register_client_driver
from .models import ClientProfile, EventExpectation, MissingSessionManagerApi


class FmClientDriver:
    client_type = "fm"
    capabilities: Set[str] = {"entrance", "front", "conf", "received_event", "http_facade"}

    METHOD_NAMES = {name: name for name in (
        "entrance_get_config", "front_authenticate", "front_login",
        "front_login_by_meeting_no", "front_login_by_invite_code", "front_login_guest",
        "front_roomlist", "front_roomlist_classified", "front_discover_node",
        "front_getroomid", "front_get_auth_type", "front_logout", "conf_login",
        "conf_logout", "chat", "set_audio_state", "set_video_state", "mic_on",
        "mic_off", "mic_request", "camera_on", "camera_off", "start_record",
        "pause_record", "stop_record", "lock_room", "unlock_room", "mute_all",
        "set_subtitle", "close_subtitle", "kick_user", "close_room", "apply_host",
        "release_host", "set_window_state", "request_right", "release_right",
        "conf_invite_call", "conf_hangup", "conf_query_call_list", "report_av_info",
        "set_conf_state", "conf_get_offline_users", "conf_get_dept_info",
        "conf_get_room_config", "conf_get_contacts", "allow_knock", "create_group",
        "enter_group", "end_groups", "bring_to_main", "rename_meeting", "send_sysmsg",
        "send_file", "recall_msg", "list_rooms", "offline_contacts", "contacts_snapshot",
        # Newly exposed sessionmanager business methods.
        "set_user_audio", "set_user_video", "set_layout", "set_role",
        "start_aux_stream", "stop_aux_stream", "configure_polling",
        "set_role_permissions", "set_user_permissions", "set_room_data",
        "get_av_mix_params", "set_av_mix_params", "set_video_subtitle_params",
        "invite_terminals", "invite_users", "invite_contacts", "lock_layout",
        "create_instant_meeting", "create_reserved_meeting", "cancel_reserved_meeting",
        # 2026-09-07 SDK extended public business methods.
        "set_multi_data_sharer", "get_user_features", "query_feature_users",
        "unsubscribe_feature_users", "set_user_state", "reply_self_state_change",
        "renegotiate_layout", "send_h5_vote", "modify_group_config",
        "change_group_state", "allow_waiting_users", "set_asr_subtitle",
        "set_user_custom_data", "send_relay_message",
        # 2026-09-23 interactive protocol SDK. Unresolved operations remain
        # centrally blocked and are not generated as executable cases.
        "set_chat_check", "review_chat_message", "review_chat_message_and_wait",
        "set_interactive_language", "set_interactive_language_and_wait",
        "request_remote_av", "reply_remote_av", "respond_remote_av",
        "request_face_recognition", "start_face_rollcall",
        "report_face_rollcall_result", "cancel_face_rollcall",
    )}

    MISSING_OPERATIONS = set()

    RESET_OPERATIONS = {
        "mic_on": "mic_off", "camera_on": "camera_off",
        "set_audio_state": "set_audio_state", "set_video_state": "set_video_state",
        "set_user_audio": "set_user_audio", "set_user_video": "set_user_video",
        "start_record": "stop_record", "pause_record": "stop_record",
        "lock_room": "unlock_room", "set_subtitle": "close_subtitle",
        "request_right": "release_right", "start_aux_stream": "stop_aux_stream",
        "apply_host": "release_host",
        "set_role": "set_role",
        "lock_layout": "lock_layout",
        "set_layout": "set_window_state", "set_window_state": "set_window_state",
        "report_av_info": "report_av_info",
        "set_role_permissions": "set_role_permissions",
        "set_user_permissions": "set_user_permissions",
        "set_conf_state": "set_conf_state",
        "configure_polling": "configure_polling",
        "set_multi_data_sharer": "set_multi_data_sharer",
        "set_asr_subtitle": "set_asr_subtitle",
        "conf_invite_call": "conf_hangup", "invite_terminals": "conf_hangup",
    }

    def __init__(self, profile: ClientProfile) -> None:
        self.profile = profile
        self.native_client = self._new_client()
        self._call_lock = threading.Lock()
        self._next_call_id = 1
        self._call_traces = []

    def _new_client(self) -> FmTestClient:
        endpoint = self.profile.endpoint
        return FmTestClient(
            endpoint["host"], int(endpoint["port"]),
            request_timeout=float(endpoint.get("request_timeout", 10)),
            conf_protocol_version=str(endpoint.get("conf_protocol_version", "2.0")))

    @property
    def conf_protocol_version(self) -> str:
        return self.native_client.conf_protocol_version

    @property
    def identity(self) -> Mapping[str, Any]:
        client = self.native_client
        return {"local_user_id": client.local_user_id_int,
                "room_user_id": client.room_user_id_int, "room_id": client.room_id,
                "user_right": client.user_right_int}

    def attach_http(self, http_client: Any) -> None:
        self.native_client.attach_http(http_client)

    def accept_online_invitation(self, online, invitation):
        result = online.accept_and_join(invitation, self.native_client,
            room_node_id=self.profile.account.get('room_node_id', self.profile.endpoint.get('room_node_id')),
            user_name=self.profile.account.get('username'), support_features=self._support_features(),
            timeout=float(self.profile.endpoint.get('request_timeout', 10)))
        return result

    def join(self, room_id: int) -> Mapping[str, Any]:
        account, endpoint = self.profile.account, self.profile.endpoint
        retries = int(endpoint.get("login_retries", 3))
        self.last_join_timing = {"front_seconds": 0.0, "conf_seconds": 0.0,
                                 "retry_count": 0}
        for attempt in range(retries):
            try:
                front_started = time.monotonic()
                front = self.native_client.front_login(
                    account.get("username", ""), account.get("password", ""), room_id=int(room_id),
                    room_node_id=str(account.get("room_node_id", endpoint.get("room_node_id", "1"))),
                    room_password=account.get("room_password"), token=account.get("token"),
                    user_type=int(account.get("user_type", 1)),
                    terminal_type=int(account.get("terminal_type", 1)),
                    client_type=int(account.get("client_type", 1)))
                self.last_join_timing["front_seconds"] += time.monotonic() - front_started
                self._prefer_conf_host()
                conf_started = time.monotonic()
                conf = self.native_client.conf_login(
                    user_name=account.get("display_name") or account.get("username") or "guest",
                    support_features=self._support_features())
                self.last_join_timing["conf_seconds"] += time.monotonic() - conf_started
                return {"front": front, "conf": conf}
            except (FmError, TimeoutError) as exc:
                self.last_join_timing["retry_count"] = attempt + 1
                retryable = isinstance(exc, TimeoutError) or getattr(
                    exc, "code", None) in {0x1001, 0x1002, 0x1004, 0x2108}
                if not retryable or attempt + 1 >= retries:
                    raise
                self.native_client.close()
                time.sleep(float(endpoint.get("login_retry_interval", 2.0)))
                self.native_client = self._new_client()
        raise RuntimeError("Fm client login retries exhausted")

    def _prefer_conf_host(self) -> None:
        """Restrict Front-provided Conf addresses to an optional test node."""
        preferred = str(self.profile.endpoint.get(
            "conf_preferred_host", "")).strip()
        if not preferred:
            return
        selected = []
        for server in self.native_client.conf_servers:
            if not isinstance(server, dict):
                continue
            addresses = [
                item.strip() for item in str(server.get("SrvAddr") or "").split(";")
                if item.strip() and preferred in item
            ]
            if addresses:
                selected.append({**server, "SrvAddr": ";".join(addresses) + ";"})
        if not selected:
            raise RuntimeError(
                f"preferred Conf host {preferred!r} is absent from Front response")
        self.native_client.conf_servers = selected

    def call(self, operation: str, params: Optional[dict] = None) -> Any:
        if operation in self.MISSING_OPERATIONS:
            raise MissingSessionManagerApi(operation)
        method_name = self.METHOD_NAMES.get(operation)
        if method_name is None:
            raise ValueError(f"unregistered fm operation: {operation}")
        self._ensure_call_trace()
        actual_params = self._operation_params(operation, params or {})
        sent_after = (self.native_client.mark_sent_commands()
                      if hasattr(self.native_client, "mark_sent_commands") else 0)
        http = getattr(self.native_client, "http_api", None)
        http_after = http.mark_requests() if hasattr(http, "mark_requests") else 0
        with self._call_lock:
            call_id = self._next_call_id
            self._next_call_id += 1
        called_at = time.time()
        try:
            return getattr(self.native_client, method_name)(**actual_params)
        finally:
            command_ids = ([item.event_id for item in
                            self.native_client.snapshot_sent_commands(after=sent_after)]
                           if hasattr(self.native_client, "snapshot_sent_commands") else [])
            request_ids = ([item["trace_id"] for item in
                            http.snapshot_requests(after=http_after)]
                           if hasattr(http, "snapshot_requests") else [])
            trace = {
                "trace_type": "public_call", "trace_id": call_id,
                "operation": operation, "method_name": method_name,
                "params": copy.deepcopy(actual_params), "called_at": called_at,
                "sent_command_ids": command_ids,
                "http_request_ids": request_ids,
            }
            with self._call_lock:
                self._call_traces.append(trace)

    def _ensure_call_trace(self) -> None:
        if not hasattr(self, "_call_lock"):
            self._call_lock = threading.Lock()
            self._next_call_id = 1
            self._call_traces = []

    def _operation_params(self, operation: str, params: Mapping[str, Any]) -> dict:
        account, endpoint = self.profile.account, self.profile.endpoint
        defaults: Dict[str, Any] = {}
        if operation in {"front_authenticate", "front_login", "front_login_by_meeting_no",
                         "front_login_by_invite_code", "front_login_guest"}:
            defaults.update(username=account.get("username", ""), password=account.get("password", ""))
        if operation in {"front_login", "front_login_guest"}:
            defaults.update(room_id=int(account.get("room_id", endpoint.get("room_id", 0))),
                            room_node_id=str(account.get("room_node_id", endpoint.get("room_node_id", "1"))))
        if operation == "front_login_by_meeting_no":
            defaults["meeting_no"] = account.get("meeting_no", endpoint.get("meeting_no", ""))
            defaults["room_node_id"] = str(
                account.get("room_node_id", endpoint.get("room_node_id", "1")))
        if operation == "front_login_by_invite_code":
            defaults["invite_code"] = account.get("invite_code", endpoint.get("invite_code", ""))
            defaults["room_node_id"] = str(
                account.get("room_node_id", endpoint.get("room_node_id", "1")))
        if operation == "conf_login":
            defaults["user_name"] = account.get("display_name") or account.get("username") or "guest"
            defaults["support_features"] = self._support_features()
        defaults.update(params)
        return defaults

    def _support_features(self):
        value = self.profile.account.get(
            "support_features", self.profile.endpoint.get("support_features"))
        if value in (None, ""):
            return None
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return list(value)

    def mark_events(self) -> int:
        return self.native_client.events.mark()

    def connect_related(self, fields):
        child = object.__new__(type(self))
        child.profile = self.profile
        child.native_client = self.native_client.connect_related_room(dict(fields))
        return child

    def wait_event(self, expectation: EventExpectation, after: int, timeout: float) -> Any:
        event = self.native_client.events.wait_event(
            after=after, source=expectation.source, cmdid=expectation.cmdid,
            kind=expectation.kind, match=expectation.match, timeout=timeout)
        if event is None:
            raise AssertionError(
                f"fm event timeout: expectation={expectation}, received={self.snapshot_events(after)}")
        return event

    def expect_no_event(self, expectation: EventExpectation, after: int, timeout: float) -> None:
        self.native_client.events.assert_no_event(
            after=after, source=expectation.source, cmdid=expectation.cmdid,
            kind=expectation.kind, match=expectation.match, duration=timeout)

    assert_no_event = expect_no_event

    def snapshot_events(self, after: int = 0) -> list:
        values = self.native_client.events.snapshot_received(after=after)
        return [asdict(value) if is_dataclass(value) else value for value in values]

    def mark_outbound(self) -> dict:
        self._ensure_call_trace()
        http = getattr(self.native_client, "http_api", None)
        with self._call_lock:
            calls = self._next_call_id - 1
        return {
            "calls": calls,
            "commands": (self.native_client.mark_sent_commands()
                         if hasattr(self.native_client, "mark_sent_commands") else 0),
            "http": http.mark_requests() if hasattr(http, "mark_requests") else 0,
        }

    def snapshot_outbound(self, after: Mapping[str, int]) -> list:
        self._ensure_call_trace()
        with self._call_lock:
            calls = [copy.deepcopy(item) for item in self._call_traces
                     if item["trace_id"] > int(after.get("calls", 0))]
        command_owner = {
            event_id: call for call in calls for event_id in call["sent_command_ids"]}
        http_owner = {
            trace_id: call for call in calls for trace_id in call["http_request_ids"]}
        records = list(calls)
        commands = (self.native_client.snapshot_sent_commands(
                    after=int(after.get("commands", 0)))
                    if hasattr(self.native_client, "snapshot_sent_commands") else [])
        for item in commands:
            record = asdict(item)
            owner = command_owner.get(item.event_id)
            record.update(
                trace_type="xml_command", protocol="XML",
                operation=owner.get("operation") if owner else None,
                call_trace_id=owner.get("trace_id") if owner else None)
            records.append(record)
        http = getattr(self.native_client, "http_api", None)
        if hasattr(http, "snapshot_requests"):
            for item in http.snapshot_requests(after=int(after.get("http", 0))):
                owner = http_owner.get(item["trace_id"])
                item.update(
                    trace_type="http_request",
                    operation=owner.get("operation") if owner else None,
                    call_trace_id=owner.get("trace_id") if owner else None)
                records.append(item)
        return sorted(records, key=lambda item: item.get("called_at", item.get("sent_at", 0)))

    def reset(self, operation: str, params: Optional[dict] = None):
        reset_name = self.RESET_OPERATIONS.get(operation)
        if reset_name is None:
            return None
        reset_params = dict(params or {})
        if operation == "set_subtitle":
            reset_params = {}
        elif operation == "request_right":
            reset_params = {"right_type": reset_params.get("right_type")}
        elif operation == "start_aux_stream":
            reset_params = {"kind": reset_params.get("kind", "media")}
        elif operation in {"set_audio_state", "set_video_state"}:
            current = reset_params.get("state")
            if operation == "set_audio_state":
                reset_params["state"] = 0 if current in (1, 2) else 2
            else:
                reset_params["state"] = 0 if current == 2 else 2
        elif operation in {"set_user_audio", "set_user_video"}:
            reset_params["state"] = 0
        elif operation == "set_role":
            reset_params["enabled"] = not bool(reset_params.get("enabled", True))
        elif operation == "set_conf_state":
            reset_params = {
                key: 1 if value == 0 else 0
                for key, value in reset_params.items()
            }
        elif operation in {"set_role_permissions", "set_user_permissions"}:
            permissions = reset_params.get("permissions") or {}
            reset_params["permissions"] = {
                key: 1 if value in (0, False) else 0
                for key, value in permissions.items()
            }
        elif operation == "set_layout":
            reset_params = {"mode": 3, "video_relay_id": 1001}
            if any(key in (params or {}) for key in (
                    "full", "full_video_id", "full_video_media_id", "valid_full_mode")):
                reset_params.update(
                    full=0, full_video_id=0, full_video_media_id=0,
                    valid_full_mode=0)
        elif operation == "lock_layout":
            reset_params = {"enabled": False}
        elif operation == "set_window_state":
            reset_params = {"mode": reset_params.get("mode", 3), "video_relay_id": 0}
            if any(key in (params or {}) for key in (
                    "full", "full_video_id", "full_video_media_id", "valid_full_mode")):
                reset_params.update(
                    full=0, full_video_id=0, full_video_media_id=0,
                    valid_full_mode=0)
        elif operation == "report_av_info":
            reset_params = {
                kind: [{**device, "Operation": 3}
                       for device in reset_params.get(kind, [])]
                for kind in ("audio", "video") if reset_params.get(kind)
            }
        elif operation == "configure_polling":
            # 单用例只创建轮询；结束后按同一ID删除，避免污染复用会议。
            reset_params = {
                "operation": 3,
                "polling_id": reset_params.get("polling_id"),
                "name": reset_params.get("name", ""),
                "user_ids": reset_params.get("user_ids", []),
            }
        elif operation == "set_multi_data_sharer":
            reset_params = {"enabled": not bool(reset_params.get("enabled", True)),
                            "force": True}
        elif operation == "set_asr_subtitle":
            reset_params = {"enabled": False, "translation": False}
        elif operation == "conf_invite_call":
            reset_params = {
                "callee_nums": [str(item.get("CalleeNum", item.get("callee_num", "")))
                                for item in reset_params.get("callees", [])],
                "call_type": int(reset_params.get("call_type", 2)),
            }
        elif operation == "invite_terminals":
            reset_params = {
                "callee_nums": [str(value) for value in reset_params.get("sns_list", [])],
                "call_type": 2,
            }
        if reset_name == "conf_hangup" and not reset_params.get("callee_nums"):
            return None
        self.call(reset_name, reset_params)
        return reset_name, reset_params

    def is_healthy(self) -> bool:
        events = self.native_client.events
        return not events.snapshot(events.disconnects) and not events.snapshot(events.create_failures)

    def close(self) -> None:
        client = self.native_client
        try:
            if getattr(client, "_conf", None) is not None:
                try: client.conf_logout()
                except Exception: pass
                time.sleep(0.2)
            if getattr(client, "_front", None) is not None:
                try: client.front_logout()
                except Exception: pass
        finally:
            client.close()


def register_fm_driver(*, replace: bool = False) -> None:
    register_client_driver("fm", FmClientDriver, replace=replace)
