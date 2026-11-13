import pytest
from meeting_test_runtime.runtime import MeetingTestRuntime
from meeting_test_runtime.session_pool import MeetingSessionPool
from meeting_test_runtime.assertion_contracts import notify_fields,response_fields,validate_response_shape


def test_cleanup_failure_fails_isolated_case():
    runtime=MeetingTestRuntime()
    def broken(): raise RuntimeError('cleanup unavailable')
    runtime.register_cleanup(broken)
    with pytest.raises(RuntimeError,match='清理失败'):
        MeetingSessionPool().release(None,runtime,isolated=True)
    runtime.close_all()  # 资源已尝试清理完，不重复抛历史错误；原始结果仍保留。
    assert runtime.cleanup_results[0]['ok'] is False


def test_subtitle_contract_tracks_actual_input_fields():
    assert notify_fields('send_subtitles', {'content':'abc<&>', 'font_size':32,'font_color':123,'align':2}) == {
        'Data.Content':'abc<&>','Data.FontSize':32,'Data.FontColor':123,'Data.Align':2}


def test_chat_font_contract_tracks_xml_attributes():
    assert notify_fields('chat', {
        'text': '字体<&>', 'dst_user_id': 0, 'color': 0x112233,
        'font': {'Name': '宋体', 'Height': 16, 'Italic': 0},
    }) == {
        'SrcUserID': '${operator.room_user_id}', 'DstUserID': 0,
        'Text': '字体<&>', 'Color': 0x112233,
        'Font.Name': '宋体', 'Font.Height': 16, 'Font.Italic': 0}


def test_layout_fullscreen_contract_preserves_zero_values():
    assert notify_fields('set_window_state', {
        'mode': 3, 'full': 0, 'video_relay_id': 4,
        'full_video_id': 0, 'full_video_media_id': 0,
        'valid_full_mode': 0,
    }) == {
        'Mode': 3, 'Full': 0, 'VideoRelayID': 4,
        'FullVideoID': 0, 'FullVideoMediaID': 0,
        'ValidFullMode': 0}


def test_http_lock_uses_lock_parameter_not_state():
    assert notify_fields('lock_meeting',{'lock':True})=={'Lock':1}
    assert notify_fields('lock_meeting',{'lock':False})=={'Lock':0}


def test_layout_checks_order_media_and_positions():
    fields=notify_fields('set_layout',{'layout_type':4,'user_ids':[11,22]})
    assert fields['Video.0.UserID']==11 and fields['Video.1.UserID']==22
    assert fields['Video.0.MediaID']==0 and fields['Video.1.Pos']==1


def test_group_contract_checks_each_config_not_only_first_name():
    result=response_fields('create_group',{'rooms':[{'Theme':'first','State':1},
        {'Theme':'second','MaxUserCount':16,'EnableJoin':1}]})
    assert result=={'RoomInfo.0.Theme':'first','RoomInfo.0.State':1,
        'RoomInfo.1.Theme':'second','RoomInfo.1.MaxUserCount':16,'RoomInfo.1.EnableJoin':1}


def test_login_success_without_routing_identity_cannot_pass():
    with pytest.raises(AssertionError):validate_response_shape('front_login',{'Result':0})
    with pytest.raises(AssertionError):validate_response_shape('front_authenticate',{'Result':0})


def test_contacts_snapshot_and_raw_contacts_have_distinct_shapes():
    validate_response_shape('contacts_snapshot', {'depts': [], 'local_user': {}, 'offline': []})
    validate_response_shape('conf_get_contacts', {'depts': [], 'local_user': {}, 'offline_users': []})
    with pytest.raises(AssertionError):
        validate_response_shape('contacts_snapshot', {'depts': [], 'local_user': {}, 'offline_users': []})
