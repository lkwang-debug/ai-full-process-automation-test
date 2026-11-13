"""完整入会→操作→退会 真机诊断：两个账号，逐个动作看 A/B 各自真实收到哪些广播。

重点验证一个假设：真机上 mic_on(6509) 不广播，可能是没先 report_av_info(6508) 上报音频设备。
用法：python3 examples/fm_lifecycle_test.py examples/multi_client.json
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fm_client import conf as C  # noqa: E402
from fm_client.testing import load_config, connect_pair  # noqa: E402


def summarize(ev) -> str:
    parts = []
    for name, dq, tag in [("user_enters", ev.user_enters, "6602"),
                          ("user_leaves", ev.user_leaves, "6605"),
                          ("user_states", ev.user_states, "6609"),
                          ("av_infos", ev.av_infos, "6608"),
                          ("conf_states", ev.conf_states, "6610"),
                          ("chats", ev.chats, "6612"),
                          ("subtitles", ev.subtitles, "6615")]:
        n = len(ev.snapshot(getattr(ev, name)))
        if n:
            parts.append(f"{tag}×{n}")
    return ", ".join(parts) or "—"


def try_action(label, fn, a, b, wait=2.0):
    a.events.clear(); b.events.clear()
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        print(f"  [{label:28s}] 发送失败: {exc}")
        return
    time.sleep(wait)
    print(f"  [{label:28s}] A收到: {summarize(a.events):20s} | B收到: {summarize(b.events)}")


def main() -> int:
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1
                      else os.environ.get("FM_CONFIG")
                      or os.path.join(os.path.dirname(os.path.abspath(__file__)), "multi_client.json"))
    a, b = connect_pair(cfg)
    try:
        print(f"A={a.room_user_id_int}({cfg['accounts'][0]['username']})  "
              f"B={b.room_user_id_int}({cfg['accounts'][1]['username']})\n")

        # 入会阶段：B 后入会，A 应已收到 B 的 6602
        a_enters = [e.user_id for e in a.events.snapshot(a.events.user_enters)]
        print(f"[入会] A 看到的成员入会(6602) uid: {a_enters}  （含 B={b.room_user_id_int} 即跨端入会广播生效）")

        print("\n[操作] 逐个动作观察广播：")
        try_action("chat('lifecycle-hi')", lambda: a.chat("lifecycle-hi"), a, b)
        try_action("mic_on（无设备上报）", a.mic_on, a, b)
        try_action("camera_on（无设备上报）", a.camera_on, a, b)
        # 关键：先上报音视频设备，再开麦
        try_action("report_av_info(6508)",
                   lambda: a.report_av_info(audio=[C.ConfCmd.av_device(C.AV_OP_ADD, 1, "mic")]), a, b)
        try_action("mic_on（设备上报后）", a.mic_on, a, b)
        try_action("camera_on + report_video",
                   lambda: (a.report_av_info(video=[C.ConfCmd.av_device(C.AV_OP_ADD, 1, "cam")]),
                            a.camera_on()), a, b)
        try_action("set_subtitle('s',1)", lambda: a.set_subtitle(" lifecycle-sub", duration=1), a, b)
        try_action("start_record", a.start_record, a, b)
        try_action("stop_record", a.stop_record, a, b)

        # 退会阶段：A 离会，B 应收到 6605
        a.events.clear(); b.events.clear()
        print("\n[退会] A conf_logout()")
        a.conf_logout()
        time.sleep(2)
        print(f"  A 收到: {summarize(a.events):20s} | B 收到: {summarize(b.events)}  （B 应见 6605）")
        return 0
    finally:
        a.close(); b.close()


if __name__ == "__main__":
    sys.exit(main())
