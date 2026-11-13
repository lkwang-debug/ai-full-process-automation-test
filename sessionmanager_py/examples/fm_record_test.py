"""录制 / 媒体 真实服务器测试脚本（交互式，每步回车继续，便于观察效果）。

用法（在 sessionmanager_py/sessionmanager_py/ 目录下）：
  # 1) 设一次环境变量（也可写进 ~/.zshrc）
  export FM_HOST=192.168.7.154 FM_USER=A7 FM_PASSWORD=<configure-locally> \
         FM_ROOM_ID=10000 FM_ROOM_NODE=396593

  # 2) 跑
  python3 examples/fm_record_test.py
"""
import os
import sys

# 让脚本能找到 fm_client / framecore_session（无论从哪运行）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fm_client import FmClient, FmListener  # noqa: E402


class PrintListener(FmListener):
    def on_user_enter(self, users):
        print("  [6602 入会]", [u.get("UserID") for u in users])

    def on_user_state_changed(self, fields):
        print("  [6609 状态变更]", fields)

    def on_conf_state_changed(self, fields):
        print("  [6610 会议状态]", fields)

    def on_chat(self, src_user_id, dst_user_id, text, msg_id=None):
        print(f"  [6612 聊天] {src_user_id}: {text}")

    def on_disconnected(self, which):
        print(f"  [断开] {which}")


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
    print("[conf] login meeting ...")
    client.conf_login(user_name=user)
    print("[conf] JOINED。下面逐个测试，每个动作按回车执行、观察服务器/客户端效果。\n")

    steps = [
        ("开麦 mic_on", client.mic_on),
        ("开摄像头 camera_on", client.camera_on),
        ("开启录制 start_record", client.start_record),
        ("暂停录制 pause_record", client.pause_record),
        ("结束录制 stop_record", client.stop_record),
        ("关麦 mic_off", client.mic_off),
        ("关摄像头 camera_off", client.camera_off),
        ("锁房 lock_room", client.lock_room),
        ("解锁 unlock_room", client.unlock_room),
    ]
    for label, fn in steps:
        input(f"按回车执行 → {label}   ")
        try:
            fn()
            print("    ✓ 已发送")
        except Exception as exc:  # noqa: BLE001
            print(f"    ✗ 失败: {exc}")

    input("\n全部完成，按回车退出 ...")
    client.conf_logout()
    client.front_logout()
    client.close()
    print("done.")


if __name__ == "__main__":
    main()
