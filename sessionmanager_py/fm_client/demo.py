"""fm_client 演示 / 真机测试 CLI：登录 → 入会 → 会中，含会前功能一键开关。

两种运行方式：
  # 1) 本地自测（内置 mock fmserver，无需真实服务）
  python -m fm_client.demo --mock --contacts

  # 2) 连真实 4u_fmserver（export 一次 FM_* 后裸跑）
  export FM_HOST=192.168.7.154 FM_USER=A7 FM_PASSWORD=<configure-locally> \
         FM_ROOM_ID=10000 FM_ROOM_NODE=396593
  python -m fm_client.demo                       # 登录→入会→聊天→开麦→退出
  python -m fm_client.demo --list-rooms-classified
  python -m fm_client.demo --contacts
  python -m fm_client.demo --invite-call 13800000000,13900000000 --stay 10

会前功能开关：
  --list-rooms-classified     拉房间列表 + 标固定/预约/即时（仅需鉴权）
  --login-by-meeting-no N     用会议号入会（GETROOMID 换算后进房）
  --login-by-invite-code CODE 用 9 位邀请码入会（real_room_id 为真实房）
  --login-guest               访客匿名入会（UserType=0，需 VerifyMode=3 房）
  --contacts / --offline-users / --dept-info / --room-config
                              入会后查通讯录快照/离线成员/组织架构/会议配置(InviteCode)
  --invite-call NUM,NUM       邀请电话/H323 联系人入会（MAKECALL 6741），随后观察 6745 推送
  --call-type {1,2}           1=电话(默认) 2=H323（配合 --invite-call）
  --stay SEC                  入会后停留秒数观察推送（默认 3，invite-call 建议 10）
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time


def _env(name, default):
    return os.environ.get(name, default)


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


from . import conf as C
from .client import FmClient, FmListener
from .errcodes import FmError
from .mockserver import MockFmServer


class PrintingListener(FmListener):
    """打印所有业务事件/推送（真机观察用）。"""

    def on_front_connected(self) -> None:
        print("[event] front session connected (app=20)")

    def on_conf_connected(self) -> None:
        print("[event] conf session connected (app=11)")

    def on_create_failed(self, which: str, reason: str) -> None:
        print(f"[event] {which} create failed: {reason}")

    def on_disconnected(self, which: str) -> None:
        print(f"[event] {which} disconnected")

    def on_user_enter(self, users) -> None:
        for u in users:
            print(f"[push 6602] user enter: id={u.get('UserID')} name={u.get('DisplayName')} right={u.get('Right')}")

    def on_user_leave(self, user_id) -> None:
        print(f"[push 6605] user leave: id={user_id}")

    def on_user_state_changed(self, fields) -> None:
        print(f"[push 6609] state changed: {fields}")

    def on_chat(self, src_user_id, dst_user_id, text, msg_id=None) -> None:
        print(f"[push 6612] chat from {src_user_id} to {dst_user_id}: {text!r} (msgid={msg_id})")

    def on_user_kicked(self, kick_id, user_id, fields) -> None:
        print(f"[push 6606] user kicked: kick_id={kick_id} user_id={user_id} {fields}")

    def on_room_closed(self, reason, description, fields) -> None:
        print(f"[push 6607] room closed: reason={reason} desc={description}")

    def on_av_info(self, fields) -> None:
        print(f"[push 6608] av info: {fields}")

    def on_conf_state_changed(self, fields) -> None:
        print(f"[push 6610] conf state: {fields}")

    def on_chair_right_ret(self, fields) -> None:
        print(f"[push 6611] chair right ret: {fields}")

    def on_silent(self, fields) -> None:
        print(f"[push 6614] silent: {fields}")

    def on_call_state_notify(self, fields) -> None:
        print(f"[push 6745] call state: {fields}")


def _login_method_label(args) -> str:
    if args.login_by_meeting_no is not None:
        return f"meeting_no={args.login_by_meeting_no}"
    if args.login_by_invite_code is not None:
        return f"invite_code={args.login_by_invite_code}"
    if args.login_guest:
        return "guest(anonymous)"
    return f"room_id={args.room_id}"


def _do_front_login(client: FmClient, args, user: str, password: str):
    """根据 args 的入会方式标志选择加入会议方法。"""
    if args.login_by_meeting_no is not None:
        return client.front_login_by_meeting_no(
            user, password, args.login_by_meeting_no,
            room_node_id=args.room_node, room_app_id=args.room_app_id)
    if args.login_by_invite_code is not None:
        return client.front_login_by_invite_code(
            user, password, args.login_by_invite_code,
            room_node_id=args.room_node, room_app_id=args.room_app_id)
    if args.login_guest:
        return client.front_login_guest(
            user, password, args.room_id,
            room_node_id=args.room_node, room_app_id=args.room_app_id)
    return client.front_login(
        user, password, room_id=args.room_id,
        room_node_id=args.room_node, room_app_id=args.room_app_id)


def _new_client(host, port) -> FmClient:
    return FmClient(host, port, PrintingListener(), request_timeout=8.0)


def run(args, host, port) -> int:
    """登录（按选定入会方式）→ 入会 → 聊天 → 开麦 → 停留观察推送 → 退出。"""
    client = _new_client(host, port)
    try:
        print(f"[front] login user={args.user!r} method={_login_method_label(args)} "
              f"node={args.room_node!r} app={args.room_app_id} ...")
        _do_front_login(client, args, args.user, args.password)
        print(f"[front] LOGGED IN: token={client.token} room_user_id={client.room_user_id} "
              f"real_room_id={client.real_room_id} room_srv_node_id={client.room_srv_node_id}")
        print(f"[front] conf servers: {client.conf_servers}")

        print("[conf] login meeting ...")
        client.conf_login(user_name=args.user)
        print(f"[conf] JOINED (right={client.user_right}); sending chat + mic on, then observe ...")

        time.sleep(0.5)
        client.chat(args.text)
        client.set_audio_state(C.AUDIO_SPEAKING)
        time.sleep(args.stay)  # 观察推送

        print("[teardown] logout ...")
        client.conf_logout()
        client.front_logout()
    finally:
        client.close()
    return 0


def discover_node(args, host, port) -> int:
    """发 ROOMLIST 取本地 RoomNodeID——即使服务端 DB 异常(0x1002)也能取到。"""
    client = _new_client(host, port)
    try:
        client.front_authenticate(args.user, args.password)
        node = client.front_discover_node()
        print(f"[front] 本地 RoomNodeID = {node!r}")
        if node:
            print(f"        设一次环境变量即可裸跑： export FM_ROOM_NODE={node}")
        else:
            print("        （回复未带 RoomNodeID，无法自动发现）")
        return 0
    finally:
        client.close()


def _print_rooms(rooms: dict, classified: bool) -> None:
    infos = rooms.get("RoomInfo")
    if isinstance(infos, dict):
        infos = [infos]
    top_node = rooms.get("RoomNodeID")
    if not infos:
        print("[front] (rep 里没有 RoomInfo，结构可能不同 —— 见上方 raw fields)")
        return
    print(f"[front] {len(infos)} room(s):")
    for ri in infos:
        node = ri.get("RoomNodeID") or top_node
        tag = f"[{ri.get('MeetingType')}]" if classified else ""
        times = ""
        if ri.get("HopeStartTime") and ri.get("HopeStartTime") != "0000-00-00 00:00:00":
            times = f" 时间={ri.get('HopeStartTime')}~{ri.get('HopeEndTime')}"
        print(f"  {tag} RoomID={ri.get('RoomID')}  RoomNodeID={node}  "
              f"Name={ri.get('Name')}  AppID={ri.get('RoomAppID')}{times}")


def _fetch_roomlist(client: FmClient, classified: bool):
    """拉房间列表；0x1002 等 DB 异常时打印说明并返回 None（而非抛错崩溃）。"""
    try:
        if classified:
            return client.front_roomlist_classified(is_get_all=1, is_get_desc=1)
        return client.front_roomlist(is_get_all=1, is_get_desc=1)
    except FmError as exc:
        print(f"[front] ROOMLIST 失败 (code={exc.code:#x})：{exc}")
        if exc.code == 0x1002:
            print("        服务端 DB 存储过程异常(p_GetUserRoomList)，非客户端问题。")
            print("        取节点用 --discover-node；查真实房间直连 DB：")
            print("        SELECT RoomID,RoomNodeID,RoomAppID,VerifyMode FROM t_roominfo WHERE Status<>0;")
        return None


def list_rooms(args, host, port) -> int:
    """USERLOGIN → ROOMLIST，打印真实 RoomID / RoomNodeID（解决 0x2102 房间未找到）。"""
    client = _new_client(host, port)
    try:
        client.front_authenticate(args.user, args.password)
        print(f"[front] authenticated as {args.user!r} (local_user_id={client.local_user_id})")
        rooms = _fetch_roomlist(client, classified=False)
        if rooms is None:
            return 1
        print(f"[front] ROOMLIST_REP raw fields:\n  {rooms}")
        _print_rooms(rooms, classified=False)
        return 0
    finally:
        client.close()


def list_rooms_classified(args, host, port) -> int:
    """拉房间列表 + 客户端推算 MeetingType（固定/预约/即时）。"""
    client = _new_client(host, port)
    try:
        client.front_authenticate(args.user, args.password)
        print(f"[front] authenticated as {args.user!r} (local_user_id={client.local_user_id})")
        rooms = _fetch_roomlist(client, classified=True)
        if rooms is None:
            return 1
        print(f"[front] ROOMLIST_REP raw fields:\n  {rooms}")
        _print_rooms(rooms, classified=True)
        return 0
    finally:
        client.close()


def get_room_id(args, host, port) -> int:
    """用"入会会议号"换算真实 RoomID（GETROOMID 30012）。"""
    client = _new_client(host, port)
    try:
        client.front_authenticate(args.user, args.password)
        rep = client.front_getroomid(args.get_room_id)
        print(f"[front] GETROOMID LoginRoomId={args.get_room_id} → {rep}")
        return 0
    finally:
        client.close()


# -------------------------------------------------------------- 入会后动作 ---
def join_and_do(args, host, port, action, label: str) -> int:
    """按选定方式登录 → 入会 → 执行 action(client) → 停留观察推送 → 退出。"""
    client = _new_client(host, port)
    try:
        print(f"[front] login user={args.user!r} method={_login_method_label(args)} ...")
        _do_front_login(client, args, args.user, args.password)
        print(f"[front] LOGGED IN: real_room_id={client.real_room_id} room_user_id={client.room_user_id}")

        print("[conf] login meeting ...")
        client.conf_login(user_name=args.user)
        print(f"[conf] JOINED (right={client.user_right})")

        print(f"\n=== {label} ===")
        try:
            action(client, args)
        except Exception as exc:  # noqa: BLE001
            # 单个动作失败不致命（如 6534/6528 在某些服务器版本会断开会话或超时）
            print(f"⚠️ 执行失败: {exc}")

        print(f"\n[observe] 等待 {args.stay}s 观察推送 ...")
        time.sleep(args.stay)

        print("[teardown] logout ...")
        try:
            client.conf_logout()
            client.front_logout()
        except Exception as exc:  # noqa: BLE001
            print(f"[teardown] best-effort: {exc}")
    finally:
        client.close()
    return 0


def _action_contacts(client: FmClient, args) -> None:
    contacts = client.conf_get_contacts()
    print(f"通讯录快照：部门 {len(contacts['depts'])} 个，离线成员 {len(contacts['offline_users'])} 人")
    print(f"  本地用户: {contacts['local_user']}")
    for d in contacts["depts"]:
        print(f"  [部门] {d}")
    for u in contacts["offline_users"]:
        print(f"  [离线] id={u.get('UserID')} name={u.get('DisplayName')} "
              f"mobile={u.get('Mobile')} dept={u.get('DeptID')}")


def _action_offline_users(client: FmClient, args) -> None:
    users = client.conf_get_offline_users()
    print(f"离线/历史成员 {len(users)} 人：")
    for u in users:
        print(f"  id={u.get('UserID')} name={u.get('DisplayName')} "
              f"mobile={u.get('Mobile')} tel={u.get('Tel')} dept={u.get('DeptID')}")


def _action_dept_info(client: FmClient, args) -> None:
    try:
        depts = client.conf_get_dept_info()
        print(f"组织架构 {len(depts)} 个部门：")
        for d in depts:
            print(f"  {d}")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 查询失败：{exc}（服务端 handler 可能被注释，组织架构通常在 6601 里已下发）")


def _action_room_config(client: FmClient, args) -> None:
    cfg = client.conf_get_room_config()
    print(f"会议配置：{cfg}")
    if cfg.get("InviteCode"):
        print(f"  邀请码 InviteCode = {cfg.get('InviteCode')}")


def _action_invite_call(client: FmClient, args) -> None:
    nums = [n.strip() for n in str(args.invite_call).split(",") if n.strip()]
    if not nums:
        print("⚠️ 未提供被叫号码（--invite-call NUM,NUM）")
        return
    callees = [C.ConfCmd.call_info(callee_num=n, callee_nick_name=n, callee_user_id=0) for n in nums]
    ctype = "电话(CallType=1)" if args.call_type == C.CALLTYPE_PHONE else "H323(CallType=2)"
    print(f"邀请 {len(nums)} 个{ctype}联系人入会：{nums}")
    rep = client.conf_invite_call(callees, call_type=args.call_type)
    print(f"MAKECALL_REP: OprResult={rep.get('OprResult')} RemainCallCount={rep.get('RemainCallCount')}")
    for ri in rep.get("ReqCallInfo", []):
        print(f"  被叫 {ri.get('CalleeNum')} → 临时 CalleeUserID={ri.get('CalleeUserID')}")
    print("（呼叫状态变化将由 [push 6745] 打印，留意上面的 call state 行）")


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="fm_client login→meeting demo / 真机测试")
    # 所有参数均支持 FM_* 环境变量默认值：export 一次，后续裸跑 `python -m fm_client.demo`
    parser.add_argument("--addr", default=_env("FM_ADDR", ""),
                        help="C++ 风格地址链，如 TCP:10.0.0.1:1089;TCP:10.0.0.2:1089")
    parser.add_argument("--host", default=_env("FM_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=_env_int("FM_PORT", 1089))
    parser.add_argument("--user", default=_env("FM_USER", "guest1"))
    parser.add_argument("--password", default=_env("FM_PASSWORD", "1111"))
    parser.add_argument("--room-id", type=int, default=_env_int("FM_ROOM_ID", 100001))
    parser.add_argument("--room-node", default=_env("FM_ROOM_NODE", "1"))
    parser.add_argument("--room-app-id", type=int, default=_env_int("FM_ROOM_APP_ID", 11),
                        help="RoomAppID，官方默认 11")
    parser.add_argument("--text", default=_env("FM_TEXT", "hello from fm_client"))
    parser.add_argument("--mock", action="store_true", help="use built-in mock fmserver")
    parser.add_argument("--stay", type=float, default=_env_int("FM_STAY", 3),
                        help="入会后停留秒数观察推送（invite-call 建议 10）")
    # 只鉴权类
    parser.add_argument("--list-rooms", action="store_true",
                        help="只鉴权 + 拉会议室列表(30002)，打印真实 RoomID/RoomNodeID")
    parser.add_argument("--list-rooms-classified", action="store_true",
                        help="拉会议室列表 + 标固定/预约/即时（客户端推算）")
    parser.add_argument("--discover-node", action="store_true",
                        help="发 ROOMLIST 取本地 RoomNodeID（服务端 DB 异常 0x1002 也能取到）")
    parser.add_argument("--get-room-id", type=int, default=None,
                        help="用入会会议号(LoginRoomId)换算真实 RoomID(30012)")
    # 加入会议变体（也可用于下面入会后动作的登录方式）
    parser.add_argument("--login-by-meeting-no", type=int, default=None,
                        help="用会议号入会（GETROOMID 换算后进房）")
    parser.add_argument("--login-by-invite-code", default=None,
                        help="用 9 位邀请码入会（real_room_id 为真实房）")
    parser.add_argument("--login-guest", action="store_true",
                        help="访客匿名入会（UserType=0，需 VerifyMode=3 房）")
    # 入会后动作
    parser.add_argument("--contacts", action="store_true", help="入会后查通讯录快照（部门+本地用户+离线成员）")
    parser.add_argument("--offline-users", action="store_true", help="入会后查离线/历史成员(6503)")
    parser.add_argument("--dept-info", action="store_true", help="入会后查企业组织架构(6528，可能无响应)")
    parser.add_argument("--room-config", action="store_true", help="入会后查会议配置(6534，含 InviteCode)")
    parser.add_argument("--invite-call", default=None,
                        help="邀请电话/H323 入会(MAKECALL 6741)，逗号分隔多个号码，如 13800000000,13900000000")
    parser.add_argument("--call-type", type=int, default=C.CALLTYPE_PHONE, choices=(C.CALLTYPE_PHONE, C.CALLTYPE_H323),
                        help="1=电话(默认) 2=H323，配合 --invite-call")
    args = parser.parse_args(argv)

    server = None
    if args.mock:
        server = MockFmServer()
        host, port = server.host, server.port
        print(f"[mock] fmserver on {host}:{port}")
    elif args.addr:
        host, port = args.addr, None
    else:
        host, port = args.host, args.port

    try:
        if args.discover_node:
            return discover_node(args, host, port)
        if args.list_rooms:
            return list_rooms(args, host, port)
        if args.list_rooms_classified:
            return list_rooms_classified(args, host, port)
        if args.get_room_id is not None:
            return get_room_id(args, host, port)
        if args.contacts:
            return join_and_do(args, host, port, _action_contacts, "通讯录快照 conf_get_contacts")
        if args.offline_users:
            return join_and_do(args, host, port, _action_offline_users, "离线成员 conf_get_offline_users")
        if args.dept_info:
            return join_and_do(args, host, port, _action_dept_info, "组织架构 conf_get_dept_info")
        if args.room_config:
            return join_and_do(args, host, port, _action_room_config, "会议配置 conf_get_room_config")
        if args.invite_call:
            args.stay = max(args.stay, 10.0)  # 邀请呼叫至少留 10s 观察状态推送
            return join_and_do(args, host, port, _action_invite_call, "通讯录邀请 MAKECALL")
        return run(args, host, port)
    finally:
        if server is not None:
            server.stop()


if __name__ == "__main__":
    sys.exit(main())
