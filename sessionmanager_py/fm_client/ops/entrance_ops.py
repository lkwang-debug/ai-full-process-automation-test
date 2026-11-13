"""Entrance 域(app=8)操作 mixin：首连接入，下发配置中心地址。

混入 FmClient，复用其 _make_session / _request 及 self._host / self._port。
Entrance 是引导步骤：拿配置中心地址。单机部署可直接连 Front(app=20)，本域可选。
权威字段源：4u_fmserver/server/Entrance/{EntranceMsgProcessor.cpp, WEntranceService.cpp}。
"""
from __future__ import annotations

import logging
from typing import Optional

from .. import entrance as E
from ..errcodes import FmError

logger = logging.getLogger("fm_client")


class EntranceOps:
    """Entrance 域：配置下发（一次性引导查询）。"""

    def entrance_get_config(
        self,
        *,
        terminal_type: int = 1,
        client_version: str = "1.0",
        device_id: str = "python-fm-client",
        timeout: Optional[float] = None,
    ) -> dict:
        """Entrance(app=8) 配置下发(1000→1100)，返回 {PlatformType, ConfigCenterAddr}。

        一次性引导查询：连 app=8 → CONFIGREQ → CONFIGREP → 关闭该会话。单机部署可跳过。"""
        sess = self._make_session(E.ENTRANCE_APP_ID, "entrance")
        if not sess.connect(self._host, self._port):
            raise FmError(0x1004, "entrance connect failed")
        try:
            rep = self._request(sess, E.EntranceCmd.build_config_req(
                terminal_type=terminal_type, client_version=client_version,
                device_id=device_id), E.PAIRS, timeout)
            assert rep is not None
            return rep.fields
        finally:
            try:
                sess.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("entrance close failed: %s", exc)
