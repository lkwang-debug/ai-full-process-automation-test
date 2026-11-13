"""Front 域（application_id=20）：用户登录 / 进入会议室 / 房间列表 / 退出 / 注销。

权威字段源：4u_fmserver/server/FrontService/MsgProcessor.cpp（读取 :329-423，写入 :596-797）。
  - C→S 报文 ver="1.2"
  - 请求/回复配对：REP = REQ + 1
  - 字段值在子元素 ``val=`` 属性（十进制字符串）
"""
from __future__ import annotations

from typing import Optional

from .xmlcmd import XmlCommand

FRONT_APP_ID = 20
FRONT_VER = "1.2"

# cmdid（FRONT_CMDID_BASE = 30000）
USERLOGIN_REQ = 30000
USERLOGIN_REP = 30001
ROOMLIST_REQ = 30002
ROOMLIST_REP = 30003
LOGINROOM_REQ = 30004
LOGINROOM_REP = 30005
EXITROOM_REQ = 30008
EXITROOM_REP = 30009
USERLOGOUT_REQ = 30010
USERLOGOUT_REP = 30011
GETROOMID_REQ = 30012
GETROOMID_REP = 30013
GETAUTHTYPE_REQ = 30014
GETAUTHTYPE_REP = 30015

# 客户端需要"等待直接回复"的请求 → 回复 cmdid（Front 规律 +1）。
# 房间列表 / 登录 / 进房 / 退房 / 注销 / 会议号换算 / 认证类型 都有直接 REP。
PAIRS = {
    USERLOGIN_REQ: USERLOGIN_REP,
    ROOMLIST_REQ: ROOMLIST_REP,
    LOGINROOM_REQ: LOGINROOM_REP,
    EXITROOM_REQ: EXITROOM_REP,
    USERLOGOUT_REQ: USERLOGOUT_REP,
    GETROOMID_REQ: GETROOMID_REP,
    GETAUTHTYPE_REQ: GETAUTHTYPE_REP,
}


class FrontCmd:
    """Front 域命令构造器。所有 build_* 返回 XmlCommand，发往 application_id=20。"""

    app_id = FRONT_APP_ID
    ver = FRONT_VER
    pairs = PAIRS

    # ---- 30000 用户登录/鉴权 ----
    @staticmethod
    def build_userlogin(
        *,
        username: str,
        password: str,
        user_login_type: int = 1,   # 1=账号 2=姓名 3=电话
        user_type: int = 1,         # 0=匿名 1=注册
        terminal_type: int = 1,     # 1=桌面 2=硬件 3=移动
        client_type: int = 1,       # 1=视频会议终端 2=IM
        app_type: int = 11,         # 11=会议应用
        product_name: str = "FMS101",
        token: Optional[str] = None,
        **extra,
    ) -> XmlCommand:
        fields = {
            "ClientType": client_type,
            "TerminalType": terminal_type,
            "UserType": user_type,
            "AppType": app_type,
            "UserLoginType": user_login_type,
            "UserName": username,
            "UserPassword": password,
            "ProductName": product_name,
        }
        if token:
            fields["Token"] = token
        fields.update(extra)
        return XmlCommand(USERLOGIN_REQ, fields, ver=FRONT_VER)

    # ---- 30004 进入会议室（拿到 Token / Conf 地址 / RoomUserID）----
    @staticmethod
    def build_loginroom(
        *,
        room_id: int,
        room_node_id: str,
        room_app_id: int = 11,
        room_password: Optional[str] = None,
        **extra,
    ) -> XmlCommand:
        fields = {
            "RoomNodeID": room_node_id,
            "RoomID": room_id,
            "RoomAppID": room_app_id,
        }
        if room_password:
            fields["RoomPassword"] = room_password
        fields.update(extra)
        return XmlCommand(LOGINROOM_REQ, fields, ver=FRONT_VER)

    @staticmethod
    def build_exitroom(*, room_id: int, room_node_id: Optional[str] = None) -> XmlCommand:
        fields: dict = {"RoomID": room_id}
        if room_node_id is not None:
            fields["RoomNodeID"] = room_node_id
        return XmlCommand(EXITROOM_REQ, fields, ver=FRONT_VER)

    @staticmethod
    def build_userlogout() -> XmlCommand:
        # 空体（UserID 来自会话上下文）
        return XmlCommand(USERLOGOUT_REQ, {}, ver=FRONT_VER)

    @staticmethod
    def build_roomlist(*, is_get_all: int = 1, is_get_desc: int = 0) -> XmlCommand:
        # IsGetAllList: 1=全部 2=本地
        return XmlCommand(
            ROOMLIST_REQ,
            {"IsGetAllList": is_get_all, "IsGetRoomDesc": is_get_desc},
            ver=FRONT_VER,
        )

    @staticmethod
    def build_getroomid(*, login_room_id) -> XmlCommand:
        # 用"用户入会输入的会议号(LoginRoomId)"换算真实 RoomID
        return XmlCommand(GETROOMID_REQ, {"LoginRoomId": login_room_id}, ver=FRONT_VER)

    @staticmethod
    def build_getauthtype() -> XmlCommand:
        # 查认证类型（无字段）；REP 带 AuthType，如 "FSMeetingAuth"
        return XmlCommand(GETAUTHTYPE_REQ, {}, ver=FRONT_VER)


# ---- 会议室类型推算（协议不下发 RoomType，客户端按 RoomID 区间 + 时间字段推算）----
# 权威源：protocol/ProtocolCommonDefine.h:43-46（ID 基址）、
#         server/FrontService/MsgProcessor.cpp:668-669（HopeStart/End 字段）、
#         db_procedure/single/procedure.sql:467（roomType 语义）。
INSTANTROOM_IDBASE = 1000000000   # 即时会议 RoomID 起始
GROUPROOM_IDBASE = 0x70000000     # 分组会议 RoomID 起始

# 推算结果（字符串标签，给 RoomInfo 加 "MeetingType" 字段用）
ROOM_TYPE_INSTANT = "instant"     # 即时会议
ROOM_TYPE_RESERVED = "reserved"  # 预约会议
ROOM_TYPE_FIXED = "fixed"        # 固定会议

_ZERO_TIME = "0000-00-00 00:00:00"


def _to_int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def infer_meeting_type(room_info: dict) -> str:
    """按 RoomID 区间 + HopeStart/End 时间推算会议类型（ROOM_TYPE_*）。

    - 即时会议：RoomID ∈ [INSTANTROOM_IDBASE, GROUPROOM_IDBASE)
    - 预约会议：HopeStartTime 或 HopeEndTime 非零值
    - 固定会议：其余
    """
    room_id = _to_int(room_info.get("RoomID"))
    if INSTANTROOM_IDBASE <= room_id < GROUPROOM_IDBASE:
        return ROOM_TYPE_INSTANT
    start = str(room_info.get("HopeStartTime") or "").strip()
    end = str(room_info.get("HopeEndTime") or "").strip()
    if (start and start != _ZERO_TIME) or (end and end != _ZERO_TIME):
        return ROOM_TYPE_RESERVED
    return ROOM_TYPE_FIXED
