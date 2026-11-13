from fm_client.mockserver import MockFmServer

from meeting_test_runtime import ClientProfile, MeetingTestRuntime
from meeting_test_runtime.expectations import OPERATION_EXPECTATIONS
from meeting_test_runtime.fm_driver import FmClientDriver


def test_every_registered_fm_operation_has_assertion_metadata():
    assert set(FmClientDriver.METHOD_NAMES) <= set(OPERATION_EXPECTATIONS)


def test_fm_driver_uses_existing_fmtestclient_api_and_event_cursor():
    server = MockFmServer()
    runtime = MeetingTestRuntime()
    try:
        profile = ClientProfile(
            "FM_01", "fm",
            {"username": "guest1", "password": "1111", "display_name": "guest1"},
            {"host": "127.0.0.1", "port": server.port, "room_node_id": "1",
             "conf_protocol_version": "2.0"},
            frozenset({"operator"}),
        )
        driver = runtime.create_client("operator", profile)
        runtime.join("operator", 100001)
        marks = runtime.mark_all_events()
        outbound_marks = runtime.mark_all_outbound()
        runtime.call("operator", "mic_on", {})
        result = runtime.assert_operation(
            "mic_on", "operator", None,
            {"UserID": "${operator.room_user_id}", "Audio.State": 2}, marks)
        assert "operator:notify:0" in result
        assert driver.native_client.__class__.__name__ == "FmTestClient"
        assert driver.conf_protocol_version == "2.0"
        outbound = runtime.snapshot_all_outbound(outbound_marks)["operator"]
        call = next(item for item in outbound if item["trace_type"] == "public_call")
        command = next(item for item in outbound if item["trace_type"] == "xml_command")
        assert call["operation"] == "mic_on" and call["params"] == {}
        assert command["cmdid"] == 6509 and command["ver"] == "2.0"
        assert command["operation"] == "mic_on"
        assert command["event_id"] in call["sent_command_ids"]
    finally:
        runtime.close_all()
        server.stop()


def test_meeting_number_and_invite_login_use_profile_node_id():
    driver = object.__new__(FmClientDriver)
    driver.profile = ClientProfile(
        "FM_01", "fm", {"username": "guest1", "password": "1111"},
        {"room_node_id": "NID:9", "meeting_no": "123456",
         "invite_code": "987654321"},
    )

    by_number = driver._operation_params("front_login_by_meeting_no", {})
    by_invite = driver._operation_params("front_login_by_invite_code", {})

    assert by_number["meeting_no"] == "123456"
    assert by_number["room_node_id"] == "NID:9"
    assert by_invite["invite_code"] == "987654321"
    assert by_invite["room_node_id"] == "NID:9"


def test_preferred_conf_host_filters_front_addresses():
    driver = object.__new__(FmClientDriver)
    driver.profile = ClientProfile(
        "FM_01", "fm", {}, {"conf_preferred_host": "192.168.6.170"})
    driver.native_client = type("FakeClient", (), {})()
    driver.native_client.conf_servers = [{
        "SrvName": "ConfSrv",
        "SrvAddr": "TCP:192.168.6.171:1089;TCP:192.168.6.170:1089;",
    }]

    driver._prefer_conf_host()

    assert driver.native_client.conf_servers == [{
        "SrvName": "ConfSrv",
        "SrvAddr": "TCP:192.168.6.170:1089;",
    }]
