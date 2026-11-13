"""双账号多端测试：读 JSON 配置 → 操作端 A + 观察端 B → A 发命令 → 验证 B 收到广播。

默认跑一组"真机验证过会广播"的动作（camera_on / set_subtitle / record），每个动作检查
A 自己收到回声 + B 收到广播。开箱即在真机上 ✅。

对应"8 步流程"里的两个模拟端（操作端 A + 观察端 B）。配置见 examples/multi_client.json。

用法（sessionmanager_py/sessionmanager_py/ 下）：
  python3 examples/fm_two_clients.py examples/multi_client.json
  FM_CONFIG=examples/multi_client.json python3 examples/fm_two_clients.py
  python3 examples/fm_two_clients.py                 # 默认读 examples/multi_client.json

Python 里直接用：
  from fm_client.testing import load_config, connect_pair
  a, b = connect_pair(load_config("examples/multi_client.json"))
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fm_client import conf as C  # noqa: E402
from fm_client.testing import load_config, connect_pair  # noqa: E402


# 一组"保证状态转移、稳定广播"的动作：(标签, 动作, 事件检查函数 check(events)->bool)
# A 和 B 用同一个 check 验证各自收到对应推送。
# 选这 3 个是因为它们不受房间残留状态影响（subtitle 每次新内容、record 先重置再切换）。
#
# 注：mic_on / camera_on 这类"开/关"状态变更，若上一次留在同状态会被服务端当 no-op（不广播）；
# 且 mic_on 已修（音频 dev_id 必须为 0）——自己能收 6609、与会者开麦也能广播给他人，
# 但"主持人自己开麦"在这台服务器不广播给他人（主讲人路径）。所以这类不放进默认电池，
# 要测就先发对应的 off 再 on（保证转移），或用与会者身份开麦。
def _actions(a):
    uid = a.room_user_id_int
    seq = [0]
    def uniq(prefix):
        seq[0] += 1
        return f"{prefix}-{seq[0]}"
    return [
        ("set_subtitle (6615)", lambda: a.set_subtitle(uniq("sub"), duration=3),
         lambda ev: ev.wait_for_subtitle(timeout=5)),
        ("start_record (6610)", a.start_record,
         lambda ev: ev.wait_for_recording_state(C.RECORD_RECORDING, timeout=5)),
        ("stop_record (6610)", a.stop_record,
         lambda ev: ev.wait_for_recording_state(C.RECORD_STOP, timeout=5)),
    ]


def main() -> int:
    cfg_path = (sys.argv[1] if len(sys.argv) > 1
                else os.environ.get("FM_CONFIG")
                or os.path.join(os.path.dirname(os.path.abspath(__file__)), "multi_client.json"))
    cfg = load_config(cfg_path)
    print(f"配置：{cfg['host']}:{cfg.get('port',1089)} room={cfg['room_id']} "
          f"账号={[a['username'] for a in cfg.get('accounts', [])]}")

    a, b = connect_pair(cfg)                 # 操作端 A + 观察端 B（都已登录入会）
    try:
        print(f"操作端 A：uid={a.room_user_id_int}")
        print(f"观察端 B：uid={b.room_user_id_int}")
        try:
            a.stop_record()                  # 重置录制状态，保证后续 start_record 转移
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)

        results = []
        for label, action, check in _actions(a):
            a.events.clear(); b.events.clear()
            try:
                action()
            except Exception as exc:  # noqa: BLE001
                print(f"  ❌ [{label}] 发送失败: {exc}")
                results.append(False)
                continue
            a_ok = bool(check(a.events))
            b_ok = bool(check(b.events))
            ok = a_ok and b_ok
            print(f"  {'✅' if ok else '❌'} [{label}]  A自收={'有' if a_ok else '无'}  B收={'有' if b_ok else '无'}")
            results.append(ok)
            time.sleep(0.3)

        print(f"\n结果：{sum(results)}/{len(results)} 通过")
        return 0 if all(results) else 1
    finally:
        b.close(); a.close()


if __name__ == "__main__":
    sys.exit(main())
