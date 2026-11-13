"""扩展会议命令真机探针：逐条尝试 MeetingOps 新命令，报告真机支持情况。

每条命令独立登录+入会（互不影响：一条把会话断开，下一条重新连），输出：
  ✓ cmdid  描述 → 关键返回字段
  ✗ cmdid  描述 → 错误（session closed=服务器不认该命令并断开；timeout=handler 未实现）

用法（sessionmanager_py/sessionmanager_py/ 下）：
  export FM_HOST=192.168.7.154 FM_USER=A7 FM_PASSWORD=<configure-locally> \
         FM_ROOM_ID=10000 FM_ROOM_NODE=396593
  python3 examples/fm_probe_new_cmds.py            # 跑全部
  python3 examples/fm_probe_new_cmds.py rename right subtitle   # 只跑指定项

⚠️ 部分命���有副作用（rename 改房名、create_group 建分组、send_sysmsg 广播），
   在测试房间跑无妨；生产环境慎用。默认探针用 room_id=10000。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fm_client import FmClient, FmListener, conf as C  # noqa: E402

HOST = os.environ.get("FM_HOST", "127.0.0.1")
PORT = int(os.environ.get("FM_PORT", "1089"))
USER = os.environ.get("FM_USER", "guest1")
PWD = os.environ.get("FM_PASSWORD", "1111")
ROOM_ID = int(os.environ.get("FM_ROOM_ID", "100001"))
NODE = os.environ.get("FM_ROOM_NODE", "1")
SYS_MSG_ID = int(os.environ.get("FM_SYS_MSG_ID", "6"))


class QuietListener(FmListener):
    pass


# (key, cmdid, 描述, 调用 lambda client) —— 调用用 try 包裹，返回 (ok, summary)
PROBES = [
    ("rename", C.MEETINGRENAMEREQ, "改会议室名 6527",
     lambda c: c.rename_meeting("probe-room")),
    ("right", C.OPERATIONRIGHTREQ, "申请白板操作权 6530",
     lambda c: c.request_right(C.RIGHT_TYPE_WHITEBOARD)),
    ("subtitle", C.MEETINGCHAR, "设会场字幕 6515",
     lambda c: c.set_subtitle("probe-subtitle", duration=1)),
    ("sysmsg", C.SYSMSGREQ, "发系统消息 6518",
     lambda c: c.send_sysmsg(sys_msg_id=SYS_MSG_ID)),
    ("window", C.WNDSTATE, "上报视图状态 6513",
     lambda c: c.set_window_state(mode=C.WND_MODE_VIDEO, video_relay_id=1)),
    ("recall", C.RECALLMSGREQ, "撤回消息 6568",
     lambda c: c.recall_msg("probe-1")),
    ("sendfile", C.SENDFILE, "发文件通知 6516",
     lambda c: c.send_file([0], C.ConfCmd.file_info(guid="probe", name="p.txt", size=1))),
    ("group", C.CREATEGROUP, "创建分组会议 6523",
     lambda c: c.create_group([C.ConfCmd.group_room(theme="probe-group")])),
    ("allowknock", C.ALLOWKNOCK, "答复敲门 6504",
     lambda c: c.allow_knock(0, allow=1)),
    ("offline", C.REQOFFLINES, "离线成员 6503",
     lambda c: c.conf_get_offline_users()),
    ("roomconfig", C.GETROOMCONFIGREQ, "会议配置 6534(已知会断开)",
     lambda c: c.conf_get_room_config()),
    ("deptinfo", C.DEPTINFOREQ, "组织架构 6528(已知超时)",
     lambda c: c.conf_get_dept_info()),
]


def run_one(key, cmdid, desc, fn):
    """独立登录+入会，跑一条，返回结果行。"""
    client = FmClient(HOST, PORT, QuietListener(), request_timeout=6.0)
    try:
        client.front_login(USER, PWD, room_id=ROOM_ID, room_app_id=11, room_node_id=NODE)
        client.conf_login(user_name=USER)
    except Exception as exc:  # noqa: BLE001
        client.close()
        return f"✗ {cmdid:5d} {desc:24s} → 登录失败: {exc}"
    try:
        rep = fn(client)
        summary = ""
        if isinstance(rep, dict):
            for k in ("Result", "Name", "OprResult", "Msg", "RoomID"):
                if k in rep:
                    summary = f"{k}={rep[k]!r}"
                    break
            if not summary:
                summary = f"{len(rep)} field(s)"
        elif isinstance(rep, list):
            summary = f"{len(rep)} item(s)"
        elif rep is None:
            summary = "fire-and-forget OK"
        client.conf_logout()
        client.front_logout()
        return f"✓ {cmdid:5d} {desc:24s} → {summary}"
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "session closed" in msg or "0x1004" in msg:
            tag = "服务器不认→断开"
        elif "no response" in msg or "TimeoutError" in type(exc).__name__:
            tag = "超时(handler 未实现?)"
        else:
            tag = type(exc).__name__
        return f"✗ {cmdid:5d} {desc:24s} → {tag}: {exc}"
    finally:
        client.close()


def main():
    only = set(sys.argv[1:])
    print(f"探针 → {HOST}:{PORT} user={USER} room={ROOM_ID} node={NODE}\n")
    for key, cmdid, desc, fn in PROBES:
        if only and key not in only:
            continue
        print("  " + run_one(key, cmdid, desc, fn))


if __name__ == "__main__":
    main()
