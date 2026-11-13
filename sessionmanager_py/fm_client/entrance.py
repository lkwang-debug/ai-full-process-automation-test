"""Entrance 域（application_id=8）：首连接入，下发配置中心地址。

权威字段源：4u_fmserver/server/Entrance/{EntranceMsgProcessor.cpp:60-69, WEntranceService.cpp:391-394}。
  - CONFIGREQ(1000) C→S：TerminalType / ClientVersion / DeviceID（三者必填）
  - CONFIGREP(1100) S→C：PlatformType(public/private) / ConfigCenterAddr
  - 请求/回复配对：REP = REQ + 100
  - C→S ver="1.0"（protocol/EntranceProtocol.h: ENTRANCE_PROTOCOL_VERSION）
  - 字段值在子元素 ``val=`` 属性

Entrance 是引导步骤：拿配置中心地址。单机部署可直接连 Front(app=20)，本域可选。
"""
from __future__ import annotations

from .xmlcmd import XmlCommand

ENTRANCE_APP_ID = 8
ENTRANCE_VER = "1.0"

# cmdid（ENTRANCE_CMDID_BASE = 1000）
CONFIGREQ = 1000
CONFIGREP = 1100

# Entrance 配对规律 +100
PAIRS = {CONFIGREQ: CONFIGREP}


class EntranceCmd:
    """Entrance 域命令构造器。build_* 返回 XmlCommand，发往 application_id=8。"""

    app_id = ENTRANCE_APP_ID
    ver = ENTRANCE_VER
    pairs = PAIRS

    @staticmethod
    def build_config_req(
        *,
        terminal_type: int = 1,    # 1=桌面 2=硬件 3=移动
        client_version: str = "1.0",
        device_id: str = "python-fm-client",
    ) -> XmlCommand:
        # 三字段均必填（服务端 :67 校验非空）
        return XmlCommand(
            CONFIGREQ,
            {
                "TerminalType": terminal_type,
                "ClientVersion": client_version,
                "DeviceID": device_id,
            },
            ver=ENTRANCE_VER,
        )
