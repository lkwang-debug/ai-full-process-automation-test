"""Conf 域(app=11)核心操作 mixin：入会 / 聊天 / 音视频 / 录制 / 会控 / 离开。

混入 FmClient，复用其 _ensure_conf / _require_conf / _request / _send 及登录态。
权威字段源：4u_fmserver/server/ConfService/ConfMsgProcessor.cpp；Conf 域 REP/Notify = REQ + 100。
"""
from __future__ import annotations

from typing import Optional

from .. import conf as C
from .._helpers import _to_int


class ConfOps:
    """Conf 域核心会中操作：入会、聊天、音视频、录制、主席、踢人、会议状态、静音、离开。"""

    # ---- 6501 登录会议室（拿会场配置 + 媒体组件地址 + 自己的 Right）----
    def conf_login(
        self,
        *,
        user_name: Optional[str] = None,
        user_type: int = 1,
        verify_mode: Optional[int] = None,
        room_password: Optional[str] = None,
        capability_mode: int = 1,
        down_bitrate: int = 500000,
        support_features=None,
        screen_count=None,
        layouts=None,
        join_type=None,
        client_type: int = 1,
        terminal_type: int = 1,
        device_id=None,
        init_complete: bool = True,
        timeout: Optional[float] = None,
    ) -> dict:
        """Conf(app=11)：LOGINREQ → 收 6601（会场配置+媒体地址），再发 INITCOMPLETE。

        verify_mode / room_password 默认从 front_login 捕获（30005 的 RoomVerifyMode +
        传入的房间密码）自动复用；显式传入则覆盖。"""
        if self.room_srv_node_id is None or self.token is None or self.room_user_id is None:
            raise RuntimeError("call front_login() first")
        if verify_mode is None:
            verify_mode = _to_int(self.room_verify_mode, default=3)
        if room_password is None:
            room_password = self.room_password
        self._conf_login_options = dict(
            user_name=user_name, user_type=user_type, capability_mode=capability_mode,
            down_bitrate=down_bitrate, support_features=support_features,
            screen_count=screen_count, layouts=layouts, client_type=client_type,
            terminal_type=terminal_type, device_id=device_id)
        self._offline_users_cache = None
        conf = self._ensure_conf()
        cmd = C.ConfCmd.build_loginreq(
            room_srv_node_id=self.room_srv_node_id, room_id=self.room_id,
            user_id=self.room_user_id, user_name=user_name or str(self.room_user_id),
            user_type=user_type, token=self.token, verify_mode=verify_mode,
            room_password=room_password, capability_mode=capability_mode,
            down_bitrate=down_bitrate, support_features=support_features,
            screen_count=screen_count, layouts=layouts, join_type=join_type,
            client_type=client_type, terminal_type=terminal_type, device_id=device_id)
        rep = self._request(conf, cmd, C.PAIRS, timeout)
        assert rep is not None
        user_info = rep.get("UserInfo")
        if isinstance(user_info, dict):
            self.user_info = user_info
            if user_info.get("Right") is not None:
                self.user_right = user_info["Right"]
        self.dept_info = rep.get("DeptInfo")
        if init_complete:
            self._send(conf, C.ConfCmd.build_initcomplete())
        return rep.fields

    def connect_related_room(self, room_info: dict, *, timeout=None, **login_options):
        """Open an independent Conf-only client using 6624/6667 routing information.

        Keeps this client's Front and main-room Conf alive. The returned client
        owns its connection and must be closed by the caller. Waiting-room 6667
        has no Token, so it reuses this identity's main-room token. SrvAuthCode
        is server routing metadata, NOT a chairman or room password.
        """
        for key in ("RoomID", "RoomSrvNodeID", "SrvAddr"):
            if not room_info.get(key):
                raise ValueError("related room information is missing " + key)
        if self.room_user_id is None or self.token is None:
            raise RuntimeError("call front_login() first")
        child = type(self)(self._host, self._port, encoding=self._encoding,
                           request_timeout=self._request_timeout,
                           conf_protocol_version=self.conf_protocol_version,
                           **self._sess_cfg)
        child.local_user_id = self.local_user_id
        child.room_user_id = self.room_user_id
        child.room_id = int(room_info["RoomID"])
        child.room_srv_node_id = room_info["RoomSrvNodeID"]
        child.room_node_id = self.room_node_id
        child.token = room_info.get("Token") or self.token
        child.room_verify_mode = "3"
        child.conf_servers = [{"SrvAddr": room_info["SrvAddr"]}]
        options = dict(self._conf_login_options)
        options.update(login_options)
        try:
            child.conf_login(timeout=timeout, **options)
        except BaseException:
            child.close()
            raise
        return child

    # ---- 6512 文字消息（公聊 DstUserID=0 / 私聊指定 ID）----
    def chat(self, text: str, *, dst_user_id: int = 0,
             color: Optional[int] = None, font: Optional[dict] = None) -> None:
        """会中文字消息。font使用协议属性名Name/Width/Height/Weight等。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_chat(
            src_user_id=self.room_user_id, text=text, dst_user_id=dst_user_id,
            color=color, font=font))

    # ---- 6509 改变用户状态（音频/发言/视频广播…）----
    def set_audio_state(self, state: int, *, dev_id: int = 0, user_id: Optional[int] = None) -> None:
        """设置音频/发言状态（0关/1等待/2发言）。6509，广播通知，不等回复。

        注意：4u 服务端音频只认 ``ID=0``（``MAX_AUDIODEVICE_COUNT=1``，``nID<1``），
        ``dev_id`` 必须为 0，否则服务端静默丢弃、不广播 6609。视频无此限制。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_change_user_state(
            user_id=user_id if user_id is not None else self.room_user_id,
            audio=C.ConfCmd.audio(dev_id=dev_id, state=state)))

    def set_video_state(self, state: int, *, dev_id: int = 1, user_id: Optional[int] = None) -> None:
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_change_user_state(
            user_id=user_id if user_id is not None else self.room_user_id,
            video=C.ConfCmd.video(dev_id=dev_id, state=state)))

    # ---------------------------- 媒体便捷方法（封装 set_audio/video_state）---
    def mic_on(self, *, dev_id: int = 0, user_id: Optional[int] = None) -> None:
        """开麦（发言，Audio.State=2）。音频 dev_id 必须为 0。"""
        self.set_audio_state(C.AUDIO_SPEAKING, dev_id=dev_id, user_id=user_id)

    def mic_off(self, *, dev_id: int = 0, user_id: Optional[int] = None) -> None:
        """关麦（Audio.State=0）。音频 dev_id 必须为 0。"""
        self.set_audio_state(C.AUDIO_OFF, dev_id=dev_id, user_id=user_id)

    def mic_request(self, *, dev_id: int = 0, user_id: Optional[int] = None) -> None:
        """申请发言（Audio.State=1 等待，主持人同意后置 2）。音频 dev_id 必须为 0。"""
        self.set_audio_state(C.AUDIO_WAITING, dev_id=dev_id, user_id=user_id)

    def camera_on(self, *, dev_id: int = 1, user_id: Optional[int] = None) -> None:
        """开摄像头（广播，Video.State=2）。"""
        self.set_video_state(C.VIDEO_BROADCAST, dev_id=dev_id, user_id=user_id)

    def camera_off(self, *, dev_id: int = 1, user_id: Optional[int] = None) -> None:
        """关摄像头（Video.State=0）。"""
        self.set_video_state(C.VIDEO_OFF, dev_id=dev_id, user_id=user_id)

    # ---------------------------- 录制（CHANGECONFSTATE 的 ServerRecordStatus）---
    def start_record(self) -> None:
        """开启会议录制（ServerRecordStatus=2）。广播 6610 带 EnableRecord。"""
        self.set_conf_state(server_record_status=C.RECORD_RECORDING)

    def stop_record(self) -> None:
        """结束会议录制（ServerRecordStatus=0）。"""
        self.set_conf_state(server_record_status=C.RECORD_STOP)

    def pause_record(self) -> None:
        """暂停录制（ServerRecordStatus=1）。"""
        self.set_conf_state(server_record_status=C.RECORD_PAUSE)

    # ---------------------------- 房间锁定便捷方法 ---
    def lock_room(self) -> None:
        """锁定会议室（Lock=1）。"""
        self.set_conf_state(lock=1)

    def unlock_room(self) -> None:
        """解锁会议室（Lock=0）。"""
        self.set_conf_state(lock=0)

    # ---------------------------- 会控（均为通知类：发送即广播 +100 给全体）---
    def kick_user(self, user_id, *, kick_type: Optional[int] = None, tips: Optional[str] = None) -> None:
        """踢人(6506→6606)。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_kickuser(
            user_id=user_id, kick_type=kick_type, tips=tips))

    def close_room(self, *, reason: int = 0, description: str = "") -> None:
        """关闭会议室(6507→6607，仅主持人)。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_closeroom(reason=reason, description=description))

    def report_av_info(self, *, audio=None, video=None, def_video_dev_id: Optional[int] = None) -> None:
        """上报本地音视频设备(6508→6608)。audio/video 用 C.ConfCmd.av_device() 构造列表。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_avinfo(
            audio=audio, video=video, def_video_dev_id=def_video_dev_id))

    def set_conf_state(self, **kwargs) -> None:
        """修改会议状态(6510→6610)：mode/lock/enable_chat/enable_p2p_chat/
        enable_pub_chat/enable_record/enable_send_file/enable_waiting_room（只传要改的）。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_change_conf_state(**kwargs))

    def apply_host(self, *, chair_pwd: str) -> None:
        """申请主持人/管理员(6511，ChairRight=1)，chair_pwd 必填。

        新产品协议下服务器会把该操作解释为申请管理员权限；传统协议下为申请主持人。
        成功走 6609 状态变更，失败走 6611。
        """
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_req_chairright(
            chair_right=C.CHAIR_APPLY, chair_pwd=chair_pwd))

    def release_host(self) -> None:
        """放弃主持人(6511，ChairRight=0)。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_req_chairright(chair_right=C.CHAIR_RELEASE))

    def mute_all(self, **extra) -> None:
        """全场静音(6514→6614，主持人下发)。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_silent(**extra))

    def conf_logout(self, *, reason: int = 0) -> None:
        """退出会议室（6505，通知类）。"""
        self._offline_users_cache = None
        if self._conf is None:
            return
        self._send(self._conf, C.ConfCmd.build_logout(
            user_id=self.room_user_id, room_srv_node_id=self.room_srv_node_id,
            room_id=self.room_id, reason=reason))
