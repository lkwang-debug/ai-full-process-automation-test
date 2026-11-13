"""轮询内存配置和有限时间窗口验证；全部经公开configure_polling调用。"""
import time
from .models import EventExpectation
from .allure_reporting import report_step, attach_json


class PollingProbe:
    def __init__(self, runtime):
        self.runtime = runtime
        self.name = 'E2E轮询边界'
        self.users = [runtime.variables[f'{a}.room_user_id'] for a in ('operator', 'observer')]

    def send(self, operation, polling_id, users=None, **fields):
        marks = self.runtime.mark_all_events()
        params = dict(operation=operation, polling_id=polling_id, name=self.name,
                      user_ids=self.users if users is None else users, **fields)
        attach_json(params, 'configure_polling实际参数（Interval毫秒）')
        self.runtime.call('operator', 'configure_polling', params)
        return marks

    def receive(self, marks, polling_id, operation=1, **fields):
        expected = EventExpectation(cmdid=6633, kind='notify', match={
            'VideoPolling.val': polling_id, 'VideoPolling.Operation': operation,
            **{f'VideoPolling.{k}': v for k,v in fields.items()}})
        events = {a: self.runtime.clients[a].wait_event(expected, marks[a], self.runtime.event_timeout)
                  for a in ('operator', 'observer')}
        if operation == 1:
            for event in events.values():
                p = event.fields['VideoPolling']
                assert str(p['CurrentUserID']) in {str(u) for u in self.users}
                assert str(p['NextUserID']) in {str(u) for u in self.users}
        attach_json(events, '各端真实6633')
        assert self.runtime.is_healthy()
        return events

    def quiet(self, marks, ids, duration=2):
        deadline = time.monotonic() + duration
        while True:
            for events in self.runtime.snapshot_all_events(marks).values():
                for event in events:
                    p = event.get('fields', {}).get('VideoPolling', {})
                    if event['cmdid'] == 6633 and str(p.get('val')) in {str(i) for i in ids}:
                        assert str(p.get('Operation')) != '1', f'不应启动轮询: {p}'
            assert self.runtime.is_healthy()
            if time.monotonic() >= deadline: break
            time.sleep(0.1)
        attach_json(self.runtime.snapshot_all_events(marks), '无启动/切换通知的观察证据')

    def sequence(self, marks, polling_id, seconds=3):
        time.sleep(seconds)
        result = {}
        for alias, events in self.runtime.snapshot_all_events(marks).items():
            result[alias] = [e for e in events if e['cmdid'] == 6633
                and str(e['fields'].get('VideoPolling', {}).get('val')) == str(polling_id)
                and str(e['fields']['VideoPolling'].get('Operation')) == '1']
        attach_json(result, f'{seconds}秒有限窗口的轮询时间序列')
        assert self.runtime.is_healthy()
        return result


def execute_polling_boundary(case, runtime):
    from .case_executor import CaseExecutionResult
    probe = PollingProbe(runtime)
    params = runtime.resolve_variables(case.params)
    probe.name = params.get('name','E2E轮询边界')
    pid = params['polling_id']
    runtime.call('operator', 'request_right', {'right_type': 2})
    try:
        if pid == 0:
            batch_polling(probe, params['operation'], interval=params['interval'])
        elif not params['user_ids'] or params['user_ids'] == [999999999]:
            with report_step('新增空/无效成员配置：启动失败但内存配置保留'):
                marks = probe.send(1, pid, params['user_ids'], interval=10, screen=1, window=1, status=2)
                probe.quiet(marks, [pid])
            with report_step('同ID携带完整有效配置修改并启动，验证该配置ID已存在'):
                marks = probe.send(2, pid, interval=10, screen=1, window=1, status=2)
                probe.receive(marks, pid, Interval=10, Screen=1, Window=1)
        elif params['interval'] in (0, -1):
            with report_step('先启动两个有效成员的轮询，首条通知不作为周期证明'):
                marks = probe.send(1, pid, interval=params['interval'], screen=1, window=1, status=2)
                probe.receive(marks, pid, Interval=params['interval'], Screen=1, Window=1)
                sequences = probe.sequence(marks, pid)
                for alias in ('operator', 'observer'):
                    values = sequences[alias]
                    if params['interval'] == 0:
                        assert len(values) >= 3, 'Interval=0应在3秒窗口内发生后续切换'
                        users = [str(e['fields']['VideoPolling']['CurrentUserID']) for e in values]
                        assert len(set(users)) == 2 and any(a != b for a,b in zip(users, users[1:]))
                        attach_json([e['received_monotonic'] for e in values], f'{alias}实际切换时间（秒）')
                    else:
                        assert len(values) == 1, 'Interval=-1有限3秒窗口出现非预期周期切换'
                if params['interval'] == -1:
                    attach_json({'定时器参数毫秒': 4294967295,
                                 '验证限制': '仅验证3秒窗口，不能证明长期等待或回绕行为'}, 'Interval=-1边界说明')
        else:
            with report_step('新增并验证ID或窗口原样下发'):
                marks = probe.send(1, pid, interval=params['interval'], screen=1, window=params['window'], status=2)
                probe.receive(marks, pid, Interval=params['interval'], Window=params['window'])
            if pid == -1:
                with report_step('同一负数ID修改、删除，验证服务端配置定位'):
                    marks = probe.send(2, pid, interval=700, screen=1, window=2, status=2)
                    probe.receive(marks, pid, Interval=700, Window=2)
                    marks = probe.send(3, pid)
                    probe.receive(marks, pid, operation=2)
        assert runtime.is_healthy()
        return CaseExecutionResult(events={})
    finally:
        # 全部场景在独立会议中，删除该会议全部内存轮询再释放操作权。
        runtime.call('operator', 'configure_polling', {'operation': 3, 'polling_id': 0,
                      'name': 'E2E清理全部轮询', 'user_ids': []})
        runtime.call('operator', 'release_right', {'right_type': 2})


def batch_polling(probe, operation, *, interval=800):
    ids = [8101, 8102]
    with report_step('独立会议预置两个不同ID的轮询'):
        for pid in ids:
            marks = probe.send(1, pid, interval=1000, screen=1, window=1, status=2)
            probe.receive(marks, pid, Interval=1000)
    if operation == 2:
        with report_step('ID=0批量修改正间隔并暂停，再恢复核对全部配置'):
            probe.send(2, 0, interval=interval, status=1)
            probe.quiet(probe.runtime.mark_all_events(), ids)
            marks = probe.send(2, 0, status=2)
            for pid in ids: probe.receive(marks, pid, Interval=interval)
        for ignored in (0, -1):
            with report_step(f'批量间隔{ignored}不生效：暂停后恢复仍为{interval}毫秒'):
                probe.send(2, 0, interval=ignored, status=1)
                probe.quiet(probe.runtime.mark_all_events(), ids)
                marks = probe.send(2, 0, status=2)
                for pid in ids: probe.receive(marks, pid, Interval=interval)
    else:
        with report_step('ID=0新增替换全部配置' if operation == 1 else 'ID=0删除全部配置'):
            marks = probe.send(operation, 0, interval=interval, screen=1, window=1, status=2)
            for pid in ids: probe.receive(marks, pid, operation=2)
            if operation == 1: probe.receive(marks, 0, Interval=interval)
        with report_step('修改原ID不能再启动：间接验证旧配置已删除'):
            marks = probe.runtime.mark_all_events()
            for pid in ids:
                probe.send(2, pid, interval=1000, screen=1, window=1, status=2)
            probe.quiet(marks, ids)
            # 正向控制：重新新增旧ID可以启动，排除连接/权限坏掉导致的假无通知。
            marks = probe.send(1, ids[0], interval=1000, screen=1, window=1, status=2)
            probe.receive(marks, ids[0], Interval=1000)
