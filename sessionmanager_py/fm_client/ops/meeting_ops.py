"""扩展会议控制操作 mixin（Conf 域 app=11）：分组会议 / 操作权 / 字幕 / 改名 / 撤回 / 敲门 / 视图。

混入 FmClient，复用其 _require_conf / _request / _send。
权威字段源：4u_fmserver/protocol/ConfProtocol.h/.xml；Conf 主段 REP = REQ + 100。

> 真机注���：部分命令在旧版服务器可能未实现（发出去会被断开会话或超时），demo/join_and_do
> 已做动作级容错；这里的方法本身按协议实现，mock 下可全流程验证。
"""
from __future__ import annotations

from typing import List, Optional, Union

from .. import conf as C
from .._helpers import _as_list


class MeetingOps:
    """扩展会议控制：等候室敲门、分组会议、操作权、字幕/改名/系统消息/撤回、视图状态、发文件。"""

    # ---------------------------- 等候室 / 敲门（6504，无回复）----
    def allow_knock(self, user_id, *, allow: int = 1) -> None:
        """主持人答复敲门（6604 推送的应答动作）。allow: 0拒绝/1允许。无回复，发送即返回。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_allowknock(user_id=user_id, allow=allow))

    # ---------------------------- 分组会议（breakout）----
    def create_group(self, rooms: List[dict], *, timeout: Optional[float] = None) -> dict:
        """主持人创建分组会议(6523→6623)。rooms 用 C.ConfCmd.group_room(...) 构造。

        返回新建分组信息（重复 <RoomInfo>{RoomID,Theme,Creator,Duration,...}）。"""
        conf = self._require_conf()
        rep = self._request(conf, C.ConfCmd.build_creategroup(rooms), C.PAIRS, timeout)
        assert rep is not None
        fields = rep.fields
        fields["RoomInfo"] = _as_list(fields.get("RoomInfo"))
        return fields

    def enter_group(self, room_id, users: List[dict]) -> None:
        """通知目标成员加入分组会议(6524→目标端 6624)。

        ``users`` 用 ``C.ConfCmd.group_user(...)`` 构造；Token/SrvAddr 等结果由目标成员的
        ``on_group_enter``/``EventCollector`` 接收，发送者不保证收到 6624。
        """
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_entergroup(room_id=room_id, users=users))

    def end_groups(self, room_ids: List, *, timeout: Optional[float] = None) -> dict:
        """主持人结束分组会议(6525→6625)。"""
        conf = self._require_conf()
        rep = self._request(conf, C.ConfCmd.build_changegroupstate(room_ids), C.PAIRS, timeout)
        assert rep is not None
        return rep.fields

    def bring_to_main(self, user_ids: List) -> None:
        """主持人把用户从分组拉回主会议(6526→目标端 6626)。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_entermain(user_ids))

    def change_group_state(self, room_ids: List, state: int, *, timeout=None) -> dict:
        """6525/6625: new GroupMeeting states 0=idle, 1=start, 2=delete."""
        if state not in (0, 1, 2):
            raise ValueError("group state must be 0, 1 or 2")
        rep = self._request(self._require_conf(), C.ConfCmd.build_changegroupstate(
            room_ids, State=state), C.PAIRS, timeout)
        return rep.fields

    # ---------------------------- 操作权（布局/共享/白板/录制等唯一权）----
    def request_right(self, right_type: int, *, timeout: Optional[float] = None) -> dict:
        """申请某项操作权(6530→6630)。right_type 见 C.RIGHT_TYPE_*。返回 Result/OccupiedUserID。"""
        conf = self._require_conf()
        rep = self._request(conf, C.ConfCmd.build_operationright(
            operation=C.RIGHT_OP_APPLY, right_type=right_type), C.PAIRS, timeout)
        assert rep is not None
        return rep.fields

    def release_right(self, right_type: int, *, timeout: Optional[float] = None) -> dict:
        """释放某项操作权(6530→6630)。"""
        conf = self._require_conf()
        rep = self._request(conf, C.ConfCmd.build_operationright(
            operation=C.RIGHT_OP_RELEASE, right_type=right_type), C.PAIRS, timeout)
        assert rep is not None
        return rep.fields

    # ---------------------------- 角色 / 指定用户功能权限 ----
    def set_role_permissions(
        self,
        role_codes: Union[str, List[str]],
        *,
        permissions: Optional[dict] = None,
        operate_public_chat: Optional[bool] = None,
        operate_private_chat: Optional[bool] = None,
    ) -> None:
        """修改角色功能权限(6532，广播 6632)。

        ``role_codes`` 可传一个角色或多个角色；常用聊天权限使用显式参数，其他协议权限
        通过 ``permissions={"ConfigChatCheck": 1, ...}`` 传入。
        """
        values = dict(permissions or {})
        if operate_public_chat is not None:
            values["OperatePublicChat"] = operate_public_chat
        if operate_private_chat is not None:
            values["OperatePrivateChat"] = operate_private_chat
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_change_role_permission(
            role_codes, values))

    def set_user_permissions(
        self,
        user_id,
        *,
        permissions: Optional[dict] = None,
        operate_public_chat: Optional[bool] = None,
        operate_private_chat: Optional[bool] = None,
    ) -> None:
        """修改指定用户功能权限(6535，广播 6635)。"""
        values = dict(permissions or {})
        if operate_public_chat is not None:
            values["OperatePublicChat"] = operate_public_chat
        if operate_private_chat is not None:
            values["OperatePrivateChat"] = operate_private_chat
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_change_user_permission(
            user_id=user_id, permissions=values))

    # ---------------------------- 会场数据 / AV 合成 / 视频字幕 ----
    def set_room_data(self, items: List[dict]) -> None:
        """设置会场/课堂数据(6519，广播 6619)。items 用 room_data_item() 构造。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_set_room_data(items))

    def get_av_mix_params(self, *, timeout: Optional[float] = None) -> dict:
        """查询 AV 合成参数(6520→6620)。"""
        conf = self._require_conf()
        rep = self._request(
            conf, C.ConfCmd.build_get_av_mix_params(), C.PAIRS, timeout)
        assert rep is not None
        return rep.fields

    def set_av_mix_params(
        self,
        *,
        enable_default: bool,
        audio: Optional[dict] = None,
        video: Optional[dict] = None,
    ) -> None:
        """设置 AV 合成参数(6521，无直接回复；可随后调用 get_av_mix_params 回读)。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_set_av_mix_params(
            enable_default=enable_default, audio=audio, video=video))

    def set_video_subtitle_params(
        self,
        *,
        date_time: Optional[dict] = None,
        user_name: Optional[dict] = None,
    ) -> None:
        """设置视频画面字幕参数(6522，广播 6622)。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_set_video_subtitle(
            date_time=date_time, user_name=user_name))

    # ---------------------------- 会议辅助 ----
    def rename_meeting(self, name: str, *, timeout: Optional[float] = None) -> dict:
        """修改会议室名称(6527→6627)。"""
        conf = self._require_conf()
        rep = self._request(conf, C.ConfCmd.build_meetingrename(name=name), C.PAIRS, timeout)
        assert rep is not None
        return rep.fields

    def set_subtitle(self, data: str, *, duration: int = 0) -> None:
        """主持人设会场字幕(6515，广播 6615)。duration 分钟，0=永久。通知类，不等回复。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_meetingchar(data=data, duration=duration))

    def close_subtitle(self, *, timeout: Optional[float] = None) -> dict:
        """关闭会场字幕(6550→6650)。"""
        conf = self._require_conf()
        rep = self._request(conf, C.ConfCmd.build_closesubtitle(), C.PAIRS, timeout)
        assert rep is not None
        return rep.fields

    def send_sysmsg(self, *, sys_msg_id: int, dst_user_id: int = 0) -> None:
        """发送服务器预配置的系统消息(6518→目标端 6618)。

        ``sys_msg_id`` 必须是服务器消息配置中存在且大于 2 的 ID；消息正文和类型由服务器
        配置决定。``dst_user_id=0`` 表示广播，服务器默认排除发送者。
        """
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_sysmsg(
            dst_user_id=dst_user_id, sys_msg_id=sys_msg_id))

    def send_file(self, dst_user_ids: List, file_info: dict) -> None:
        """发文件通知(6516，广播 6616 给他人)。file_info 用 C.ConfCmd.file_info(...)。通知类。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_sendfile(dst_user_ids=dst_user_ids, file_info=file_info))

    def recall_msg(self, msg_id, timeout: Optional[float] = None) -> dict:
        """撤回聊天消息(6568 MsgID→6668，广播6669 MsgID/SrcUserID)。"""
        conf = self._require_conf()
        rep = self._request(
            conf, C.ConfCmd.build_recallmsg(msg_id), C.PAIRS, timeout)
        assert rep is not None
        return rep.fields

    # ---------------------------- 视图 / 布局（6513，广播 6613）----
    def set_window_state(self, *, mode: int = C.WND_MODE_NORMAL, full: Optional[int] = None,
                         data_active: Optional[int] = None, video_relay_id: Optional[int] = None,
                         full_video_id: Optional[int] = None,
                         full_video_media_id: Optional[int] = None,
                         valid_full_mode: Optional[int] = None,
                         videos: Optional[List[dict]] = None) -> None:
        """上报当前视图状态(6513，广播 6613 转发布局/视频)。videos 用 C.ConfCmd.wnd_video()。通知类。"""
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_wndstate(
            mode=mode, full=full, data_active=data_active,
            video_relay_id=video_relay_id, full_video_id=full_video_id,
            full_video_media_id=full_video_media_id,
            valid_full_mode=valid_full_mode, videos=videos))
