"""Read-only evidence for the real Web SDK transport; never constructs requests."""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
import json
import threading
import time
from typing import Any, Mapping

from .models import EventExpectation


@dataclass(frozen=True)
class WebReceivedEvent:
    source: str
    cmdid: int | None
    fields: dict
    raw_json: str
    received_at: float
    event_id: int
    received_monotonic: float
    kind: str = "unknown"
    name: str = "WEB_MESSAGE"
    transport: str = "websocket_json"
    connection_id: int = 0
    error_code: Any = None
    raw_xml: bytes = b""  # The Web transport is JSON; no XML is synthesized.
    metadata: dict = field(default_factory=dict)


def field_values(value, path: str):
    """Resolve singleton/repeated XML-style nodes without discarding peers."""
    parts = path.split(".") if path else []
    if not parts:
        return [value]
    key, tail = parts[0], ".".join(parts[1:])
    if isinstance(value, Mapping):
        if key in value:
            return field_values(value[key], tail)
        if key == "0":
            return field_values(value, tail)
    if isinstance(value, (tuple, list)):
        if key.isdigit():
            index = int(key)
            return field_values(value[index], tail) if index < len(value) else []
        return [v for item in value for v in field_values(item, path)]
    return []


def matches_fields(fields: Mapping, expected: Mapping) -> bool:
    # 将共同路径合并，同一重复节点必须同时满足全部字段，不能跨成员/设备拼凑。
    tree = {}
    for path, wanted in expected.items():
        node=tree
        for part in path.split("."):
            node=node.setdefault(part,{})
        node[None]=wanted
    def match(value, branches):
        if None in branches and str(value)!=str(branches[None]):
            return False
        children={key:item for key,item in branches.items() if key is not None}
        if not children:
            return True
        if isinstance(value,(list,tuple)):
            if all(key.isdigit() for key in children):
                return all(int(key)<len(value) and match(value[int(key)],branch)
                           for key,branch in children.items())
            return any(match(item,branches) for item in value)
        if not isinstance(value,Mapping):
            return False
        return all(match(value[key],branch) if key in value else
                   match(value,branch) if key=="0" else False
                   for key,branch in children.items())
    return match(fields,tree)


def event_matches(event: WebReceivedEvent, expectation: EventExpectation) -> bool:
    return ((expectation.source is None or event.source == expectation.source)
            and (expectation.cmdid is None or event.cmdid == expectation.cmdid)
            and (expectation.kind is None or event.kind == expectation.kind)
            and matches_fields(event.fields, expectation.match))


class WebEventJournal:
    """A monotonic journal shared by the browser worker and synchronous pytest."""
    RESPONSES = {6601, 6611, 6630, 6671}

    def __init__(self):
        self._condition = threading.Condition()
        self._events: list[WebReceivedEvent] = []
        self._next = 1

    def mark(self) -> int:
        with self._condition:
            return self._next - 1

    def snapshot(self, after=0):
        with self._condition:
            return [copy.deepcopy(e) for e in self._events if e.event_id > after]

    def append_frame(self, payload, connection_id: int, source_hint="unknown"):
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="replace")
        raw = str(payload)
        if raw == "{pong}":
            return self.append(source="paas",cmdid=30000,fields={},raw_json=raw,
                               connection_id=connection_id,kind="heartbeat")
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError("message is not an object")
            proxy = "CmdID" in message
            cmdid = message.get("CmdID") if proxy else message.get("id")
            cmdid = int(cmdid) if cmdid is not None else None
            source = "conf" if proxy and cmdid is not None and 6500 <= cmdid < 7000 else (
                "web_proxy" if proxy else "paas" if "business" in message or "id" in message
                else source_hint)
            fields = copy.deepcopy(message.get("Data") or {}) if proxy else copy.deepcopy(message)
            error = message.get("ErrorCode") if proxy else message.get("result")
            kind = ("response" if proxy and cmdid in self.RESPONSES else
                    "notify" if source == "conf" else "unknown")
            if not proxy and message.get('business')=='WBS' and cmdid in {19001,19003}:
                kind='response'  # SDK实际注册的白板创建/关闭回复，不添加任何成功字段。
            metadata = {"wire_cmdid": cmdid, "wire_message": copy.deepcopy(message)}
        except (ValueError, TypeError):
            source, cmdid, fields, error, kind = source_hint, None, {}, None, "decode_error"
            metadata = {"decode_error": "invalid JSON object"}
        return self.append(source=source, cmdid=cmdid, fields=fields,
                           raw_json=raw, connection_id=connection_id,
                           error_code=error, kind=kind, metadata=metadata)

    def append(self, **values):
        with self._condition:
            event = WebReceivedEvent(received_at=time.time(), event_id=self._next,
                received_monotonic=time.monotonic(), **copy.deepcopy(values))
            self._next += 1
            self._events.append(event)
            self._condition.notify_all()
            return event

    def wait(self, expectation, after, timeout):
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for event in self._events:
                    if event.event_id > after and event_matches(event, expectation):
                        return copy.deepcopy(event)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def assert_no(self, expectation, after, duration):
        event = self.wait(expectation, after, duration)
        if event is not None:
            raise AssertionError(f"unexpected Web event {event.cmdid}: {event.fields}")

    def as_dicts(self, after=0):
        return [asdict(e) for e in self.snapshot(after)]
