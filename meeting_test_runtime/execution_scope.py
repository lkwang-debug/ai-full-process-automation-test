"""Collection-time feature exclusions, before clients or resources are created."""

RECORDING_OPERATIONS = {
    'start_record', 'stop_record', 'pause_record', 'resume_record', 'operate_record',
    'download_meeting_summary', 'export_meeting_summary', 'export_transcript',
    'get_meeting_summary', 'get_record_share', 'get_record_summary',
    'list_record_participants', 'list_record_summaries', 'list_record_tasks',
    'list_records', 'list_room_record_summaries',
}
RECORDING_SCENARIOS = {'record', 'role_then_record'}
FILE_OPERATIONS = {'download_meeting_summary', 'export_meeting_summary', 'export_transcript'}


def recording_scope(config):
    scope=config.get('execution','recording_scope',env='FM_RECORDING_SCOPE',fallback='configured')
    if scope not in ('configured','controls_and_queries'):
        raise ValueError('recording_scope必须为configured或controls_and_queries')
    return scope


def recording_dataset_enabled(config):
    return config.getbool('recording','enabled',fallback=True) or recording_scope(config)=='controls_and_queries'


def recording_exclusion(config, *, fm_case=None, fm_flow=None, web_case=None, web_flow=None):
    if recording_scope(config)=='controls_and_queries':
        required=(fm_case is not None and fm_case.operation in FILE_OPERATIONS)
        if fm_flow is not None:
            required=required or fm_flow.flow_id=='FLOW-RECORD-RESULT' or any(
                step.operation in FILE_OPERATIONS for step in getattr(fm_flow,'steps',()))
        return 'USER_EXCLUDED: 本轮保留录制启停/结果查询，仅下载/导出不执行' if required else None
    if config.getbool('recording', 'enabled', fallback=True):
        return None
    required = False
    if fm_case is not None:
        operation = fm_case.operation
        params = fm_case.params or {}
        required = (operation in RECORDING_OPERATIONS
                    or getattr(fm_case.closure_type, 'value', fm_case.closure_type) == 'HTTP_RESULT'
                    or str(getattr(fm_case, 'precondition', '')).startswith('RECORD')
                    or (operation in {'request_right', 'release_right'} and str(params.get('right_type')) == '10')
                    or (operation == 'set_conf_state' and 'ServerRecordStatus' in params))
    if fm_flow is not None:
        required = required or fm_flow.flow_id == 'FLOW-RECORD-RESULT'
        required = required or any(step.operation in RECORDING_OPERATIONS for step in getattr(fm_flow, 'steps', ()))
    if web_case is not None:
        required = required or web_case.scenario in RECORDING_SCENARIOS
    if web_flow is not None:
        required = required or web_flow.get('scenario') in RECORDING_SCENARIOS
    return 'USER_EXCLUDED: 本轮recording.enabled=false，录制/纪要/下载/导出不执行' if required else None
