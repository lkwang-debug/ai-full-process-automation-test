from meeting_test_runtime.models import ClientProfile
from meeting_test_runtime.recording_data import RecordingDatasetProvider


class FakeEvents:
    def mark(self): return 0
    def wait_event(self, **kwargs): return {"cmdid": kwargs["cmdid"], "match": kwargs["match"]}


class FakeFmClient:
    instances = []
    def __init__(self, *args, **kwargs):
        self.events = FakeEvents(); self.calls = []; self.conf_servers = []
        self.instances.append(self)
    def front_login(self, *args, **kwargs): self.calls.append(("front_login", args, kwargs))
    def conf_login(self, **kwargs): self.calls.append(("conf_login", kwargs))
    def pause_record(self): self.calls.append(("pause_record",))
    def start_record(self): self.calls.append(("start_record",))
    def stop_record(self): self.calls.append(("stop_record",))
    def close(self): self.calls.append(("close",))


class FakeApi:
    def __init__(self): self.calls = []
    def add_room(self, **kwargs):
        self.calls.append(("create", kwargs)); return {"data": {"roomId": 9001, "nodeId": "2"}}
    def operate_record(self, room_id, state): self.calls.append(("record", room_id, state)); return {"code": 0}
    def close_meeting(self, room_id): self.calls.append(("close", room_id)); return {"code": 0}
    def list_room_record_summaries(self, room_id, **params):
        self.calls.append(("list", room_id))
        return {"data": {"records": [{"roomId": room_id, "groupId": "group-1"}]}}
    def delete_record_summaries(self, ids): self.calls.append(("delete_summary", ids))
    def delete_room(self, room_id): self.calls.append(("delete_room", room_id))


def test_recording_dataset_is_created_once_and_cleaned(monkeypatch):
    monkeypatch.setattr("meeting_test_runtime.recording_data.FmTestClient", FakeFmClient)
    api = FakeApi()
    profile = ClientProfile("FM", "fm", {"username": "operator", "password": "secret"},
                            {"host": "127.0.0.1", "port": 1089, "room_node_id": "1"})
    provider = RecordingDatasetProvider(api, profile, user_name="operator",
                                        capture_seconds=0, ready_timeout=1, poll_interval=0,
                                        join_settle_time=0)
    first = provider.get(); second = provider.get()
    assert first is second
    assert first.room_id == 9001 and first.group_id == "group-1"
    assert first.state_transitions == (2, 0)
    assert ("record", 9001, 2) in api.calls
    assert ("record", 9001, 0) in api.calls
    create_call = next(call for call in api.calls if call[0] == "create")
    assert create_call[1]["meeting_minutes"] is True
    assert ("pause_record",) not in FakeFmClient.instances[-1].calls
    assert ("start_record",) not in FakeFmClient.instances[-1].calls
    assert ("stop_record",) not in FakeFmClient.instances[-1].calls
    provider.cleanup()
    assert ("delete_summary", ["group-1"]) in api.calls
    assert ("delete_room", 9001) in api.calls


def test_find_room_record_handles_nested_api_response():
    value = {"data": {"records": [{"roomId": 1, "groupId": "a"},
                                     {"roomId": 2, "groupId": "b"}]}}
    assert RecordingDatasetProvider._find_room_record(value, 2)["groupId"] == "b"


def test_prepared_dataset_only_reads_and_never_deletes(monkeypatch):
    def forbidden_client(*args, **kwargs):
        raise AssertionError("预置数据不得创建媒体客户端")
    monkeypatch.setattr("meeting_test_runtime.recording_data.FmTestClient", forbidden_client)
    api = FakeApi()
    provider = RecordingDatasetProvider(
        api, ClientProfile("FM", "fm", {}, {}), user_name="operator",
        data_source="prepared", prepared_room_id=10000)
    assert provider.get().data_source == "prepared"
    assert provider.get().state_transitions == ()
    provider.cleanup()
    assert api.calls == [("list", 10000)]


def test_prepared_dataset_rejects_wrong_group():
    import pytest
    from meeting_test_runtime.recording_data import RecordingDataUnavailable
    provider = RecordingDatasetProvider(
        FakeApi(), ClientProfile("FM", "fm", {}, {}), user_name="operator",
        data_source="prepared", prepared_room_id=10000, prepared_group_id="wrong")
    with pytest.raises(RecordingDataUnavailable):
        provider.get()


def test_recording_dataset_falls_back_to_fm_when_http_has_no_notify(monkeypatch):
    class FallbackEvents(FakeEvents):
        def wait_event(self, **kwargs):
            # HTTP control waits three seconds; the Fm fallback waits fifteen.
            return None if kwargs["timeout"] == 3 else {
                "cmdid": kwargs["cmdid"], "match": kwargs["match"]}

    class FallbackFmClient(FakeFmClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.events = FallbackEvents()

    monkeypatch.setattr(
        "meeting_test_runtime.recording_data.FmTestClient", FallbackFmClient)
    api = FakeApi()
    profile = ClientProfile(
        "FM", "fm", {"username": "operator", "password": "secret"},
        {"host": "127.0.0.1", "port": 1089, "room_node_id": "1"})
    provider = RecordingDatasetProvider(
        api, profile, user_name="operator", capture_seconds=0,
        ready_timeout=1, poll_interval=0, join_settle_time=0)

    provider.get()

    calls = FallbackFmClient.instances[-1].calls
    assert ("start_record",) in calls
    assert ("stop_record",) in calls
