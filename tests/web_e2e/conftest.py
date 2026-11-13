"""Independent key-flow entrypoint; the existing FM Excel is never modified."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import hashlib

import pytest

from meeting_test_runtime import ClientProfile, MeetingTestRuntime, ServiceProfile, TestEnvironmentConfig, WebApiServiceDriver, EventExpectation
from meeting_test_runtime.drivers import register_client_driver
from meeting_test_runtime.web_client import BrowserWorker
from meeting_test_runtime.web_driver import WebClientDriver, CrossEndpointFmDriver
from meeting_test_runtime.web_key_scenarios import WebKeyRuntime
from meeting_test_runtime.web_key_loader import load_web_key_flows
from meeting_test_runtime.web_full_cases import load_web_full_cases
from meeting_test_runtime.web_full_runtime import WebMeetingFactory
from meeting_test_runtime.execution_scope import recording_exclusion, recording_scope

ROOT=Path(__file__).resolve().parents[2]
DATA=Path(__file__).parent/'data/web_key_flows.xlsx'


def _fingerprint():
    digest=hashlib.sha256()
    for base in (ROOT/'meeting_test_runtime',ROOT/'web_sdk_demo',Path(__file__).parent):
        for path in sorted(base.rglob('*')):
            if path.is_file() and path.suffix in {'.py','.js','.html','.json','.xlsx'}:
                digest.update(str(path.relative_to(ROOT)).encode());digest.update(path.read_bytes())
    digest.update((ROOT/'hst-rtc-sdk-1.8.18.es.unmin.js').read_bytes())
    return digest.hexdigest()


def pytest_sessionstart(session):
    session.config._web_key_results=[]
    session.config._web_key_fingerprint=_fingerprint()


def pytest_sessionfinish(session,exitstatus):
    folder=getattr(session.config.option,'allure_report_dir',None)
    if not folder: return
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    sdk=ROOT/'hst-rtc-sdk-1.8.18.es.unmin.js'
    cfg=TestEnvironmentConfig()
    report={'exitstatus':int(exitstatus),'collected':session.testscollected,
            'fingerprint':session.config._web_key_fingerprint,'source_unchanged':_fingerprint()==session.config._web_key_fingerprint,
            'sdk_sha256':hashlib.sha256(sdk.read_bytes()).hexdigest(),
            'environment':{'ces_addr':cfg.get('web_sdk','ces_addr'),'front_host':cfg.get('fm_server','host'),
                'allowed_conf_hosts':cfg.get('web_sdk','allowed_conf_hosts'),'browser_channel':cfg.get('web_sdk','browser_channel'),
                'recording_enabled':cfg.getbool('recording','enabled',fallback=True),
                'recording_scope':recording_scope(cfg),
                'headless':cfg.getbool('web_sdk','headless',fallback=True)},
            'results':getattr(session.config,'_web_key_results',[])}
    (folder/'web_key_acceptance_manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item,call):
    outcome=yield
    report=outcome.get_result()
    arguments=getattr(getattr(item,'callspec',None),'params',{})
    value=arguments.get('web_flow',{})
    full=arguments.get('web_full_case')
    item.config._web_key_results.append({'nodeid':item.nodeid,'run_id':value.get('run_id'),
        'phase':report.when,'outcome':report.outcome,'duration':report.duration,
        'full_case_id':getattr(full,'case_id',None)})
    if report.failed and report.when in {'setup','call','teardown'}:
        if item.get_closest_marker('p0') or any(term in str(report.longrepr) for term in
                ('conf session closed','0x1004','连接不健康')):
            item.session.shouldstop='Web关键流程安全停止：基础失败或异常连接，先分析证据'


def pytest_generate_tests(metafunc):
    if 'web_full_case' in metafunc.fixturenames:
        config=TestEnvironmentConfig()
        path=Path(__file__).parent/'data/web_e2e_cases.xlsx'
        values=[]
        for case in load_web_full_cases(path):
            marks=[pytest.mark.real_server,getattr(pytest.mark,case.priority.lower())]
            excluded=recording_exclusion(config,web_case=case)
            if excluded:marks.append(pytest.mark.skip(reason=excluded))
            if case.status!='IMPLEMENTED':marks.append(pytest.mark.skip(reason=case.status+': '+case.reason))
            if case.risk=='DESTRUCTIVE_ISOLATED':marks.append(pytest.mark.destructive)
            if case.case_type=='BUSINESS_EXCEPTION':marks.append(pytest.mark.business_exception)
            values.append(pytest.param(case,id=case.case_id,marks=marks))
        metafunc.parametrize('web_full_case',values)
        return
    if 'web_flow' not in metafunc.fixturenames:
        return
    values=[]
    config=TestEnvironmentConfig()
    for case in load_web_key_flows(DATA):
        marks=[pytest.mark.real_server,getattr(pytest.mark,case['priority'].lower())]
        excluded=recording_exclusion(config,web_flow=case)
        if excluded:marks.append(pytest.mark.skip(reason=excluded))
        values.append(pytest.param(case,id=case['run_id'],marks=marks))
    metafunc.parametrize('web_flow',values)


@pytest.fixture(scope='session')
def web_config():
    cfg=TestEnvironmentConfig()
    if not cfg.getbool('web_sdk','run_real_key_flows',env='WEB_RUN_REAL',fallback=False):
        pytest.skip('Web真实关键流程默认关闭；使用WEB_RUN_REAL=1明确启动')
    return cfg


@pytest.fixture(scope='session')
def browser_worker(web_config):
    cfg=web_config
    worker=BrowserWorker(sdk_path=ROOT/cfg.get('web_sdk','sdk_path',fallback='hst-rtc-sdk-1.8.18.es.unmin.js'),
        headless=cfg.getbool('web_sdk','headless',fallback=True),
        ignore_https_errors=cfg.getbool('web_sdk','ignore_https_errors',fallback=False),
        browser_channel=cfg.get('web_sdk','browser_channel',fallback='chrome'))
    register_client_driver('web',lambda profile:WebClientDriver(profile,worker=worker),replace=True)
    register_client_driver('fm',CrossEndpointFmDriver,replace=True)
    from meeting_test_runtime.web_client import WebTestClient
    probe=WebTestClient(ClientProfile('PREFLIGHT','web',{},{}),worker=worker)
    try:
        probe.call('init',{'ces_addr':cfg.get('web_sdk','ces_addr') or cfg.get('http','base_url'),'timeout_ms':20000})
        event=probe.events.wait(EventExpectation(source='web_proxy',cmdid=1519,kind=None),0,10)
        assert event, 'Web未取得实际服务地址映射，禁止建会'
        fronts=[address['url'] for entry in event.fields.get('ServiceAddr',[]) if entry.get('type')=='FRONT'
                for address in entry.get('addresses',[])]
        expected=cfg.get('fm_server','host')
        assert any(f'TCP:{expected}:' in address for address in fronts), f'Web发现的Front与FM环境不一致: {fronts}'
    except BaseException:
        worker.close()
        raise
    finally:
        probe.close()
    yield worker
    register_client_driver('web',WebClientDriver,replace=True)
    from meeting_test_runtime.fm_driver import FmClientDriver
    register_client_driver('fm',FmClientDriver,replace=True)
    worker.close()


@pytest.fixture
def web_runtime(web_flow,web_config,browser_worker,request):
    previous=getattr(request.config,'_web_full_factory',None)
    if previous:previous.close()
    cfg=web_config
    runtime=WebKeyRuntime(event_timeout=cfg.getfloat('execution','event_timeout',fallback=10),
                               quiet_timeout=cfg.getfloat('execution','quiet_timeout',fallback=2))
    service=WebApiServiceDriver(ServiceProfile('WEB_HTTP','web_api',{
        'base_url':cfg.get('http','base_url'),'app_key':cfg.get('http','app_key'),
        'app_secret':cfg.get('http','app_secret'),'timeout':cfg.getfloat('http','timeout',fallback=10),
        'verify_tls':cfg.getbool('http','verify_tls',fallback=False)}))
    runtime.add_service('http_admin',service)
    created=service.native_client.add_room(room_name='web-key-'+web_flow['id'][-15:],verify_mode='3',
        maxUserCount=10,roomMode='standard',enableChairPwd='1',
        chairPassword=cfg.get('protocol','chair_password'),creatorName=cfg.get('http','user_name') or cfg.get('operator','username'))
    room_id=int(created['data']['roomId'])
    node=str(created['data'].get('nodeId') or cfg.get('meeting','room_node_id'))
    runtime.register_cleanup(WebMeetingFactory.verify_deleted,service.native_client,room_id)
    runtime.register_cleanup(service.native_client.delete_room,room_id)
    runtime.register_cleanup(service.native_client.close_meeting,room_id)
    runtime.variables.update({'meeting.room_id':room_id,'env.chair_password':cfg.get('protocol','chair_password')})
    try:
        types=web_flow['combination'].split('->')
        for alias,kind in zip(('operator','observer'),types):
            account={'username':cfg.get(alias,'username'),'password':cfg.get(alias,'password'),
                     'display_name':cfg.get(alias,'display_name',fallback=alias)}
            endpoint=({'host':cfg.get('fm_server','host'),'port':cfg.getint('fm_server','port',fallback=1089),
                'room_node_id':node,'room_id':room_id,'conf_protocol_version':'5.0' if web_flow['scenario']=='remote' else cfg.get_protocol_version(),
                'allowed_conf_hosts':cfg.get_host_list('web_sdk','allowed_conf_hosts',fallback=cfg.get('fm_server','host')),
                'support_features':cfg.get('protocol','support_features')}
                if kind=='fm' else {'ces_addr':cfg.get('web_sdk','ces_addr') or cfg.get('http','base_url'),
                    'room_id':room_id,'room_node_id':node,'event_timeout':runtime.event_timeout,
                    'join_settle_time':cfg.getfloat('web_sdk','join_settle_time',fallback=2),
                    'operation_timeout':cfg.getfloat('web_sdk','operation_timeout',fallback=30)})
            runtime.create_client(alias,ClientProfile('KEY_'+alias,kind,account,endpoint))
        scenario=web_flow['scenario']
        if scenario in {'remote','direct'}:
            configured=service.native_client.update_room_permission(room_id=room_id,room_mode='custom',
                room_permission={'openUserAvRequiresRequest':'true' if scenario=='remote' else 'false',
                                 'broadcastOwnAudio':True,'broadcastOwnVideo':True,'operatePublicChat':True})
            assert str(configured.get('code'))=='0', '远程会议权限前置配置失败'
        elif scenario in {'self_av','mute','chat'}:
            configured=service.native_client.update_room_permission(room_id=room_id,room_mode='custom',
                room_permission={'broadcastOwnAudio':True,'broadcastOwnVideo':True,'operatePublicChat':True})
            assert str(configured.get('code'))=='0', '基础会议权限前置配置失败'
        # 确保角色测试真正从普通身份申请；其他操作前置在场景中显式验证。
        runtime.join('operator',room_id)
        if scenario!='lock':
            runtime.join('observer',room_id)
        yield runtime
    except BaseException:
        from meeting_test_runtime.allure_reporting import attach_json
        attach_json(runtime.snapshot_all_events({}),'前置或流程失败时各端实际事件')
        attach_json({alias:getattr(getattr(driver,'native_client',None),'conf_servers',None)
                     for alias,driver in runtime.clients.items()},'Front实际返回的Conf地址')
        raise
    finally:
        from meeting_test_runtime.allure_reporting import attach_json
        try:
            runtime.close_all()
        finally:
            attach_json(runtime.cleanup_results,'关键流程资源清理逐项结果')


@pytest.fixture(scope='session')
def web_meeting_factory(web_config,browser_worker,request):
    factory=WebMeetingFactory(web_config)
    request.config._web_full_factory=factory
    yield factory
    factory.close()


@pytest.fixture
def web_full_runtime(web_full_case,web_meeting_factory,request):
    runtime=web_meeting_factory.acquire(web_full_case)
    failed=True
    try:
        yield runtime
        failed=getattr(request.node,'web_execution_failed',False)
    finally:
        from meeting_test_runtime.allure_reporting import attach_json
        try:web_meeting_factory.release(web_full_case,runtime,failed=failed)
        finally:attach_json(runtime.cleanup_results,'全量用例资源释放/关闭删除结果')
