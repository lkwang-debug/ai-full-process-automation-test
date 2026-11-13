"""fm_client 端到端测试：内置 mock fmserver，覆盖 登录 → 入会 → 会中（聊天/状态/退出）。"""
from __future__ import annotations

import socket
import threading
import time
from collections import deque

import pytest

from fm_client import ConfLoginWaiting, FmClient, FmListener
from fm_client import conf as C
from fm_client import front as F
from fm_client.errcodes import FmError, INVALIDARG, PASSWORDERROR, UNSUPPORTED
from fm_client.mockserver import MockFmServer
from fm_client.testing import FmTestClient


def _wait_for(predicate, timeout=2.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_recall_builder_uses_only_new_msg_id_contract():
    cmd = C.ConfCmd.build_recallmsg("msg-123")
    assert cmd.cmdid == C.RECALLMSGREQ
    assert cmd.fields == {"MsgID": "msg-123"}
    with pytest.raises(ValueError, match="msg_id is required"):
        C.ConfCmd.build_recallmsg("")
    with pytest.raises(TypeError):
        C.ConfCmd.build_recallmsg(message_id="legacy")


class CaptureListener(FmListener):
    def __init__(self) -> None:
        self.front_connected = threading.Event()
        self.conf_connected = threading.Event()
        self.users: deque = deque()
        self.chats: deque = deque()
        self.states: deque = deque()
        self.leaves: deque = deque()
        self.kicked: deque = deque()
        self.conf_states: deque = deque()
        self.silents: deque = deque()
        self.av_infos: deque = deque()
        self.room_closes: deque = deque()
        self.chair_rets: deque = deque()
        self.call_states: deque = deque()
        self.subtitles: deque = deque()
        self.send_files: deque = deque()
        self.window_states: deque = deque()
        self.chat_recalls: deque = deque()
        self.role_perms: deque = deque()
        self.system_messages: deque = deque()
        self.wait_open_doors: deque = deque()

    def on_front_connected(self) -> None:
        self.front_connected.set()

    def on_conf_connected(self) -> None:
        self.conf_connected.set()

    def on_user_enter(self, users) -> None:
        self.users.extend(users)

    def on_user_leave(self, user_id) -> None:
        self.leaves.append(user_id)

    def on_user_state_changed(self, fields) -> None:
        self.states.append(fields)

    def on_chat(self, src_user_id, dst_user_id, text, msg_id=None) -> None:
        self.chats.append((src_user_id, dst_user_id, text, msg_id))

    def on_user_kicked(self, kick_id, user_id, fields) -> None:
        self.kicked.append((kick_id, user_id))

    def on_conf_state_changed(self, fields) -> None:
        self.conf_states.append(fields)

    def on_silent(self, fields) -> None:
        self.silents.append(fields)

    def on_av_info(self, fields) -> None:
        self.av_infos.append(fields)

    def on_room_closed(self, reason, description, fields) -> None:
        self.room_closes.append((reason, description))

    def on_chair_right_ret(self, fields) -> None:
        self.chair_rets.append(fields)

    def on_call_state_notify(self, fields) -> None:
        self.call_states.append(fields)

    def on_subtitle(self, fields) -> None:
        self.subtitles.append(fields)

    def on_send_file(self, src_user_id, file_info, fields) -> None:
        self.send_files.append((src_user_id, file_info))

    def on_window_state(self, fields) -> None:
        self.window_states.append(fields)

    def on_chat_recalled(self, fields) -> None:
        self.chat_recalls.append(fields)

    def on_role_permission(self, roles, fields) -> None:
        self.role_perms.append(roles)

    def on_system_message(self, fields) -> None:
        self.system_messages.append(fields)

    def on_wait_open_door(self) -> None:
        self.wait_open_doors.append(1)


def test_login_to_meeting_flow():
    server = MockFmServer()
    try:
        listener = CaptureListener()
        client = FmClient("127.0.0.1", server.port, listener, request_timeout=5.0)

        # Front: 登录 + 进房
        rep = client.front_login("guest1", "1111", room_id=100001, room_node_id="1")
        assert rep["Result"] == "0"
        assert client.local_user_id == "1001"
        assert client.token == "mock-token-xyz"
        assert client.room_user_id == "1001"
        assert client.room_srv_node_id == "1$SID:11$AID:11"
        assert client.user_right == "2"
        assert listener.front_connected.wait(2.0)

        # Conf: 入会 → 收 6601 + 自己的 6602 → 发 INITCOMPLETE → 补发 6602
        rep2 = client.conf_login(user_name="guest1")
        assert rep2["Result"] == "0"
        assert listener.conf_connected.wait(2.0)
        assert _wait_for(lambda: any(u.get("UserID") == "1001" for u in listener.users))
        assert _wait_for(lambda: any(u.get("UserID") == "2002" for u in listener.users))

        # 会中：聊天（6512 → 收 6612 回发）
        client.chat("hello meeting")
        assert _wait_for(lambda: any(c[2] == "hello meeting" for c in listener.chats))

        # 会中：改音频状态（6509 → 收 6609）
        client.set_audio_state(C.AUDIO_SPEAKING)
        assert _wait_for(lambda: any(s.get("UserID") == "1001" for s in listener.states))
        st = listener.states[0]
        assert st.get("Audio") == {"ID": "0", "State": "2"}

        # 干净退出
        client.conf_logout()
        client.front_logout()
        client.close()
    finally:
        server.stop()


def test_locked_room_login_reports_waiting_state():
    server = MockFmServer()
    operator = None
    waiting = None
    try:
        operator_listener = CaptureListener()
        operator = FmClient("127.0.0.1", server.port, operator_listener, request_timeout=1.0)
        operator.front_login("operator", "pwd", room_id=100001, room_node_id="1")
        operator.conf_login(user_name="operator")
        operator.lock_room()
        assert _wait_for(lambda: operator_listener.conf_states)

        waiting_listener = CaptureListener()
        waiting = FmClient("127.0.0.1", server.port, waiting_listener, request_timeout=0.2)
        waiting.front_login("waiting", "pwd", room_id=100001, room_node_id="1")
        with pytest.raises(ConfLoginWaiting):
            waiting.conf_login(user_name="waiting")
        assert waiting_listener.wait_open_doors
        assert _wait_for(lambda: operator_listener.users)
    finally:
        if operator is not None:
            operator.unlock_room()
            operator.close()
        if waiting is not None:
            waiting.close()
        server.stop()


def test_conf_protocol_version_applies_to_login_and_later_commands():
    server = MockFmServer()
    try:
        client = FmClient(
            "127.0.0.1", server.port, CaptureListener(), request_timeout=5.0,
            conf_protocol_version="2.0")
        client.front_login("guest1", "1111", room_id=100001, room_node_id="1")
        client.conf_login(user_name="guest1")
        client.chat("version-check")

        assert _wait_for(lambda: any(
            app_id == C.CONF_APP_ID and cmd.cmdid == C.CHATMSG
            for app_id, cmd in server.received_commands))
        conf_commands = [
            cmd for app_id, cmd in server.received_commands
            if app_id == C.CONF_APP_ID
        ]
        assert {cmd.cmdid for cmd in conf_commands} >= {
            C.LOGINREQ, C.INITCOMPLETE, C.CHATMSG,
        }
        assert all(cmd.ver == "2.0" for cmd in conf_commands)

        front_commands = [
            cmd for app_id, cmd in server.received_commands
            if app_id == F.FRONT_APP_ID
        ]
        assert front_commands
        assert all(cmd.ver == F.FRONT_VER for cmd in front_commands)
        client.close()
    finally:
        server.stop()


@pytest.mark.parametrize("version", ["", "v2", "2.x", "0", "0.9", None])
def test_conf_protocol_version_rejects_invalid_values(version):
    with pytest.raises(ValueError, match="conf_protocol_version"):
        FmClient("127.0.0.1", conf_protocol_version=version)


def test_login_password_error_raises():
    server = MockFmServer(login_result=PASSWORDERROR)
    try:
        client = FmClient("127.0.0.1", server.port, CaptureListener(), request_timeout=3.0)
        with pytest.raises(FmError) as exc_info:
            client.front_login("guest1", "wrong", room_id=100001, room_node_id="1")
        assert exc_info.value.code == PASSWORDERROR
        client.close()
    finally:
        server.stop()


def test_entrance_and_front_discovery():
    """入会前置：Entrance 配置 + GetAuthType + RoomList + GetRoomID。"""
    server = MockFmServer()
    try:
        client = FmClient("127.0.0.1", server.port, CaptureListener(), request_timeout=5.0)

        # Entrance(app=8) 配置下发
        cfg = client.entrance_get_config(device_id="dev-1")
        assert cfg["PlatformType"] == "private"
        assert cfg["ConfigCenterAddr"].startswith("https://")

        # 认证类型（仅需 Front 会话，不必先登录）
        auth = client.front_get_auth_type()
        assert auth["AuthType"] == "FSMeetingAuth"

        # 鉴权后拉房间列表
        client.front_authenticate("guest1", "1111")
        rooms = client.front_roomlist()
        infos = rooms.get("RoomInfo")
        assert isinstance(infos, list) and len(infos) >= 1
        assert any(r.get("RoomID") == "100001" for r in infos)

        # 会议号换算真实 RoomID
        rid = client.front_getroomid(8888)
        assert rid["RoomId"] == "100001"

        client.close()
    finally:
        server.stop()


def test_discover_node_when_roomlist_fails():
    """ROOMLIST 返 0x1002（DB 异常）时，仍能从回复里取出 RoomNodeID。"""
    server = MockFmServer(roomlist_result=0x1002)
    try:
        client = FmClient("127.0.0.1", server.port, CaptureListener(), request_timeout=5.0)
        client.front_authenticate("guest1", "1111")
        node = client.front_discover_node()
        assert node == "1"
        client.close()
    finally:
        server.stop()


def test_discover_node_normal():
    """ROOMLIST 正常时也能取到 RoomNodeID。"""
    server = MockFmServer()
    try:
        client = FmClient("127.0.0.1", server.port, CaptureListener(), request_timeout=5.0)
        client.front_authenticate("guest1", "1111")
        assert client.front_discover_node() == "1"
        client.close()
    finally:
        server.stop()


def test_conf_login_reuses_front_room_verification():
    """front_login 捕获 RoomVerifyMode/room_password → conf_login 自动复用。"""
    server = MockFmServer()
    try:
        client = FmClient("127.0.0.1", server.port, CaptureListener(), request_timeout=5.0)
        client.front_login("guest1", "1111", room_id=100001, room_node_id="1",
                           room_password="secret")  # mock 房间 VerifyMode=3
        assert client.room_verify_mode == "3"
        assert client.room_password == "secret"
        # conf_login 不传 verify_mode/room_password → 自动取捕获值
        client.conf_login(user_name="guest1")
        assert client.user_right == "2"
        client.close()
    finally:
        server.stop()


def test_conf_session_uses_front_advertised_address_link():
    unavailable = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    unavailable.bind(("127.0.0.1", 0))
    unavailable_port = unavailable.getsockname()[1]
    unavailable.close()

    conf_server = MockFmServer()
    front_server = MockFmServer(conf_address_link=(
        f"TCP:127.0.0.1:{unavailable_port};TCP:127.0.0.1:{conf_server.port}"
    ))
    client = None
    try:
        client = FmClient("127.0.0.1", front_server.port, CaptureListener(), request_timeout=5.0)
        client.front_login("guest1", "1111", room_id=100001, room_node_id="1")
        client.conf_login(user_name="guest1")
        assert C.CONF_APP_ID in conf_server.received_app_ids
        assert C.CONF_APP_ID not in front_server.received_app_ids
    finally:
        if client is not None:
            client.close()
        front_server.stop()
        conf_server.stop()


def test_front_logout_uses_room_node_not_room_service_node():
    server = MockFmServer()
    try:
        client = FmClient("127.0.0.1", server.port, CaptureListener(), request_timeout=5.0)
        client.front_login("guest1", "1111", room_id=100001, room_node_id="1")
        client.front_logout()
        exits = [cmd for app_id, cmd in server.received_commands
                 if app_id == F.FRONT_APP_ID and cmd.cmdid == F.EXITROOM_REQ]
        assert exits
        assert exits[-1].get("RoomNodeID") == "1"
        assert exits[-1].get("RoomNodeID") != client.room_srv_node_id
        client.close()
    finally:
        server.stop()


def test_concurrent_same_command_requests_are_correlated():
    server = MockFmServer()
    try:
        client = FmClient("127.0.0.1", server.port, CaptureListener(), request_timeout=5.0)
        client.front_get_auth_type()
        worker_count = 8
        barrier = threading.Barrier(worker_count)
        results = deque()
        errors = deque()

        def request_auth_type():
            barrier.wait()
            try:
                results.append(client.front_get_auth_type()["AuthType"])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=request_auth_type) for _ in range(worker_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=6.0)

        assert not errors
        assert len(results) == worker_count
        assert set(results) == {"FSMeetingAuth"}
        assert all(not thread.is_alive() for thread in threads)
        client.close()
    finally:
        server.stop()


def test_disconnect_wakes_pending_request_immediately():
    server = MockFmServer(ignored_cmdids={F.GETAUTHTYPE_REQ})
    client = None
    try:
        client = FmClient("127.0.0.1", server.port, CaptureListener(), request_timeout=5.0)
        errors = deque()

        def request_auth_type():
            try:
                client.front_get_auth_type()
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=request_auth_type)
        started = time.monotonic()
        thread.start()
        assert _wait_for(lambda: bool(client._pending))
        server.stop()
        thread.join(timeout=1.0)

        assert not thread.is_alive()
        assert errors and isinstance(errors[0], FmError)
        assert time.monotonic() - started < 2.0
    finally:
        if client is not None:
            client.close()
        server.stop()


def test_meeting_control_commands():
    """会控：踢人/会议状态/申请主席/静音/设备上报/关房（发送 → 收 +100 广播推送）。"""
    server = MockFmServer()
    try:
        listener = CaptureListener()
        client = FmClient("127.0.0.1", server.port, listener, request_timeout=5.0)
        client.front_login("guest1", "1111", room_id=100001, room_node_id="1")
        client.conf_login(user_name="guest1")

        # 踢人 6506 → 6606
        client.kick_user(2002)
        assert _wait_for(lambda: any(uid == "2002" for _, uid in listener.kicked))

        # 会议状态 6510 → 6610
        client.set_conf_state(lock=1, enable_chat=0)
        assert _wait_for(lambda: listener.conf_states
                         and listener.conf_states[0].get("Lock") == "1"
                         and listener.conf_states[0].get("EnableChat") == "0")

        # 申请主席 6511 → 成功走 6609（Right=3）
        client.apply_host(chair_pwd="1111")
        assert _wait_for(lambda: any(s.get("Right") == "3" for s in listener.states))

        # 全场静音 6514 → 6614
        client.mute_all()
        assert _wait_for(lambda: listener.silents)

        # 设备上报 6508 → 6608
        client.report_av_info(audio=[C.ConfCmd.av_device(C.AV_OP_ADD, 1, "mic1")])
        assert _wait_for(lambda: listener.av_infos)

        # 关闭会议室 6507 → 6607
        client.close_room(reason=1, description="test end")
        assert _wait_for(lambda: listener.room_closes
                         and listener.room_closes[0] == ("1", "test end"))
        client.close()
    finally:
        server.stop()


def test_media_and_record():
    """媒体便捷方法（开麦/开摄像头）+ 录制（开/停/暂停）+ 锁房。"""
    server = MockFmServer()
    try:
        listener = CaptureListener()
        client = FmClient("127.0.0.1", server.port, listener, request_timeout=5.0)
        client.front_login("guest1", "1111", room_id=100001, room_node_id="1")
        client.conf_login(user_name="guest1")

        def _has_state(predicate):
            return any(predicate(s) for s in listener.states)

        # 开麦 → 6609 Audio.State=2
        client.mic_on()
        assert _wait_for(lambda: _has_state(
            lambda s: isinstance(s.get("Audio"), dict) and s["Audio"].get("State") == "2"))

        # 开摄像头 → 6609 Video.State=2
        client.camera_on()
        assert _wait_for(lambda: _has_state(
            lambda s: isinstance(s.get("Video"), dict) and s["Video"].get("State") == "2"))

        # 关麦
        client.mic_off()
        assert _wait_for(lambda: _has_state(
            lambda s: isinstance(s.get("Audio"), dict) and s["Audio"].get("State") == "0"))

        # 开启录制 → 6610 ServerRecordStatus=2
        client.start_record()
        assert _wait_for(lambda: listener.conf_states
                         and listener.conf_states[-1].get("ServerRecordStatus") == "2")

        # 暂停录制 → =1
        client.pause_record()
        assert _wait_for(lambda: listener.conf_states
                         and listener.conf_states[-1].get("ServerRecordStatus") == "1")

        # 结束录制 → =0
        client.stop_record()
        assert _wait_for(lambda: listener.conf_states
                         and listener.conf_states[-1].get("ServerRecordStatus") == "0")

        # 锁房 → 6610 Lock=1
        client.lock_room()
        assert _wait_for(lambda: listener.conf_states
                         and listener.conf_states[-1].get("Lock") == "1")
        client.close()
    finally:
        server.stop()


def test_login_variants():
    """加入会议变体：会议号 / 邀请码 / 访客匿名。"""
    server = MockFmServer()
    try:
        client = FmClient("127.0.0.1", server.port, CaptureListener(), request_timeout=5.0)

        # 会议号入会：GETROOMID(8888)→RoomId=100001→LOGINROOM
        rep = client.front_login_by_meeting_no("guest1", "1111", 8888, room_node_id="1")
        assert rep["Result"] == "0"
        assert client.room_id == 100001
        assert client.real_room_id == "100001"           # mock 回显 RealRoomID=RoomID
        client.front_logout()

        # 邀请码入会：邀请码当 RoomID，real_room_id 被捕获
        rep = client.front_login_by_invite_code("guest1", "1111", 100100100, room_node_id="1")
        assert rep["Result"] == "0"
        assert client.real_room_id == "100100100"
        client.front_logout()

        # 访客入会：USERLOGIN 带 UserType=0 / UserLoginType=0
        rep = client.front_login_guest("", "", 100001, room_node_id="1")
        assert rep["Result"] == "0"
        logins = [cmd for app_id, cmd in server.received_commands
                  if app_id == F.FRONT_APP_ID and cmd.cmdid == F.USERLOGIN_REQ]
        assert logins and logins[-1].get("UserType") == "0"
        assert logins[-1].get("UserLoginType") == "0"
        client.close()
    finally:
        server.stop()


def test_addressbook_query():
    """通讯录查询：6601 部门树/本地联系人缓存 + 6503 离线成员 + 6528 组织架构 + 6534 邀请码。"""
    server = MockFmServer()
    try:
        client = FmClient("127.0.0.1", server.port, CaptureListener(), request_timeout=5.0)
        client.front_login("guest1", "1111", room_id=100001, room_node_id="1")
        client.conf_login(user_name="guest1")

        # 6601 缓存：组织架构 + 本地用户联系人
        assert client.dept_info and client.dept_info["Dept"][0]["DeptName"] == "root"
        assert client.user_info and client.user_info["Mobile"] == "13800000000"

        # 离线成员 6503
        offline = client.conf_get_offline_users()
        assert len(offline) == 1 and offline[0]["Mobile"] == "13900000000"

        # 组织架构 6528
        depts = client.conf_get_dept_info()
        assert len(depts) == 2 and depts[1]["DeptName"] == "dev"

        # 会议配置 6534（含 9 位邀请码）
        cfg = client.conf_get_room_config()
        assert cfg["InviteCode"] == "100100100"

        # 组装通讯录快照
        contacts = client.conf_get_contacts()
        assert len(contacts["depts"]) == 2
        assert contacts["local_user"]["EMail"] == "u@example.com"
        assert len(contacts["offline_users"]) == 1
        client.close()
    finally:
        server.stop()


def test_paged_addressbook_queries_and_mainline_offline_cache():
    """6503 caches per Conf login; explicit refresh and 6528 still collect every page."""
    server = MockFmServer()
    server.offline_users = [
        {"UserID": 3000 + index, "Name": f"offline-{index}"}
        for index in range(125)
    ]
    server.departments = [
        {"DeptID": index + 1, "DeptName": f"dept-{index}",
         "ParentDeptID": 0, "NodeID": 1}
        for index in range(305)
    ]
    try:
        client = FmTestClient("127.0.0.1", server.port, request_timeout=3.0)
        client.front_login("guest1", "1111", room_id=100001, room_node_id="1")
        client.conf_login(user_name="guest1")

        offline_mark = client.events.mark()
        assert len(client.conf_get_offline_users()) == 125
        offline_pages = [
            event for event in client.events.snapshot_received(after=offline_mark)
            if event.cmdid == C.OUTLINE
        ]
        assert len(offline_pages) == 3
        assert all(event.kind == "response" for event in offline_pages)

        offline_request_count = lambda: sum(
            1 for app_id, cmd in server.received_commands
            if app_id == C.CONF_APP_ID and cmd.cmdid == C.REQOFFLINES)
        assert offline_request_count() == 1

        # 主线每次 Conf 登录只响应一次 6503，重复调用必须复用缓存且返回副本。
        server.offline_users = []
        cached = client.conf_get_offline_users()
        assert len(cached) == 125
        cached.clear()
        assert len(client.conf_get_offline_users()) == 125
        assert offline_request_count() == 1

        # 仅供明确支持重复 6503 的服务端做能力探测。
        assert client.conf_get_offline_users(refresh=True) == []
        assert offline_request_count() == 2

        dept_mark = client.events.mark()
        assert len(client.conf_get_dept_info()) == 305
        dept_pages = [
            event for event in client.events.snapshot_received(after=dept_mark)
            if event.cmdid == C.DEPTINFOREP
        ]
        assert len(dept_pages) == 3
        assert all(event.kind == "response" for event in dept_pages)

        server.departments = []
        assert client.conf_get_dept_info() == []

        # 新的 Conf 登录周期会清掉离线成员缓存，首次调用重新发送 6503。
        server.offline_users = [{"UserID": 9001, "Name": "new-session"}]
        client.conf_logout()
        client.conf_login(user_name="guest1")
        assert client.conf_get_offline_users() == [
            {"UserID": "9001", "Name": "new-session"}]
        assert offline_request_count() == 3
        client.close()
    finally:
        server.stop()


def test_av_mix_unsupported_returns_protocol_error():
    server = MockFmServer()
    server.av_mix_supported = False
    try:
        client = FmClient("127.0.0.1", server.port, request_timeout=3.0)
        client.front_login("guest1", "1111", room_id=100001, room_node_id="1")
        client.conf_login(user_name="guest1")

        with pytest.raises(FmError) as exc_info:
            client.get_av_mix_params()
        assert exc_info.value.code == UNSUPPORTED
        client.close()
    finally:
        server.stop()


@pytest.mark.parametrize("right_type", [0, 13, 99])
def test_invalid_operation_right_type_returns_protocol_error(right_type):
    server = MockFmServer()
    try:
        client = FmClient("127.0.0.1", server.port, request_timeout=3.0)
        client.front_login("guest1", "1111", room_id=100001, room_node_id="1")
        client.conf_login(user_name="guest1")

        with pytest.raises(FmError) as exc_info:
            client.request_right(right_type)
        assert exc_info.value.code == INVALIDARG
        client.close()
    finally:
        server.stop()


def test_invite_call_and_state():
    """通讯录邀请：MAKECALL(6741) 邀请电话入会 + 6745 呼叫状态推送 + 挂断 + 查列表。"""
    server = MockFmServer()
    try:
        listener = CaptureListener()
        client = FmClient("127.0.0.1", server.port, listener, request_timeout=5.0)
        client.front_login("guest1", "1111", room_id=100001, room_node_id="1")
        client.conf_login(user_name="guest1")

        # 邀请两个电话联系人入会
        rep = client.conf_invite_call([
            C.ConfCmd.call_info(callee_num="13800000000", callee_nick_name="张三", callee_user_id=101),
            C.ConfCmd.call_info(callee_num="13900000000", callee_nick_name="李四"),
        ], call_type=C.CALLTYPE_PHONE)
        assert rep["OprResult"] == "0"
        assert rep["RemainCallCount"] == "10"
        assert len(rep["ReqCallInfo"]) == 2

        # 呼叫状态推送：每个被叫一条 6745
        assert _wait_for(lambda: len(listener.call_states) == 2)
        assert all(cs.get("CallState") == str(C.CALL_STATE_CONFIRMED) for cs in listener.call_states)

        # 查呼叫列表 6746
        lst = client.conf_query_call_list()
        assert lst["OprResult"] == "0"
        assert lst["CalleeInfo"][0]["CalleeUserID"] == "5000"

        # 挂断 6743
        rep = client.conf_hangup(["13800000000"])
        assert rep["OprResult"] == "0"
        client.close()
    finally:
        server.stop()


def test_roomlist_classified():
    """列表分类：RoomInfo 打 MeetingType 标签（固定/预约/即时）。"""
    server = MockFmServer()
    try:
        client = FmClient("127.0.0.1", server.port, CaptureListener(), request_timeout=5.0)
        client.front_authenticate("guest1", "1111")
        rep = client.front_roomlist_classified()
        rooms = {r["RoomID"]: r["MeetingType"] for r in rep["RoomInfo"]}
        assert rooms["100001"] == F.ROOM_TYPE_FIXED
        assert rooms["100002"] == F.ROOM_TYPE_RESERVED
        assert rooms["1000000001"] == F.ROOM_TYPE_INSTANT
        client.close()
    finally:
        server.stop()


def test_meeting_advanced():
    """扩展会议控制：分组会议 / 操作权 / 改名 / 字幕 / 发文件 / 撤回 / 视图（请求+广播推送）。"""
    server = MockFmServer()
    try:
        listener = CaptureListener()
        client = FmClient("127.0.0.1", server.port, listener, request_timeout=5.0)
        client.front_login("guest1", "1111", room_id=100001, room_node_id="1")
        client.conf_login(user_name="guest1")

        # 创建分组会议 6523→6623（返回新建 RoomID）
        rep = client.create_group([
            C.ConfCmd.group_room(theme=" breakout-A",
                                 users=[C.ConfCmd.group_user(id=1001, right=3)]),
        ])
        assert rep["RoomInfo"][0]["RoomID"] == str(0x70000001)
        assert rep["RoomInfo"][0]["Theme"] == " breakout-A"

        # 申请白板操作权 6530→6630
        rep = client.request_right(C.RIGHT_TYPE_WHITEBOARD)
        assert rep["Result"] == "0"
        assert rep["RightType"] == str(C.RIGHT_TYPE_WHITEBOARD)
        # 释放
        client.release_right(C.RIGHT_TYPE_WHITEBOARD)

        # 改会议室名 6527→6627
        rep = client.rename_meeting("new-name")
        assert rep["Name"] == "new-name"

        # 设会场字幕 6515→广播 6615
        client.set_subtitle("hello subtitle", duration=5)
        assert _wait_for(lambda: listener.subtitles
                         and listener.subtitles[0].get("Data") == "hello subtitle")
        # 关闭字幕 6550→6650（空 body 回复，无 Result；_request 不报错即成功）
        assert isinstance(client.close_subtitle(), dict)

        # 系统消息 6518→目标端 6618；正文/类型来自服务器消息配置
        client.send_sysmsg(sys_msg_id=6, dst_user_id=client.room_user_id)
        assert _wait_for(lambda: listener.system_messages)
        assert listener.system_messages[0]["Type"] == str(C.SYSMSG_TYPE_WINDOW)

        # 发文件 6516→广播 6616
        client.send_file([2002], C.ConfCmd.file_info(guid="g1", name="a.txt", size=10))
        assert _wait_for(lambda: listener.send_files
                         and listener.send_files[0][1].get("Name") == "a.txt")

        # 撤回消息 6568→6668 + 广播 6669
        recall = client.recall_msg("m-1")
        assert recall["MsgID"] == "m-1" and recall["Result"] == "0"
        assert _wait_for(lambda: listener.chat_recalls)
        assert listener.chat_recalls[0]["MsgID"] == "m-1"
        assert listener.chat_recalls[0]["SrcUserID"] == "1001"
        with pytest.raises(FmError) as duplicate:
            client.recall_msg("m-1")
        assert duplicate.value.code == 1
        time.sleep(0.1)
        assert len(listener.chat_recalls) == 1

        # 视图状��� 6513→广播 6613
        client.set_window_state(mode=C.WND_MODE_VIDEO, video_relay_id=1)
        assert _wait_for(lambda: listener.window_states
                         and listener.window_states[0].get("Mode") == str(C.WND_MODE_VIDEO))

        # 主持人答复敲门 6504（fire-and-forget，不发崩）
        client.allow_knock(2002, allow=1)

        client.close()
    finally:
        server.stop()


def test_role_permission_push():
    """6631 角色权限推送（_dispatch_push 把重复 <Role> 归一成 list 传给 listener）。"""
    import socket as _socket
    # 直接用 mock 不便注入推送；这里构造一条 6631 由 mock 触发不了，改用单元级校验：
    # 确认 on_role_permission 在 _dispatch_push 里收到 list。
    from fm_client.xmlcmd import XmlCommand
    server = MockFmServer()
    try:
        listener = CaptureListener()
        client = FmClient("127.0.0.1", server.port, listener, request_timeout=5.0)
        client.front_login("guest1", "1111", room_id=100001, room_node_id="1")
        client.conf_login(user_name="guest1")
        # 直接走 client 的分发逻辑模拟一条 6631
        cmd = XmlCommand(C.ROLEPERMISSIONNOTIFY, {
            "Role": [
                {"RoleCode": "chairman", "Permissions": {"Audio": "1"}},
                {"RoleCode": "mentee", "Permissions": {"Audio": "0"}},
            ],
            "RoleEnd": "",
        })
        client._dispatch_push("conf", cmd)
        assert _wait_for(lambda: listener.role_perms)
        roles = listener.role_perms[0]
        assert isinstance(roles, list) and len(roles) == 2
        assert roles[0]["RoleCode"] == "chairman"
        client.close()
    finally:
        server.stop()
