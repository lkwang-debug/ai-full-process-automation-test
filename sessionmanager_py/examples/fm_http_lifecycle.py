"""HTTP 建会 → 信令入会 → 操作 → 退会 完整闭环。

读 http_api.json（Web API）+ multi_client.json（信令账号），演示两条通道衔接：
  1. WebApiClient.create_instant_meeting()  → 拿到 roomId / inviteCode
  2. FmTestClient.front_login(roomId) + conf_login()  → 信令入会
  3. set_subtitle() + wait_for_subtitle()  → 操作并断言自收广播
  4. conf_logout + front_logout  → 退会

用法（sessionmanager_py/sessionmanager_py/ 下）：
  python3 examples/fm_http_lifecycle.py [http_api.json] [multi_client.json]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fm_client import conf as C  # noqa: E402
from fm_client.http_api import WebApiClient  # noqa: E402
from fm_client.testing import FmTestClient, load_config  # noqa: E402


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    http_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "http_api.json")
    sig_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "multi_client.json")
    http_cfg = load_config(http_path)
    sig_cfg = load_config(sig_path)
    account = sig_cfg["accounts"][0]

    # 1) HTTP 建即时会议
    api = WebApiClient.from_config(http_cfg)
    rep = api.create_instant_meeting(meeting_name="http闭环测试", user_name=account["username"])
    data = rep["data"]
    room_id = int(data["roomId"])
    invite_code = data.get("inviteCode")
    print(f"[HTTP] 建即时会议 ✅  roomId={room_id}  inviteCode={invite_code}  creator={data.get('creatorId')}")

    # 2) 信令入会（用新建的 roomId）
    client = FmTestClient(sig_cfg["host"], int(sig_cfg.get("port", 1089)),
                          request_timeout=float(sig_cfg.get("request_timeout", 8.0)),
                          conf_protocol_version=sig_cfg.get(
                              "conf_protocol_version", C.CONF_VER_CS))
    try:
        client.front_login(account["username"], account["password"],
                            room_id=room_id, room_node_id=str(sig_cfg["room_node_id"]),
                            room_app_id=int(sig_cfg.get("room_app_id", 11)))
        client.conf_login(user_name=account["username"])
        print(f"[信令] 入会 ✅  uid={client.room_user_id_int}  right={client.user_right_int}")

        # 3) 操作 + 断言自收广播
        client.events.clear()
        client.set_subtitle("http闭环字幕", duration=3)
        subs = client.events.wait_for_subtitle(timeout=5)
        ok = bool(subs)
        print(f"[操作] set_subtitle → 自收 6615 {'✅' if ok else '❌'}  {subs[-1] if subs else '未收到'}")

        # 4) 退会
        client.conf_logout()
        client.front_logout()
        print("[退会] ✅")
        return 0 if ok else 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
