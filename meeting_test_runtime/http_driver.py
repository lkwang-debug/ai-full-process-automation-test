"""Restricted WebApiClient adapter used as a server trigger/query, never as an endpoint."""
from __future__ import annotations

import copy
import threading
import time
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from fm_client.http_api import WebApiClient

from .models import ServiceProfile


class WebApiServiceDriver:
    TRIGGER_OPERATIONS = {
        "create_instant_meeting", "create_reserved_meeting", "cancel_reserved_meeting",
        "invite_users", "invite_terminals_http", "lock_layout", "lock_meeting",
        "set_layout_http", "close_meeting", "mute_all_http", "send_subtitles",
        "close_meeting_subtitle", "operate_record", "rename_meeting_http",
        "set_user_manager", "set_user_presenter", "remove_user", "control_user_audio",
        "control_user_video", "rename_user", "kick_out_user", "kick_out_user_by_id",
        "send_room_message",
    }
    RESULT_OPERATIONS = {
        "download_meeting_summary", "get_meeting_summary", "list_room_record_summaries",
        "list_record_summaries", "get_record_summary", "list_record_participants",
        "list_record_tasks", "list_records", "get_record_share", "export_meeting_summary",
        "export_transcript", "delete_record_summaries", "delete_record_tasks",
        "query_record_settings", "list_room_participants", "list_meeting_users",
        "query_meeting_info",
    }
    DATA_SETUP_OPERATIONS = {
        "add_department", "edit_department", "list_departments", "delete_department",
        "add_user", "edit_user", "delete_user", "list_users", "user_online",
        "authorize_user_room", "authorize_room_users", "list_authorized_users",
    }
    METHOD_NAMES = {name: name for name in
                    TRIGGER_OPERATIONS | RESULT_OPERATIONS | DATA_SETUP_OPERATIONS}
    METHOD_NAMES["mute_all_http"] = "mute_all"

    def __init__(self, profile: ServiceProfile | Mapping[str, Any]) -> None:
        config = profile.config if isinstance(profile, ServiceProfile) else dict(profile)
        self.profile = profile
        config = dict(config)
        base_url = str(config.get("base_url", "")).rstrip("/")
        parts = urlsplit(base_url)
        if parts.path in {"", "/"}:
            base_url = urlunsplit((parts.scheme, parts.netloc, "/api", parts.query, parts.fragment))
        config["base_url"] = base_url
        self.native_client = WebApiClient.from_config(config)
        self._call_lock = threading.Lock()
        self._next_call_id = 1
        self._call_traces = []

    def call(self, operation: str, params: Optional[dict] = None) -> Any:
        method_name = self.METHOD_NAMES.get(operation)
        if method_name is None:
            raise ValueError(f"unregistered HTTP operation: {operation}")
        values = dict(params or {})
        if operation in self.RESULT_OPERATIONS and "page_num" in values:
            values.setdefault("page_number", values.pop("page_num"))
        request_after = (self.native_client.mark_requests()
                         if hasattr(self.native_client, "mark_requests") else 0)
        with self._call_lock:
            call_id = self._next_call_id
            self._next_call_id += 1
        called_at = time.time()
        try:
            return getattr(self.native_client, method_name)(**values)
        finally:
            request_ids = ([item["trace_id"] for item in
                            self.native_client.snapshot_requests(after=request_after)]
                           if hasattr(self.native_client, "snapshot_requests") else [])
            with self._call_lock:
                self._call_traces.append({
                    "trace_type": "public_call", "trace_id": call_id,
                    "operation": operation, "method_name": method_name,
                    "params": copy.deepcopy(values), "called_at": called_at,
                    "http_request_ids": request_ids, "sent_command_ids": [],
                })

    def mark_outbound(self) -> dict:
        with self._call_lock:
            calls = self._next_call_id - 1
        return {"calls": calls,
                "http": (self.native_client.mark_requests()
                         if hasattr(self.native_client, "mark_requests") else 0)}

    def snapshot_outbound(self, after: Mapping[str, int]) -> list:
        with self._call_lock:
            calls = [copy.deepcopy(item) for item in self._call_traces
                     if item["trace_id"] > int(after.get("calls", 0))]
        owners = {trace_id: call for call in calls
                  for trace_id in call["http_request_ids"]}
        records = list(calls)
        requests = (self.native_client.snapshot_requests(after=int(after.get("http", 0)))
                    if hasattr(self.native_client, "snapshot_requests") else [])
        for item in requests:
            owner = owners.get(item["trace_id"])
            item.update(
                trace_type="http_request",
                operation=owner.get("operation") if owner else None,
                call_trace_id=owner.get("trace_id") if owner else None)
            records.append(item)
        return sorted(records, key=lambda item: item.get("called_at", item.get("sent_at", 0)))

    def close(self) -> None:
        # WebApiClient uses short-lived urllib requests and owns no persistent session.
        return None
