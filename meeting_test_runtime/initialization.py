"""Observe Conf initialization without changing business event cursors."""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Mapping, Sequence


INITIALIZATION_CMDIDS = {6602, 6608, 6613, 6631, 6639}
EVENT_READY_SCENES = frozenset({"BASIC_MEETING", "GROUP_CONFIG_00"})
FIXED_WAIT_OPERATIONS = frozenset({"set_user_permissions"})


def select_wait_mode(requested: str, scene_type: str, operation: str = "") -> str:
    if requested not in {"fixed", "events"}:
        raise ValueError(f"invalid initialization wait mode: {requested}")
    return ("events" if requested == "events"
            and scene_type in EVENT_READY_SCENES
            and operation not in FIXED_WAIT_OPERATIONS else "fixed")


@dataclass(frozen=True)
class InitializationEvidence:
    ready: bool
    ready_at: float | None
    missing: dict[str, list[str]]
    late_events: dict[str, list[int]]


def _members(value: Any) -> set[str]:
    if isinstance(value, dict):
        if "UserID" in value:
            return {str(value["UserID"])}
        return set().union(*(_members(item) for item in value.values())) if value else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(_members(item) for item in value)) if value else set()
    return set()


def inspect_initialization(
    events: Mapping[str, Sequence[Mapping[str, Any]]],
    outbound: Mapping[str, Sequence[Mapping[str, Any]]],
    user_ids: Mapping[str, Any],
    aliases: Sequence[str],
    *,
    not_before: float = 0.0,
) -> InitializationEvidence:
    """Require protocol evidence for every joined client and every peer."""
    missing: dict[str, list[str]] = {}
    ready_times: list[float] = [not_before]
    for alias in aliases:
        received = events.get(alias, ())
        sent = outbound.get(alias, ())
        absent: list[str] = []

        def received_one(cmdid: int, predicate) -> bool:
            for item in received:
                if (item.get("source") == "conf" and item.get("cmdid") == cmdid
                        and predicate(item.get("fields") or {})):
                    ready_times.append(float(item.get("received_monotonic") or 0.0))
                    return True
            return False

        if not received_one(6601, lambda fields: str(fields.get("Result")) == "0"):
            absent.append("6601.Result=0")
        init_commands = [item for item in sent if item.get("source") == "conf"
                         and item.get("cmdid") == 6517]
        if init_commands:
            ready_times.append(float(init_commands[0].get("sent_monotonic") or 0.0))
        else:
            absent.append("sent 6517")
        if not received_one(6631, lambda fields: "RoleEnd" in fields):
            absent.append("6631.RoleEnd")
        if not received_one(6639, lambda fields: "End" in fields):
            absent.append("6639.End")
        for peer in aliases:
            # The server confirms this client's own entry through 6601;
            # its 6602 snapshot contains the other meeting members.
            if peer == alias:
                continue
            expected = user_ids.get(peer)
            if expected is None:
                absent.append(f"{peer}.room_user_id")
            elif not received_one(6602, lambda fields, uid=str(expected):
                                  uid in _members(fields.get("User"))):
                absent.append(f"6602.UserID={expected}")
        if absent:
            missing[alias] = absent

    ready = not missing
    ready_at = max(ready_times) if ready else None
    late: dict[str, list[int]] = {}
    if ready_at is not None:
        for alias in aliases:
            cmdids = [int(item["cmdid"]) for item in events.get(alias, ())
                      if item.get("source") == "conf"
                      and item.get("cmdid") in INITIALIZATION_CMDIDS
                      and float(item.get("received_monotonic") or 0.0) > ready_at]
            if cmdids:
                late[alias] = cmdids
    return InitializationEvidence(ready, ready_at, missing, late)


def wait_for_initialization(
    runtime, aliases: Sequence[str], marks: Mapping[str, int],
    outbound_marks: Mapping[str, Any], *, settle_seconds: float,
    mode: str = "fixed", poll_interval: float = 0.05,
) -> dict[str, Any]:
    """Fixed mode samples the original window; events mode may finish early."""
    if mode not in {"fixed", "events"}:
        raise ValueError(f"invalid initialization wait mode: {mode}")
    started = time.monotonic()
    deadline = started + settle_seconds
    user_ids = {alias: runtime.variables.get(f"{alias}.room_user_id") for alias in aliases}
    while True:
        if mode == "fixed":
            while time.monotonic() < deadline:
                time.sleep(max(0.0, deadline - time.monotonic()))
        evidence = inspect_initialization(
            runtime.snapshot_all_events(marks), runtime.snapshot_all_outbound(outbound_marks),
            user_ids, aliases, not_before=started)
        now = time.monotonic()
        if mode == "fixed" or evidence.ready or now >= deadline:
            return {
                "mode": mode, "wait_seconds": round(now - started, 3),
                "settle_seconds": settle_seconds,
                "ready": evidence.ready, "fallback": not evidence.ready,
                "ready_offset_seconds": (round(evidence.ready_at - started, 3)
                                         if evidence.ready_at is not None else None),
                "missing": evidence.missing, "late_events": evidence.late_events,
            }
        time.sleep(min(poll_interval, max(0.0, deadline - now)))
