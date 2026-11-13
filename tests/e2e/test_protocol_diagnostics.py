"""Opt-in isolated probes for blocked protocol contracts."""
import os
import time
from dataclasses import replace
import pytest
from meeting_test_runtime import ExcelCaseLoader, EnvironmentRisk
from meeting_test_runtime.allure_reporting import attach_json, report_step
from pathlib import Path
DATA_FILE = Path(__file__).parent / 'data/fm_e2e_cases.xlsx'

pytestmark = [pytest.mark.real_server,
              pytest.mark.skipif(os.environ.get('FM_DIAGNOSE_PROTOCOLS') != '1',
                                 reason='Explicit protocol diagnostics only')]

def _remaining_boundaries():
    return [c for c in ExcelCaseLoader(DATA_FILE).load_cases(include_disabled=True)
            if c.status.value == 'SKIP_BLOCKED' and
            (c.operation in {'configure_polling', 'set_video_state', 'set_layout',
                             'request_right', 'release_right', 'allow_knock'} or
             (c.operation == 'set_conf_state' and 'mode' in c.params))]


_BOUNDARY_CASES = _remaining_boundaries()


@pytest.mark.parametrize('case', _BOUNDARY_CASES,
                         ids=[case.case_id for case in _BOUNDARY_CASES])
def test_remaining_boundary_observation(case, fm_runtime_factory):
    """只采集真实行为，不将本测试完成算入功能验收通过数。"""
    from meeting_test_runtime.case_executor import FunctionalCaseExecutor
    _, factory = fm_runtime_factory
    runtime = factory(replace(case, isolated=True,
                             environment_risk=EnvironmentRisk.ISOLATED_STATEFUL))
    try:
        FunctionalCaseExecutor()._prepare(case, runtime)
        params = runtime.resolve_variables(case.params)
        marks = runtime.mark_all_events()
        try:
            response = runtime.call(case.actor, case.operation, params)
            error = None
        except Exception as exc:
            response = None
            error = {'type': type(exc).__name__, 'code': getattr(exc, 'code', None), 'message': str(exc)}
        time.sleep(2)
        states = {alias: d.is_healthy() for alias,d in runtime.clients.items()}
        attach_json({'case_id': case.case_id, 'operation': case.operation,
                     'params': params, 'response': response, 'error': error,
                     'health': states, 'events': runtime.snapshot_all_events(marks)},
                    '边界行为观察：不是业务通过结论')
        assert states.get('observer'), '非操作端异常，停止探测'
    finally:
        runtime.close_all()


@pytest.mark.parametrize('change', [
    {'operation': 0}, {'operation': 4}, {'polling_id': -1},
    {'user_ids': []}, {'user_ids': [999999999]}, {'interval': 0},
    {'interval': -1}, {'screen': -1}, {'window': -1}, {'polling_id': 0},
])
def test_polling_invalid_observation(change, fm_runtime_factory):
    """仅采集边界行为，不把诊断脚本完成计作业务异常用例通过。"""
    case = next(c for c in ExcelCaseLoader(DATA_FILE).load_cases(include_disabled=True)
                if c.operation == 'configure_polling')
    _, factory = fm_runtime_factory
    runtime = factory(replace(case, isolated=True,
                             environment_risk=EnvironmentRisk.ISOLATED_STATEFUL))
    try:
        runtime.call('operator', 'request_right', {'right_type': 2})
        params = dict(operation=1, polling_id=7901, name='invalid-single-field',
                      user_ids=[runtime.clients['operator'].identity['room_user_id'],
                                runtime.clients['observer'].identity['room_user_id']],
                      interval=5, screen=1, window=1, status=2)
        params.update(change)
        marks = runtime.mark_all_events()
        runtime.call('operator', 'configure_polling', params)
        time.sleep(3)
        snapshots = runtime.snapshot_all_events(marks)
        attach_json({'parameters': params, 'events': snapshots,
                     'healthy': runtime.is_healthy()}, '异常参数行为观察（非通过证据）')
        print(change, {alias: [(e['cmdid'], e['fields']) for e in events]
                       for alias, events in snapshots.items()})
        assert runtime.is_healthy(), '异常断会，停止本轮探测'
    finally:
        runtime.close_all()


@pytest.mark.parametrize('operation', ['configure_polling', 'enter_group'])
def test_minimal_protocol(operation, fm_runtime_factory):
    case = next(c for c in ExcelCaseLoader(DATA_FILE).load_cases(include_disabled=True)
                if c.operation == operation)
    _, factory = fm_runtime_factory
    runtime = factory(replace(case, isolated=True, environment_risk=EnvironmentRisk.ISOLATED_STATEFUL))
    a, b = runtime.clients['operator'].native_client, runtime.clients['observer'].native_client
    marks = runtime.mark_all_events()
    try:
        right = 2 if operation == 'configure_polling' else 11
        assert str(a.request_right(right)['Result']) == '0'
        if operation == 'configure_polling':
            for label, op, state in [('add', 1, 2), ('pause', 2, 1),
                                     ('resume', 2, 2), ('stop', 2, 0), ('delete', 3, 0)]:
                with report_step(label):
                    marks = runtime.mark_all_events()
                    a.configure_polling(op, 7001, 'diagnostic-polling',
                                        [a.room_user_id_int, b.room_user_id_int],
                                        interval=5, screen=1, window=1, status=state)
                    time.sleep(6)
                    snapshots = runtime.snapshot_all_events(marks)
                    attach_json(snapshots, label)
                    print(label, {alias:[(e['cmdid'],e['fields']) for e in events]
                                  for alias,events in snapshots.items()})
                    assert runtime.is_healthy()
                    if label == 'add':
                        assert any(e['cmdid'] == 6633 for e in snapshots['observer']), 'No initial polling notify'
        else:
            created = a.create_group([{'Theme': 'diagnostic-group'}])
            room = created['RoomInfo'][0]['RoomID']
            attach_json(created, 'created group')
            marks = runtime.mark_all_events()
            a.enter_group(room, [{'ID': b.room_user_id_int, 'Right': 2}])
            event = b.events.wait_event(after=marks['observer'], cmdid=6624,
                                       kind='notify', match={'RoomID': room}, timeout=10)
            attach_json(runtime.snapshot_all_events(marks), 'group invitation')
            assert event is not None
            assert runtime.is_healthy()
            child = b.connect_related_room(dict(event.fields), timeout=10)
            try:
                marks = runtime.mark_all_events()
                child_mark = child.events.mark()
                a.bring_to_main([b.room_user_id_int])
                returned = b.events.wait_event(after=marks['observer'], cmdid=6626,
                                              kind='notify', timeout=10)
                attach_json(child.events.snapshot_received(after=child_mark), 'child return messages')
                attach_json(runtime.snapshot_all_events(marks), 'main return messages')
                assert returned is not None
            finally:
                child.close()
            assert a.change_group_state([room], 2) is not None
            assert str(a.release_right(right)['Result']) == '0'
    finally:
        attach_json(runtime.snapshot_all_events(marks), 'final diagnostic events')
        runtime.close_all()
