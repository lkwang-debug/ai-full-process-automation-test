"""4u_fmserver 错误码（取自 4u_fmserver/protocol/ProtocolError.h）。

`Result` 字段为 0 表示成功；非 0 时用本模块查名字/判断。
"""
from __future__ import annotations

SUCCESS = 0x0000

# 通用
UNKNOWN = 0x1001
EXCEPTION = 0x1002
SERVERERROR = 0x1003
INVALIDSESSION = 0x1004
USERNOTFOUND = 0x1005
VERIFYFAILED = 0x1006
ACCESSDENIED = 0x1007
INVALIDARG = 0x1008
NOWMAINTENANCE = 0x1009
TOOACCESS = 0x100A
TOKEN_TIMEOUT = 0x100B
UNAUTHORIZED = 0x1801
UNSUPPORTED = 0x1802

# 房间 / 登录相关（登录到会中常用）
SRV_RES_OVERFLOW = 0x2100
PASSWORDERROR = 0x2101
ROOMNOTFOUND = 0x2102
ROOMOVERFLOW = 0x2103
APPMISMATCH = 0x2104
SRVMISMATCH = 0x2105
SRVNOTFOUND = 0x2106
SRVOVERFLOW = 0x2107
USEREXISTED = 0x2108
NEEDROOMPASSWORD = 0x2109
ROOMLOCKED = 0x210A
ROOMOVERTIME = 0x210B
ROOMOFF = 0x210C
DENYKNOCK = 0x210D
MULTIDENYKNOCK = 0x210E
SERVERCASFAILED = 0x210F
SRVNOSTART = 0x2110
SRVSTOPED = 0x2111
NEEDPAYMENT = 0x2112
PRODUCTSRVSTOPED = 0x2113
CHAIR_PWDERR = 0x2114
ROOM_IDERR = 0x2115
LOGIN_NICKNAME = 0x2116
CHAIR_OPEN_OPTION = 0x2117
CHAIR_NOTOPENROOM = 0x2118
INVITECODE_ERROR = 0x2119
INVITECODE_EXPIRES = 0x211A
DISABLE_MOBILE_LOGIN = 0x211B
BOOKING_ROOM_NOT_START = 0x211C
ORGLICENSEFULL = 0x211D
RIGHTOCCUPIED = 0x211E
TOKEN_STATE_ERROR = 0x211F


class FmError(Exception):
    """服务端返回的业务错误（携带 Result 错误码）。"""

    def __init__(self, code: int, message: str = "") -> None:
        self.code = code
        self.message = message or code_name(code)
        super().__init__(f"0x{code:04X} {self.message}")


# 客户端侧标记码（非服务端错误码，用高位段避免冲突）
NOT_IN_SIGNALING = 0x9001   # 接口不在 4u 终端 XML 信令里，需走其它通道
WAITING_FOR_HOST = 0x9002   # Conf 登录已进入敲门等待态（客户端侧状态）


class ProtocolNotInSignaling(FmError):
    """接口在 4u 终端 XML 信令里**不存在**，需走其它通道（FSP HTTP / Web 后台 / DB）。

    测试侧可 catch 它区分"协议本身不支持"与"业务报错"，避免把"没这个命令"误当成"发送成功"。
    """

    def __init__(self, api: str, channel: str, hint: str = "") -> None:
        msg = f"{api} 不在 4u 终端信令里；真实通道：{channel}"
        if hint:
            msg += f"（{hint}）"
        super().__init__(NOT_IN_SIGNALING, msg)
        self.api = api
        self.channel = channel


class ConfLoginWaiting(FmError):
    """登录已收到 6617 或 6667，当前正在等待主持人允许入会。"""

    def __init__(self, timeout: float, *, notification=None) -> None:
        cmdid = notification.cmdid if notification is not None else 6617
        name = "WAITOPENDOOR" if cmdid == 6617 else "WAITINGROOMINFO"
        super().__init__(
            WAITING_FOR_HOST,
            f"received {cmdid} {name}; host approval was not received within {timeout}s",
        )
        self.timeout = timeout
        self.notification_cmdid = cmdid
        self.fields = dict(notification.fields) if notification is not None else {}


_NAMES = {
    value: name
    for name, value in globals().items()
    if isinstance(value, int) and value != 0 and name.isupper()
}


def code_name(code: int) -> str:
    """错误码 → 可读名字（未知则返回 hex）。"""
    return _NAMES.get(code, f"0x{code:04X}")


def is_success(code: int) -> bool:
    return code == SUCCESS
