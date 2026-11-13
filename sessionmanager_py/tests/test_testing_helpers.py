"""测试人员视角示例：用 FmTestClient / EventCollector / fixture 写自动化测试。

对比 tests/test_fm_client.py（裸 FmClient + 手写 CaptureListener + _wait_for），
这里的写法更顺手：typed 返回、int 字段自动解析、内置 wait_for_*、fixture 自动建连。
"""
from __future__ import annotations

from fm_client.testing import ChatMsg, UserEnter


def test_typed_login_and_state(fm):  # `fm` fixture：连着 mock 的 FmTestClient，自动建连/清理
    """typed 状态访问 + typed 入会事件。"""
    fm.front_login("guest1", "1111", room_id=100001, room_node_id="1")
    assert fm.local_user_id_int == 1001          # int，不用自己 int("1001")
    fm.conf_login(user_name="guest1")
    assert fm.user_right_int == 2                 # 主席/出席/旁听 直接比 int

    enters = fm.wait_for_user_enter(timeout=3)    # 内置等推送，不用手写 _wait_for
    assert enters, "未收到入会事件"
    assert isinstance(enters[0], UserEnter)
    assert any(e.user_id == 1001 for e in enters)  # typed 字段，不是 dict["UserID"]


def test_typed_chat(fm):
    """typed 聊天消息断言。"""
    fm.front_login("guest1", "1111", room_id=100001, room_node_id="1")
    fm.conf_login(user_name="guest1")
    fm.chat("hello-test")
    msgs = fm.wait_for_chat(text="hello-test", timeout=3)
    assert msgs and isinstance(msgs[0], ChatMsg)
    assert msgs[0].text == "hello-test"


def test_typed_roomlist(fm):
    """list_rooms 返回 typed Room 列表，meeting_type 已推算。"""
    fm.front_authenticate("guest1", "1111")
    rooms = fm.list_rooms()                       # List[Room]，不是 raw dict
    by_id = {r.room_id: r for r in rooms}
    assert by_id[100001].meeting_type == "fixed"
    assert by_id[100002].meeting_type == "reserved"
    assert by_id[1000000001].meeting_type == "instant"
    assert all(isinstance(r.room_id, int) for r in rooms)  # int，不是 "100001"


def test_typed_contacts(fm):
    """offline_contacts 返回 typed Contact 列表。"""
    fm.front_login("guest1", "1111", room_id=100001, room_node_id="1")
    fm.conf_login(user_name="guest1")
    contacts = fm.offline_contacts()
    assert contacts
    c = contacts[0]
    assert isinstance(c.user_id, int) and c.name                 # typed
    assert any(co.mobile == "13900000000" for co in contacts)     # 直接用属性断言


def test_recording_state_wait(fm):
    """wait_for_recording_state 等录制状态变更（6610 ServerRecordStatus）。"""
    fm.front_login("guest1", "1111", room_id=100001, room_node_id="1")
    fm.conf_login(user_name="guest1")
    fm.start_record()
    assert fm.wait_for_recording_state(2, timeout=3)              # 2=录制中
    fm.stop_record()
    assert fm.wait_for_recording_state(0, timeout=3)


def test_event_collector_snapshot_and_clear(fm):
    """EventCollector 的 snapshot/clear：分段断言。"""
    fm.front_login("guest1", "1111", room_id=100001, room_node_id="1")
    fm.conf_login(user_name="guest1")
    fm.wait_for_user_enter(timeout=3)
    assert fm.events.snapshot(fm.events.user_enters)              # 快照当前事件
    fm.events.clear()                                             # 清空，下一段断言
    assert fm.events.snapshot(fm.events.user_enters) == []


def test_invite_call_wait(fm):
    """通讯录邀请：MAKECALL + 等 6745 呼叫状态推送。"""
    from fm_client import conf as C
    fm.front_login("guest1", "1111", room_id=100001, room_node_id="1")
    fm.conf_login(user_name="guest1")
    fm.conf_invite_call([C.ConfCmd.call_info(callee_num="13800000000", callee_nick_name="x")])
    states = fm.wait_for_call_state(call_state=C.CALL_STATE_CONFIRMED, timeout=3)
    assert states
