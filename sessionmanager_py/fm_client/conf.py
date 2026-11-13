"""Conf 域（application_id=11）：入会 / 成员 / 聊天 / 状态 / 离开。

权威字段源：4u_fmserver/server/ConfService/ConfMsgProcessor.cpp
  （LOGINREQ :89-200、LOGOUTREQ :243-260、CHANGEUSERSTATE :366-525、CHATMSG :633-660、
   INITCOMPLETE :890-900、MakeLoginRep :1645-1989、USERENTER :1991-2056、
   USERSTATECHANGED 系列 :2237-2527、USERLEAVE :2634-2646）。
  - 构造器默认 C→S ver="1.2"；FmClient 可按实例统一覆盖 Conf 请求版本
  - 6601 登录回复 ver="4.0"
  - 请求/回复配对：REP/Notify = REQ + 100
  - 6517 无回复；6509/6512 的 +100 是"广播通知"（含回发给发送者），按推送处理
  - 字段值在子元素 ``val=`` 属性；<Audio>/<Video> 为容器（ID/State 为 val= 子元素）
"""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from typing import Any, List, Optional, Union

from .xmlcmd import XmlCommand
from .interaction import InteractionBuilders

CONF_APP_ID = 11
CONF_VER_CS = "1.2"           # 客户端默认发送版本；FmClient 实例可覆盖
CONF_VER_LOGINREP = "4.0"     # 6601 服务端回复

CHAT_FONT_ATTRIBUTES = frozenset({
    "Name", "Width", "Height", "Weight", "Underline",
    "StrikeOut", "CharSet", "Italic",
})

# cmdid（CONF_CMDID_BASE = 6500）
LOGINREQ = 6501
LOGINREP = 6601
USERENTER = 6602
LOGOUTREQ = 6505
USERLEAVE = 6605
KICKUSER = 6506
USERKICKED = 6606
CLOSEROOM = 6507
ROOMCLOSED = 6607
AVINFO = 6508
AVINFONOTIFY = 6608
CHANGEUSERSTATE = 6509
USERSTATECHANGED = 6609
CHANGECONFSTATE = 6510
CONFSTATECHANGED = 6610
REQCHAIRRIGHT = 6511          # 申请(1)/放弃(0)主持人
REQCHAIRRIGHTRET = 6611       # 仅失败时回；成功走 6609 改 Right
CHATMSG = 6512
CHATMSGNOTIFY = 6612
SILENT = 6514                 # 全场静音（主持人下发）
SILENTNOTIFY = 6614
INITCOMPLETE = 6517

# 最新 ConfProtocol.xml 中的会议数据 / AV 合成 / 视频字幕
SETROOMDATA = 6519             # 主持人设置会场数据（重复 DataItem）
ROOMDATANOTIFY = 6619          # 广播：会场数据更新
GETAVMIXPARAMREQ = 6520        # 查询 AV 合成参数
GETAVMIXPARAMREP = 6620
SETAVMIXPARAM = 6521           # 设置 AV 合成参数（通知类）
SETVIDEOSUBTITLE = 6522        # 设置视频画面字幕参数
VIDEOSUBTITLENOTIFY = 6622     # 广播：视频字幕参数
CHATCHECKRESULT = 6629         # 广播：聊天消息审批结果
CHATCHECKREQ = 6529
SETINTERACTIVELANGUAGE = 6540
INTERACTIVELANGUAGENOTIFY = 6640
ROOMLANGUAGESNOTIFY = 6641
REMOTEAVREQ = 6542
REMOTEAVNOTIFY = 6642
REMOTEAVREPLY = 6543
REMOTEAVRESULTNOTIFY = 6643

# 通讯录 / 组织架构 / 会议配置（CONF_CMDID_BASE 段，REP = REQ + 100）
# 权威源：protocol/ConfProtocol.h / ConfProtocol.xml
REQOFFLINES = 6503            # 请求离线/历史成员列表（空 body）；处理 LocalRoom.cpp:1369
OUTLINE = 6603                # 离线成员列表回复（重复 <User>）
DEPTINFOREQ = 6528            # 请求企业组织架构（空 body）；handler ConfMsgProcessor.cpp:1251（可能被注释，best-effort）
DEPTINFOREP = 6628            # 组织架构回复（重复 <Dept>{DeptID,DeptName,ParentDeptID,NodeID}，末尾 <DeptEnd/>）
GETROOMCONFIGREQ = 6534       # 请求会议配置（InviteCode 等占位项）
GETROOMCONFIGREP = 6634       # 会议配置回复（含 9 位 <InviteCode>）

# 终端呼叫子块（TERMINAL_BASE=6740，注意 REP = REQ + 1，与上面 +100 不同）
# 权威源：ConfProtocol.h 末尾 TERMINAL_BASE 段、PhoneAccessMsgCodec.cpp
TERMINAL_BASE = 6740
MAKECALL_REQ = 6741           # 邀请电话/H323 联系人入会（重复 <ReqCallInfo>{CalleeNum,CalleeNickName,CalleeUserID}）
MAKECALL_REP = 6742
HANGUP_REQ = 6743             # 挂断（<ReqCallList> 内重复 <CalleeNum>）
HANGUP_REP = 6744
CALLSTATE_NOTIFY = 6745       # 呼叫状态通知（S→C 推送：CallState/ReleaseCause）
CALL_LIST_REQ = 6746          # 查询已邀请拨号列表（CallerUserID/RoomID）
CALL_LIST_REP = 6747          # 回复：重复 <CalleeInfo>{CalleeNum,CalleeUserID,CallState}

# ====== 扩展会议控制（CONF_CMDID_BASE 段，REP = REQ + 100；权威源 ConfProtocol.h/.xml）======
# 等候室 / 敲门
ALLOWKNOCK = 6504             # 主持人答复敲门（UserID/Allow 0拒/1允）；无回复
KNOCKNOTIFY = 6604            # 推送：有人敲门（UserID/DisplayName/Online）
WAITOPENDOOR = 6617           # 推送：敲门端等待主持人开门（空体）
# 分组会议（breakout）
CREATEGROUP = 6523            # 主持人创建分组（重复 <RoomInfo>{Theme,MaxUserCount,EnableJoin,EnableLeave,<User>{ID,Right}}>）
GROUPINFO = 6623              # 回复：新建分组信息（<RoomInfo>{RoomID,Theme,Creator,Duration,MaxUserCount,...}）
ENTERGROUPREQ = 6524          # 加入分组（RoomID + 重复 <User>{ID,Right}）
ENTERGROUPREP = 6624          # 回复/推送：进入分组（SrcUserID/RoomSrvNodeID/SrvAuthCode/RoomID/Theme/Token/SrvAddr）
CHANGEGROUPSTATEREQ = 6525    # 主持人结束分组（重复 <RoomID>）
CHANGEGROUPSTATEREP = 6625    # 回复：结束分组（重复 <RoomID>）
ENTERMAINREQ = 6526           # 主持人把用户拉回主会议（重复 <UserID>）
ENTERMAINREP = 6626           # 回复/推送：回到主会议（SrcUserID）
# 操作权（布局/轮询/共享/白板/录制等唯一操作权）
OPERATIONRIGHTREQ = 6530      # 申请/释放操作权（Operation 1申请/0释放, RightType 见 RIGHT_TYPE_*）
OPERATIONRIGHTREP = 6630      # 回复（Result/OccupiedUserID/Operation/RightType）
ROLEPERMISSIONNOTIFY = 6631   # 推送：角色权限配置（重复 <Role>{RoleCode,<Permissions>...}，末尾 <RoleEnd/>）
CHANGEROLEPERMISSION = 6532    # 修改一个或多个角色的功能权限
ROLEPERMISSIONCHANGED = 6632   # 广播：角色权限变更
# 会议辅助
MEETINGRENAMEREQ = 6527       # 改会议室名（Name）
MEETINGRENAMEREP = 6627       # 回复：改名通知（Name）
MEETINGCHAR = 6515            # 主持人设会场字幕（Duration 分钟/0永久, Data 字幕文本）
MEETINGCHARNOTIFY = 6615      # 推送：字幕转发（Data）
CLOSESUBTITLEREQ = 6550       # 关闭会场字幕（空 body）
CLOSESUBTITLEREP = 6650       # 回复：字幕关闭（空 body）
SYSMSGREQ = 6518              # 发系统通知/广播（DstUserID 0=广播, SysMsgID）
SYSMSG = 6618                 # 回复/推送：系统消息（Type 1文字/2窗口/3WEB/4改主题, Msg）
SENDFILE = 6516               # 发文件通知（<DstUser>{UserID...}, <FileInfo>{Guid,Name,Size,...}）
SENDFILENOTIFY = 6616         # 推送：通知他人收文件（SrcUserID/<FileInfo>）
RECALLMSGREQ = 6568           # 撤回聊天消息（MsgID）
RECALLMSGREP = 6668           # 回复：MsgID/Result
RECALLCHATMSGNOTIFY = 6669    # 推送：MsgID/SrcUserID
# 视图 / 布局
WNDSTATE = 6513               # 上报当前视图状态/布局（Mode/Full/DataActive/VideoRelayID/重复 Video/DstInfo 定向）
WNDSTATENOTIFY = 6613         # 推送：视图状态/布局转发
# 视频轮询
CHANGEVIDEOPOLLINGREQ = 6533  # 视频轮询 增/改/删（重复 <VideoPolling>{Operation,Name,UserList,Interval,Screen,Window,Status}）
VIDEOPOLLINGNOITFY = 6633     # 推送：轮询状态（<VideoPolling>{Operation 1用户变更/2停止,CurrentUserID,NextUserID,...}）
CHANGEUSERPERMISSION = 6535    # 修改指定用户功能权限
USERPERMISSIONCHANGED = 6635   # 广播：指定用户权限变更
SYSTEMNOTIFY = 6636           # 旧版系统通知；新协议为操作权占用状态（兼容保留）
SERVERRECORDERRORNOTIFY = 6637  # 录制异常：RecordType/Error/Description
WAITINGUSERLISTNOTIFY = 6647  # 等候室用户列表分页；UserList.End 结束
PRODUCTFEATURESNOTIFY = 6639   # 登录后推送产品能力列表，以 End 结束
# Web 端触发的推送（S→C）
MULTICASTNOTIFY = 6651         # 推送：客户端组播状态
WEBKICKUSER = 6652            # 历史 Web 请出推送；当前 HTTP USER_REMOVE 统一下发 6606
WEBCLOSEROOM = 6653           # 推送：Web 端关闭会议室（operator/appBackNotify）
WEBSENDNOTIFY = 6654          # 推送：Web 端发通知（UserID/operator/appBackNotify）

# 人脸识别 / 人脸点名
FACERECOGNITIONREQ = 6555
FACERECOGNITIONREP = 6655
FACEROLLCALLSTARTREQ = 6556
FACEROLLCALLSTARTREP = 6656
FACEROLLCALLSTARTNOTIFY = 6657
FACEROLLCALLRESULTREQ = 6558
FACEROLLCALLRESULTNOTIFY = 6658
FACEROLLCALLCANCELREQ = 6559
FACEROLLCALLCANCELNOTIFY = 6659

# 服务器透明中转；具体业务由内层 <msg id="6701...6723"> 区分
RELAYMESSAGE = 6700

# Extended protocols: only commands with direct replies belong in PAIRS.
SETMULTIDATASHARER = 6538
MULTIDATASHARERNOTIFY = 6638
QUERYUSERFEATURES = 6544
USERFEATURESREP = 6644
QUERYFEATUREUSERS = 6545
FEATUREUSERSREP = 6645
MODIFYSELFSTATERESULT = 6546  # client acknowledgement, no direct reply
MODIFYSELFSTATENOTIFY = 6646
ABILITYRENEGOTIATION = 6560
ABILITYRENEGOTIATIONREP = 6660
H5VOTE = 6561
H5VOTENOTIFY = 6661
MODIFYGROUPCONFIG = 6565
MODIFYGROUPCONFIGREP = 6665
GROUPUSERSTATENOTIFY = 6666
WAITINGROOMINFO = 6667
ALLOWWAITINGUSERS = 6570
ALLOWWAITINGUSERSREP = 6670
SETASRSUBTITLE = 6571
SETASRSUBTITLEREP = 6671
SETUSERCUSTOMDATA = 6572
USERCUSTOMDATANOTIFY = 6672

# 客户端"等待直接回复"的请求：仅登录（+100）。其余为通知/广播，发送即返回。
# 客户端"等待直接回复"的请求（+100 段与终端呼叫 +1 段混在一张表，只记 REQ→REP 映射）。
PAIRS = {
    SETMULTIDATASHARER: MULTIDATASHARERNOTIFY,
    QUERYUSERFEATURES: USERFEATURESREP,
    QUERYFEATUREUSERS: FEATUREUSERSREP,
    ABILITYRENEGOTIATION: ABILITYRENEGOTIATIONREP,
    MODIFYGROUPCONFIG: MODIFYGROUPCONFIGREP,
    ALLOWWAITINGUSERS: ALLOWWAITINGUSERSREP,
    SETASRSUBTITLE: SETASRSUBTITLEREP,
    LOGINREQ: LOGINREP,
    REQOFFLINES: OUTLINE,
    DEPTINFOREQ: DEPTINFOREP,
    GETROOMCONFIGREQ: GETROOMCONFIGREP,
    GETAVMIXPARAMREQ: GETAVMIXPARAMREP,
    FACERECOGNITIONREQ: FACERECOGNITIONREP,
    FACEROLLCALLSTARTREQ: FACEROLLCALLSTARTREP,
    MAKECALL_REQ: MAKECALL_REP,       # 终端呼叫子块：REP = REQ + 1
    HANGUP_REQ: HANGUP_REP,
    CALL_LIST_REQ: CALL_LIST_REP,
    CREATEGROUP: GROUPINFO,           # 下面均为 CONF 主段 REP = REQ + 100
    CHANGEGROUPSTATEREQ: CHANGEGROUPSTATEREP,
    OPERATIONRIGHTREQ: OPERATIONRIGHTREP,
    MEETINGRENAMEREQ: MEETINGRENAMEREP,
    RECALLMSGREQ: RECALLMSGREP,
    CLOSESUBTITLEREQ: CLOSESUBTITLEREP,
}

# 推送类（S→C 广播）→ 走 FmListener
PUSHES = {
    SERVERRECORDERRORNOTIFY, WAITINGUSERLISTNOTIFY,
    INTERACTIVELANGUAGENOTIFY, ROOMLANGUAGESNOTIFY,
    REMOTEAVNOTIFY, REMOTEAVRESULTNOTIFY,
    MULTIDATASHARERNOTIFY, FEATUREUSERSREP, MODIFYSELFSTATENOTIFY,
    H5VOTENOTIFY, GROUPUSERSTATENOTIFY, WAITINGROOMINFO, USERCUSTOMDATANOTIFY,
    USERENTER, USERLEAVE, USERSTATECHANGED, CHATMSGNOTIFY,
    USERKICKED, ROOMCLOSED, AVINFONOTIFY, CONFSTATECHANGED,
    REQCHAIRRIGHTRET, SILENTNOTIFY, CALLSTATE_NOTIFY,
    KNOCKNOTIFY, WAITOPENDOOR, MEETINGCHARNOTIFY, SENDFILENOTIFY,
    WNDSTATENOTIFY, ROLEPERMISSIONNOTIFY, RECALLCHATMSGNOTIFY,
    WEBKICKUSER, WEBCLOSEROOM, WEBSENDNOTIFY, VIDEOPOLLINGNOITFY,
    # 同一 cmdid 对发送者是 response，对其他客户端是 notify。
    SYSMSG, ENTERGROUPREP, ENTERMAINREP, CLOSESUBTITLEREP,
    # ConfProtocol.xml 新增的服务端主动下行。
    ROOMDATANOTIFY, VIDEOSUBTITLENOTIFY, CHATCHECKRESULT,
    ROLEPERMISSIONCHANGED, USERPERMISSIONCHANGED, SYSTEMNOTIFY,
    MULTICASTNOTIFY, PRODUCTFEATURESNOTIFY, FACEROLLCALLSTARTNOTIFY,
    FACEROLLCALLRESULTNOTIFY, FACEROLLCALLCANCELNOTIFY, RELAYMESSAGE,
}

# 设备上报 Operation 取值（AVINFO）
AV_OP_ADD, AV_OP_MODIFY, AV_OP_REMOVE = 1, 2, 3
# 主席申请 ChairRight 取值
CHAIR_APPLY, CHAIR_RELEASE = 1, 0
# 录制状态 ServerRecordStatus（CHANGECONFSTATE 字段，控制开启/暂停/结束录制）
RECORD_STOP, RECORD_PAUSE, RECORD_RECORDING = 0, 1, 2

# 音频/视频 State 取值（与 4u 一致）
AUDIO_OFF, AUDIO_WAITING, AUDIO_SPEAKING = 0, 1, 2
VIDEO_OFF, VIDEO_BROADCAST = 0, 2

# 通讯录呼叫 CallType（MAKECALL/HANGUP/CALL_LIST）
CALLTYPE_PHONE, CALLTYPE_H323 = 1, 2
# 呼叫状态 CallState（CALLSTATE_NOTIFY 推送；ConfProtocol.xml:1826-1832）
(CALL_STATE_NULL, CALL_STATE_CALLING, CALL_STATE_INCOMING, CALL_STATE_EARLY,
 CALL_STATE_CONNECTING, CALL_STATE_CONFIRMED, CALL_STATE_DISCONNECTED) = range(7)
# 断开原因 ReleaseCause（CALLSTATE_NOTIFY 推送）
(RELEASE_NORMAL, RELEASE_BUSY, RELEASE_REJECT, RELEASE_TIMEOUT,
 RELEASE_NET_UNREACHABLE, RELEASE_GW_UNKNOWN) = 0, 1, 2, 3, 4, 5

# 操作权 Operation（OPERATIONRIGHTREQ 字段）
RIGHT_OP_RELEASE, RIGHT_OP_APPLY = 0, 1
# 操作权 RightType（OPERATIONRIGHTREQ 字段；ConfProtocol.h RIGHT_TYPE_*）
RIGHT_TYPE_LAYOUT = 1          # 布局
RIGHT_TYPE_VIDEO_POLLING = 2   # 视频轮询
RIGHT_TYPE_MANUAL_ROLL = 3     # 手动点名
RIGHT_TYPE_SCREEN_SHARE = 4    # 屏幕共享
RIGHT_TYPE_MEDIA_SHARE = 5     # 媒体共享
RIGHT_TYPE_WHITEBOARD = 6      # 白板
RIGHT_TYPE_QUICK_ROLL = 7      # 快速签到
RIGHT_TYPE_USER_SORT = 8       # 参会人排序
RIGHT_TYPE_LIVE_PUSH = 9       # 直播推流
RIGHT_TYPE_CLOUD_RECORD = 10   # 云录制
RIGHT_TYPE_GROUP = 11          # 分组会议
RIGHT_TYPE_WAITING_ROOM = 12   # 等候室
# 系统消息类型 SysMsgType（SYSMSG 6618 推送）
SYSMSG_TYPE_TEXT, SYSMSG_TYPE_WINDOW, SYSMSG_TYPE_WEB, SYSMSG_TYPE_RENAME = 1, 2, 3, 4
# 视图模式（WNDSTATE 6513）
WND_MODE_NORMAL, WND_MODE_DATA, WND_MODE_VIDEO = 1, 2, 3

# 主讲人 DataState / 管理员 ManagerState（6509 字段，2=指定/0=取消）
DATASTATE_NONE, DATASTATE_PRESENTER = 0, 2
MANAGERSTATE_NONE, MANAGERSTATE_ADMIN = 0, 2
# 辅流：媒体共享 MediaShareState（0停/1暂停/2开启）；屏幕共享 VNCState（0停/2开）
MEDIASHARE_STOP, MEDIASHARE_PAUSE, MEDIASHARE_START = 0, 1, 2
VNC_OFF, VNC_ON = 0, 2
# 角色（set_role 的 role 参数取值）
ROLE_PRESENTER = "presenter"      # 主讲人（6509 DataState）
ROLE_ADMIN = "admin"              # 管理员（6509 ManagerState）
ROLE_CHAIRMAN = "chairman"        # 主持人（6511，仅自申请/放弃，不能指定他人）
# 视频轮询 Operation（6533，增/改/删）
POLLING_OP_ADD, POLLING_OP_MODIFY, POLLING_OP_DEL = 1, 2, 3
# 视频轮询 Status（0停/1暂停/2运行）
POLLING_STATUS_STOP, POLLING_STATUS_PAUSE, POLLING_STATUS_RUNNING = 0, 1, 2
# 视频轮询下行 6633 Operation（1轮询用户变更/2轮询停止）
POLLING_NOTIFY_CHANGE, POLLING_NOTIFY_STOP = 1, 2


class ConfCmd(InteractionBuilders):
    """Conf 域命令构造器。所有 build_* 返回 XmlCommand，发往 application_id=11。"""

    app_id = CONF_APP_ID
    ver = CONF_VER_CS
    pairs = PAIRS
    pushes = PUSHES

    @staticmethod
    def build_relay_message(src_user_id, dst_user_id, messages, *, dst_user_ids=None):
        """6700 transparent relay; messages=[{'id': 6718, 'fields': {...}}].

        DstUserID=0 broadcasts excluding the source. Otherwise DstUserList adds
        recipients to the primary target. This command has no direct response.
        """
        def number(value, name, minimum=0):
            if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
                raise ValueError(name + " must be an integer")
            value = int(value)
            if not minimum <= value <= 0x7fffffff:
                raise ValueError(name + " is outside the protocol range")
            return value

        src = number(src_user_id, "src_user_id", 1)
        dst = number(dst_user_id, "dst_user_id")
        if not isinstance(messages, (list, tuple)) or not messages:
            raise ValueError("messages must be a nonempty list of {id, fields} objects")
        inner = []
        for message in messages:
            if not isinstance(message, dict) or set(message) - {"id", "fields"}:
                raise ValueError("each message must contain id and optional fields")
            msg_id = number(message.get("id"), "message.id", 1)
            fields = message.get("fields", {})
            if not isinstance(fields, dict) or any(str(key).startswith("@") for key in fields):
                raise ValueError("message.fields must be a field tree, without msg attributes")
            inner.append({"@id": msg_id, **copy.deepcopy(fields)})
        fields = {"SrcUserID": src, "DstUserID": dst, "msg": inner}
        if dst_user_ids is not None:
            if not isinstance(dst_user_ids, (list, tuple)) or not dst_user_ids:
                raise ValueError("dst_user_ids must be a nonempty list")
            if dst == 0:
                raise ValueError("broadcast cannot be combined with dst_user_ids")
            targets = list(dict.fromkeys(number(uid, "dst_user_ids", 1) for uid in dst_user_ids))
            targets = [uid for uid in targets if uid != dst]
            if targets:
                fields["DstUserList"] = {"UserID": targets}
        command = XmlCommand(RELAYMESSAGE, fields, ver=ConfCmd.ver)
        try:
            ET.fromstring(command.to_bytes())
        except (ET.ParseError, TypeError, AttributeError) as exc:
            raise ValueError("invalid relay XML field tree") from exc
        return command

    # ---- 6501 登录会议室（拿到会场配置 + 媒体组件地址 + 自己的 Right）----
    @staticmethod
    def build_loginreq(
        *,
        room_srv_node_id: str,
        room_id: int,
        user_id: int,
        user_name: str,
        user_type: int,
        token: str,
        verify_mode: int = 3,          # 1=用户密码 2=会议室密码 3=匿名
        room_password: Optional[str] = None,
        client_type: int = 1,
        terminal_type: int = 1,
        capability_mode: int = 1,      # 1=完整 2=合流 3=最小
        down_bitrate: int = 500000,
        forced_login: int = 0,
        device_id: Optional[str] = None,
        support_features=None,
        screen_count: Optional[int] = None,
        layouts=None,
        join_type: Optional[int] = None,
        **extra,
    ) -> XmlCommand:
        fields = {
            "ClientType": client_type,
            "TerminalType": terminal_type,
            "RoomSrvNodeID": room_srv_node_id,
            "RoomID": room_id,
            "UserID": user_id,
            "UserName": user_name,
            "UserType": user_type,
            "Token": token,
            "VerifyMode": verify_mode,
            "Capability": {"Mode": capability_mode, "DownBitrate": down_bitrate},
            "ForcedLogin": forced_login,
        }
        if room_password:
            fields["RoomPassword"] = room_password
        if device_id:
            fields["DeviceID"] = device_id
        if support_features is not None:
            fields["SupportFeatures"] = ConfCmd._csv(support_features)
        if screen_count is not None:
            if int(screen_count) <= 0:
                raise ValueError("screen_count must be positive")
            fields["ScreenCount"] = screen_count
        if layouts is not None:
            fields["Layout"] = ConfCmd._csv(layouts)
        if join_type is not None:
            fields["JoinType"] = join_type
        fields.update(extra)
        return XmlCommand(LOGINREQ, fields, ver=CONF_VER_CS)

    @staticmethod
    def build_initcomplete() -> XmlCommand:
        # 空体；发出后服务端补发用户列表/窗口状态
        return XmlCommand(INITCOMPLETE, {}, ver=CONF_VER_CS)

    # ---- 6512 文字消息（公聊 DstUserID=0 / 私聊指定 ID）----
    @staticmethod
    def build_chat(
        *,
        src_user_id: int,
        text: str,
        dst_user_id: int = 0,
        color: Optional[int] = None,
        font: Optional[dict] = None,
        **extra,
    ) -> XmlCommand:
        fields = {"SrcUserID": src_user_id, "DstUserID": dst_user_id, "Text": text}
        if color is not None:
            fields["Color"] = color
        if font is not None:
            if not isinstance(font, dict) or not font:
                raise ValueError("font must be a non-empty dictionary")
            unknown = set(font) - CHAT_FONT_ATTRIBUTES
            if unknown:
                raise ValueError(f"unsupported chat font attributes: {sorted(unknown)}")
            attributes = {f"@{key}": value for key, value in font.items()
                          if value is not None}
            if not attributes:
                raise ValueError("font must contain at least one non-null attribute")
            fields["Font"] = attributes
        fields.update(extra)
        return XmlCommand(CHATMSG, fields, ver=CONF_VER_CS)

    # ---- 6509 改变用户状态（音频/发言/静音/主讲/视频广播…，按需字段组合）----
    @staticmethod
    def build_change_user_state(
        *,
        user_id: int,
        audio: Union[None, dict, List[dict]] = None,
        video: Union[None, dict, List[dict]] = None,
        data_state: Optional[int] = None,            # 主讲人 DataState（2指定/0取消）
        manager_state: Optional[int] = None,         # 管理员 ManagerState（2指定/0取消）
        media_share_state: Optional[int] = None,     # 媒体共享 MediaShareState（0停/1暂停/2开）
        audio_share_id: Optional[int] = None,
        video_share_id: Optional[int] = None,
        vnc_state: Optional[int] = None,             # 屏幕共享 VNCState（0停/2开）
        vnc_audio_id: Optional[int] = None,
        private_talk: Optional[dict] = None,
        audio_out_mute: Optional[int] = None,
        info: Optional[dict] = None,
        wb_mark_state: Optional[int] = None,
        enable_chat: Optional[int] = None,
        group_state: Optional[int] = None,
        online_state: Optional[int] = None,
        record_state: Optional[int] = None,
        speaker_state: Optional[int] = None,
        camera_state: Optional[int] = None,
        microphone_state: Optional[int] = None,
        local_vp_state: Optional[int] = None,
        **extra,
    ) -> XmlCommand:
        fields: dict = {"UserID": user_id}
        if audio is not None:
            fields["Audio"] = audio
        if video is not None:
            fields["Video"] = video
        for name, val in (("DataState", data_state), ("ManagerState", manager_state),
                          ("MediaShareState", media_share_state), ("AudioShareID", audio_share_id),
                          ("VideoShareID", video_share_id), ("VNCState", vnc_state),
                          ("VNCAudioID", vnc_audio_id), ("PrivateTalk", private_talk),
                          ("AudioOutMute", audio_out_mute), ("Info", info),
                          ("WBMarkState", wb_mark_state), ("EnableChat", enable_chat),
                          ("GroupState", group_state), ("OnlineState", online_state),
                          ("RecordState", record_state), ("SpeakerState", speaker_state),
                          ("CameraState", camera_state), ("MicrophoneState", microphone_state),
                          ("LocalVPState", local_vp_state)):
            if val is not None:
                fields[name] = val
        fields.update(extra)
        return XmlCommand(CHANGEUSERSTATE, fields, ver=CONF_VER_CS)

    @staticmethod
    def _csv(values) -> str:
        if isinstance(values, str):
            return values
        if not isinstance(values, (list, tuple)):
            raise TypeError("expected a comma-separated string or list/tuple")
        return ",".join(str(value) for value in values)

    @staticmethod
    def build_multi_data_sharer(*, enabled: bool, force: bool = False) -> XmlCommand:
        if enabled not in (0, 1) or force not in (0, 1):
            raise ValueError("enabled and force must be boolean or 0/1")
        return XmlCommand(SETMULTIDATASHARER, {
            "EnableMultiDataSharer": int(enabled), "Force": int(force)}, ver=CONF_VER_CS)

    @staticmethod
    def build_user_features(user_id) -> XmlCommand:
        return XmlCommand(QUERYUSERFEATURES, {"UserID": user_id}, ver=CONF_VER_CS)

    @staticmethod
    def build_feature_users(feature: str, *, query_type: int = 0) -> XmlCommand:
        if not feature or query_type not in (0, 1, 2):
            raise ValueError("feature is required; query_type must be 0, 1 or 2")
        return XmlCommand(QUERYFEATUREUSERS, {
            "Feature": feature, "Type": query_type}, ver=CONF_VER_CS)

    @staticmethod
    def build_self_state_result(*, dst_user_id, vnc_state: int, error=None) -> XmlCommand:
        if vnc_state not in (VNC_OFF, VNC_ON):
            raise ValueError("vnc_state must be 0 or 2")
        fields = {"DstUserID": dst_user_id, "VNCState": vnc_state}
        if error is not None:
            fields["Error"] = error
        return XmlCommand(MODIFYSELFSTATERESULT, fields, ver=CONF_VER_CS)

    @staticmethod
    def build_renegotiate_layout(*, screen_count: int, layouts) -> XmlCommand:
        if int(screen_count) <= 0:
            raise ValueError("screen_count must be positive")
        return XmlCommand(ABILITYRENEGOTIATION, {"Layout": {
            "ScreenCount": screen_count, "Code": ConfCmd._csv(layouts)}}, ver=CONF_VER_CS)

    @staticmethod
    def build_h5_vote(vote_id, *, state: int, duration: int = 1,
                      user_ids=None, data: Optional[dict] = None) -> XmlCommand:
        if (isinstance(vote_id, bool) or not isinstance(vote_id, (int, str))
                or not str(vote_id).isascii() or not str(vote_id).isdigit()
                or not 0 <= int(vote_id) <= 0x7FFFFFFF):
            raise ValueError("vote_id must be a nonnegative signed 32-bit integer")
        vote_id = int(vote_id)
        if state not in (0, 2) or duration < 0:
            raise ValueError("vote state must be 0 (end) or 2 (start); duration >= 0")
        fields = dict(data or {})
        if {"ID", "State", "Dura", "UserList", "Creator"}.intersection(fields):
            raise ValueError("vote data must not override protocol routing fields")
        fields.update(ID=vote_id, State=state)
        if state == 2:
            fields["Dura"] = duration
        if user_ids is not None:
            ids = list(user_ids)
            if any(str(uid) == "0" for uid in ids) and len(ids) > 1:
                raise ValueError("broadcast user ID 0 cannot be mixed with target IDs")
            fields["UserList"] = {"UserID": ids}
        return XmlCommand(H5VOTE, fields, ver=CONF_VER_CS)

    @staticmethod
    def build_modify_group_config(rooms: List[dict]) -> XmlCommand:
        if not rooms or any(not room.get("RoomID") for room in rooms):
            raise ValueError("rooms must contain existing nonzero RoomID values")
        return XmlCommand(MODIFYGROUPCONFIG, {"RoomInfo": rooms}, ver=CONF_VER_CS)

    @staticmethod
    def build_allow_waiting_users(users: List[dict]) -> XmlCommand:
        if not users or any(not user.get("UserID") or user.get("Allow") not in (0, 1)
                            for user in users):
            raise ValueError("users must contain UserID and Allow (0 or 1)")
        return XmlCommand(ALLOWWAITINGUSERS, {
            "UserList": {"UserInfo": users}}, ver=CONF_VER_CS)

    @staticmethod
    def build_asr_subtitle(*, enabled: bool, translation: bool = False,
                           src_language=None, target_language=None) -> XmlCommand:
        if enabled not in (0, 1) or translation not in (0, 1):
            raise ValueError("enabled and translation must be boolean or 0/1")
        if enabled and translation and not target_language:
            raise ValueError("target_language is required when enabling translation")
        fields = {"EnableAsrSubtitle": int(enabled),
                  "EnableAsrSubtitleTranslation": int(translation)}
        if src_language is not None:
            fields["SrcLanguage"] = src_language
        if target_language is not None:
            fields["TargetLanguage"] = target_language
        return XmlCommand(SETASRSUBTITLE, fields, ver=CONF_VER_CS)

    @staticmethod
    def build_user_custom_data(user_id, *, public_data=None, private_data=None) -> XmlCommand:
        if public_data is None and private_data is None:
            raise ValueError("public_data or private_data is required (empty string clears it)")
        fields = {"UserID": user_id}
        for key, value in (("PublicData", public_data), ("PrivateData", private_data)):
            if value is not None:
                if not isinstance(value, str):
                    raise TypeError("custom data must be a string; use json.dumps for JSON")
                fields[key] = value
        return XmlCommand(SETUSERCUSTOMDATA, fields, ver=CONF_VER_CS)

    @staticmethod
    def audio(dev_id: int = 0, state: int = AUDIO_SPEAKING) -> dict:
        """构造一个 <Audio> 子结构（用于 build_change_user_state 的 audio 参数）。

        注意：4u 服务端音频只认 ``ID=0``（MAX_AUDIODEVICE_COUNT=1），默认 0。"""
        return {"ID": dev_id, "State": state}

    @staticmethod
    def video(dev_id: int = 1, state: int = VIDEO_BROADCAST) -> dict:
        return {"ID": dev_id, "State": state}

    # ---- 6505 退出会议室（通知类，无直接回复；6605 广播给其它人）----
    @staticmethod
    def build_logout(
        *,
        user_id: int,
        room_srv_node_id: str,
        room_id: int,
        reason: int = 0,
    ) -> XmlCommand:
        return XmlCommand(
            LOGOUTREQ,
            {"UserID": user_id, "RoomSrvNodeID": room_srv_node_id,
             "RoomID": room_id, "Reason": reason},
            ver=CONF_VER_CS,
        )

    # ---- 6506 踢人（广播 6606 USERKICKED）----
    @staticmethod
    def build_kickuser(*, user_id, kick_type=None, tips=None, **extra) -> XmlCommand:
        fields: dict = {"UserID": user_id}
        if kick_type is not None:
            fields["KickType"] = kick_type
        if tips is not None:
            fields["Tips"] = tips
        fields.update(extra)
        return XmlCommand(KICKUSER, fields, ver=CONF_VER_CS)

    # ---- 6507 关闭会议室（广播 6607 ROOMCLOSED）----
    @staticmethod
    def build_closeroom(*, reason: int = 0, description: str = "", **extra) -> XmlCommand:
        fields: dict = {"Reason": reason, "Description": description}
        fields.update(extra)
        return XmlCommand(CLOSEROOM, fields, ver=CONF_VER_CS)

    # ---- 6508 本地音视频设备上报（广播 6608 AVINFONOTIFY）----
    @staticmethod
    def build_avinfo(*, audio=None, video=None, def_video_dev_id=None, **extra) -> XmlCommand:
        """audio/video：list of {Operation,ID,Name}（Operation 1增/2改/3删）。可用 av_device() 构造。"""
        fields: dict = {}
        if audio is not None:
            fields["Audio"] = audio
        if video is not None:
            fields["Video"] = video
        if def_video_dev_id is not None:
            fields["DefVideoDevID"] = def_video_dev_id
        fields.update(extra)
        return XmlCommand(AVINFO, fields, ver=CONF_VER_CS)

    @staticmethod
    def av_device(operation: int, dev_id: int, name: str, **extra) -> dict:
        """构造一个 <Audio>/<Video> 设备子结构（用于 build_avinfo）。"""
        d = {"Operation": operation, "ID": dev_id, "Name": name}
        d.update(extra)
        return d

    # ---- 6510 修改会议状态（广播 6610 CONFSTATECHANGED；只下发要改的字段）----
    @staticmethod
    def build_change_conf_state(
        *,
        mode=None, lock=None,
        enable_chat=None, enable_p2p_chat=None, enable_pub_chat=None,
        enable_chat_check=None, enable_force_operate_user_av=None,
        enable_record=None, enable_send_file=None, enable_waiting_room=None,
        server_record_status=None,
        **extra,
    ) -> XmlCommand:
        """server_record_status 控制录制：RECORD_STOP=0 / RECORD_PAUSE=1 / RECORD_RECORDING=2。"""
        fields: dict = {}
        for name, val in (("Mode", mode), ("Lock", lock),
                          ("EnableChat", enable_chat), ("EnableP2PChat", enable_p2p_chat),
                          ("EnableChatMsgCheck", enable_chat_check),
                          ("EnableForceOperateUserAV", enable_force_operate_user_av),
                          ("EnablePubChat", enable_pub_chat), ("EnableRecord", enable_record),
                          ("EnableSendFile", enable_send_file),
                          ("EnableWaitingRoom", enable_waiting_room),
                          ("ServerRecordStatus", server_record_status)):
            if val is not None:
                fields[name] = val
        fields.update(extra)
        return XmlCommand(CHANGECONFSTATE, fields, ver=CONF_VER_CS)

    # ---- 6511 申请/放弃主持人（ChairRight 1申请/0放弃；成功走 6609，失败回 6611）----
    @staticmethod
    def build_req_chairright(*, chair_right: int, chair_pwd=None, **extra) -> XmlCommand:
        if chair_right == CHAIR_APPLY and not chair_pwd:
            raise ValueError("chair_pwd is required when applying for chair/admin rights")
        fields: dict = {"ChairRight": chair_right}
        if chair_pwd is not None:
            fields["ChairPwd"] = chair_pwd
        fields.update(extra)
        return XmlCommand(REQCHAIRRIGHT, fields, ver=CONF_VER_CS)

    # ---- 6514 全场静音（主持人下发，广播 6614 SILENTNOTIFY）----
    @staticmethod
    def build_silent(**extra) -> XmlCommand:
        return XmlCommand(SILENT, dict(extra), ver=CONF_VER_CS)

    # ==================== 通讯录 / 组织架构 / 会议配置 ==========================

    # ---- 6503 请求离线/历史成员列表（空 body；回复 6603 重复 <User>）----
    @staticmethod
    def build_reqofflines() -> XmlCommand:
        return XmlCommand(REQOFFLINES, {}, ver=CONF_VER_CS)

    # ---- 6528 请求企业组织架构（空 body；回复 6628 重复 <Dept>）----
    @staticmethod
    def build_deptinforeq() -> XmlCommand:
        return XmlCommand(DEPTINFOREQ, {}, ver=CONF_VER_CS)

    # ---- 6534 请求会议配置（InviteCode 等占位项）----
    @staticmethod
    def build_getroomconfigreq(*, group_room_config: bool = False, **extra) -> XmlCommand:
        # 分组配置独立查询，避免邀请码先响应导致请求匹配器提前返回。
        fields = {"GroupRoomConfig": {}} if group_room_config else {"InviteCode": {}}
        fields.update(extra)
        return XmlCommand(GETROOMCONFIGREQ, fields, ver=CONF_VER_CS)

    # ==================== 终端呼叫子块（邀请电话/H323 联系人入会）===============
    # REP = REQ + 1（与上面 +100 段不同）

    # ---- 6741 邀请电话/H323 联系人入会（重复 <ReqCallInfo>）----
    @staticmethod
    def build_makecall(
        *, call_type: int = CALLTYPE_PHONE, callees: List[dict], **extra,
    ) -> XmlCommand:
        """callees：list of call_info(...)，每个含 CalleeNum/CalleeNickName/CalleeUserID。"""
        fields: dict = {"CallType": call_type, "ReqCallInfo": callees}
        fields.update(extra)
        return XmlCommand(MAKECALL_REQ, fields, ver=CONF_VER_CS)

    @staticmethod
    def call_info(
        *, callee_num: str, callee_nick_name: str = "", callee_user_id=0, **extra,
    ) -> dict:
        """构造一个 <ReqCallInfo> 子结构（用于 build_makecall）。"""
        d = {"CalleeNum": callee_num, "CalleeNickName": callee_nick_name,
             "CalleeUserID": callee_user_id}
        d.update(extra)
        return d

    # ---- 6743 挂断邀请（<ReqCallList> 内重复 <CalleeNum>）----
    @staticmethod
    def build_hangup(
        *, call_type: int = CALLTYPE_PHONE, callee_nums: List[str], **extra,
    ) -> XmlCommand:
        fields: dict = {"CallType": call_type,
                        "ReqCallList": {"CalleeNum": list(callee_nums)}}
        fields.update(extra)
        return XmlCommand(HANGUP_REQ, fields, ver=CONF_VER_CS)

    # ---- 6746 查询已邀请拨号列表（回复 6747 重复 <CalleeInfo>）----
    @staticmethod
    def build_call_list(
        *, call_type: int = CALLTYPE_PHONE,
        caller_user_id=None, room_id=None, **extra,
    ) -> XmlCommand:
        fields: dict = {"CallType": call_type}
        if caller_user_id is not None:
            fields["CallerUserID"] = caller_user_id
        if room_id is not None:
            fields["RoomID"] = room_id
        fields.update(extra)
        return XmlCommand(CALL_LIST_REQ, fields, ver=CONF_VER_CS)

    # ==================== 扩展会议控制 =========================================

    # ---- 6504 主持人答复敲门（0拒/1允；无回复）----
    @staticmethod
    def build_allowknock(*, user_id, allow: int = 1, **extra) -> XmlCommand:
        return XmlCommand(ALLOWKNOCK, {"UserID": user_id, "Allow": allow, **extra}, ver=CONF_VER_CS)

    # ---- 6523 主持人创建分组会议（重复 <RoomInfo>）----
    @staticmethod
    def build_creategroup(rooms: List[dict], **extra) -> XmlCommand:
        """rooms：list of group_room(...)。回复 6623 带新建 RoomID。"""
        return XmlCommand(CREATEGROUP, {"RoomInfo": rooms, **extra}, ver=CONF_VER_CS)

    @staticmethod
    def group_room(
        *, theme: str, max_user_count: int = 0, enable_join: int = 1,
        enable_leave: int = 1, users: Optional[List[dict]] = None, **extra,
    ) -> dict:
        """构造 <RoomInfo> 子结构（用于 build_creategroup）。users 用 group_user()。"""
        d: dict = {"Theme": theme, "MaxUserCount": max_user_count,
                   "EnableJoin": enable_join, "EnableLeave": enable_leave}
        if users:
            d["User"] = users
        d.update(extra)
        return d

    @staticmethod
    def group_user(*, id, right: int = 0, **extra) -> dict:
        """构造分组 <User>（right=0 由服务器生成）。"""
        return {"ID": id, "Right": right, **extra}

    # ---- 6524 加入分组会议（回复 6624 带 Token/SrvAddr）----
    @staticmethod
    def build_entergroup(*, room_id, users: List[dict], **extra) -> XmlCommand:
        """users：list of group_user(...)。"""
        return XmlCommand(ENTERGROUPREQ, {"RoomID": room_id, "User": users, **extra}, ver=CONF_VER_CS)

    # ---- 6525 主持人结束分组（重复 <RoomID>）----
    @staticmethod
    def build_changegroupstate(room_ids: List, **extra) -> XmlCommand:
        return XmlCommand(CHANGEGROUPSTATEREQ, {"RoomID": list(room_ids), **extra}, ver=CONF_VER_CS)

    # ---- 6526 主持人把用户拉回主会议（重复 <UserID>）----
    @staticmethod
    def build_entermain(user_ids: List, **extra) -> XmlCommand:
        return XmlCommand(ENTERMAINREQ, {"UserID": list(user_ids), **extra}, ver=CONF_VER_CS)

    # ---- 6530 申请/释放操作权（Operation 1申请/0释放，RightType 见 RIGHT_TYPE_*）----
    @staticmethod
    def build_operationright(*, operation: int, right_type: int, **extra) -> XmlCommand:
        return XmlCommand(OPERATIONRIGHTREQ,
                          {"Operation": operation, "RightType": right_type, **extra}, ver=CONF_VER_CS)

    # ---- 6532 修改角色权限（广播 6632；RoleCode 可重复）----
    @staticmethod
    def build_change_role_permission(
        role_codes: Union[str, List[str]], permissions: dict,
    ) -> XmlCommand:
        roles = [role_codes] if isinstance(role_codes, str) else list(role_codes)
        roles = [str(role).strip() for role in roles
                 if role is not None and str(role).strip()]
        if not roles:
            raise ValueError("role_codes must not be empty")
        values = {key: value for key, value in dict(permissions).items()
                  if value is not None}
        if "RoleCode" in values:
            raise ValueError("permissions must not contain reserved field RoleCode")
        if not values:
            raise ValueError("permissions must contain at least one field")
        return XmlCommand(
            CHANGEROLEPERMISSION,
            {"RoleCode": roles, **values},
            ver=CONF_VER_CS,
        )

    # ---- 6535 修改指定用户权限（广播 6635）----
    @staticmethod
    def build_change_user_permission(*, user_id, permissions: dict) -> XmlCommand:
        if user_id is None or str(user_id).strip() == "":
            raise ValueError("user_id must not be empty")
        values = {key: value for key, value in dict(permissions).items()
                  if value is not None}
        if "UserID" in values:
            raise ValueError("permissions must not contain reserved field UserID")
        if not values:
            raise ValueError("permissions must contain at least one field")
        return XmlCommand(
            CHANGEUSERPERMISSION,
            {"UserID": user_id, **values},
            ver=CONF_VER_CS,
        )

    # ---- 6527 修改会议室名称（回复 6627）----
    @staticmethod
    def build_meetingrename(*, name: str, **extra) -> XmlCommand:
        return XmlCommand(MEETINGRENAMEREQ, {"Name": name, **extra}, ver=CONF_VER_CS)

    # ---- 6515 主持人设会场字幕（Duration 分钟/0永久，Data 文本）----
    @staticmethod
    def build_meetingchar(*, data: str, duration: int = 0, **extra) -> XmlCommand:
        return XmlCommand(MEETINGCHAR, {"Duration": duration, "Data": data, **extra}, ver=CONF_VER_CS)

    # ---- 6550 关闭会场字幕（空 body，回复 6650）----
    @staticmethod
    def build_closesubtitle(**extra) -> XmlCommand:
        return XmlCommand(CLOSESUBTITLEREQ, dict(extra), ver=CONF_VER_CS)

    # ---- 6518 发系统通知/广播（DstUserID 0=广播，目标端推送 6618）----
    @staticmethod
    def build_sysmsg(*, sys_msg_id: int, dst_user_id: int = 0, **extra) -> XmlCommand:
        if isinstance(sys_msg_id, bool) or not isinstance(sys_msg_id, int) or sys_msg_id <= 2:
            raise ValueError("sys_msg_id must be a configured server message ID greater than 2")
        fields: dict = {"DstUserID": dst_user_id, "SysMsgID": sys_msg_id}
        fields.update(extra)
        return XmlCommand(SYSMSGREQ, fields, ver=CONF_VER_CS)

    # ---- 6519 主持人设置会场/课堂数据（广播 6619）----
    @staticmethod
    def build_set_room_data(items: List[dict]) -> XmlCommand:
        values = list(items)
        if not values:
            raise ValueError("items must not be empty")
        return XmlCommand(
            SETROOMDATA, {"DataItem": values}, ver=CONF_VER_CS)

    @staticmethod
    def room_data_item(item_id: int, room_data: Any, **extra) -> dict:
        """构造 6519 的 <DataItem>；room_data 可为嵌套 dict 或标量。"""
        return {"ItemID": item_id, "RoomData": room_data, **extra}

    # ---- 6520/6521 查询 / 设置 AV 合成参数 ----
    @staticmethod
    def build_get_av_mix_params() -> XmlCommand:
        return XmlCommand(GETAVMIXPARAMREQ, {}, ver=CONF_VER_CS)

    @staticmethod
    def build_set_av_mix_params(
        *, enable_default: bool, audio: Optional[dict] = None,
        video: Optional[dict] = None,
    ) -> XmlCommand:
        if not enable_default and audio is None and video is None:
            raise ValueError(
                "audio or video is required when enable_default is false")
        fields: dict = {"EnableDefault": enable_default}
        if audio is not None:
            fields["Audio"] = dict(audio)
        if video is not None:
            fields["Video"] = dict(video)
        return XmlCommand(SETAVMIXPARAM, fields, ver=CONF_VER_CS)

    @staticmethod
    def av_mix_audio(*, fec_type: int, encoder_id: int, **extra) -> dict:
        return {"FecType": fec_type, "EncoderID": encoder_id, **extra}

    @staticmethod
    def av_mix_video(
        *, width: int, height: int, frame_rate: int, encoder_id: int,
        encoder_mode: int, bitrate: int, vbr_quality: int,
        key_frame_interval: int, **extra,
    ) -> dict:
        return {
            "Width": width,
            "Height": height,
            "FrameRate": frame_rate,
            "EncoderID": encoder_id,
            "EncoderMode": encoder_mode,
            "Bitrate": bitrate,
            "VBRQuality": vbr_quality,
            "KeyFrameInterval": key_frame_interval,
            **extra,
        }

    # ---- 6522 设置视频画面字幕参数（广播 6622）----
    @staticmethod
    def build_set_video_subtitle(
        *, date_time: Optional[dict] = None,
        user_name: Optional[dict] = None,
    ) -> XmlCommand:
        if date_time is None and user_name is None:
            raise ValueError("date_time or user_name is required")
        fields: dict = {}
        if date_time is not None:
            fields["DateTime"] = dict(date_time)
        if user_name is not None:
            fields["UserName"] = dict(user_name)
        return XmlCommand(SETVIDEOSUBTITLE, fields, ver=CONF_VER_CS)

    @staticmethod
    def video_subtitle_style(*, pos: int, size: int, color: int) -> dict:
        """视频字幕样式：pos 1左上/2右上/3左下/4右下。"""
        return {"Pos": pos, "Size": size, "Color": color}

    # ---- 6516 发文件通知（通知他人收文件；<DstUser> 内重复 UserID + <FileInfo>）----
    @staticmethod
    def build_sendfile(*, dst_user_ids: List, file_info: dict, **extra) -> XmlCommand:
        return XmlCommand(SENDFILE,
                          {"DstUser": {"UserID": list(dst_user_ids)}, "FileInfo": file_info, **extra},
                          ver=CONF_VER_CS)

    @staticmethod
    def file_info(*, guid: str, name: str, size: int = 0, check_code: str = "",
                  srv_app_id: int = 0, srv_addr_link: str = "", **extra) -> dict:
        """构造 <FileInfo>（用于 build_sendfile）。"""
        d = {"Guid": guid, "Name": name, "Size": size, "CheckCode": check_code,
             "SrvAppID": srv_app_id, "SrvAddrLink": srv_addr_link}
        d.update(extra)
        return d

    # ---- 6568 撤回聊天消息（回复 6668，广播 6669）----
    @staticmethod
    def build_recallmsg(msg_id) -> XmlCommand:
        if msg_id in (None, ""):
            raise ValueError("msg_id is required")
        return XmlCommand(RECALLMSGREQ, {"MsgID": msg_id}, ver=CONF_VER_CS)

    # ---- 6513 上报视图状态/布局（推送 6613；DstInfo 定向接收人，下行前被剥离）----
    @staticmethod
    def build_wndstate(
        *, mode: int = WND_MODE_NORMAL, full: Optional[int] = None,
        data_active: Optional[int] = None, video_relay_id: Optional[int] = None,
        full_video_id: Optional[int] = None,
        full_video_media_id: Optional[int] = None,
        valid_full_mode: Optional[int] = None,
        videos: Optional[List[dict]] = None,
        dst_user_ids: Optional[List] = None, exclude_user_ids: Optional[List] = None,
        terminal_type: Optional[int] = None,
        **extra,
    ) -> XmlCommand:
        """布局：``video_relay_id``=布局类型(VideoRelayID)；``videos``=各窗格成员(wnd_video)；
        ``dst_user_ids``/``exclude_user_ids`` 定向接收人（写 DstInfo，服务端剥离后不下发）。"""
        fields: dict = {"Mode": mode}
        if full is not None:
            fields["Full"] = full
        if data_active is not None:
            fields["DataActive"] = data_active
        if video_relay_id is not None:
            fields["VideoRelayID"] = video_relay_id
        if full_video_id is not None:
            fields["FullVideoID"] = full_video_id
        if full_video_media_id is not None:
            fields["FullVideoMediaID"] = full_video_media_id
        if valid_full_mode is not None:
            fields["ValidFullMode"] = valid_full_mode
        if videos is not None:
            fields["Video"] = videos
        if dst_user_ids or exclude_user_ids or terminal_type is not None:
            dst: dict = {}
            if dst_user_ids is not None:
                dst["DstUserList"] = {"UserID": list(dst_user_ids)}
            if exclude_user_ids is not None:
                dst["ExcludeUserList"] = {"UserID": list(exclude_user_ids)}
            if terminal_type is not None:
                (dst.setdefault("DstUserList", {}) if "DstUserList" in dst else dst)["TerminalType"] = terminal_type
            fields["DstInfo"] = dst
        fields.update(extra)
        return XmlCommand(WNDSTATE, fields, ver=CONF_VER_CS)

    @staticmethod
    def wnd_video(*, user_id, media_id, pos: int = 0, **extra) -> dict:
        """构造 <Video> 子结构（用于 build_wndstate）。"""
        return {"UserID": user_id, "MediaID": media_id, "Pos": pos, **extra}

    # ---- 6533 视频轮询 增/改/删（推送 6633）----
    @staticmethod
    def build_videopolling(pollings: List[dict], **extra) -> XmlCommand:
        """pollings：list of video_polling(...)。通知类，发送即返回，结果看 6633 推送。"""
        return XmlCommand(CHANGEVIDEOPOLLINGREQ, {"VideoPolling": pollings, **extra}, ver=CONF_VER_CS)

    @staticmethod
    def video_polling(
        *, polling_id: int, operation: int, name: str, user_ids: List,
        interval: Optional[int] = None, screen: Optional[int] = None,
        window: Optional[int] = None, status: Optional[int] = None, **extra,
    ) -> dict:
        """构造 <VideoPolling val="id"> 子结构（用于 build_videopolling）。

        - operation: POLLING_OP_ADD/MODIFY/DEL = 1/2/3
        - status: POLLING_STATUS_STOP/PAUSE/RUNNING = 0/1/2（暂停/恢复/停止用 MODIFY+status 表达）
        - polling_id: 新增时客户端给一个非 0 值"""
        if operation not in (POLLING_OP_ADD, POLLING_OP_MODIFY, POLLING_OP_DEL):
            raise ValueError("polling operation must be 1(add), 2(modify), or 3(delete)")
        if status is not None and status not in (
                POLLING_STATUS_STOP, POLLING_STATUS_PAUSE, POLLING_STATUS_RUNNING):
            raise ValueError("polling status must be 0(stop), 1(pause), or 2(running)")
        d: dict = {"@val": polling_id, "Operation": operation, "Name": name,
                   "UserList": {"UserID": list(user_ids)}}
        for k, v in (("Interval", interval), ("Screen", screen), ("Window", window), ("Status", status)):
            if v is not None:
                d[k] = v
        d.update(extra)
        return d
