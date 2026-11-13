"""会议控制补齐接口 mixin：布局 / 角色 / 辅流 / 轮询 / 邀请 / 建会。

对应《全量协议闭环自动化测试方案》§4 的"待开发补齐接口"。其中：
  - **真实存在**的用 4u 终端 XML 信令实现（set_user_audio/video、set_layout、set_role、
    start/stop_aux_stream、configure_polling、invite_terminals）。
  - **4u 信令里不存在**的（invite_users/invite_contacts、create/cancel reserved·instant meeting、
    lock_layout）抛 ``ProtocolNotInSignaling``，并注明真实通道（FSP HTTP / Web 后台 / DB），
    避免测试侧把"没这个命令"误当"发送成功"。

权威源：4u_fmserver/protocol/ConfProtocol.{h,xml}、server/ConfService/{ConfMsgProcessor,LocalRoom}.cpp。
"""
from __future__ import annotations

from typing import List, Optional

from .. import conf as C
from ..errcodes import ProtocolNotInSignaling


class MeetingControlOps:
    """会议控制补齐接口（方案 §4）。真实命令实打实发；信令没有的明确报错。"""

    # ---------------------------- 主持人设置指定用户的音视频状态（6509 → 6609）----
    def set_user_audio(self, user_id, state: int, *, dev_id: int = 0) -> None:
        """主持人设置**指定用户**的音频状态。6509，广播 6609。音频 dev_id 必须 0。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_change_user_state(
            user_id=user_id, audio=C.ConfCmd.audio(dev_id=dev_id, state=state)))

    def set_user_video(self, user_id, state: int, *, dev_id: int = 1) -> None:
        """主持人设置**指定用户**的视频状态。6509，广播 6609。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_change_user_state(
            user_id=user_id, video=C.ConfCmd.video(dev_id=dev_id, state=state)))

    # ---------------------------- 会议布局（6513 WNDSTATE → 6613）----
    def set_layout(self, layout_type: int, user_ids: Optional[List] = None, *,
                   target_user_ids: Optional[List] = None,
                   exclude_user_ids: Optional[List] = None,
                   videos: Optional[List[dict]] = None,
                   mode: int = C.WND_MODE_VIDEO, full: Optional[int] = None,
                   data_active: Optional[int] = None,
                   full_video_id: Optional[int] = None,
                   full_video_media_id: Optional[int] = None,
                   valid_full_mode: Optional[int] = None) -> None:
        """设置会议布局（分屏类型 + 成员位置 + 定向接收）。6513，广播 6613。

        - layout_type：VideoRelayID（布局 ID）
        - user_ids：成员列表 → 自动按位置生成 wnd_video（也可用 videos 精细控制）
        - target_user_ids/exclude_user_ids：定向接收（写 DstInfo，服务端剥离后不下发）
        - 前置：广播会议布局一般需先申请 ``RIGHT_TYPE_LAYOUT`` 操作权
        """
        conf = self._require_conf()
        if videos is None and user_ids:
            videos = [C.ConfCmd.wnd_video(user_id=uid, media_id=0, pos=i)
                      for i, uid in enumerate(user_ids)]
        self._send(conf, C.ConfCmd.build_wndstate(
            mode=mode, full=full, data_active=data_active,
            video_relay_id=layout_type, full_video_id=full_video_id,
            full_video_media_id=full_video_media_id,
            valid_full_mode=valid_full_mode, videos=videos,
            dst_user_ids=target_user_ids, exclude_user_ids=exclude_user_ids))

    def lock_layout(self, enabled: bool = True, **kwargs) -> dict:
        """布局锁定/解锁。4u XML 信令里没有 → 走 Web API ``/v1/control/{roomId}`` LOCK_LAYOUT。

        需先 ``client.attach_http(...)``，且操作当前会议（self.room_id）。未 attach 抛 ProtocolNotInSignaling。"""
        http = self._need_http("lock_layout(布局锁定)", "Web API /v1/control LOCK_LAYOUT",
                               "XML 信令无布局锁定字段；未 attach_http")
        return http.lock_layout(self.room_id, enabled, **kwargs)

    # ---------------------------- 角色（presenter/admin 走 6509；chairman 走 6511）----
    def set_role(self, user_id, role: str, *, enabled: bool = True, chair_pwd: Optional[str] = None) -> None:
        """设置/取消用户角色。role: ``ROLE_PRESENTER``/``ROLE_ADMIN``/``ROLE_CHAIRMAN``。

        - presenter(主讲)/admin(管理员)：6509 的 DataState/ManagerState（2指定/0取消）→ 广播 6609
        - chairman(主持人)：**只能自申请/放弃**（6511），不能指定他人；指定他人无信令命令
        """
        conf = self._require_conf()
        if role == C.ROLE_PRESENTER:
            self._send(conf, C.ConfCmd.build_change_user_state(
                user_id=user_id,
                data_state=C.DATASTATE_PRESENTER if enabled else C.DATASTATE_NONE))
        elif role == C.ROLE_ADMIN:
            self._send(conf, C.ConfCmd.build_change_user_state(
                user_id=user_id,
                manager_state=C.MANAGERSTATE_ADMIN if enabled else C.MANAGERSTATE_NONE))
        elif role == C.ROLE_CHAIRMAN:
            if str(user_id) != str(self.room_user_id):
                raise ProtocolNotInSignaling(
                    "set_role(chairman, 指定他人)", "HTTP 会控 API（4u 信令无）",
                    "主持人 6511 只能自申请/放弃，不能指定他人为主持人")
            if enabled:
                self.apply_host(chair_pwd=chair_pwd)
            else:
                self.release_host()
        else:
            raise ValueError(f"unknown role {role!r}（用 C.ROLE_PRESENTER/ADMIN/CHAIRMAN）")

    # ---------------------------- 辅流（6509 MediaShareState/VNCState → 6609）----
    def start_aux_stream(self, *, kind: str = "media",
                         audio_share_id: Optional[int] = None,
                         video_share_id: Optional[int] = None) -> None:
        """开始辅流。kind: ``"media"``(MediaShareState=2) / ``"vnc"``(VNCState=2)。

        前置：调用方一般需先 6530 申请对应操作权；media 使用
        ``RIGHT_TYPE_MEDIA_SHARE``，vnc 使用 ``RIGHT_TYPE_SCREEN_SHARE``。
        media 的 audio_share_id/video_share_id 均为服务器必需字段。
        """
        conf = self._require_conf()
        if kind == "media":
            if audio_share_id is None or video_share_id is None:
                raise ValueError(
                    "media aux stream requires audio_share_id and video_share_id")
            self._send(conf, C.ConfCmd.build_change_user_state(
                user_id=self.room_user_id, media_share_state=C.MEDIASHARE_START,
                audio_share_id=audio_share_id, video_share_id=video_share_id))
        elif kind == "vnc":
            self._send(conf, C.ConfCmd.build_change_user_state(
                user_id=self.room_user_id, vnc_state=C.VNC_ON))
        else:
            raise ValueError(f"unknown aux kind {kind!r}（用 'media' 或 'vnc'）")

    def stop_aux_stream(self, *, kind: str = "media") -> None:
        """停止辅流。kind: ``"media"``(MediaShareState=0) / ``"vnc"``(VNCState=0)。"""
        conf = self._require_conf()
        if kind == "media":
            self._send(conf, C.ConfCmd.build_change_user_state(
                user_id=self.room_user_id, media_share_state=C.MEDIASHARE_STOP,
                audio_share_id=0, video_share_id=0))
        elif kind == "vnc":
            self._send(conf, C.ConfCmd.build_change_user_state(
                user_id=self.room_user_id, vnc_state=C.VNC_OFF))
        else:
            raise ValueError(f"unknown aux kind {kind!r}（用 'media' 或 'vnc'）")

    # ---------------------------- 视频轮询（6533 → 6633）----
    def configure_polling(self, operation: int, polling_id: int, name: str,
                          user_ids: List, *, interval: Optional[int] = None,
                          screen: Optional[int] = None, window: Optional[int] = None,
                          status: Optional[int] = None) -> None:
        """视频轮询 增/改/删。6533，下行 6633。

        - operation: ``POLLING_OP_ADD/MODIFY/DEL`` = 1/2/3
        - 暂停/恢复/停止：``POLLING_OP_MODIFY`` + ``status`` = PAUSE/RUNNING/STOP
        - polling_id：新增时客户端给一个非 0 值；前置需持有 RIGHT_TYPE_VIDEO_POLLING(2) 操作权
        """
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_videopolling([C.ConfCmd.video_polling(
            polling_id=polling_id, operation=operation, name=name, user_ids=user_ids,
            interval=interval, screen=screen, window=window, status=status)]))

    # ---------------------------- 邀请硬件终端（6741 MAKECALL CallType=2）----
    def invite_terminals(self, sns_list, *, nick_names: Optional[List[str]] = None) -> dict:
        """按 SNS 号码邀请 H323 硬件终端入会。= conf_invite_call(CallType=2)，SNS 填 CalleeNum。

        返回 MAKECALL_REP；呼叫进展看 on_call_state_notify(6745)。"""
        sns_list = [str(x) for x in sns_list]
        nicks = list(nick_names) if nick_names else sns_list
        callees = [C.ConfCmd.call_info(callee_num=sns, callee_nick_name=nicks[i] if i < len(nicks) else sns,
                                       callee_user_id=0)
                   for i, sns in enumerate(sns_list)]
        return self.conf_invite_call(callees, call_type=C.CALLTYPE_H323)

    # ==================== 信令里没有 → 走 Web 后台 HTTP（需先 attach_http）====================
    def _need_http(self, api: str, channel: str, hint: str = ""):
        """取 http_api；未 attach 则抛 ProtocolNotInSignaling（带真实通道）。"""
        if self.http_api is None:
            raise ProtocolNotInSignaling(api, channel, hint)
        return self.http_api

    def create_instant_meeting(self, **kwargs) -> dict:
        """创建即时会议。4u 信令无 → Web API ``POST /v1/room/createInstant``（需 attach_http）。

        关键字段：meeting_name、user_name（必填），meeting_type(0/1/2)、verify_mode(1/2/3) 可选。
        返回 data 含 roomId/inviteCode/nodeId，可供其它模拟端入会。未 attach 抛 ProtocolNotInSignaling。"""
        return self._need_http("create_instant_meeting", "Web API /v1/room/createInstant",
                               "未 attach_http").create_instant_meeting(**kwargs)

    def create_reserved_meeting(self, **kwargs) -> dict:
        """创建预约会议。4u 信令无 → Web API addRoomInfo + reservation（需 attach_http）。

        关键字段：room_name、hope_start_time、hope_end_time(``yyyy-MM-dd HH:mm:ss``)。
        返回 {roomId, addRoom, reservation}。未 attach 抛 ProtocolNotInSignaling。"""
        return self._need_http("create_reserved_meeting", "Web API addRoomInfo+reservation",
                               "未 attach_http").create_reserved_meeting(**kwargs)

    def cancel_reserved_meeting(self, room_id=None, *, hard: bool = False, **kwargs) -> dict:
        """取消会议。4u 信令无 → Web API editRoomStatus(status=0)/delRoomInfo（需 attach_http）。

        room_id 省略则用当前会议 self.room_id；hard=True 彻底删除。"""
        http = self._need_http("cancel_reserved_meeting", "Web API editRoomStatus/delRoomInfo", "未 attach_http")
        return http.cancel_reserved_meeting(room_id if room_id is not None else self.room_id,
                                            hard=hard, **kwargs)

    def invite_users(self, user_ids) -> dict:
        """邀请平台内部用户入会。4u 终端信令无 → Web API ``/v1/control/{roomId}`` INVITE_USER。

        操作当前会议（self.room_id），需先 attach_http。data.errors 为邀请失败的 userId 列表。"""
        http = self._need_http("invite_users", "Web API /v1/control INVITE_USER", "未 attach_http")
        return http.invite_users(self.room_id, user_ids)

    def invite_contacts(self, user_ids) -> dict:
        """从通讯录发出邀请；联系人已解析为平台 userId，等价于 invite_users。

        HTTP 成功只说明提交；接收端使用 PaasOnlineClient 等待邀请再入会。
        端到端例子见 examples/fm_invited_join_demo.py。
        """
        http = self._need_http("invite_contacts", "Web API /v1/control INVITE_USER",
                               "通讯录号→userId 在客户端解析；未 attach_http")
        return http.invite_users(self.room_id, user_ids)
