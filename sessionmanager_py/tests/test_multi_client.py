"""多模拟端测试：8 步流程——A 自驱自收 + B 验证广播送达其他与会者。

流程：
  1. A/B 各自登录 Front（不同账号 → mock 分配不同 uid）
  2. A/B 各自登录 Conf 会议（同房间）
  3. events.clear() 清掉建立连接时的入会事件
  4. A 发业务命令（开麦/聊天/踢人…）
  5. 服务器（mock）回复 + 广播 XML 给会内成员（含 A 自己、B）
  6. A/B 各自的 EventCollector 收到并解析
  7. A 按 cmdid / 用户 ID / 状态字段断言（自己收到回声）
  8. B 也正常收到（验证广播真正送达其他与会者）

真机同理：把两个 FmTestClient 都连真实 fmserver、用两个不同账号即可
（mock 已支持多端广播，所以这套流程离线/CI 也能跑）。
"""
from __future__ import annotations

from fm_client import conf as C
from fm_client.testing import FmTestClient, connect_client, connect_pair


def _two_clients_in_room(fm_mock_server):
    """建两个 FmTestClient，都进同一房间，返回 (a, b)。"""
    host, port = fm_mock_server.host, fm_mock_server.port
    a = FmTestClient(host, port, request_timeout=5.0)
    b = FmTestClient(host, port, request_timeout=5.0)
    a.front_login("userA", "pwd", room_id=100001, room_node_id="1")   # 步骤1（A）
    a.conf_login(user_name="A")                                        # 步骤2（A）
    b.front_login("userB", "pwd", room_id=100001, room_node_id="1")   # 步骤1（B）
    b.conf_login(user_name="B")                                        # 步骤2（B）
    return a, b


def test_state_change_broadcast_to_other(fm_mock_server):
    """A 开麦 → A 自己收到 6609，B 也收到 A 的状态广播。"""
    a, b = _two_clients_in_room(fm_mock_server)
    try:
        a_uid = a.room_user_id_int
        b_uid = b.room_user_id_int
        assert a_uid != b_uid                                    # mock 给两端分配了不同 uid

        a.events.clear(); b.events.clear()                       # 步骤3
        a.mic_on()                                               # 步骤4

        # 步骤5-7：A 自己收到回声
        a_states = a.wait_for_user_state(user_id=a_uid, timeout=3)
        assert a_states, "A 未收到自己的状态广播"
        assert a_states[-1].get("Audio", {}).get("State") == str(C.AUDIO_SPEAKING)

        # 步骤8：B 也收到 A 的状态广播
        b_states = b.wait_for_user_state(user_id=a_uid, timeout=3)
        assert b_states, "B 未收到 A 的状态广播（广播未送达其他与会者）"
        assert b_states[-1].get("Audio", {}).get("State") == str(C.AUDIO_SPEAKING)
    finally:
        a.close(); b.close()


def test_chat_broadcast_to_other(fm_mock_server):
    """A 发公聊 → B 收到 6612。"""
    a, b = _two_clients_in_room(fm_mock_server)
    try:
        a_uid = a.room_user_id_int
        a.events.clear(); b.events.clear()
        a.chat("hi-broadcast")                                   # 步骤4

        msgs = b.wait_for_chat(text="hi-broadcast", src=a_uid, timeout=3)   # 步骤8
        assert msgs, "B 未收到 A 的公聊"
    finally:
        a.close(); b.close()


def test_chat_font_attributes_reach_other_client(fm_mock_server):
    a, b = _two_clients_in_room(fm_mock_server)
    try:
        mark = b.events.mark()
        sent_mark = a.mark_sent_commands()
        a.chat("font-message", color=0x112233, font={
            "Name": "宋体", "Height": 16, "Weight": 400,
            "Underline": 1, "Italic": 0})

        event = b.events.wait_event(
            after=mark, source="conf", cmdid=C.CHATMSGNOTIFY, kind="notify",
            match={"Text": "font-message", "Color": 0x112233,
                   "Font.Name": "宋体", "Font.Height": 16,
                   "Font.Weight": 400, "Font.Underline": 1,
                   "Font.Italic": 0}, timeout=3)
        assert event is not None
        sent = a.snapshot_sent_commands(after=sent_mark)
        assert len(sent) == 1
        assert sent[0].cmdid == C.CHATMSG
        assert sent[0].source == "conf" and sent[0].ver == a.conf_protocol_version
        assert sent[0].fields["Font"]["@Name"] == "宋体"
        assert b'<Font Name=' in sent[0].raw_xml
    finally:
        a.close(); b.close()


def test_kick_reaches_target(fm_mock_server):
    """A 踢 B → B 收到 6606 被踢推送（目标用户断言）。"""
    a, b = _two_clients_in_room(fm_mock_server)
    try:
        b_uid = b.room_user_id_int
        a.events.clear(); b.events.clear()
        a.kick_user(b_uid)                                       # 步骤4：A 踢 B

        # 步骤8：B 收到被踢推送，且 user_id == B
        kicked = b.events.wait_for_kicked(user_id=b_uid, timeout=3)
        assert kicked, "B 未收到被踢推送"
        assert str(kicked[-1]["user_id"]) == str(b_uid)
    finally:
        a.close(); b.close()


def test_user_enter_seen_by_existing_member(fm_mock_server):
    """B 入会时，已在场的 A 收到 B 的 6602 入会广播。"""
    host, port = fm_mock_server.host, fm_mock_server.port
    a = FmTestClient(host, port, request_timeout=5.0)
    try:
        a.front_login("userA", "pwd", room_id=100001, room_node_id="1")
        a.conf_login(user_name="A")
        a.events.clear()

        b = FmTestClient(host, port, request_timeout=5.0)
        b.front_login("userB", "pwd", room_id=100001, room_node_id="1")
        b.conf_login(user_name="B")                              # B 入会 → 触发 6602 广播
        try:
            b_uid = b.room_user_id_int
            enters = a.wait_for_user_enter(user_id=b_uid, timeout=3)   # A 看到 B 进来
            assert enters, "A 未收到 B 的入会广播"
        finally:
            b.close()
    finally:
        a.close()


def test_connect_pair_from_config(fm_mock_server):
    """JSON 配置化：connect_pair 用 accounts[0]/[1] 建操作端+观察端，广播送达。"""
    cfg = {
        "host": fm_mock_server.host, "port": fm_mock_server.port,
        "room_id": 100001, "room_node_id": "1", "room_app_id": 11,
        "request_timeout": 5.0,
        "conf_protocol_version": "2.0",
        "accounts": [
            {"username": "userA", "password": "pwd"},
            {"username": "userB", "password": "pwd"},
        ],
    }
    a, b = connect_pair(cfg)
    try:
        assert a.conf_protocol_version == "2.0"
        assert b.conf_protocol_version == "2.0"
        conf_commands = [
            cmd for app_id, cmd in fm_mock_server.received_commands
            if app_id == C.CONF_APP_ID
        ]
        assert conf_commands and all(cmd.ver == "2.0" for cmd in conf_commands)
        assert a.room_user_id_int != b.room_user_id_int     # 两个不同账号 → 不同 uid
        a.events.clear(); b.events.clear()
        a.mic_on()
        # A 自己收到 + B 也收到
        assert a.wait_for_user_state(user_id=a.room_user_id_int, timeout=3)
        assert b.wait_for_user_state(user_id=a.room_user_id_int, timeout=3)
    finally:
        b.close(); a.close()


def test_connect_client_account_dict(fm_mock_server):
    """connect_client 直接传 account dict（不经 accounts 数组）。"""
    cfg = {"host": fm_mock_server.host, "port": fm_mock_server.port,
           "room_id": 100001, "room_node_id": "1"}
    c = connect_client(cfg, account={"username": "userC", "password": "pwd"})
    try:
        assert c.room_user_id_int is not None
    finally:
        c.close()
