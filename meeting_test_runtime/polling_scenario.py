"""Polling assertions distinguish active switching from silent paused states."""
from .models import EventExpectation
from .allure_reporting import report_step, attach_json


def assert_ignored(runtime, params, marks):
    """无该ID的轮询通知是观察期证据，不推断不可观察的内部配置。"""
    with report_step('Operation=4不处理：等待期内A/B无对应6633且连接健康'):
        event = EventExpectation(cmdid=6633, kind='notify', match={
            'VideoPolling.val': params['polling_id']})
        for alias in ('operator', 'observer'):
            runtime.clients[alias].expect_no_event(event, marks[alias], runtime.event_timeout)
        assert runtime.is_healthy(), '忽略Operation=4后A/B不应断会'
        attach_json({'观察时长每端秒': runtime.event_timeout,
                     '各端新增事件': runtime.snapshot_all_events(marks),
                     '连接健康': True}, '忽略操作的消息与健康证据')


def assert_quiet(runtime, actor, params, marks):
    expectation = EventExpectation(cmdid=6633, kind='notify', match={
        'VideoPolling.val': params['polling_id'], 'VideoPolling.Operation': 1})
    for alias in ('operator', 'observer'):
        runtime.clients[alias].expect_no_event(expectation, marks[alias], runtime.quiet_timeout)
    assert runtime.is_healthy(), '轮询静默阶段连接不健康'


def resume_inactive_add(runtime, actor, params, marks):
    with report_step('确认非运行态不切换用户，再携带完整配置修改为运行态'):
        assert_quiet(runtime, actor, params, marks)
        marks = runtime.mark_all_events()
        # 非零ID修改为整项替换，必须显式发送配置；不依赖缺字段保留语义。
        values = {
            'operation': 2, 'polling_id': params['polling_id'],
            'name': params['name'], 'user_ids': params['user_ids'],
            'interval': params['interval'], 'screen': params['screen'],
            'window': params['window'], 'status': 2}
        attach_json(values, '正常启动轮询的完整修改参数')
        runtime.call(actor, 'configure_polling', values)
    return marks
