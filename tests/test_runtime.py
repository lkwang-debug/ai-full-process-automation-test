from __future__ import annotations

from collections import defaultdict
import threading

import pytest

from meeting_test_runtime.drivers import register_client_driver
from meeting_test_runtime.models import ClientProfile, EventExpectation, SessionKey
from meeting_test_runtime.runtime import MeetingTestRuntime
from meeting_test_runtime.session_pool import MeetingSessionPool


class FakeBus:
    def __init__(self):
        self.drivers = {}

    def add(self, driver):
        self.drivers[driver.profile.config_id] = driver

    def publish(self, operation, sender, params):
        if operation == "mic_on":
            for driver in self.drivers.values():
                driver.push(6609, "notify", {
                    "UserID": sender.profile.config_id, "Audio": {"State": 2}})
            return None
        if operation == "set_window_state":
            for driver in self.drivers.values():
                if driver is not sender:
                    driver.push(6613, "notify", dict(params))
            return None
        if operation == "request_right":
            value = {"Result": 0, "Operation": 1,
                     "RightType": params["right_type"], "OccupiedUserID": ""}
            sender.push(6630, "response", value)
            return value
        raise ValueError(operation)


class FakeDriver:
    client_type = "fake_runtime_test"
    capabilities = set()
    bus = FakeBus()

    def __init__(self, profile):
        self.profile = profile
        self.events = []
        self.closed = False
        self.bus.add(self)

    @property
    def identity(self):
        return {"room_user_id": self.profile.config_id}

    def join(self, room_id):
        return {"room_id": room_id}

    def call(self, operation, params=None):
        return self.bus.publish(operation, self, params or {})

    def mark_events(self):
        return len(self.events)

    def wait_event(self, expectation, after, timeout):
        for event in self.events[after:]:
            if event["cmdid"] == expectation.cmdid and event["kind"] == expectation.kind and all(
                str(_path(event["fields"], path)) == str(expected)
                for path, expected in expectation.match.items()):
                return event
        raise AssertionError((expectation, self.events))

    def expect_no_event(self, expectation, after, timeout):
        for event in self.events[after:]:
            if event["cmdid"] == expectation.cmdid and event["kind"] == expectation.kind and all(
                str(_path(event["fields"], path)) == str(expected)
                for path, expected in expectation.match.items()):
                raise AssertionError(event)

    def push(self, cmdid, kind, fields):
        self.events.append({"source": "conf", "cmdid": cmdid, "kind": kind, "fields": fields})

    def snapshot_events(self, after=0):
        return self.events[after:]

    def reset(self, operation, params=None):
        return None

    def is_healthy(self):
        return not self.closed

    def close(self):
        self.closed = True


def _path(value, path):
    for part in path.split("."):
        value = value.get(part)
    return value


@pytest.fixture(autouse=True)
def register_fake_driver():
    FakeDriver.bus = FakeBus()
    register_client_driver("fake_runtime_test", FakeDriver, replace=True)


def _profile(name):
    return ClientProfile(name, "fake_runtime_test", {}, {}, frozenset())


def _runtime():
    runtime = MeetingTestRuntime(quiet_timeout=0)
    runtime.create_client("operator", _profile("A"))
    runtime.create_client("observer", _profile("B"))
    runtime.join("operator", 10000)
    runtime.join("observer", 10000)
    return runtime


def test_all_participants_receive_6509_style_event():
    runtime = _runtime()
    marks = runtime.mark_all_events()
    runtime.call("operator", "mic_on", {})
    result = runtime.assert_operation(
        "mic_on", "operator", None,
        {"UserID": "${operator.room_user_id}", "Audio.State": 2}, marks)
    assert set(result) == {"operator:notify:0", "observer:notify:0"}


def test_others_only_receive_6513_style_event():
    runtime = _runtime()
    marks = runtime.mark_all_events()
    runtime.call("operator", "set_window_state", {"Mode": 3, "VideoRelayID": 4})
    result = runtime.assert_operation(
        "set_window_state", "operator", None,
        {"Mode": 3, "VideoRelayID": 4}, marks)
    assert set(result) == {"observer:notify:0"}


def test_sender_only_asserts_6530_response():
    runtime = _runtime()
    marks = runtime.mark_all_events()
    runtime.call("operator", "request_right", {"right_type": 1})
    result = runtime.assert_operation(
        "request_right", "operator", None, {}, marks,
        response_match={"Result": 0, "Operation": 1, "RightType": 1,
                        "OccupiedUserID": ""})
    assert result["operator:response"]["fields"]["Operation"] == 1


def test_each_notify_can_target_a_different_receiver():
    runtime = _runtime()
    marks = runtime.mark_all_events()
    runtime.clients["operator"].push(6604, "notify", {"UserID": "B"})
    runtime.clients["observer"].push(6617, "notify", {})

    result = runtime.assert_operation(
        "allow_knock", "operator", "observer", {}, marks,
        notify_overrides=(
            EventExpectation(cmdid=6604, kind="notify", match={"UserID": "B"},
                             receivers=("operator",),
                             forbidden_receivers=("observer",)),
            EventExpectation(cmdid=6617, kind="notify", receivers=("observer",),
                             forbidden_receivers=("operator",)),
        ),
    )

    assert set(result) == {"operator:notify:0", "observer:notify:1"}


def test_target_policy_accepts_notify_local_receiver_without_global_target():
    runtime = _runtime()
    marks = runtime.mark_all_events()
    runtime.clients["observer"].push(6624, "notify", {"RoomID": 70001})

    result = runtime.assert_operation(
        "enter_group", "operator", None, {}, marks,
        notify_overrides=(EventExpectation(
            cmdid=6624, kind="notify", match={"RoomID": 70001},
            receivers=("observer",), forbidden_receivers=("operator",)),),
    )

    assert set(result) == {"observer:notify:0"}


def test_runtime_manages_named_async_public_calls():
    runtime = _runtime()
    release = threading.Event()
    original_call = runtime.call

    def delayed_call(alias, operation, params=None):
        if operation == "delayed":
            assert release.wait(1)
            return {"Result": 0}
        return original_call(alias, operation, params)

    runtime.call = delayed_call
    runtime.start_async_call("login", "observer", "delayed", {})
    state = runtime.async_call_state("login")
    assert state["alias"] == "observer"
    assert not state["done"].is_set()

    release.set()
    assert runtime.wait_async_call("login", 1) == {"Result": 0}


def test_session_pool_reuses_healthy_and_rebuilds_dirty_runtime():
    pool = MeetingSessionPool()
    key = SessionKey("BASIC", "fm->fm", 10000, ("operator", "observer"))
    builds = []

    def builder():
        runtime = _runtime()
        builds.append(runtime)
        return runtime

    first = pool.acquire(key, builder)
    assert pool.acquire(key, builder) is first
    first.dirty = True
    second = pool.acquire(key, builder)
    assert second is not first
    assert len(builds) == 2
    pool.close_all()


def test_session_pool_invalidates_same_room_role_conflicts():
    pool = MeetingSessionPool()
    shared_key = SessionKey("BASIC", "fm->fm", 10000, ("operator", "observer"))
    unrelated_key = SessionKey("BASIC", "fm->fm", 20000, ("operator", "observer"))
    shared = pool.acquire(shared_key, _runtime)
    unrelated = pool.acquire(unrelated_key, _runtime)
    shared_drivers = list(shared.clients.values())
    unrelated_drivers = list(unrelated.clients.values())

    destructive_key = SessionKey("KICK", "fm->fm", 10000, ("operator", "observer"))
    pool.invalidate_conflicts(destructive_key)

    assert all(driver.closed for driver in shared_drivers)
    assert all(not driver.closed for driver in unrelated_drivers)
    pool.close_all()


def test_runtime_closes_clients_before_resource_cleanup():
    runtime = _runtime()
    drivers = list(runtime.clients.values())
    observed = []
    runtime.register_cleanup(
        lambda: observed.append(all(driver.closed for driver in drivers)))

    runtime.close_all()

    assert observed == [True]
    assert runtime.cleanup_results[0]["ok"] is True


def test_cleanup_accepts_close_result4_as_already_absent():
    runtime = _runtime()

    def close_meeting(_room_id):
        return {"code": 0, "data": {"result": 4}}

    runtime.register_cleanup(close_meeting, 10001)
    runtime.close_all()

    assert runtime.cleanup_results[0]["ok"] is True
    assert runtime.cleanup_results[0]["already_absent"] is True
    assert runtime.cleanup_results[0]["result"]["data"]["result"] == 4


def test_cleanup_retries_idempotent_timeout_once():
    runtime = _runtime()
    calls = []

    def close_meeting(_room_id):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("temporary timeout")
        return {"code": 0, "data": {"result": 4}}

    runtime.register_cleanup(close_meeting, 10001)
    runtime.close_all()

    result = runtime.cleanup_results[0]
    assert len(calls) == 2
    assert result["ok"] and result["already_absent"]
    assert result["attempts"] == 2
    assert "temporary timeout" in result["retry_errors"][0]


def test_cleanup_persistent_timeout_still_fails():
    runtime = _runtime()
    calls = []

    def delete_room(_room_id):
        calls.append(1)
        raise TimeoutError("persistent timeout")

    runtime.register_cleanup(delete_room, 10001)
    with pytest.raises(RuntimeError, match="清理失败"):
        runtime.close_all()
    assert len(calls) == 2


def test_fm_state_reset_uses_explicit_inverse_state():
    from meeting_test_runtime.fm_driver import FmClientDriver

    class Native:
        def __init__(self):
            self.calls = []

        def set_audio_state(self, **params):
            self.calls.append(("audio", params))

        def set_video_state(self, **params):
            self.calls.append(("video", params))

    driver = object.__new__(FmClientDriver)
    driver.profile = ClientProfile("fake", "fm", {}, {}, frozenset())
    driver.native_client = Native()
    assert driver.reset("set_audio_state", {
        "state": 0, "dev_id": 0, "user_id": 123}) == (
            "set_audio_state", {"state": 2, "dev_id": 0, "user_id": 123})
    assert driver.reset("set_video_state", {
        "state": 2, "dev_id": 1}) == (
            "set_video_state", {"state": 0, "dev_id": 1})


def test_negative_target_only_assertion_checks_all_connected_clients_without_alias():
    runtime = _runtime()
    marks = runtime.mark_all_events()

    runtime.assert_no_operation_event(
        "kick_user", "operator", None, {"UserID": 999999999}, marks)


def test_positive_target_only_assertion_still_requires_alias():
    runtime = _runtime()
    marks = runtime.mark_all_events()

    with pytest.raises(ValueError, match="target required"):
        runtime.assert_operation(
            "kick_user", "operator", None, {"UserID": 999999999}, marks)
