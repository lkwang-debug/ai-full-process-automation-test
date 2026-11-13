from types import SimpleNamespace
import pytest
from meeting_test_runtime.execution_scope import recording_exclusion,recording_dataset_enabled


def cfg(enabled,scope='configured'):
    return SimpleNamespace(getbool=lambda *a,**kw:enabled,get=lambda *a,**kw:scope)


@pytest.mark.parametrize('op',['start_record','stop_record','operate_record','download_meeting_summary','export_meeting_summary','list_record_tasks'])
def test_disabled_recording_blocks_business_before_runtime(op):
    case=SimpleNamespace(operation=op,params={},closure_type='FM_TO_FM',precondition='NONE')
    assert recording_exclusion(cfg(False),fm_case=case).startswith('USER_EXCLUDED')
    assert recording_exclusion(cfg(True),fm_case=case) is None


def test_disabled_recording_blocks_preconditions_flows_and_web_role_recording():
    assert recording_exclusion(cfg(False),fm_case=SimpleNamespace(operation='chat',params={},closure_type='FM_TO_FM',precondition='RECORDING'))
    assert recording_exclusion(cfg(False),fm_flow=SimpleNamespace(flow_id='FLOW-RECORD-RESULT',steps=[]))
    assert recording_exclusion(cfg(False),web_case=SimpleNamespace(scenario='role_then_record'))
    assert recording_exclusion(cfg(False),web_flow={'scenario':'record'})


def test_recording_exclusion_preserves_non_recording_and_never_uses_chat_content():
    assert recording_exclusion(cfg(False),fm_case=SimpleNamespace(operation='chat',params={'text':'start_record'},closure_type='FM_TO_FM',precondition='NONE')) is None
    assert recording_exclusion(cfg(False),fm_case=SimpleNamespace(operation='request_right',params={'right_type':1},closure_type='FM_TO_FM',precondition='NONE')) is None
    assert recording_exclusion(cfg(False),web_case=SimpleNamespace(scenario='whiteboard')) is None


@pytest.mark.parametrize('op',['start_record','stop_record','operate_record','get_record_summary','list_records','list_record_tasks'])
def test_current_user_scope_keeps_controls_and_queries_even_when_ini_disabled(op):
    c=cfg(False,'controls_and_queries')
    assert recording_exclusion(c,fm_case=SimpleNamespace(operation=op)) is None
    assert recording_dataset_enabled(c)
    assert recording_exclusion(c,web_case=SimpleNamespace(scenario='record')) is None
    assert recording_exclusion(c,web_flow={'scenario':'record'}) is None


@pytest.mark.parametrize('op',['download_meeting_summary','export_meeting_summary','export_transcript'])
def test_current_user_scope_excludes_files_before_resources(op):
    assert recording_exclusion(cfg(False,'controls_and_queries'),fm_case=SimpleNamespace(operation=op)).startswith('USER_EXCLUDED')


def test_node_whitelist_accepts_commas_and_semicolons_without_adding_hosts(tmp_path):
    from meeting_test_runtime.config import TestEnvironmentConfig
    path=tmp_path/'environment.ini'
    # Test fixture file uses the public parser in memory; no config secrets involved.
    config=object.__new__(TestEnvironmentConfig)
    import configparser
    config.parser=configparser.ConfigParser()
    config.parser.read_string('[web_sdk]\nallowed_conf_hosts = 192.168.7.185;192.168.7.186, 192.168.7.185;\n')
    assert config.get_host_list('web_sdk','allowed_conf_hosts')==['192.168.7.185','192.168.7.186']
