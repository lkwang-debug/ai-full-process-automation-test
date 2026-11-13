"""通讯录 / 组织架构 / 会议配置 操作 mixin（均在 Conf 域 app=11）。

混入 FmClient，复用其 _require_conf / _request 及登录态（dept_info / user_info）。
4u 没有"全公司联系人搜索"命令，通讯录语义=本会议成员(在线6602/离线6503)+企业组织架构(6601/6528)。
"""
from __future__ import annotations

import copy
from typing import List, Optional

from .. import conf as C
from .._helpers import _as_list, _dept_list


class ContactsOps:
    """通讯录查询：离线成员、组织架构、会议配置、通讯录快照。"""

    def conf_get_offline_users(
        self,
        *,
        timeout: Optional[float] = None,
        refresh: bool = False,
    ) -> List[dict]:
        """查本会议离线/历史成员列表(6503→6603)，返回 <User> 列表。

        主线服务端每个用户在一次 Conf 登录中只响应一次 6503，因此默认缓存首次结果；
        后续调用直接返回缓存副本。仅在服务端明确合入重复查询能力时使用
        ``refresh=True`` 强制再次发送 6503。

        每人含 Tel/Mobile/EMail/DeptID/UserLevel 等联系人字段。在线成员走 6602 推送，不在此。"""
        conf = self._require_conf()
        if not refresh and self._offline_users_cache is not None:
            return copy.deepcopy(self._offline_users_cache)
        pages = self._request_pages(
            conf, C.ConfCmd.build_reqofflines(), C.PAIRS, timeout)
        users = []
        for page in pages:
            users.extend(_as_list(page.get("User")))
        self._offline_users_cache = copy.deepcopy(users)
        return copy.deepcopy(users)

    def conf_get_dept_info(self, *, timeout: Optional[float] = None) -> List[dict]:
        """请求企业组织架构(6528→6628)，返回 <Dept> 列表（DeptID/DeptName/ParentDeptID/NodeID）。

        组织架构通常在 conf_login 的 6601 里已下发，见 self.dept_info / conf_get_contacts()。"""
        conf = self._require_conf()
        pages = self._request_pages(
            conf,
            C.ConfCmd.build_deptinforeq(),
            C.PAIRS,
            timeout,
            complete=lambda page: "DeptEnd" in page.fields,
        )
        depts = []
        for page in pages:
            depts.extend(_as_list(page.get("Dept")))
        return depts

    def conf_get_room_config(self, *, group_room_config: bool = False,
                             timeout: Optional[float] = None) -> dict:
        """取会议配置(6534→6634)，含 9 位 <InviteCode> 等。"""
        conf = self._require_conf()
        # 默认仍只查邀请码；新版分组可显式读取服务端人数配置。
        rep = self._request(conf, C.ConfCmd.build_getroomconfigreq(
            group_room_config=group_room_config), C.PAIRS, timeout)
        assert rep is not None
        return rep.fields

    def conf_get_contacts(self, *, timeout: Optional[float] = None) -> dict:
        """组装"通讯录"快照：{depts, local_user, offline_users}。

        depts 来自 conf_login 缓存的 6601 DeptInfo；offline_users 每次 Conf 登录首次拉 6503、后续复用缓存；
        在线成员由推送 on_user_enter(6602) 增量送达（listener 维护名册），不在此返回。"""
        return {
            "depts": _dept_list(self.dept_info),
            "local_user": self.user_info,
            "offline_users": self.conf_get_offline_users(timeout=timeout),
        }
