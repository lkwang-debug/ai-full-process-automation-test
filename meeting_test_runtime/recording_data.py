"""Dynamically create recording/summary data for HTTP_RESULT cases."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from fm_client.http_api import WebApiClient
from fm_client.testing import FmTestClient

from .models import ClientProfile


@dataclass(frozen=True)
class RecordingDataset:
    room_id: int
    group_id: str
    summary: Mapping[str, Any]
    state_transitions: tuple[int, ...] = ()
    data_source: str = "dynamic"


class RecordingDataUnavailable(RuntimeError):
    """The environment accepted recording control but produced no summary data."""


class RecordingDatasetProvider:
    """Lazily provision one reusable recorded meeting per test session."""

    def __init__(self, api: WebApiClient, operator: ClientProfile, *,
                 user_name: str, capture_seconds: float = 3.0,
                 ready_timeout: float = 180.0, poll_interval: float = 3.0,
                 join_settle_time: float = 2.0, data_source: str = "dynamic",
                 prepared_room_id: int = 0, prepared_group_id: str = "") -> None:
        self.api = api; self.operator = operator; self.user_name = user_name
        self.capture_seconds = float(capture_seconds)
        self.ready_timeout = float(ready_timeout); self.poll_interval = float(poll_interval)
        self.join_settle_time = float(join_settle_time)
        if data_source not in {"dynamic", "prepared"}:
            raise ValueError("recording.data_source must be dynamic or prepared")
        self.data_source = data_source
        self.prepared_room_id = int(prepared_room_id)
        self.prepared_group_id = str(prepared_group_id).strip()
        if data_source == "prepared" and self.prepared_room_id <= 0:
            raise ValueError("prepared模式需要有效的recording.room_id")
        self._dataset: Optional[RecordingDataset] = None
        self._created_room_id: Optional[int] = None
        self._error: Optional[Exception] = None

    def get(self) -> RecordingDataset:
        if self._error is not None:
            raise self._error
        if self._dataset is None:
            try:
                self._dataset = (self._load_prepared() if self.data_source == "prepared"
                                 else self._provision())
            except Exception as exc:
                self._error = exc
                raise
        return self._dataset

    def _load_prepared(self) -> RecordingDataset:
        records = []
        page = 1
        while True:
            response = self.api.list_room_record_summaries(
                self.prepared_room_id, page_number=page, page_size=100)
            data = response.get("data") or {}
            if not isinstance(data, Mapping):
                raise AssertionError("预置录制列表data不是对象")
            for item in data.get("records") or []:
                if (isinstance(item, Mapping) and item.get("groupId") and
                        str(item.get("roomId")) == str(self.prepared_room_id)):
                    records.append(item)
            pages = int(data.get("pages") or 1)
            if page >= pages:
                break
            if page >= 100:
                raise RecordingDataUnavailable("录制列表超过100页，请配置group_id缩小数据范围")
            page += 1
        if self.prepared_group_id:
            records = [r for r in records if str(r["groupId"]) == self.prepared_group_id]
        if not records:
            raise RecordingDataUnavailable(
                f"预置会议{self.prepared_room_id}没有匹配的录制记录，group_id={self.prepared_group_id!r}")
        # 固定选取最新记录；内容完整性由后续查询和导出断言验证。
        record = max(records, key=lambda r: (str(r.get("startTime") or ""), str(r["groupId"])))
        return RecordingDataset(self.prepared_room_id, str(record["groupId"]),
                                record, (), "prepared")

    def _provision(self) -> RecordingDataset:
        created = self.api.add_room(
            room_name=f"fm-e2e-recording-{int(time.time())}",
            verify_mode="3", maxUserCount=10, roomMode="standard",
            meeting_minutes=True, creatorName=self.user_name)
        data = created.get("data") if isinstance(created, Mapping) else None
        if not isinstance(data, Mapping) or not data.get("roomId"):
            raise AssertionError(f"创建录制测试会议未返回roomId: {created!r}")
        room_id = int(data["roomId"])
        self._created_room_id = room_id
        node_id = str(data.get("nodeId") or self.operator.endpoint.get("room_node_id") or "1")
        account, endpoint = self.operator.account, self.operator.endpoint
        client = FmTestClient(endpoint["host"], int(endpoint["port"]),
                              request_timeout=float(endpoint.get("request_timeout", 10)),
                              conf_protocol_version=str(
                                  endpoint.get("conf_protocol_version", "2.0")))
        try:
            client.front_login(
                account.get("username", ""), account.get("password", ""),
                room_id=room_id, room_node_id=node_id,
                room_password=account.get("room_password"))
            preferred = str(endpoint.get("conf_preferred_host") or "").strip()
            if preferred:
                selected = []
                for server in client.conf_servers:
                    if not isinstance(server, dict):
                        continue
                    addresses = [
                        item.strip()
                        for item in str(server.get("SrvAddr") or "").split(";")
                        if item.strip() and preferred in item
                    ]
                    if addresses:
                        selected.append({
                            **server, "SrvAddr": ";".join(addresses) + ";"})
                if not selected:
                    raise RuntimeError(
                        f"preferred Conf host {preferred!r} is absent from Front response")
                client.conf_servers = selected
            client.conf_login(user_name=account.get("display_name") or account.get("username"))
            time.sleep(self.join_settle_time)
            mark = client.events.mark()
            start_response = self.api.operate_record(room_id, 2)
            started = client.events.wait_event(
                after=mark, source="conf", cmdid=6610, kind="notify",
                match={"ServerRecordStatus": 2}, timeout=3)
            if started is None:
                mark = client.events.mark()
                client.start_record()
                started = client.events.wait_event(
                    after=mark, source="conf", cmdid=6610, kind="notify",
                    match={"ServerRecordStatus": 2}, timeout=15)
                if started is None:
                    raise AssertionError(
                        "HTTP和Fm端均未能启动录制: "
                        f"http_response={start_response!r}, events={client.events.snapshot_received(after=mark)!r}")
            time.sleep(self.capture_seconds)
            mark = client.events.mark()
            stop_response = self.api.operate_record(room_id, 0)
            stopped = client.events.wait_event(
                after=mark, source="conf", cmdid=6610, kind="notify",
                match={"ServerRecordStatus": 0}, timeout=3)
            if stopped is None:
                mark = client.events.mark()
                client.stop_record()
                stopped = client.events.wait_event(
                    after=mark, source="conf", cmdid=6610, kind="notify",
                    match={"ServerRecordStatus": 0}, timeout=15)
                if stopped is None:
                    raise AssertionError(
                        f"HTTP和Fm端均未能停止录制: http_response={stop_response!r}")
            self.api.close_meeting(room_id)
        finally:
            client.close()

        deadline = time.monotonic() + self.ready_timeout
        last = None
        while time.monotonic() < deadline:
            last = self.api.list_room_record_summaries(room_id)
            record = self._find_room_record(last, room_id)
            if record and record.get("groupId"):
                return RecordingDataset(
                    room_id, str(record["groupId"]), record, (2, 0))
            time.sleep(self.poll_interval)
        raise RecordingDataUnavailable(
            f"录制结束后未在{self.ready_timeout}秒内生成摘要: room_id={room_id}, last={last!r}")

    @classmethod
    def _find_room_record(cls, value: Any, room_id: int) -> Optional[Mapping[str, Any]]:
        if isinstance(value, Mapping):
            if value.get("groupId") and str(value.get("roomId")) == str(room_id):
                return value
            for child in value.values():
                found = cls._find_room_record(child, room_id)
                if found: return found
        elif isinstance(value, list):
            for child in value:
                found = cls._find_room_record(child, room_id)
                if found: return found
        return None

    def cleanup(self) -> None:
        # 预置数据永远不归测试所有，即使INI误开cleanup也不得删除。
        if self.data_source == "prepared":
            return
        if self._dataset is not None:
            try: self.api.delete_record_summaries([self._dataset.group_id])
            except Exception: pass
        if self._created_room_id is not None:
            try: self.api.delete_room(self._created_room_id)
            except Exception: pass
