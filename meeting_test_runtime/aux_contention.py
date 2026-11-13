"""FM-to-FM VNC contention: wait for the active sharer to finish."""
from fm_client.errcodes import FmError

from .allure_reporting import attach_json, report_step
from .models import EventExpectation


def _state(runtime, marks, user_id, state):
    return {alias: runtime.clients[alias].wait_event(
        EventExpectation(cmdid=6609, kind='notify',
                         match={'UserID': user_id, 'VNCState': state}),
        marks[alias], runtime.event_timeout)
        for alias in ('operator', 'observer')}


def execute_fm_aux_contention(runtime):
    from .case_executor import CaseExecutionResult

    a, b = runtime.clients['operator'], runtime.clients['observer']
    uid_a = runtime.variables['operator.room_user_id']
    uid_b = runtime.variables['observer.room_user_id']
    events = {}
    with report_step('A取得屏幕共享权并发VNC，A/B收到A的6609状态2'):
        acquired = runtime.call('operator', 'request_right', {'right_type': 4})
        assert str(acquired['Result']) == '0'
        assert str(acquired['OccupiedUserID']) == str(uid_a)
        marks = runtime.mark_all_events()
        runtime.call('operator', 'start_aux_stream', {'kind': 'vnc'})
        events['a_started'] = _state(runtime, marks, uid_a, 2)

    with report_step('B争抢失败：6630返回8486、占用者A，A/B无共享者切换'):
        marks = runtime.mark_all_events()
        try:
            result = runtime.call('observer', 'request_right', {'right_type': 4})
        except FmError as error:
            result = {'Result': error.code}
        response = b.wait_event(EventExpectation(cmdid=6630, kind='response',
            match={'Result': 8486, 'Operation': 1, 'RightType': 4,
                   'OccupiedUserID': uid_a}), marks['observer'], runtime.event_timeout)
        assert int(result['Result']) == 8486
        events['b_rejected'] = response
        for alias in ('operator', 'observer'):
            runtime.clients[alias].expect_no_event(EventExpectation(cmdid=6609, kind='notify',
                match={'UserID': uid_a, 'VNCState': 0}), marks[alias], runtime.quiet_timeout)
            runtime.clients[alias].expect_no_event(EventExpectation(cmdid=6609, kind='notify',
                match={'UserID': uid_b, 'VNCState': 2}), marks[alias], runtime.quiet_timeout)
        assert runtime.is_healthy()

    with report_step('A结束并释放共享权，B再申请并发VNC，A/B收到B的6609'):
        marks = runtime.mark_all_events()
        runtime.call('operator', 'stop_aux_stream', {'kind': 'vnc'})
        events['a_stopped'] = _state(runtime, marks, uid_a, 0)
        released = runtime.call('operator', 'release_right', {'right_type': 4})
        assert str(released['Result']) == '0'
        acquired = runtime.call('observer', 'request_right', {'right_type': 4})
        assert str(acquired['Result']) == '0'
        assert str(acquired['OccupiedUserID']) == str(uid_b)
        marks = runtime.mark_all_events()
        runtime.call('observer', 'start_aux_stream', {'kind': 'vnc'})
        events['b_started'] = _state(runtime, marks, uid_b, 2)
        assert runtime.is_healthy()

    with report_step('B停止并释放操作权，确认A/B状态归零和连接健康'):
        marks = runtime.mark_all_events()
        runtime.call('observer', 'stop_aux_stream', {'kind': 'vnc'})
        events['b_stopped'] = _state(runtime, marks, uid_b, 0)
        released = runtime.call('observer', 'release_right', {'right_type': 4})
        assert str(released['Result']) == '0'
        assert runtime.is_healthy()
        attach_json({'busy_result': 8486, 'first_owner': uid_a,
                     'next_owner': uid_b}, '辅流争抢与顺序接替的核心字段')
    return CaseExecutionResult(response={'Result': 0}, events=events)
