"""会前功能真实服务器测试脚本（交互式菜单，便于逐项观察）。

覆盖：会议室列表分类、通讯录查询（部门/离线成员/组织架构/会议配置）、通讯录邀请（MAKECALL/挂断/查列表）。

用法（在 sessionmanager_py/sessionmanager_py/ 目录下）：
  # 1) 设一次环境变量（也可写进 ~/.zshrc）
  export FM_HOST=192.168.7.154 FM_PORT=1089 FM_USER=A7 FM_PASSWORD=<configure-locally> \
         FM_ROOM_ID=10000 FM_ROOM_NODE=396593

  # 2) 跑
  python3 examples/fm_pre_meeting_test.py

入会后进入菜单，输入编号执行、观察服务器/客户端效果；邀请/挂断会提示输入号码。
"""
import os
import sys

# 让脚本能找到 fm_client / framecore_session（无论从哪运行）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fm_client import FmClient, FmListener  # noqa: E402
from fm_client import conf as C  # noqa: E402


class PrintListener(FmListener):
    def on_front_connected(self):
        print("  [front 连接]")

    def on_conf_connected(self):
        print("  [conf 连接]")

    def on_user_enter(self, users):
        print("  [6602 入会]", [(u.get("UserID"), u.get("DisplayName")) for u in users])

    def on_user_leave(self, user_id):
        print(f"  [6605 离场] {user_id}")

    def on_user_state_changed(self, fields):
        print("  [6609 状态变更]", fields)

    def on_conf_state_changed(self, fields):
        print("  [6610 会议状态]", fields)

    def on_chat(self, src_user_id, dst_user_id, text, msg_id=None):
        print(f"  [6612 聊天] {src_user_id}: {text}")

    def on_call_state_notify(self, fields):
        print(f"  [6745 呼叫状态] {fields}")

    def on_disconnected(self, which):
        print(f"  [断开] {which}")


def _parse_nums(prompt):
    raw = input(prompt).strip()
    return [n.strip() for n in raw.split(",") if n.strip()]


def _menu():
    print(
        "\n===== 会前功能测试菜单 =====\n"
        "  1) 会议室列表分类（固定/预约/即时）\n"
        "  2) 通讯录快照（部门+本地用户+离线成员）\n"
        "  3) 离线/历史成员（6503）\n"
        "  4) 企业组织架构（6528，handler 可能被注释）\n"
        "  5) 会议配置 / 邀请码（6534）\n"
        "  6) 邀请电话入会（MAKECALL 6741）\n"
        "  7) 查已邀请拨号列表（6746）\n"
        "  8) 挂断呼叫（6743）\n"
        "  0) 退出\n"
    )


def main():
    host = os.environ.get("FM_HOST", "127.0.0.1")
    port = int(os.environ.get("FM_PORT", "1089"))
    user = os.environ.get("FM_USER", "guest1")
    pwd = os.environ.get("FM_PASSWORD", "1111")
    room_id = int(os.environ.get("FM_ROOM_ID", "100001"))
    node = os.environ.get("FM_ROOM_NODE", "1")

    client = FmClient(host, port, PrintListener(), request_timeout=8.0)
    print(f"[front] login user={user} room={room_id} node={node} ...")
    client.front_login(user, pwd, room_id=room_id, room_app_id=11, room_node_id=node)
    print(f"[front] OK  real_room_id={client.real_room_id} room_user_id={client.room_user_id}")
    print("[conf] login meeting ...")
    client.conf_login(user_name=user)
    print(f"[conf] JOINED  right={client.user_right}")

    while True:
        _menu()
        choice = input("选择 > ").strip()
        try:
            if choice == "0":
                break
            elif choice == "1":
                rep = client.front_roomlist_classified(is_get_all=1, is_get_desc=1)
                infos = rep.get("RoomInfo")
                if isinstance(infos, dict):
                    infos = [infos]
                print(f"  共 {len(infos or [])} 个房间：")
                for ri in infos or []:
                    print(f"    [{ri.get('MeetingType')}] RoomID={ri.get('RoomID')} "
                          f"Name={ri.get('Name')} 时间={ri.get('HopeStartTime')}~{ri.get('HopeEndTime')}")
            elif choice == "2":
                contacts = client.conf_get_contacts()
                print(f"  本地用户: {contacts['local_user']}")
                print(f"  部门({len(contacts['depts'])}): {contacts['depts']}")
                print(f"  离线成员({len(contacts['offline_users'])}):")
                for u in contacts["offline_users"]:
                    print(f"    id={u.get('UserID')} name={u.get('DisplayName')} mobile={u.get('Mobile')}")
            elif choice == "3":
                users = client.conf_get_offline_users()
                print(f"  离线成员 {len(users)} 人：")
                for u in users:
                    print(f"    id={u.get('UserID')} name={u.get('DisplayName')} "
                          f"mobile={u.get('Mobile')} tel={u.get('Tel')} dept={u.get('DeptID')}")
            elif choice == "4":
                try:
                    depts = client.conf_get_dept_info()
                    print(f"  组织架构 {len(depts)} 个部门：")
                    for d in depts:
                        print(f"    {d}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  ⚠️ 失败：{exc}（handler 可能被注释，组织架构通常在 6601 已下发）")
            elif choice == "5":
                cfg = client.conf_get_room_config()
                print(f"  会议配置: {cfg}")
            elif choice == "6":
                nums = _parse_nums("  被叫号码(逗号分隔，如 13800000000,13900000000): ")
                if not nums:
                    print("  未输入，跳过")
                    continue
                callees = [C.ConfCmd.call_info(callee_num=n, callee_nick_name=n, callee_user_id=0) for n in nums]
                rep = client.conf_invite_call(callees, call_type=C.CALLTYPE_PHONE)
                print(f"  MAKECALL_REP: OprResult={rep.get('OprResult')} "
                      f"RemainCallCount={rep.get('RemainCallCount')}")
                for ri in rep.get("ReqCallInfo", []):
                    print(f"    被叫 {ri.get('CalleeNum')} → CalleeUserID={ri.get('CalleeUserID')}")
                print("  （留意上方 [6745 呼叫状态] 推送）")
            elif choice == "7":
                rep = client.conf_query_call_list()
                print(f"  OprResult={rep.get('OprResult')} 列表：")
                for ci in rep.get("CalleeInfo", []):
                    print(f"    {ci.get('CalleeNum')} uid={ci.get('CalleeUserID')} state={ci.get('CallState')}")
            elif choice == "8":
                nums = _parse_nums("  要挂断的号码(逗号分隔): ")
                if not nums:
                    print("  未输入，跳过")
                    continue
                rep = client.conf_hangup(nums)
                print(f"  HANGUP_REP: {rep}")
            else:
                print("  无效选择")
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ 执行失败: {exc}")

    print("\n[teardown] logout ...")
    client.conf_logout()
    client.front_logout()
    client.close()
    print("done.")


if __name__ == "__main__":
    main()
