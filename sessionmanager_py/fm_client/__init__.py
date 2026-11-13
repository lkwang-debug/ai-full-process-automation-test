"""fm_client —— 4u_fmserver 会议业务层（登录 → 入会 → 会中）。

本包是 framecore 会话层之上的业务封装，**独立于** `framecore_session`：
  - 传输/信封：复用 framecore_session（framecore 会话 + XOR + XmlMsgIOPack 信封）
  - 业务体：4u 会议 XML 命令 `<cmd id="..."><字段 val="..."/></cmd>`

分层：
  xmlcmd.py    —— `<cmd>` XML 编解码（业务体）
  errcodes.py  —— ProtocolError.h 错误码
  front.py     —— Front 域（app=20）：登录 / 进房 / 退出 / 注销
  conf.py      —— Conf 域（app=11）：入会 / 成员 / 聊天 / 状态 / 离开
  client.py    —— FmClient：管理两条会话(app=20/app=11)、请求/响应配对、推送分发

典型流程：
  client = FmClient(host, port, ...)
  client.front_login(user, pwd)        # app=20: USERLOGIN → LOGINROOM，拿到 Token/Conf 地址
  client.conf_login()                  # app=11: LOGINREQ → 收 USERENTER → INITCOMPLETE
  client.chat("hello")                 # 会中文字消息
  client.close()
"""
from .xmlcmd import XmlCommand
from .errcodes import ConfLoginWaiting, is_success, code_name, FmError
from .front import FrontCmd, FRONT_APP_ID
from .conf import ConfCmd, CONF_APP_ID
from .entrance import EntranceCmd, ENTRANCE_APP_ID
from .client import FmClient, FmListener, ReceivedEvent, SentCommand, cmdid_name
from .errcodes import ProtocolNotInSignaling
from .http_api import WebApiClient, HttpApiError, gen_token
from .online import OnlineError, OnlineInvitation, PaasOnlineClient
from .mockserver import MockFmServer
from .testing import FmTestClient, EventCollector, Room, Contact, UserEnter, ChatMsg
from .interaction_testing import NotificationTimeout

__all__ = [
    "XmlCommand",
    "is_success",
    "code_name",
    "FmError",
    "ConfLoginWaiting",
    "ProtocolNotInSignaling",
    "FrontCmd",
    "FRONT_APP_ID",
    "ConfCmd",
    "CONF_APP_ID",
    "EntranceCmd",
    "ENTRANCE_APP_ID",
    "FmClient",
    "FmListener",
    "ReceivedEvent",
    "SentCommand",
    "cmdid_name",
    "WebApiClient",
    "HttpApiError",
    "gen_token",
    "OnlineError",
    "OnlineInvitation",
    "PaasOnlineClient",
    "MockFmServer",
    "FmTestClient",
    "NotificationTimeout",
    "EventCollector",
    "Room",
    "Contact",
    "UserEnter",
    "ChatMsg",
]
