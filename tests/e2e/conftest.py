"""Real-server fixtures configured by tests/e2e/config/test_env.ini."""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from datetime import datetime, timedelta
import time
import hashlib
import json
import os

import pytest
from fm_client.http_api import HttpApiError
from fm_client.testing import FmTestClient

from meeting_test_runtime import (
    CaseStatus, CaseType, ClientProfile, ClosureType, EnvironmentRisk, ExcelCaseLoader, MeetingSessionPool,
    MeetingTestRuntime, RecordingDataUnavailable, RecordingDatasetProvider, ServiceProfile, TestEnvironmentConfig,
    WebApiServiceDriver, blocker_for_operation,
)
from meeting_test_runtime.initialization import select_wait_mode, wait_for_initialization
from meeting_test_runtime.execution_scope import recording_exclusion, recording_dataset_enabled, recording_scope

DATA_FILE = Path(__file__).parent / "data" / "fm_e2e_cases.xlsx"


def _performance_summary(initialization, case_timings):
    def total(rows, field):
        return round(sum(float(row.get(field) or 0) for row in rows), 3)

    joins = [part for row in initialization for part in row.get("join", {}).values()]
    event_rows = [row for row in initialization if row.get("mode") == "events"]
    return {
        "initialization_count": len(initialization),
        "event_mode_count": len(event_rows),
        "event_ready_count": sum(bool(row.get("ready")) for row in event_rows),
        "event_fallback_count": sum(bool(row.get("fallback")) for row in event_rows),
        "fixed_mode_count": len(initialization) - len(event_rows),
        "late_initialization_count": sum(
            bool(row.get("late_events")) for row in initialization),
        "meeting_creation_seconds": total(initialization, "meeting_creation_seconds"),
        "front_login_seconds": total(joins, "front_seconds"),
        "conf_login_seconds": total(joins, "conf_seconds"),
        "initialization_wait_seconds": total(initialization, "wait_seconds"),
        "excel_case_count": len(case_timings),
        "business_assertion_seconds": (
            total(case_timings, "operation_assertion_seconds") if case_timings else None),
        "cleanup_seconds": total(case_timings, "cleanup_seconds") if case_timings else None,
        "session_recycle_seconds": (
            total(case_timings, "recycle_seconds") if case_timings else None),
    }


def _allure_step_summary(folder: Path):
    totals = {"prepare": 0.0, "business": 0.0, "cleanup": 0.0}
    cases = 0
    for path in folder.glob("*-result.json"):
        result = json.loads(path.read_text(encoding="utf-8"))
        steps = result.get("steps") or []
        if not any(str(step.get("name", "")).startswith("步骤1") for step in steps):
            continue
        cases += 1
        for step in steps:
            name = str(step.get("name", ""))
            stage = ("prepare" if name.startswith("步骤1") else
                     "business" if name.startswith("步骤3") else
                     "cleanup" if name.startswith("步骤5") else None)
            if stage and step.get("start") is not None and step.get("stop") is not None:
                totals[stage] += (step["stop"] - step["start"]) / 1000
    return {"allure_case_count": cases, **{
        f"allure_{stage}_seconds": round(value, 3)
        for stage, value in totals.items()}}


def _create_test_meeting(client, *, name: str, user_name: str) -> tuple[dict, str]:
    """Create an owned room; fall back only when this environment rejects createInstant."""
    try:
        return client.create_instant_meeting(
            meeting_name=name, user_name=user_name, meeting_type=2), "instant"
    except HttpApiError as exc:
        if str(exc.code) != "9999":
            raise
        created = client.add_room(
            room_name=name, verify_mode="3", maxUserCount=10,
            roomMode="standard", creatorName=user_name)
        data = created.get("data") if isinstance(created, dict) else None
        if not isinstance(data, dict) or not data.get("roomId"):
            raise
        fixed = client.make_room_fixed(room_id=int(data["roomId"]))
        if not isinstance(fixed, dict) or str(fixed.get("code", 0)) != "0":
            raise RuntimeError(f"测试会议转固定会议失败: {fixed!r}")
        return created, "fixed_fallback"


def pytest_sessionstart(session):
    """新验收批次绑定代码与用例指纹，不收集INI密码或token。"""
    root=Path(__file__).resolve().parents[2]
    digest=hashlib.sha256()
    for folder in ('meeting_test_runtime','tests/e2e','sessionmanager_py/fm_client','sessionmanager_py/framecore_session'):
        for path in sorted((root/folder).rglob('*.py')):
            if path.name.startswith('._'): continue
            digest.update(str(path.relative_to(root)).encode());digest.update(path.read_bytes())
    digest.update(DATA_FILE.read_bytes())
    session.config._acceptance_fingerprint=digest.hexdigest()
    session.config._acceptance_results=[]
    session.config._initialization_records=[]
    session.config._case_timings=[]
    session.config._consecutive_timeouts=0
    settings=TestEnvironmentConfig()
    session.config._acceptance_environment={
        'front_host':settings.get('fm_server','host'),
        'http_base_url':settings.get('http','base_url'),
        'conf_protocol_version':settings.get_protocol_version(),
        'levels':settings.get('execution','levels'),
        'run_destructive_cases':settings.run_destructive_cases(),
        'run_business_exceptions':settings.getbool('execution','run_business_exceptions',fallback=True),
        'recording_source':settings.get('recording','data_source'),
        'recording_enabled':settings.getbool('recording','enabled',fallback=True),
        'recording_scope':recording_scope(settings),
        'initialization_wait_mode':os.environ.get(
            'FM_INITIALIZATION_WAIT_MODE',
            settings.get('execution','initialization_wait_mode',fallback='fixed')),
    }


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome=yield
    report=outcome.get_result()
    config=item.config
    if not hasattr(config,'_acceptance_results'): return
    case=getattr(item,'callspec',None)
    value=case.params.get('fm_excel_case') if case else None
    config._acceptance_results.append({'nodeid':item.nodeid,'case_id':getattr(value,'case_id',None),
        'phase':report.when,'outcome':report.outcome,'duration':report.duration})
    if report.when=='call':
        detail=str(report.longrepr) if report.failed else ''
        timeout=report.failed and ('TimeoutError' in detail or 'fm event timeout' in detail)
        config._consecutive_timeouts=config._consecutive_timeouts+1 if timeout else 0
        fatal=report.failed and any(s in detail for s in ('conf session closed','0x1004','连接不健康','连接异常'))
        if fatal or config._consecutive_timeouts>=2 or (report.failed and item.get_closest_marker('p0')):
            item.session.shouldstop='验收阶段停止：P0失败、异常连接或连续协议超时；保留证据后分析'


def pytest_sessionfinish(session, exitstatus):
    config=session.config
    results=getattr(config,'_acceptance_results',None)
    dest=getattr(config.option,'allure_report_dir',None)
    if results is not None and dest:
        folder=Path(dest);folder.mkdir(parents=True,exist_ok=True)
        initialization=getattr(config,'_initialization_records',[])
        case_timings=getattr(config,'_case_timings',[])
        performance=_performance_summary(initialization,case_timings)
        performance.update(_allure_step_summary(folder))
        baseline_path=os.environ.get('FM_INIT_BASELINE_MANIFEST')
        if baseline_path and Path(baseline_path).is_file():
            baseline=json.loads(Path(baseline_path).read_text(encoding='utf-8'))
            previous=_performance_summary(
                baseline.get('initialization',[]),baseline.get('case_timings',[]))
            previous.update(_allure_step_summary(Path(baseline_path).parent))
            performance['baseline']=previous
        (folder/'initialization_performance.json').write_text(json.dumps(
            performance,ensure_ascii=False,indent=2),encoding='utf-8')
        properties={key:value for key,value in performance.items()
                    if not isinstance(value,dict)}
        properties.update({f'baseline_{key}':value for key,value in
                           performance.get('baseline',{}).items()})
        (folder/'environment.properties').write_text('\n'.join(
            f'{key}={value}' for key,value in properties.items())+'\n',encoding='utf-8')
        (folder/'acceptance_manifest.json').write_text(json.dumps({
            'fingerprint':config._acceptance_fingerprint,'exitstatus':int(exitstatus),
            'environment':config._acceptance_environment,
            'collected':session.testscollected,'argv':list(config.invocation_params.args),
            'results':results,
            'initialization':initialization,
            'case_timings':case_timings,
        },ensure_ascii=False,indent=2),encoding='utf-8')
def pytest_generate_tests(metafunc):
    if "fm_dependency_flow" in metafunc.fixturenames:
        config = TestEnvironmentConfig()
        flows = ExcelCaseLoader(DATA_FILE).load_dependency_flows() if DATA_FILE.exists() else []
        levels = {x.strip().upper() for x in config.get(
            "execution", "levels", fallback="P0,P1").split(",")}
        flows = [flow for flow in flows if flow.status is CaseStatus.IMPLEMENTED and
                 ("ALL" in levels or flow.priority in levels)]
        if not config.run_destructive_cases():
            flows = [flow for flow in flows
                     if flow.environment_risk is not EnvironmentRisk.DESTRUCTIVE_ISOLATED]
        values = []
        for flow in flows:
            marks = [getattr(pytest.mark, flow.priority.lower()), pytest.mark.fm_to_fm]
            excluded=recording_exclusion(config,fm_flow=flow)
            if excluded:marks.append(pytest.mark.skip(reason=excluded))
            if flow.environment_risk is EnvironmentRisk.ISOLATED_STATEFUL:
                marks.append(pytest.mark.isolated_stateful)
            elif flow.environment_risk is EnvironmentRisk.DESTRUCTIVE_ISOLATED:
                marks.append(pytest.mark.destructive)
            elif flow.environment_risk is EnvironmentRisk.RISKY_MANUAL:
                marks.extend((pytest.mark.risky_protocol_probe, pytest.mark.destructive))
            values.append(pytest.param(flow, marks=marks, id=flow.flow_id))
        metafunc.parametrize("fm_dependency_flow", values)
        return
    if "fm_excel_case" not in metafunc.fixturenames:
        return
    config = TestEnvironmentConfig()
    run_risky = config.getbool(
        "execution", "run_risky_protocol_probes", fallback=False)
    cases = (ExcelCaseLoader(DATA_FILE).load_cases(include_disabled=True)
             if DATA_FILE.exists() else [])
    # 已确认阻塞也必须出现在报告中，不能因Excel禁用而无声消失。
    cases = [case for case in cases if case.enabled or case.status is CaseStatus.SKIP_BLOCKED]
    if run_risky:
        cases = [case for case in cases if case.enabled or (
            blocker_for_operation(case.operation) is not None and
            blocker_for_operation(case.operation).kind.value ==
            "RISKY_PROTOCOL_PROBE")]
    levels = {x.strip().upper() for x in config.get("execution", "levels", fallback="P0,P1").split(",")}
    if "ALL" not in levels:
        cases = [case for case in cases if case.priority.upper() in levels]
    if not config.getbool("execution", "run_business_exceptions", fallback=True):
        cases = [case for case in cases if case.case_type is not CaseType.BUSINESS_EXCEPTION]
    if not config.run_destructive_cases():
        cases = [case for case in cases
                 if case.environment_risk is not EnvironmentRisk.DESTRUCTIVE_ISOLATED]
    values = []
    for case in cases:
        marks = [getattr(pytest.mark, case.priority.lower())]
        excluded=recording_exclusion(config,fm_case=case)
        if excluded:marks.append(pytest.mark.skip(reason=excluded))
        if case.status is CaseStatus.SKIP_BLOCKED and not case.enabled:
            marks.append(pytest.mark.blocked)
            marks.append(pytest.mark.skip(
                reason=f"SKIP_BLOCKED: {case.blocker_id or case.operation}"))
        marks.append(getattr(pytest.mark, case.closure_type.value.lower()))
        if case.case_type is CaseType.BUSINESS_EXCEPTION:
            marks.append(pytest.mark.business_exception)
        if case.environment_risk is EnvironmentRisk.ISOLATED_STATEFUL:
            marks.append(pytest.mark.isolated_stateful)
        if case.environment_risk is EnvironmentRisk.DESTRUCTIVE_ISOLATED:
            marks.append(pytest.mark.destructive)
        blocker = blocker_for_operation(case.operation)
        if blocker is not None:
            marks.append(pytest.mark.blocked)
            if blocker.kind.value == "MISSING_SERVER_HANDLER":
                marks.append(pytest.mark.missing_server_handler)
            if blocker.kind.value == "RISKY_PROTOCOL_PROBE":
                marks.extend((pytest.mark.risky_protocol_probe, pytest.mark.destructive))
            if not (run_risky and blocker.kind.value == "RISKY_PROTOCOL_PROBE"):
                marks.append(pytest.mark.skip(
                    reason=f"{blocker.kind.value}: {blocker.reason}"))
        values.append(pytest.param(case, marks=marks, id=case.case_id))
    metafunc.parametrize("fm_excel_case", values)


@pytest.fixture(scope="session")
def test_config():
    return TestEnvironmentConfig()


@pytest.fixture(scope="session")
def meeting_session_pool():
    pool = MeetingSessionPool()
    yield pool
    pool.close_all()


@pytest.fixture(scope="session")
def fm_profiles(test_config, request):
    cfg = test_config
    auto_shared = cfg.getbool(
        "execution", "auto_create_shared_meeting", fallback=True)
    def needs_shared_runtime(item):
        params = getattr(getattr(item, "callspec", None), "params", {})
        case = params.get("fm_excel_case")
        if case is None:
            return False
        lifecycle = (
            case.operation == "entrance_get_config" or
            case.operation.startswith("front_") or
            case.operation == "conf_login" or
            case.closure_type in {ClosureType.HTTP_TO_FM_JOIN,
                                  ClosureType.HTTP_RESULT})
        return not (case.isolated or lifecycle or
                    case.environment_risk in {
                        EnvironmentRisk.ISOLATED_STATEFUL,
                        EnvironmentRisk.DESTRUCTIVE_ISOLATED})
    # A targeted run containing only isolated cases must not create an
    # unrelated shared instant meeting before the cases own their resources.
    if request.session.items and not any(
            needs_shared_runtime(item) for item in request.session.items):
        auto_shared = False
    # 纯会后预置查询不需要建会；避免查询测试触发任何会议控制。
    if cfg.get("recording", "data_source", fallback="dynamic") == "prepared":
        def prepared_result_item(item):
            params = getattr(getattr(item, "callspec", None), "params", {})
            case = params.get("fm_excel_case")
            flow = params.get("fm_dependency_flow")
            return ((case is not None and case.closure_type is ClosureType.HTTP_RESULT)
                    or (flow is not None and flow.flow_id == "FLOW-RECORD-RESULT"))
        if request.session.items and all(prepared_result_item(i) for i in request.session.items):
            auto_shared = False
    required = [
        ("fm_server", "host"), ("meeting", "room_node_id"),
        ("operator", "username"), ("operator", "password"),
        ("observer", "username"), ("observer", "password"),
    ]
    if not auto_shared:
        required.append(("meeting", "room_id"))
    missing = cfg.missing(required)
    if missing:
        pytest.skip("真实FM环境未配置: " + ", ".join(missing))
    endpoint = {
        "paas_bootstrap_file": cfg.get('paas_online', 'bootstrap_file', fallback='sessionmanager_py/examples/paas_bootstrap.json'),
        "paas_invitation_timeout": cfg.getfloat('paas_online', 'invitation_timeout', fallback=30),
        "host": cfg.get("fm_server", "host", env="FM_HOST"),
        "port": cfg.getint("fm_server", "port", env="FM_PORT", fallback=1089),
        "request_timeout": cfg.getfloat("fm_server", "request_timeout", env="FM_REQUEST_TIMEOUT", fallback=10),
        "login_retries": cfg.getint("execution", "login_retries", fallback=3),
        "login_retry_interval": cfg.getfloat(
            "execution", "login_retry_interval", fallback=2),
            "conf_protocol_version": cfg.get_protocol_version(),
            "conf_preferred_host": cfg.get(
                "fm_server", "conf_preferred_host", fallback=""),
        "support_features": [item.strip() for item in cfg.get(
            "protocol", "support_features", fallback="ChatRecall").split(",")
                             if item.strip()],
        "room_id": cfg.getint(
            "meeting", "room_id", env="FM_ROOM_ID", fallback=0),
        "room_node_id": cfg.get("meeting", "room_node_id", env="FM_ROOM_NODE_ID"),
        "meeting_no": cfg.get("meeting", "meeting_no", env="FM_MEETING_NO"),
        "invite_code": cfg.get("meeting", "invite_code", env="FM_INVITE_CODE"),
    }
    room_password = cfg.get("meeting", "room_password", env="FM_ROOM_PASSWORD") or None
    shared_service = None
    if auto_shared:
        missing_http = cfg.missing([
            ("http", "base_url"), ("http", "app_key"), ("http", "app_secret")])
        if missing_http:
            pytest.skip("自动创建共享会议缺少HTTP配置: " + ", ".join(missing_http))
        shared_service = WebApiServiceDriver(ServiceProfile(
            "HTTP_SHARED_MEETING", "web_api", {
                "base_url": cfg.get("http", "base_url", env="FM_HTTP_BASE_URL"),
                "app_key": cfg.get("http", "app_key", env="FM_HTTP_APP_KEY"),
                "app_secret": cfg.get("http", "app_secret", env="FM_HTTP_APP_SECRET"),
                "timeout": cfg.getfloat("http", "timeout", fallback=10),
                "verify_tls": cfg.getbool("http", "verify_tls", fallback=False),
            }))
        created, shared_resource_type = _create_test_meeting(
            shared_service.native_client,
            name=f"fm-e2e-shared-{int(time.time())}",
            user_name=(cfg.get("http", "user_name", env="FM_HTTP_USER") or
                       cfg.get("operator", "username", env="FM_OPERATOR_USER")))
        data = created.get("data") if isinstance(created, dict) else None
        if not isinstance(data, dict) or not data.get("roomId"):
            pytest.fail(f"自动创建共享会议未返回roomId: {created!r}")
        endpoint.update({
            "room_id": int(data["roomId"]),
            "room_node_id": str(data.get("nodeId") or endpoint["room_node_id"]),
            "meeting_no": data.get("meetingNo") or data.get("meetingNumber") or "",
            "invite_code": data.get("inviteCode") or "",
            "test_resource_type": shared_resource_type,
        })
    profiles = {
        "operator": ClientProfile("FM_01", "fm", {
            "username": cfg.get("operator", "username", env="FM_OPERATOR_USER"),
            "password": cfg.get("operator", "password", env="FM_OPERATOR_PASSWORD"),
            "display_name": cfg.get("operator", "display_name", fallback="operator"),
            "room_password": room_password,
        }, endpoint, frozenset({"operator"})),
        "observer": ClientProfile("FM_02", "fm", {
            "username": cfg.get("observer", "username", env="FM_OBSERVER_USER"),
            "password": cfg.get("observer", "password", env="FM_OBSERVER_PASSWORD"),
            "display_name": cfg.get("observer", "display_name", fallback="observer"),
            "room_password": room_password,
        }, endpoint, frozenset({"observer", "target"})),
    }
    witness_user = cfg.get("invitee", "username", env="FM_WITNESS_USER")
    witness_password = cfg.get("invitee", "password", env="FM_WITNESS_PASSWORD")
    if witness_user and witness_password:
        profiles["witness"] = ClientProfile("FM_03", "fm", {
            "username": witness_user,
            "password": witness_password,
            "display_name": cfg.get("invitee", "display_name", fallback="witness"),
            "room_password": room_password,
        }, endpoint, frozenset({"witness"}))
    yield profiles
    if shared_service is not None:
        room_id = endpoint["room_id"]
        try:
            shared_service.native_client.close_meeting(room_id)
        except Exception:
            pass
        try:
            shared_service.native_client.delete_room(room_id)
        except Exception:
            pass
        shared_service.close()


@pytest.fixture(scope="session")
def recording_dataset_provider(test_config, fm_profiles):
    cfg = test_config
    if not recording_dataset_enabled(cfg):
        yield None
        return
    if cfg.missing([("http", "base_url"), ("http", "app_key"), ("http", "app_secret")]):
        yield None
        return
    service = WebApiServiceDriver(ServiceProfile("HTTP_RECORDING", "web_api", {
        "base_url": cfg.get("http", "base_url", env="FM_HTTP_BASE_URL"),
        "app_key": cfg.get("http", "app_key", env="FM_HTTP_APP_KEY"),
        "app_secret": cfg.get("http", "app_secret", env="FM_HTTP_APP_SECRET"),
        "timeout": cfg.getfloat("http", "timeout", env="FM_HTTP_TIMEOUT", fallback=10),
        "verify_tls": cfg.getbool("http", "verify_tls", env="FM_HTTP_VERIFY_TLS", fallback=False),
    }))
    provider = RecordingDatasetProvider(
        service.native_client, fm_profiles["operator"],
        user_name=cfg.get("http", "user_name", env="FM_HTTP_USER") or
                  cfg.get("operator", "username", env="FM_OPERATOR_USER"),
        capture_seconds=cfg.getfloat("recording", "capture_seconds", fallback=3),
        ready_timeout=cfg.getfloat("recording", "ready_timeout", fallback=180),
        poll_interval=cfg.getfloat("recording", "poll_interval", fallback=3),
        join_settle_time=cfg.getfloat(
            "execution", "join_settle_time", fallback=2),
        data_source=cfg.get("recording", "data_source", fallback="dynamic"),
        prepared_room_id=cfg.getint("recording", "room_id", fallback=0),
        prepared_group_id=cfg.get("recording", "group_id", fallback=""))
    yield provider
    if cfg.getbool("recording", "cleanup", fallback=True):
        provider.cleanup()


@pytest.fixture
def fm_runtime_factory(fm_profiles, test_config, recording_dataset_provider, request):
    cfg = test_config
    room_id = int(fm_profiles["operator"].endpoint["room_id"])

    def _build(case, runtime):
        setup_started = time.monotonic()
        meeting_creation_seconds = 0.0
        start = datetime.now() + timedelta(minutes=5)
        end = start + timedelta(hours=1)
        runtime.variables.update({
            "env.meeting_no": cfg.get("meeting", "meeting_no", env="FM_MEETING_NO"),
            "env.invite_code": cfg.get("meeting", "invite_code", env="FM_INVITE_CODE"),
            "env.message_id": "",
            "env.sys_msg_id": cfg.getint("protocol", "sys_msg_id", fallback=6),
            "env.chair_password": (
                cfg.get("protocol", "chair_password") or "FmE2E1234"),
            "env.h323": cfg.get("h323", "sns", env="FM_TEST_H323"),
            "env.invalid_h323": cfg.get("h323", "invalid_sns", fallback="invalid-h323"),
            "env.invitee_user_id": cfg.get("invitee", "user_id", env="FM_INVITEE_USER_ID"),
            "env.invitee_username": cfg.get(
                "invitee", "username", env="FM_INVITEE_USER"),
            "env.http_user": cfg.get("http", "user_name", env="FM_HTTP_USER") or
                             cfg.get("operator", "username", env="FM_OPERATOR_USER"),
            "env.start_time": cfg.get("reservation", "start_time", env="FM_RESERVED_START") or start.strftime("%Y-%m-%d %H:%M:%S"),
            "env.end_time": cfg.get("reservation", "end_time", env="FM_RESERVED_END") or end.strftime("%Y-%m-%d %H:%M:%S"),
            "env.reserved_room_id": cfg.get("reservation", "reserved_room_id", env="FM_RESERVED_ROOM_ID"),
            "unique.name": f"fm-e2e-{case.case_id[-12:]}",
        })
        if case.operation in {"front_login_by_meeting_no", "front_getroomid"} and not cfg.get("meeting", "meeting_no"):
            pytest.skip("未配置meeting.meeting_no，跳过按会议号入会用例")

        if case.operation in {"conf_invite_call", "invite_terminals", "invite_terminals_http",
                              "conf_hangup", "conf_query_call_list"}:
            if not cfg.getbool("h323", "enabled", fallback=True):
                pytest.skip("H.323测试已禁用")
            if case.operation in {"conf_invite_call", "invite_terminals", "invite_terminals_http"} and not runtime.variables["env.h323"]:
                pytest.skip("H.323 SNS未配置: h323.sns")

        roomless_operations = {
            "entrance_get_config", "front_authenticate", "front_get_auth_type",
            "front_discover_node", "front_roomlist", "front_roomlist_classified",
            "front_getroomid", "list_rooms", "create_instant_meeting",
            "create_reserved_meeting", "cancel_reserved_meeting",
        }
        uses_special_join_resource = case.operation in {
            "front_login_by_invite_code", "front_login_by_meeting_no",
        }
        needs_managed_room = (
            case.closure_type != ClosureType.HTTP_RESULT and
            case.operation not in roomless_operations and
            not uses_special_join_resource)
        needs_http = (case.closure_type.value.startswith("HTTP_") or
                      needs_managed_room or
                      case.isolated or
                      case.environment_risk is EnvironmentRisk.ISOLATED_STATEFUL or
                      case.environment_risk is EnvironmentRisk.DESTRUCTIVE_ISOLATED or
                      case.operation in {
            "invite_users", "invite_contacts", "lock_layout", "create_instant_meeting",
            "create_reserved_meeting", "cancel_reserved_meeting", "front_login_by_invite_code",
        })
        service = None
        if needs_http:
            if not cfg.getbool("http", "enabled", fallback=True):
                pytest.skip("HTTP辅助测试已禁用")
            missing_http = cfg.missing([("http", "base_url"), ("http", "app_key"), ("http", "app_secret")])
            if missing_http:
                pytest.skip("HTTP环境未配置: " + ", ".join(missing_http))
            service = WebApiServiceDriver(ServiceProfile("HTTP_01", "web_api", {
                "base_url": cfg.get("http", "base_url", env="FM_HTTP_BASE_URL"),
                "app_key": cfg.get("http", "app_key", env="FM_HTTP_APP_KEY"),
                "app_secret": cfg.get("http", "app_secret", env="FM_HTTP_APP_SECRET"),
                "timeout": cfg.getfloat("http", "timeout", env="FM_HTTP_TIMEOUT", fallback=10),
                "verify_tls": cfg.getbool("http", "verify_tls", env="FM_HTTP_VERIFY_TLS", fallback=False),
            }))
            runtime.add_service("http_admin", service)

        active_room_id = room_id
        active_profiles = dict(fm_profiles)
        unjoined_http_create_flow = (
            case.scene_type == "DEPENDENCY_FLOW" and
            case.operation == "create_instant_meeting")
        create_managed_room = needs_managed_room
        if (create_managed_room and
                not unjoined_http_create_flow and
                case.operation not in {"create_instant_meeting",
                                       "create_reserved_meeting",
                                       "cancel_reserved_meeting"}):
            if service is None:
                pytest.skip("破坏性用例必须配置HTTP动态建会能力")
            meeting_started = time.monotonic()
            host_right_case = (
                case.operation in {"apply_host", "release_host"} or (
                    case.operation == "set_role" and
                    case.params.get("role") == "chairman"))
            offline_authorization_case = case.operation in {
                "conf_get_offline_users", "offline_contacts",
                "conf_get_contacts", "contacts_snapshot",
            }
            fixed_interaction_case = case.scene_type in {
                "WEB_NOTIFY_6654", "REMOTE_AV_CONSENT", "RECORD_ERROR_6637",
                "CHAT_REVIEW"}
            if host_right_case or offline_authorization_case or fixed_interaction_case:
                # createInstant does not accept chairPassword/enableChairPwd.
                # Host-right tests therefore own a fixed room explicitly created
                # with the chair-password option enabled.
                created = service.native_client.add_room(
                    room_name=(
                        f"fm-e2e-host-{case.case_id[-12:]}"
                        if host_right_case else
                        f"fm-e2e-interaction-{case.case_id[-12:]}"
                        if fixed_interaction_case else
                        f"fm-e2e-contacts-{case.case_id[-12:]}"),
                    verify_mode="3",
                    maxUserCount=10,
                    roomMode="standard",
                    enableChairPwd="1" if host_right_case else "0",
                    **({"chairPassword": runtime.variables["env.chair_password"]}
                       if host_right_case else {}),
                    creatorName=runtime.variables["env.http_user"],
                )
            else:
                created, resource_type = _create_test_meeting(
                    service.native_client,
                    name=f"fm-e2e-isolated-{case.case_id[-12:]}",
                    user_name=runtime.variables["env.http_user"])
            meeting_creation_seconds = time.monotonic() - meeting_started
            data = created.get("data") if isinstance(created, dict) else None
            if not isinstance(data, dict) or not data.get("roomId"):
                pytest.fail(f"破坏性用例动态建会未返回roomId: {created!r}")
            active_room_id = int(data["roomId"])
            if fixed_interaction_case:
                fixed = service.native_client.make_room_fixed(
                    room_id=active_room_id)
                if (not isinstance(fixed, dict) or
                        str(fixed.get("code", 0)) != "0"):
                    pytest.fail(f"交互协议临时会议转固定会议失败: {fixed!r}")
            if case.scene_type == "REMOTE_AV_CONSENT":
                permission = service.native_client.update_room_permission(
                    room_id=active_room_id, room_mode="custom",
                    room_permission={
                        "openUserAvRequiresRequest": "true",
                        "broadcastOwnAudio": True,
                        "broadcastOwnVideo": True,
                    })
                if (not isinstance(permission, dict) or
                        str(permission.get("code", 0)) not in {"0", "0000"}):
                    pytest.fail(
                        f"远程音视频确认模式配置失败: {permission!r}")
            if case.scene_type in {
                    "REMOTE_AV_CONSENT", "RECORD_ERROR_6637", "CHAT_REVIEW"}:
                try:
                    authorized = service.native_client.authorize_room_users(
                        room_id=active_room_id,
                        room_user_str=(
                            f"{fm_profiles['operator'].account['username']},3"),
                        auth_type="1")
                except HttpApiError as exc:
                    if "404 Not Found" not in str(exc):
                        raise
                    time.sleep(1.0)
                    authorized = service.native_client.authorize_room_users(
                        room_id=active_room_id,
                        room_user_str=(
                            f"{fm_profiles['operator'].account['username']},3"),
                        auth_type="1")
                if (not isinstance(authorized, dict) or
                        str(authorized.get("code", 0)) not in {"0", "0000"}):
                    pytest.fail(f"交互协议管理员前置授权失败: {authorized!r}")
            if case.scene_type == "RECORD_ERROR_6637":
                account = fm_profiles["operator"].account
                endpoint = fm_profiles["operator"].endpoint
                identity = FmTestClient(
                    endpoint["host"], int(endpoint["port"]),
                    request_timeout=float(endpoint.get("request_timeout", 10)))
                try:
                    platform_user_id = int(identity.front_authenticate(
                        account["username"], account["password"])["LocalUserID"])
                finally:
                    identity.close()
                record_settings = {
                    "standard": False, "auto_record": False,
                    "record_video_resolution": 2, "record_mode": 4,
                    "data_display_mode": 3, "video_display_mode": 2,
                    "slice_interval": 60, "record_file_slice": True,
                    "end_record_interval": 60, "watermark_mode": 0,
                    "watermark_switch": [False, False, False],
                    "watermark_text": "E2E",
                    "watermark_text_css": json.dumps({
                        "color": "#ffffff", "opacity": 100, "size": 14,
                        "location": 3, "mode": 1}),
                    "timestamp_info_location": 3,
                    "watermark_picture": "", "picture_download_url": "",
                    "watermark_picture_css": json.dumps({
                        "location": 3, "mode": 1, "opacity": 100}),
                    "auto_audit": False, "record_layout_mode": 1,
                    "record_users": [platform_user_id],
                    "record_layout_code": [],
                }
                configured = service.native_client.edit_record_settings(
                    room_id=active_room_id, **record_settings)
                if (not isinstance(configured, dict) or
                        str(configured.get("code", 0)) not in {"0", "0000"}):
                    pytest.fail(f"6637录制模式前置配置失败: {configured!r}")
                runtime.variables["recording.platform_user_id"] = platform_user_id
            node_id = str(data.get("nodeId") or fm_profiles["operator"].endpoint.get("room_node_id") or "1")
            runtime.variables.update({
                "meeting.room_id": active_room_id,
                "meeting.node_id": node_id,
                "meeting.meeting_no": data.get("meetingNo") or data.get("meetingNumber") or "",
                "meeting.invite_code": data.get("inviteCode") or "",
                "meeting.resource_type": (
                    "fixed" if host_right_case or offline_authorization_case
                    or fixed_interaction_case else resource_type),
            })
            active_profiles = {
                alias: replace(profile, endpoint={**profile.endpoint,
                                                  "room_id": active_room_id,
                                                  "room_node_id": node_id})
                for alias, profile in fm_profiles.items()
            }
            runtime.register_cleanup(
                service.native_client.delete_room, active_room_id)
            runtime.register_cleanup(
                service.native_client.close_meeting, active_room_id)
            if case.operation in {
                    "conf_get_offline_users", "offline_contacts",
                    "conf_get_contacts", "contacts_snapshot"}:
                offline_username = runtime.variables["env.invitee_username"]
                if not offline_username:
                    pytest.skip(
                        "6503/6603需要配置invitee.username作为未入会授权用户")
                auth_response = service.native_client.authorize_room_users(
                    room_id=active_room_id,
                    room_user_str=f"{offline_username},2",
                    auth_type="1",
                )
                auth_code = auth_response.get("code") if isinstance(
                    auth_response, dict) else None
                if str(auth_code) not in {"0", "0000"}:
                    pytest.fail(
                        f"动态会议授权离线成员失败: {auth_response!r}")
                authorized_users = service.native_client.list_authorized_users(
                    active_room_id)
                runtime.variables["meeting.authorized_offline_user"] = (
                    offline_username)
                runtime.variables["meeting.authorization_response"] = auth_response
                runtime.variables["meeting.authorized_users"] = authorized_users

        if case.scene_type in {"REMOTE_AV_CONSENT", "CHAT_REVIEW"}:
            interactive_version = cfg.get(
                "protocol", "interactive_conf_protocol_version",
                fallback="5.0")
            active_profiles = {
                alias: replace(profile, endpoint={
                    **profile.endpoint,
                    "conf_protocol_version": interactive_version,
                })
                for alias, profile in active_profiles.items()
            }

        if service is not None and case.operation == "front_login_by_invite_code":
            created = service.native_client.create_instant_meeting(
                meeting_name=f"fm-e2e-invite-{case.case_id[-8:]}",
                user_name=runtime.variables["env.http_user"], meeting_type=2,
                enable_invite=True)
            data = created.get("data") if isinstance(created, dict) else None
            if (not isinstance(data, dict) or not data.get("roomId") or
                    not data.get("inviteCode")):
                pytest.fail(f"动态建会未返回roomId/inviteCode: {created!r}")
            invite_room_id = int(data["roomId"])
            invite_node_id = str(
                data.get("nodeId") or active_profiles["operator"].endpoint.get(
                    "room_node_id") or "1")
            invite_code = str(data["inviteCode"])
            runtime.variables.update({
                "env.invite_code": invite_code,
                "meeting.room_id": invite_room_id,
                "meeting.node_id": invite_node_id,
                "meeting.invite_code": invite_code,
            })
            active_profiles["operator"] = replace(
                active_profiles["operator"], endpoint={
                    **active_profiles["operator"].endpoint,
                    "room_id": invite_room_id,
                    "room_node_id": invite_node_id,
                    "invite_code": invite_code,
                })
            runtime.register_cleanup(
                service.native_client.delete_room, invite_room_id)
            runtime.register_cleanup(
                service.native_client.close_meeting, invite_room_id)

        if case.operation in {"front_login_by_meeting_no", "front_getroomid"}:
            configured_meeting_no = cfg.get(
                "meeting", "meeting_no", env="FM_MEETING_NO")
            configured_node_id = cfg.get(
                "meeting", "room_node_id", env="FM_ROOM_NODE_ID")
            if not configured_meeting_no or not configured_node_id:
                pytest.skip("按会议号入会需要meeting.meeting_no和meeting.room_node_id")
            active_profiles["operator"] = replace(
                active_profiles["operator"], endpoint={
                    **active_profiles["operator"].endpoint,
                    "meeting_no": configured_meeting_no,
                    "room_node_id": configured_node_id,
                })

        runtime.create_client("operator", active_profiles["operator"])
        if service is not None:
            runtime.attach_http("operator", "http_admin")

        if case.closure_type.value == "HTTP_RESULT":
            if recording_dataset_provider is None:
                pytest.skip("动态录制数据未启用或HTTP配置不完整")
            try:
                dataset = recording_dataset_provider.get()
            except RecordingDataUnavailable as exc:
                pytest.skip(f"当前环境未生成录制纪要数据: {exc}")
            runtime.variables["env.record_group_id"] = dataset.group_id
            runtime.variables['recording.expected_title'] = dataset.summary.get('roomName','')
            start_day=datetime.strptime(str(dataset.summary['startTime'])[:10],'%Y-%m-%d')
            runtime.variables['recording.window_begin']=start_day.strftime('%Y-%m-%d 00:00:00')
            runtime.variables['recording.window_end']=(start_day+timedelta(days=1)).strftime('%Y-%m-%d 00:00:00')
            runtime.variables["operator.room_id"] = dataset.room_id
            from meeting_test_runtime.http_result_assertions import SUMMARY_FILE_OPERATIONS,load_export_reference
            if case.operation in SUMMARY_FILE_OPERATIONS:
                from meeting_test_runtime.allure_reporting import attach_json,report_step
                import hashlib
                api=service.native_client;reference_mark=api.mark_requests()
                with report_step('建立同一录制记录的独立文件内容基准'):
                    try:
                        reference=load_export_reference(api,case.operation,dataset.group_id,dataset.room_id)
                        runtime.variables['recording.file_reference']=reference
                        attach_json({'group_id':reference.group_id,'room_id':reference.room_id,
                            'reference_method':reference.source_operation,'size_bytes':len(reference.content),
                            'sha256':hashlib.sha256(reference.content).hexdigest(),
                            'assertion':'使用另一公开接口的同记录正文，纪要正文不强制重复会议标题'},'独立纪要内容基准')
                    finally:attach_json(api.snapshot_requests(after=reference_mark),'基准查询实际HTTP请求（敏感字段脱敏）')
            return runtime

        if case.operation == "cancel_reserved_meeting":
            # Cancellation must own its resource. Never use a static room ID
            # from INI because that could disable a real reservation.
            created = service.native_client.create_reserved_meeting(
                room_name=f"fm-e2e-cancel-{case.case_id[-10:]}",
                hope_start_time=runtime.variables["env.start_time"],
                hope_end_time=runtime.variables["env.end_time"])
            reserved_room_id = created.get("roomId") if isinstance(created, dict) else None
            if reserved_room_id is None:
                pytest.fail(f"取消预约前置建会未返回roomId: {created!r}")
            runtime.variables["env.reserved_room_id"] = int(reserved_room_id)
            runtime.register_cleanup(
                service.native_client.cancel_reserved_meeting,
                int(reserved_room_id), hard=True)

        if unjoined_http_create_flow:
            runtime.create_client("observer", active_profiles["observer"])
            return runtime

        operation = case.operation
        if operation in {"entrance_get_config", "front_get_auth_type"}:
            return runtime
        if operation == "conf_login":
            runtime.call("operator", "front_login", {})
            return runtime
        if operation in {"front_roomlist", "front_roomlist_classified", "list_rooms",
                         "front_discover_node", "front_getroomid"}:
            runtime.call("operator", "front_authenticate", {})
            if operation == "front_getroomid":
                probe = runtime.call("operator", "front_getroomid", {
                    "login_room_id": runtime.variables["env.meeting_no"]})
                if not int(probe.get("RoomId") or 0):
                    pytest.skip(
                        "SKIP_ENV: meeting.meeting_no在当前Front返回RoomId=0，"
                        "请提供可解析的有效会议号")
            return runtime
        if operation == "front_logout":
            runtime.call("operator", "front_login", {})
            return runtime
        if operation.startswith("front_"):
            if operation == "front_login_by_meeting_no":
                runtime.call("operator", "front_authenticate", {})
                probe = runtime.call("operator", "front_getroomid", {
                    "login_room_id": runtime.variables["env.meeting_no"]})
                if not int(probe.get("RoomId") or 0):
                    pytest.skip(
                        "SKIP_ENV: meeting.meeting_no在当前Front返回RoomId=0，"
                        "请提供可解析的有效会议号")
            return runtime
        if case.scene_type == 'PAAS_INVITED_JOIN':
            section = cfg.get('paas_online', 'account_section', fallback='invitee')
            if cfg.missing([(section, 'username'), (section, 'password')]):
                pytest.skip(f'邀请接收端缺少账号配置: [{section}]')
            active_profiles['observer'] = replace(active_profiles['observer'], account={
                **active_profiles['observer'].account,
                'username': cfg.get(section, 'username'), 'password': cfg.get(section, 'password'),
                'display_name': cfg.get(section, 'display_name', fallback='被邀请端')})
        runtime.create_client("observer", active_profiles["observer"])
        needs_witness = (
            "${witness." in str(case.params) or
            "witness" in case.expected_receivers or
            "witness" in case.forbidden_receivers)
        if needs_witness:
            if "witness" not in active_profiles:
                pytest.skip("布局定向/排除用例需要invitee账号作为第三个Fm端C")
            runtime.create_client("witness", active_profiles["witness"])
        if case.scene_type == 'PAAS_INVITED_JOIN':
            runtime.join('operator', active_room_id)
            runtime.attach_http('operator')
            return runtime  # B仅创建，待PaaS凭据获取后Front认证，不能预先入会。
        aliases = ("operator", "observer", "witness") if needs_witness else (
            "operator", "observer")
        event_marks = runtime.mark_all_events()
        outbound_marks = runtime.mark_all_outbound()
        joining_started = time.monotonic()
        runtime.join("operator", active_room_id)
        runtime.join("observer", active_room_id)
        if needs_witness:
            runtime.join("witness", active_room_id)
        requested_mode = os.environ.get(
            "FM_INITIALIZATION_WAIT_MODE",
            cfg.get("execution", "initialization_wait_mode", fallback="events")
        ).strip().lower()
        mode = select_wait_mode(requested_mode, case.scene_type, case.operation)
        evidence = wait_for_initialization(
            runtime, aliases, event_marks, outbound_marks,
            settle_seconds=cfg.getfloat("execution", "join_settle_time", fallback=2.0),
            mode=mode)
        record = {
            "case_id": case.case_id, "scene_type": case.scene_type,
            "requested_mode": requested_mode,
            "topology": list(aliases), "room_id": active_room_id,
            "meeting_creation_seconds": round(meeting_creation_seconds, 3),
            "other_setup_seconds": round(
                joining_started - setup_started - meeting_creation_seconds, 3),
            "join": {alias: runtime.clients[alias].last_join_timing for alias in aliases},
            "total_setup_seconds": round(time.monotonic() - setup_started, 3),
            **evidence,
        }
        runtime.initialization_timing = record
        request.config._initialization_records.append(record)
        from meeting_test_runtime.allure_reporting import attach_json
        attach_json(record, "入会初始化分段耗时与就绪证据")
        return runtime

    def build(case):
        runtime = MeetingTestRuntime(
            event_timeout=cfg.getfloat('execution','event_timeout',fallback=10),
            quiet_timeout=max(2.0,cfg.getfloat('execution','quiet_timeout',fallback=2.0)))
        try:
            return _build(case,runtime)
        except BaseException:
            from meeting_test_runtime.allure_reporting import attach_json
            try:
                runtime.close_all()
            except Exception as cleanup_error:
                attach_json({'error':str(cleanup_error)}, '前置失败后的清理错误')
            finally:
                attach_json(runtime.cleanup_results,'前置失败后的资源清理结果')
            raise
    return room_id, build
