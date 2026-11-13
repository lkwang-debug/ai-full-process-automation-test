"""Front 域(app=20)操作 mixin：用户登录 / 加入会议变体 / 房间列表 / 节点发��� / 注销。

混入 FmClient，复用其 _ensure_front / _request / _send / _loginroom 及登录态。
权威字段源：4u_fmserver/server/FrontService/MsgProcessor.cpp；Front 域 REP = REQ + 1。
"""
from __future__ import annotations

import logging
from typing import Optional

from .. import front as F
from ..errcodes import FmError
from .._helpers import _as_list, _to_int

logger = logging.getLogger("fm_client")


class FrontOps:
    """Front 域操作：登录、加入会议（标准/会议号/邀请码/访客）、房间列表、发现、注销。"""

    # ---- 30000 用户登录/鉴权（仅登录，不进房）----
    def front_authenticate(
        self,
        username: str,
        password: str,
        *,
        token: Optional[str] = None,
        user_login_type: int = 1,
        user_type: int = 1,
        terminal_type: int = 1,
        client_type: int = 1,
        app_type: int = 11,
        product_name: str = "FMS101",
        timeout: Optional[float] = None,
    ) -> dict:
        """Front(app=20)：仅 USERLOGIN 鉴权，返回 USERLOGIN_REP 字段。

        成功后填充 local_user_id。是 front_roomlist / front_getroomid / front_login 的前置。
        不会登录 PaaS 在线服务；接收后台邀请需要独立的 PaasOnlineClient。"""
        front = self._ensure_front()
        rep = self._request(front, F.FrontCmd.build_userlogin(
            username=username, password=password, user_login_type=user_login_type,
            user_type=user_type, terminal_type=terminal_type, client_type=client_type,
            app_type=app_type, product_name=product_name, token=token), F.PAIRS, timeout)
        assert rep is not None
        self.local_user_id = rep.get("LocalUserID")
        return rep.fields

    def front_join_room(self, room_id, *, room_node_id, room_password=None,
                        room_app_id=11, timeout=None) -> dict:
        """Authenticated Front -> 30004; room_id may be a received invite code.

        Does not reauthenticate or establish Conf; call conf_login() afterwards.
        """
        if self._front is None or self.local_user_id is None:
            raise RuntimeError("call front_authenticate() first")
        if self._conf is not None:
            raise RuntimeError("leave the current Conf before joining another room")
        return self._loginroom(room_id=room_id, room_node_id=str(room_node_id),
            room_password=room_password, room_app_id=room_app_id, timeout=timeout)

    # ---- 30004 进入会议室（拿到 Token / Conf 地址 / RoomUserID）----
    def front_login(
        self,
        username: str,
        password: str,
        *,
        room_id: int,
        room_node_id: str = "1",
        room_password: Optional[str] = None,
        room_app_id: int = 11,
        token: Optional[str] = None,
        user_login_type: int = 1,
        user_type: int = 1,
        terminal_type: int = 1,
        client_type: int = 1,
        app_type: int = 11,
        product_name: str = "FMS101",
        timeout: Optional[float] = None,
    ) -> dict:
        """Front(app=20)：USERLOGIN → LOGINROOM，返回 LOGINROOM_REP 字段。

        成功后填充 local_user_id / room_node_id / room_user_id / room_srv_node_id / token /
        user_right / room_verify_mode / room_password（供 conf_login 自动复用）。
        room_app_id 默认 11（官方值；房间在 t_roominfo.RoomAppID）。"""
        self.front_authenticate(
            username, password, token=token, user_login_type=user_login_type,
            user_type=user_type, terminal_type=terminal_type, client_type=client_type,
            app_type=app_type, product_name=product_name, timeout=timeout)
        return self._loginroom(
            room_id=room_id, room_node_id=room_node_id,
            room_password=room_password, room_app_id=room_app_id, timeout=timeout)

    def _loginroom(
        self, *, room_id, room_node_id, room_password, room_app_id,
        timeout: Optional[float] = None,
    ) -> dict:
        """LOGINROOM(30004) + 状态捕获（front_login / front_login_by_meeting_no 复用）。"""
        front = self._front
        assert front is not None
        rep2 = self._request(front, F.FrontCmd.build_loginroom(
            room_id=room_id, room_node_id=room_node_id, room_app_id=room_app_id,
            room_password=room_password), F.PAIRS, timeout)
        assert rep2 is not None
        self.room_id = _to_int(rep2.get("RoomID"), default=room_id)
        self.real_room_id = rep2.get("RealRoomID")
        self.room_node_id = rep2.get("RoomNodeID") or room_node_id
        self.room_user_id = rep2.get("RoomUserID")
        self.room_srv_node_id = rep2.get("RoomSrvNodeID")
        self.token = rep2.get("Token")
        self.user_right = rep2.get("UserRight")
        self.room_verify_mode = rep2.get("RoomVerifyMode")
        self.room_password = room_password
        servers = rep2.get("Server")
        self.conf_servers = servers if isinstance(servers, list) else ([servers] if servers else [])
        return rep2.fields

    # ---------------------------- 加入会议变体（均复用 LOGINROOM 30004）---
    def front_login_by_meeting_no(
        self, username, password, meeting_no, *,
        room_node_id: str = "1", room_password: Optional[str] = None,
        room_app_id: int = 11, timeout: Optional[float] = None, **login_kwargs,
    ) -> dict:
        """按"会议号(LoginRoomId)"入会：USERLOGIN → GETROOMID(30012) 换算 RoomID → LOGINROOM。

        GETROOMID 需先登录，故顺序是 鉴权→会议号换算→进房。成功后 room_id / real_room_id 已填充。"""
        self.front_authenticate(username, password, timeout=timeout, **login_kwargs)
        rid_rep = self.front_getroomid(meeting_no, timeout=timeout)
        room_id = _to_int(rid_rep.get("RoomId"))
        if not room_id:
            raise FmError(0x1002, f"GETROOMID returned empty RoomId for meeting_no={meeting_no!r}")
        return self._loginroom(
            room_id=room_id, room_node_id=room_node_id,
            room_password=room_password, room_app_id=room_app_id, timeout=timeout)

    def front_login_by_invite_code(
        self, username, password, invite_code, *,
        room_node_id: str = "1", room_app_id: int = 11,
        timeout: Optional[float] = None, **login_kwargs,
    ) -> dict:
        """按 9 位邀请码入会：把邀请码当 RoomID 发 LOGINROOM(30004)，服务端走 t_invitecode
        解析为真实房间。入会后 self.real_room_id 为真实 RoomID（≠ 传入的邀请码）。"""
        return self.front_login(
            username, password, room_id=invite_code, room_node_id=room_node_id,
            room_app_id=room_app_id, timeout=timeout, **login_kwargs)

    def front_login_guest(
        self, username, password, room_id, *,
        room_node_id: str = "1", room_app_id: int = 11,
        timeout: Optional[float] = None, **login_kwargs,
    ) -> dict:
        """访客/匿名入会：USERLOGIN(UserType=0, UserLoginType=0) + LOGINROOM 到 VerifyMode=3 的房。

        目标房必须 VerifyMode=3（允许匿名）；username/password 可为空。"""
        login_kwargs.setdefault("user_type", 0)
        login_kwargs.setdefault("user_login_type", 0)
        return self.front_login(
            username, password, room_id=room_id, room_node_id=room_node_id,
            room_app_id=room_app_id, timeout=timeout, **login_kwargs)

    # ---------------------------- 房间列表 / 发现 / 会议号换算 / 认证类型 ---
    def front_roomlist(
        self,
        *,
        is_get_all: int = 1,
        is_get_desc: int = 0,
        timeout: Optional[float] = None,
    ) -> dict:
        """拉会议室列表(30002)。需先 front_authenticate/front_login。

        返回 ROOMLIST_REP 字段（含 RoomNodeID 与多个 RoomInfo{Name,RoomID,RoomAppID,...}）。"""
        if self.local_user_id is None or self._front is None:
            raise RuntimeError("call front_authenticate()/front_login() first")
        rep = self._request(self._front, F.FrontCmd.build_roomlist(
            is_get_all=is_get_all, is_get_desc=is_get_desc), F.PAIRS, timeout)
        assert rep is not None
        return rep.fields

    def front_roomlist_classified(
        self, *, is_get_all: int = 1, is_get_desc: int = 0,
        timeout: Optional[float] = None,
    ) -> dict:
        """拉会议室列表并给每个 RoomInfo 打 MeetingType 标签(instant/reserved/fixed)。

        协议不下发 RoomType，按 RoomID 区间 + HopeStart/End 时间推算（F.infer_meeting_type）。"""
        rep = self.front_roomlist(is_get_all=is_get_all, is_get_desc=is_get_desc, timeout=timeout)
        for room in _as_list(rep.get("RoomInfo")):
            if isinstance(room, dict):
                room["MeetingType"] = F.infer_meeting_type(room)
        return rep

    def front_discover_node(self, *, timeout: Optional[float] = None):
        """发 ROOMLIST 取本地 RoomNodeID。

        即使服务端 DB 异常（Result=0x1002，房间列表查不到），回复里仍带 RoomNodeID
        （服务端在取列表前就写入了节点 ID），故用 raise_on_error=False 把它取出来。
        这样无需查数据库、无需修服务端即可拿到入会必需的节点 ID。"""
        if self.local_user_id is None or self._front is None:
            raise RuntimeError("call front_authenticate()/front_login() first")
        rep = self._request(self._front, F.FrontCmd.build_roomlist(),
                            F.PAIRS, timeout, raise_on_error=False)
        assert rep is not None
        return rep.get("RoomNodeID")

    def front_getroomid(self, login_room_id, *, timeout: Optional[float] = None) -> dict:
        """用"入会会议号(LoginRoomId)"换算真实 RoomID(30012)。需先 front_authenticate。"""
        if self.local_user_id is None or self._front is None:
            raise RuntimeError("call front_authenticate()/front_login() first")
        rep = self._request(self._front, F.FrontCmd.build_getroomid(
            login_room_id=login_room_id), F.PAIRS, timeout)
        assert rep is not None
        return rep.fields

    def front_get_auth_type(self, *, timeout: Optional[float] = None) -> dict:
        """查认证类型(30014→30015)，返回 {AuthType: ...}。只需 Front 会话，无需先登录。"""
        front = self._ensure_front()
        rep = self._request(front, F.FrontCmd.build_getauthtype(), F.PAIRS, timeout)
        assert rep is not None
        return rep.fields

    # ---------------------------- 注销（退房 + 注销）---
    def front_logout(self, *, exit_room: bool = True, timeout: Optional[float] = None) -> None:
        """Front 注销：EXITROOM(30008) → USERLOGOUT(30010)，均有直接 REP。"""
        if self._front is None:
            return
        try:
            if exit_room and self.room_id is not None:
                self._request(self._front, F.FrontCmd.build_exitroom(
                    room_id=self.room_id, room_node_id=self.room_node_id), F.PAIRS, timeout)
            self._request(self._front, F.FrontCmd.build_userlogout(), F.PAIRS, timeout)
        except (FmError, TimeoutError) as exc:
            logger.warning("front_logout best-effort failed: %s", exc)
