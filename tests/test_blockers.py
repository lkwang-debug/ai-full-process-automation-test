from meeting_test_runtime.blockers import (
    BlockerKind,
    blocker_for_cmdid,
    blocker_for_operation,
)


def test_development_verified_fm_notifies_are_unblocked():
    for operation in (
        "send_sysmsg", "send_file", "mute_all", "stop_aux_stream",
        "conf_get_room_config",
    ):
        assert blocker_for_operation(operation) is None
    for cmdid in (6514, 6614, 6516, 6616, 6518, 6618, 6534, 6634):
        assert blocker_for_cmdid(cmdid) is None


def test_legacy_meeting_number_protocol_is_explicitly_unsupported():
    by_number = blocker_for_operation("front_login_by_meeting_no")
    lookup = blocker_for_operation("front_getroomid")
    assert by_number is lookup
    assert by_number is not None
    assert by_number.kind is BlockerKind.UNSUPPORTED_PROTOCOL
    assert blocker_for_cmdid(30012) is by_number
    assert blocker_for_cmdid(30013) is by_number


def test_aux_stream_fix_does_not_change_shared_user_state_protocol_metadata():
    assert blocker_for_operation("stop_aux_stream") is None
    assert blocker_for_cmdid(6509) is None
    assert blocker_for_cmdid(6609) is None


def test_polling_delete_notify_contract_is_unblocked():
    assert blocker_for_operation("configure_polling") is None
    assert blocker_for_cmdid(6533) is None
    assert blocker_for_cmdid(6633) is None


def test_http_av_control_is_independent_from_target_consent_contract():
    audio = blocker_for_operation("control_user_audio")
    video = blocker_for_operation("control_user_video")
    assert audio is None  # HTTP直接路径不依赖目标确认；确认协议仍保持缺口。
    assert video is None
    assert blocker_for_cmdid(6542) is None
    assert blocker_for_cmdid(6642) is None
    # Direct Fm 6509/6609 controls remain independently executable.
    assert blocker_for_cmdid(6509) is None
    assert blocker_for_cmdid(6609) is None


def test_http_invites_do_not_misuse_generic_6654_notify():
    users = blocker_for_operation("invite_users")
    contacts = blocker_for_operation("invite_contacts")
    assert users is contacts
    assert users is None  # 0914已有独立PaaS接收与接受入会能力。
    assert blocker_for_cmdid(6654) is None


def test_waiting_list_is_product_unused_and_record_error_is_executable():
    assert blocker_for_cmdid(6637) is None
    blocker = blocker_for_cmdid(6647)
    assert blocker.kind is BlockerKind.UNSUPPORTED_PROTOCOL
    assert not blocker.allow_automatic_execution


def test_new_sdk_does_not_unblock_unfixed_server_features():
    for cmdid in (6540, 6640):
        assert blocker_for_cmdid(cmdid).kind is BlockerKind.UNSUPPORTED_PROTOCOL
    assert blocker_for_cmdid(6641).kind is BlockerKind.UNSUPPORTED_PROTOCOL
    for cmdid in (6555, 6556, 6558, 6559, 6655, 6656, 6657, 6658, 6659):
        assert blocker_for_cmdid(cmdid).kind is BlockerKind.MISSING_TRIGGER_CONTRACT


def test_remote_av_confirmation_and_web_notify_are_unblocked():
    for cmdid in (6542, 6642, 6543, 6643, 6654):
        assert blocker_for_cmdid(cmdid) is None
    for operation in ("request_remote_av", "reply_remote_av",
                      "respond_remote_av", "send_room_message"):
        assert blocker_for_operation(operation) is None


def test_chat_review_approval_protocol_is_unblocked():
    for cmdid in (6529, 6629):
        assert blocker_for_cmdid(cmdid) is None
    for operation in ("review_chat_message", "review_chat_message_and_wait"):
        assert blocker_for_operation(operation) is None


def test_host_right_operations_can_be_retested_in_isolated_room():
    assert blocker_for_operation("apply_host") is None
    assert blocker_for_operation("release_host") is None
    assert blocker_for_cmdid(6511) is None
    assert blocker_for_cmdid(6611) is None


def test_front_logout_can_assert_collected_responses():
    blocker = blocker_for_operation("front_logout")
    assert blocker is None
    assert blocker_for_cmdid(30008) is None
    assert blocker_for_cmdid(30011) is None


def test_http_close_meeting_uses_6607_and_6653_is_retired():
    assert blocker_for_operation("close_meeting") is None
    blocker = blocker_for_cmdid(6653)
    assert blocker is not None
    assert blocker.kind is BlockerKind.UNSUPPORTED_PROTOCOL


def test_av_mix_and_legacy_room_list_are_product_unused():
    for operation in (
            "get_av_mix_params", "set_av_mix_params", "front_roomlist",
            "front_roomlist_classified", "list_rooms"):
        blocker = blocker_for_operation(operation)
        assert blocker is not None
        assert blocker.kind is BlockerKind.UNSUPPORTED_PROTOCOL


def test_offline_users_can_run_with_authorized_meeting_fixture():
    assert blocker_for_operation("conf_get_offline_users") is None
    assert blocker_for_cmdid(6503) is None
    assert blocker_for_cmdid(6603) is None


def test_http_remove_user_uses_confirmed_target_6606_contract():
    assert blocker_for_operation("remove_user") is None
    blocker = blocker_for_cmdid(6652)
    assert blocker is not None
    assert blocker.kind is BlockerKind.UNSUPPORTED_PROTOCOL


def test_terminal_scope_excludes_node_only_and_unimplemented_multicast():
    for cmdid in (6636, 6651):
        blocker = blocker_for_cmdid(cmdid)
        assert blocker is not None
        assert blocker.kind is BlockerKind.UNSUPPORTED_PROTOCOL


def test_obsolete_department_protocol_is_never_automatically_executed():
    blocker = blocker_for_operation("conf_get_dept_info")
    assert blocker is not None
    assert blocker.kind is BlockerKind.UNSUPPORTED_PROTOCOL
    assert blocker_for_cmdid(6528) is blocker
    assert blocker_for_cmdid(6628) is blocker


def test_custom_data_is_reenabled_after_server_health_fix():
    assert blocker_for_operation("set_user_custom_data") is None
    assert blocker_for_cmdid(6572) is None
    assert blocker_for_cmdid(6672) is None


def test_h323_hangup_requires_a_successfully_established_call():
    blocker = blocker_for_operation("conf_hangup")
    assert blocker is None  # 生命周期执行器已构造真实呼叫前置。
    assert blocker_for_cmdid(6743) is None
    assert blocker_for_cmdid(6744) is None
