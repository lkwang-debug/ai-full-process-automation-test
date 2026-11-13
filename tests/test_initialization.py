import time

from meeting_test_runtime.initialization import (
    inspect_initialization, select_wait_mode, wait_for_initialization,
)


def _evidence(aliases, *, missing=(), delayed=()):
    now = time.monotonic()
    users = {alias: str(1000 + index) for index, alias in enumerate(aliases)}
    events = {}
    outbound = {}
    for alias in aliases:
        values = [
            {"source": "conf", "cmdid": 6639, "fields": {"End": ""},
             "received_monotonic": now - 0.2},
            {"source": "conf", "cmdid": 6631, "fields": {"RoleEnd": ""},
             "received_monotonic": now - 0.3},
            {"source": "conf", "cmdid": 6601, "fields": {"Result": "0"},
             "received_monotonic": now - 0.4},
            {"source": "conf", "cmdid": 6602,
             "fields": {"User": [{"UserID": uid} for peer, uid in users.items()
                                  if peer != alias]},
             "received_monotonic": now - 0.1},
        ]
        if alias in delayed:
            values.append({"source": "conf", "cmdid": 6613, "fields": {},
                           "received_monotonic": now - 0.05})
        events[alias] = [item for item in values if item["cmdid"] not in missing]
        outbound[alias] = [{"source": "conf", "cmdid": 6517,
                            "sent_monotonic": now - 0.15}]
    return events, outbound, users


def test_initialization_accepts_out_of_order_three_client_evidence():
    aliases = ("operator", "observer", "witness")
    events, outbound, users = _evidence(aliases)
    result = inspect_initialization(events, outbound, users, aliases)
    assert result.ready and not result.missing and not result.late_events


def test_initialization_reports_missing_marker_and_late_snapshot():
    aliases = ("operator", "observer")
    events, outbound, users = _evidence(aliases, missing=(6631,))
    result = inspect_initialization(events, outbound, users, aliases)
    assert not result.ready
    assert result.missing["observer"] == ["6631.RoleEnd"]

    events, outbound, users = _evidence(aliases, delayed=("observer",))
    result = inspect_initialization(events, outbound, users, aliases)
    assert result.ready
    assert result.late_events == {"observer": [6613]}


def test_initialization_requires_each_other_client_in_member_list():
    aliases = ("operator", "observer", "witness")
    events, outbound, users = _evidence(aliases)
    events["operator"] = [item for item in events["operator"]
                          if item["cmdid"] != 6602]
    result = inspect_initialization(events, outbound, users, aliases)
    assert not result.ready
    assert result.missing["operator"] == [
        f"6602.UserID={users['observer']}",
        f"6602.UserID={users['witness']}",
    ]


class _Runtime:
    def __init__(self, events, outbound, users):
        self.events = events
        self.outbound = outbound
        self.variables = {f"{alias}.room_user_id": uid for alias, uid in users.items()}

    def snapshot_all_events(self, marks):
        return self.events

    def snapshot_all_outbound(self, marks):
        return self.outbound


def test_wait_falls_back_to_original_window_when_evidence_missing():
    aliases = ("operator", "observer")
    events, outbound, users = _evidence(aliases, missing=(6639,))
    result = wait_for_initialization(
        _Runtime(events, outbound, users), aliases, {}, {},
        settle_seconds=0.04, mode="events", poll_interval=0.005)
    assert not result["ready"] and result["fallback"]
    assert result["wait_seconds"] >= 0.04
    assert "6639.End" in result["missing"]["operator"]


def test_wait_returns_early_when_delayed_event_arrives():
    aliases = ("operator", "observer")
    events, outbound, users = _evidence(aliases, missing=(6639,))
    runtime = _Runtime(events, outbound, users)
    start = time.monotonic()

    def add_delayed_event():
        if time.monotonic() - start > 0.025:
            for alias in aliases:
                if not any(item["cmdid"] == 6639 for item in runtime.events[alias]):
                    runtime.events[alias].append({
                        "source": "conf", "cmdid": 6639, "fields": {"End": ""},
                        "received_monotonic": time.monotonic()})
        return runtime.events

    runtime.snapshot_all_events = lambda marks: add_delayed_event()
    result = wait_for_initialization(
        runtime, aliases, {}, {}, settle_seconds=0.15,
        mode="events", poll_interval=0.005)
    assert result["ready"] and not result["fallback"]
    assert 0.02 <= result["wait_seconds"] < 0.15


def test_fixed_mode_keeps_full_window_even_when_ready():
    aliases = ("operator", "observer")
    events, outbound, users = _evidence(aliases)
    result = wait_for_initialization(
        _Runtime(events, outbound, users), aliases, {}, {},
        settle_seconds=0.04, mode="fixed")
    assert result["ready"] and result["wait_seconds"] >= 0.04


def test_only_baselined_scenes_use_event_readiness():
    assert select_wait_mode("events", "BASIC_MEETING") == "events"
    assert select_wait_mode("events", "GROUP_CONFIG_00") == "events"
    assert select_wait_mode("events", "H323_LIFECYCLE") == "fixed"
    assert select_wait_mode("events", "BASIC_MEETING", "set_user_permissions") == "fixed"
    assert select_wait_mode("fixed", "BASIC_MEETING") == "fixed"
