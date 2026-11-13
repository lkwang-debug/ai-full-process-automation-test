"""Readable Chinese Allure metadata and protocol evidence formatting."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict, is_dataclass
from enum import Enum
import json
import re
from typing import Any, Mapping

from .redaction import redact

try:
    import allure
except ImportError:  # pragma: no cover
    allure = None


OPERATION_NAMES = {
    'request_presenter':'申请Web主持/数据控制角色','release_presenter':'放弃Web主持/数据控制角色',
    'request_manager':'申请管理员','release_manager':'放弃管理员',
    'get_identity':'读取当前会议身份','get_devices':'读取实际媒体设备','get_users':'读取当前成员',
    'get_user_info':'查看用户个人信息','get_authenticated_profile':'读取真实认证账户资料',
    'get_capabilities':'读取实际角色和产品能力','start_screen_share':'开始桌面共享',
    'stop_screen_share':'停止桌面共享','start_whiteboard':'创建并共享白板',
    'join_guest':'游客匿名入会','approve_self_av':'批准成员本人音视频申请',
    'stop_whiteboard':'关闭本次共享白板','leave':'退出Web会议','close_remote_av':'关闭目标用户音视频',
    'invite_terminals_http': '后台邀请H.323终端：HTTP结果与真实入会通知',
    'invite_terminals': '按SNS邀请H.323终端：接通与挂断',
    'conf_invite_call': 'Fm呼叫H.323终端：响应、接通与挂断',
    'conf_hangup': 'H.323挂断：合法呼叫与无效目标',
    'invite_users': '普通用户邀请：PaaS在线接收后真实入会',
    'invite_contacts': '通讯录邀请：PaaS在线接收后真实入会',
    'send_relay_message': 'Relay定向往返：来源目标与内层消息原样转发',
    'send_room_message': '后台向指定会议端发送Web通知',
    'request_remote_av': '管理员请求远程开启成员音视频',
    'review_chat_message_and_wait': '审批聊天消息并确认结果',
    'reply_remote_av': '目标端回复远程音视频请求',
    'respond_remote_av': '目标端按实际请求同意或拒绝远程音视频',
    'front_logout': 'Front退房并注销：验证两条真实响应',
    'control_user_audio': '后台控制音频：HTTP设备映射与端侧状态通知',
    'control_user_video': '后台控制视频：HTTP设备映射与端侧状态通知',
    "camera_off": "关闭摄像头", "camera_on": "开启摄像头",
    "cancel_reserved_meeting": "取消预约会议", "chat": "发送会中聊天消息",
    "close_room": "关闭会议", "close_subtitle": "关闭会议字幕",
    "conf_get_room_config": "查询会议室配置", "conf_login": "登录Conf会议",
    "conf_logout": "退出Conf会议", "conf_query_call_list": "查询H.323呼叫列表",
    "configure_polling": "配置视频轮询", "create_group": "创建分组会议",
    "create_instant_meeting": "创建即时会议", "create_reserved_meeting": "创建预约会议",
    "download_meeting_summary": "下载会议纪要", "entrance_get_config": "查询接入配置",
    "export_meeting_summary": "导出会议纪要", "export_transcript": "导出会议字幕",
    "front_authenticate": "Front账号认证", "front_discover_node": "发现会议节点",
    "front_get_auth_type": "查询认证方式", "front_login": "登录Front并进入房间",
    "front_login_by_invite_code": "使用邀请码入会", "front_login_guest": "访客入会",
    "get_meeting_summary": "查询会议纪要", "get_record_share": "查询录制分享信息",
    "get_record_summary": "查询录制摘要详情", "kick_user": "请出会议成员",
    "close_meeting": "后台关闭会议",
    "list_record_participants": "查询录制参与人", "list_record_summaries": "查询录制摘要列表",
    "list_record_tasks": "查询录制任务", "list_records": "查询录制文件",
    "list_room_record_summaries": "按会议室查询录制摘要", "lock_layout": "锁定或解锁布局",
    "lock_meeting": "后台锁定或解锁会议", "lock_room": "锁定会议",
    "mic_off": "关闭麦克风", "mic_on": "开启麦克风", "mic_request": "申请发言",
    "mute_all": "Fm端全体静音", "mute_all_http": "后台全体静音", "pause_record": "暂停录制",
    "conf_get_offline_users": "查询会议离线授权成员",
    "offline_contacts": "读取离线成员缓存",
    "conf_get_contacts": "查询会议通讯录快照",
    "contacts_snapshot": "读取通讯录快照",
    "apply_host": "申请主持人", "release_host": "释放主持人",
    "operate_record": "后台开始或停止录制", "remove_user": "后台请出会议成员",
    "release_right": "释放操作权", "rename_meeting": "修改会议名称",
    "report_av_info": "上报音视频设备", "request_right": "申请操作权",
    "send_file": "发送文件通知", "send_subtitles": "后台发送会议字幕",
    "send_sysmsg": "发送系统消息", "set_audio_state": "设置本端音频状态",
    "set_conf_state": "设置会议状态", "set_layout": "设置Fm会议布局",
    "set_layout_http": "后台设置会议布局", "set_role": "设置成员角色",
    "set_role_permissions": "设置角色权限", "set_room_data": "设置会议自定义数据",
    "set_subtitle": "设置会议字幕", "set_user_audio": "控制指定成员音频",
    "set_user_manager": "后台设置管理员", "set_user_permissions": "设置成员权限",
    "set_user_presenter": "后台设置主讲人", "set_user_video": "控制指定成员视频",
    "set_video_state": "设置本端视频状态", "set_video_subtitle_params": "设置视频字幕样式",
    "set_window_state": "上报窗口布局状态", "start_aux_stream": "开始辅流",
    "start_record": "开始录制", "stop_aux_stream": "停止辅流",
    "stop_record": "停止录制", "unlock_room": "解锁会议",
    "replace_client": "重建客户端", "start_async_call": "异步启动客户端操作",
    "wait_async_events": "等待异步协议通知", "wait_async_call": "等待异步操作完成",
    "allow_knock": "处理敲门入会请求", "bring_to_main": "返回主会议",
    "end_groups": "结束分组会议", "enter_group": "进入分组会议",
    "recall_msg": "撤回聊天消息", "set_user_video": "控制指定成员视频",
    "set_multi_data_sharer": "切换多人数据共享模式",
    "get_user_features": "查询成员功能能力",
    "query_feature_users": "查询支持指定能力的成员",
    "unsubscribe_feature_users": "取消成员能力订阅",
    "set_user_state": "设置扩展成员状态",
    "reply_self_state_change": "回复远程状态修改请求",
    "renegotiate_layout": "重新协商布局能力",
    "send_h5_vote": "发送H5投票状态",
    "modify_group_config": "修改分组会议配置",
    "change_group_state": "切换分组会议状态",
    "allow_waiting_users": "审批等候室成员",
    "set_asr_subtitle": "设置语音识别字幕",
    "set_user_custom_data": "设置用户自定义数据",
}

ALIAS_NAMES = {
    "operator": "操作端A", "observer": "观察端B", "witness": "无关端C",
    "invitee": "被邀请端", "http_admin": "后台HTTP服务",
}

CLOSURE_NAMES = {
    "FM_TO_FM": "Fm端到Fm端", "HTTP_TO_FM": "后台会控到Fm端",
    "HTTP_TO_FM_JOIN": "后台建会与Fm入会", "HTTP_RESULT": "会后结果查询",
}

PARAMETER_NAMES = {
    "state": "状态", "dev_id": "设备ID", "device_id": "设备ID",
    "user_id": "目标用户", "user_ids": "成员列表", "layout_type": "分屏布局",
    "role": "角色", "enabled": "是否启用", "operation": "操作类型",
    "polling_id": "轮询ID", "interval": "轮询间隔", "screen": "屏幕数",
    "window": "窗口", "kind": "辅流类型", "duration": "持续时间",
    "text": "消息内容", "data": "字幕内容", "content": "字幕内容",
    "room_id": "会议室ID", "call_type": "呼叫类型", "name": "名称",
    "font_size": "字号", "font_color": "字体颜色", "align": "对齐方式",
    "subtitle_type": "字幕类型", "right_type": "操作权类型",
    "sys_msg_id": "系统消息ID", "invite_code": "邀请码",
    "terminal_type": "终端类型", "client_version": "客户端版本",
    "device_id": "设备标识", "user_login_type": "登录方式",
    "user_type": "用户类型", "client_type": "客户端类型",
    "app_type": "应用类型", "product_name": "产品名称",
    "language": "语言", "page_num": "页码", "page_size": "每页数量",
    "reason": "退出原因", "description": "说明", "color": "消息颜色",
    "dst_user_id": "接收用户", "dst_user_ids": "接收用户",
    "audio_share_id": "音频共享ID", "video_share_id": "视频共享ID",
    "msg_id": "消息ID",
    "repeat_after_reject": "拒绝后是否重复同意",
}

PRECONDITION_NAMES = {
    "AUDIO_ON": "前置为麦克风开启", "AUDIO_OFF": "前置为麦克风关闭",
    "VIDEO_ON": "前置为摄像头开启", "VIDEO_OFF": "前置为摄像头关闭",
    "TARGET_AUDIO_ON": "观察端B前置为麦克风开启",
    "TARGET_AUDIO_OFF": "观察端B前置为麦克风关闭",
    "TARGET_VIDEO_ON": "观察端B前置为摄像头开启",
    "TARGET_VIDEO_OFF": "观察端B前置为摄像头关闭",
    "RECORDING": "前置为录制中", "RECORDING_STOPPED": "前置为未录制",
    "ROOM_LOCKED": "前置为会议已锁定", "ROOM_UNLOCKED": "前置为会议未锁定",
    "SUBTITLE_SET": "前置为字幕已开启", "RIGHT_HELD": "前置为已取得对应操作权",
    "LAYOUT_RIGHT": "已取得布局操作权", "POLLING_RIGHT": "已取得轮询操作权",
    "GROUP_RIGHT": "已取得分组操作权", "MEDIA_RIGHT": "已取得媒体辅流操作权",
    "VNC_RIGHT": "已取得VNC辅流操作权",
    "MEDIA_STARTED_WITH_RIGHT": "媒体辅流已开始且持有操作权",
    "VNC_STARTED_WITH_RIGHT": "VNC辅流已开始且持有操作权",
    "TARGET_ADMIN_ON": "观察端B前置为管理员",
    "TARGET_PRESENTER_ON": "观察端B前置为主讲人",
    "HTTP_PRESENTER_ON": "后台已将观察端B设为主讲人",
}

RIGHT_NAMES = {
    1: "布局权", 2: "轮询权", 3: "白板权", 4: "VNC辅流权",
    5: "媒体辅流权", 6: "文档共享权", 7: "桌面共享权",
    8: "远程控制权", 9: "录制权", 10: "字幕权",
    11: "分组权", 12: "会议管理权",
}

_SECRET_XML = re.compile(
    r'(<(?:Token|[A-Za-z]*(?:Password|Passwd|Pwd)|Authorization|AppSecret|CheckCode|SrvAuthCode|AuthCode)\b[^>]*\bval=")[^"]*(")',
    re.IGNORECASE,
)


def operation_name(operation: str) -> str:
    return OPERATION_NAMES.get(operation, operation)


def alias_name(alias: str | None) -> str:
    if not alias:
        return "无指定目标"
    return ALIAS_NAMES.get(alias, alias)


def closure_name(value: Any) -> str:
    key = getattr(value, "value", value)
    return CLOSURE_NAMES.get(str(key), str(key))


def case_title(case: Any) -> str:
    case_kind = "业务异常" if getattr(case.case_type, "value", case.case_type) == "BUSINESS_EXCEPTION" else "正常场景"
    return f"{operation_name(case.operation)}｜{case_kind}｜{scenario_description(case)}"


def scenario_description(case: Any) -> str:
    operation = case.operation
    params = case.params
    if case.scene_type == 'AUX_FM_CONTENTION':
        return 'A共享中B无法抢发；A结束后B取得共享权并启动VNC'
    if case.scene_type == 'WAITING_REVIEW':
        return ('拒绝B、允许C，同批审批后仅C入会' if params['decision'] == 'mixed'
                else '拒绝B，审批后B不能进入主会场')
    if operation == "create_group":
        scene = getattr(case, 'scene_type', '')
        parent = scene.removeprefix('GROUP_CONFIG_')
        prefix = (f'主会议加入={parent[0]}、离开={parent[1]}；' if
                  scene.startswith('GROUP_CONFIG_') and len(parent) == 2 else '')
        return prefix + _group_description(params.get("rooms") or [])
    if operation == "set_layout":
        return _layout_description(params, case)
    if operation == "set_layout_http":
        layout = params.get("layout") or {}
        return (f"后台下发布局{layout.get('videoRelayId')}；模式{layout.get('mode')}；"
                f"数据窗口{layout.get('dataActive')}；全屏{_yes_no(layout.get('full'))}")
    if operation == "report_av_info":
        return _device_description(params)
    if operation in {"set_audio_state", "set_user_audio"}:
        target = "观察端B" if params.get("user_id") else "操作端A"
        state = {0: "关闭", 1: "申请发言", 2: "发言"}.get(params.get("state"), params.get("state"))
        device = (f"显式设备ID={params['dev_id']}" if "dev_id" in params else
                  "使用默认音频设备ID=0")
        return _join_parts([f"{target}音频{state}", device,
                            PRECONDITION_NAMES.get(case.precondition)])
    if operation in {"set_video_state", "set_user_video"}:
        target = "观察端B" if params.get("user_id") else "操作端A"
        state = {0: "关闭", 2: "广播"}.get(params.get("state"), params.get("state"))
        device = (f"显式设备ID={params['dev_id']}" if "dev_id" in params else
                  "使用默认视频设备ID=1")
        return _join_parts([f"{target}视频{state}", device,
                            PRECONDITION_NAMES.get(case.precondition)])
    if operation == "chat":
        target = "群聊" if params.get("dst_user_id", 0) == 0 else f"私聊{_display_value(params.get('dst_user_id'))}"
        font = params.get("font") or {}
        return _join_parts([target, f"内容={_text_feature(params.get('text', ''))}",
                            f"颜色={params['color']}" if "color" in params else None,
                            ("字体=" + "/".join(f"{key}:{_display_value(value)}"
                                                 for key, value in font.items())) if font else None])
    if operation in {"set_role_permissions", "set_user_permissions"}:
        subject = ("角色=" + "/".join(_display_value(x) for x in params.get("role_codes", []))
                   if operation == "set_role_permissions" else
                   f"成员={_display_value(params.get('user_id'))}")
        permissions = params.get("permissions") or {}
        values = "/".join(f"{_field_name(k)}={_enabled_value(v)}" for k, v in permissions.items())
        return f"{subject}；{values}"
    if operation == "set_role":
        role = {"admin": "管理员", "presenter": "主讲人", "chairman": "主持人"}.get(
            params.get("role"), params.get("role"))
        return _join_parts([
            f"{_display_value(params.get('user_id'))}{'设置为' if params.get('enabled', True) else '取消'}{role}",
            PRECONDITION_NAMES.get(case.precondition),
        ])
    if operation in {"request_right", "release_right"}:
        right = params.get("right_type")
        return f"{RIGHT_NAMES.get(right, f'未知操作权{right}')}；{PRECONDITION_NAMES.get(case.precondition, '直接操作')}"
    if operation in {"set_subtitle", "send_subtitles"}:
        content = params.get("data", params.get("content", ""))
        duration = params.get("duration")
        return _join_parts([
            f"内容={_text_feature(content)}",
            "永久显示" if duration == 0 else (f"持续{duration}分钟" if duration is not None else None),
            f"字号={params['font_size']}" if "font_size" in params else None,
            f"颜色={params['font_color']}" if "font_color" in params else None,
            f"字幕类型={params['subtitle_type']}" if "subtitle_type" in params else None,
        ])
    if operation == "set_video_subtitle_params":
        style = params.get("date_time") or params.get("user_name") or {}
        return "；".join(f"{_field_name(k)}={v}" for k, v in style.items())
    if operation == "set_conf_state":
        return "；".join(f"{_field_name(k)}={_enabled_value(v)}" for k, v in params.items())
    if operation == "set_room_data":
        items = params.get("items") or []
        values = []
        for item in items:
            room_data = item.get("RoomData") or {}
            values.append(
                f"数据项ID={item.get('ItemID')}，键={room_data.get('Key')}，"
                f"值={_display_value(room_data.get('Value'))}")
        return "；".join(values) or "空会议自定义数据"
    if operation == "configure_polling":
        status = {0: "停止", 1: "暂停", 2: "运行"}.get(params.get("status"), params.get("status"))
        action = {1: '新增', 2: '修改', 3: '删除'}.get(params.get('operation'), f"操作{params.get('operation')}")
        return _join_parts([
            f"{action}轮询{params.get('polling_id')}", f"状态={status}",
            f"成员={_display_list(params.get('user_ids') or [])}",
            f"间隔={params.get('interval')}毫秒", f"屏幕={params.get('screen')}；窗口={params.get('window')}",
        ])
    if operation in {"start_aux_stream", "stop_aux_stream"}:
        kind = {"media": "媒体辅流", "vnc": "VNC辅流"}.get(params.get("kind"), params.get("kind") or "空类型")
        return _join_parts([kind, PRECONDITION_NAMES.get(case.precondition)])
    if operation == "conf_logout":
        return f"退出原因={params.get('reason')}；观察端B接收成员离会通知"
    if operation in {"list_record_participants", "list_record_summaries", "list_record_tasks", "list_records", "list_room_record_summaries"}:
        return _join_parts([f"页码={params.get('page_num', '默认')}",
                            f"每页={params.get('page_size', '默认')}条",
                            f"会议室={_display_value(params.get('room_id'))}" if params.get("room_id") else None])
    if operation in {"download_meeting_summary", "export_meeting_summary", "export_transcript"}:
        kind={1:'Markdown',2:'PDF'}.get(params.get('type'),'默认格式（不传type）')
        return f"格式={kind}；使用查询取得的groupId"
    if operation == "front_authenticate":
        return _join_parts([
            f"登录方式={params.get('user_login_type', '默认')}", f"用户类型={params.get('user_type', '默认')}",
            f"终端类型={params.get('terminal_type', '默认')}", f"客户端类型={params.get('client_type', '默认')}",
            f"产品={params.get('product_name', '默认')}",
        ])
    if operation == "entrance_get_config":
        return _join_parts([f"终端类型={params.get('terminal_type', '默认')}",
                            f"客户端版本={params.get('client_version', '默认')}",
                            f"设备标识={params.get('device_id', '默认')}"])
    generic = _generic_parameter_parts(params)
    precondition = PRECONDITION_NAMES.get(case.precondition)
    if precondition:
        generic.append(precondition)
    route = _route_description(case)
    if route:
        generic.append(route)
    return _join_parts(generic) or "标准协议闭环"


def parameter_summary(params: Mapping[str, Any], limit: int = 3) -> str:
    parts = []
    for key, value in params.items():
        if key not in PARAMETER_NAMES:
            continue
        if key == "room_id" and value == "${operator.room_id}":
            continue
        parts.append(f"{PARAMETER_NAMES[key]}={_short_value(value)}")
        if len(parts) >= limit:
            break
    return "，".join(parts)


def _group_description(rooms: list[Mapping[str, Any]]) -> str:
    if not rooms:
        return "创建空分组配置"
    first = rooms[0]
    members = first.get("User") or []
    member_text = "/".join(_group_member(user) for user in members) or "无预置成员"
    themes = "/".join(str(room.get("Theme", "未命名")) for room in rooms)
    prefix = "单分组" if len(rooms) == 1 else f"创建{len(rooms)}个分组"
    return (
        f"{prefix}；主题测试值={themes}；状态值={first.get('State', '默认')}；"
        f"请求人数上限={first.get('MaxUserCount', '不限')}（新版按会议配置）；"
        f"{'允许' if first.get('EnableJoin') else '禁止'}主动加入；"
        f"{'允许' if first.get('EnableLeave') else '禁止'}主动离开；成员={member_text}")


def _group_member(user: Mapping[str, Any]) -> str:
    right = user.get("Right")
    role = {1: "成员", 2: "管理员", 3: "主持"}.get(right, f"权限{right}")
    return f"{_display_value(user.get('ID'))}({role})"


def _layout_description(params: Mapping[str, Any], case: Any) -> str:
    layout = params.get("layout_type")
    videos = params.get("videos") or []
    users = params.get("user_ids") or [item.get("UserID") for item in videos]
    route = (f"仅发送给{_display_list(params['target_user_ids'])}" if params.get("target_user_ids") else
             f"排除{_display_list(params['exclude_user_ids'])}" if params.get("exclude_user_ids") else
             _route_description(case) or "广播给跟随布局成员")
    if case.forbidden_receivers and "不应接收=" not in route:
        route += f"；不应接收={_display_list(case.forbidden_receivers)}"
    return _join_parts([
        f"{layout}分屏" if isinstance(layout, int) and layout < 1000 else f"布局={layout}",
        f"模式={params.get('mode', 3)}", f"全屏={_yes_no(params.get('full'))}" if "full" in params else None,
        f"全屏成员={_display_value(params.get('full_video_id'))}" if "full_video_id" in params else None,
        f"全屏媒体ID={params.get('full_video_media_id')}" if "full_video_media_id" in params else None,
        f"保持全屏={_yes_no(params.get('valid_full_mode'))}" if "valid_full_mode" in params else None,
        f"数据窗口={params.get('data_active')}" if "data_active" in params else None,
        f"成员顺序={_display_list(users)}" if users else "空成员窗口", route,
    ])


def _device_description(params: Mapping[str, Any]) -> str:
    parts = []
    operations = {1: "新增", 2: "修改", 3: "删除"}
    for kind, label in (("audio", "音频设备"), ("video", "视频设备")):
        for device in params.get(kind) or []:
            parts.append(
                f"{operations.get(device.get('Operation'), device.get('Operation'))}{label}"
                f"ID={device.get('ID')}，名称={device.get('Name')}")
    if "def_video_dev_id" in params:
        parts.append(f"默认视频设备ID={params['def_video_dev_id']}")
    return _join_parts(parts) or "空设备信息上报"


def _generic_parameter_parts(params: Mapping[str, Any]) -> list[str]:
    parts = []
    for key, value in params.items():
        if key == "room_id" and value == "${operator.room_id}":
            continue
        if key in {"group_id", "groupId"}:
            parts.append("使用本次录制动态groupId")
        elif isinstance(value, Mapping):
            parts.append(f"{_field_name(key)}=" + "/".join(
                f"{_field_name(child)}:{_display_value(item)}" for child, item in value.items()))
        else:
            parts.append(f"{PARAMETER_NAMES.get(key, _field_name(key))}={_display_value(value)}")
    return parts


def _route_description(case: Any) -> str:
    received = _display_list(case.expected_receivers)
    forbidden = _display_list(case.forbidden_receivers)
    return _join_parts([f"接收端={received}" if received else None,
                        f"不应接收={forbidden}" if forbidden else None])


def _display_list(values: Any) -> str:
    return "→".join(_display_value(value) for value in values)


def _display_value(value: Any) -> str:
    aliases = {
        "operator": "操作端A", "observer": "观察端B", "witness": "无关端C",
        "invitee": "被邀请端", "http_admin": "后台HTTP服务",
        "${operator.room_user_id}": "操作端A", "${observer.room_user_id}": "观察端B",
        "${witness.room_user_id}": "无关端C", "${env.record_group_id}": "动态groupId",
        "${env.invite_code}": "动态邀请码", "${unique.name}": "动态唯一名称",
        "${env.http_user}": "操作端平台账号", "${env.start_time}": "动态开始时间",
        "${env.end_time}": "动态结束时间", "${env.reserved_room_id}": "动态预约会议",
    }
    if isinstance(value, str):
        return aliases.get(value, value if len(value) <= 24 else value[:24] + "...")
    if isinstance(value, list):
        return _display_list(value)
    if isinstance(value, bool):
        return "是" if value else "否"
    if value is None:
        return "默认"
    return str(value)


def _text_feature(text: Any) -> str:
    value = str(text)
    if not value:
        return "空文本"
    visible = value.replace("\n", "\\n").replace("\t", "\\t")
    marker = re.search(r"-(\d+)$", value)
    marker_text = f"，内容后缀={marker.group(1)}" if marker else ""
    if "\n" in value:
        return f"多行文本({visible[:20]})"
    if "\t" in value:
        return f"含制表符文本({visible[:20]})"
    if any(char in value for char in "<&>"):
        return f"XML特殊字符文本({value[:12]})"
    if len(value) >= 60:
        return f"长度{len(value)}边界文本{marker_text}"
    return visible


def _field_name(value: str) -> str:
    names = {
        "Pos": "位置", "Size": "字号", "Color": "颜色",
        "enable_chat": "会中聊天", "enable_p2p_chat": "点对点聊天",
        "enable_pub_chat": "公开聊天", "enable_record": "录制功能",
        "enable_send_file": "文件发送", "OperatePublicChat": "公开聊天权限",
        "OperatePrivateChat": "私聊权限", "OperateLayout": "布局权限",
        "ConfigChatCheck": "聊天审批权限", "OperateRecord": "录制权限",
        "OperateMediaShare": "媒体共享权限", "OperateVideoPolling": "轮询权限",
        "OperateGroup": "分组权限", "layout": "布局参数",
    }
    return names.get(value, PARAMETER_NAMES.get(value, value))


def _enabled_value(value: Any) -> str:
    return {0: "关闭", 1: "开启", 2: "开启", False: "关闭", True: "开启"}.get(value, str(value))


def _yes_no(value: Any) -> str:
    return "是" if value in (1, True) else "否"


def _join_parts(parts: list[Any]) -> str:
    return "；".join(str(part) for part in parts if part not in (None, ""))


def _short_value(value: Any) -> str:
    if isinstance(value, str):
        variables = {
            "${operator.room_user_id}": "操作端A", "${observer.room_user_id}": "观察端B",
            "${witness.room_user_id}": "无关端C", "${operator.room_id}": "当前会议",
        }
        value = variables.get(value, value)
        return value if len(value) <= 18 else value[:18] + "..."
    if isinstance(value, list):
        return f"{len(value)}项"
    if isinstance(value, Mapping):
        return f"{len(value)}个字段"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def configure_case(case: Any) -> None:
    if allure is None:
        return
    allure.dynamic.parent_suite("会议服务器与终端协议闭环")
    allure.dynamic.suite(case.module)
    allure.dynamic.sub_suite(closure_name(case.closure_type))
    allure.dynamic.title(case_title(case))
    allure.dynamic.id(case.case_id)
    allure.dynamic.label("用例ID", case.case_id)
    allure.dynamic.label("业务操作", operation_name(case.operation))
    allure.dynamic.description(
        f"**用例ID：** `{case.case_id}`  \n"
        f"**公开方法：** `{case.operation}()`  \n"
        f"**操作端：** {alias_name(case.actor)}  \n"
        f"**目标端：** {alias_name(case.target)}  \n"
        f"**前置状态：** `{case.precondition}`  \n"
        f"**预期结果：** `{getattr(case.expected_outcome, 'value', case.expected_outcome)}`"
    )
    allure.dynamic.parameter(
        "fm_excel_case", case.case_id, excluded=True,
        mode=allure.parameter_mode.HIDDEN)


def configure_flow(flow: Any) -> None:
    if allure is None:
        return
    allure.dynamic.parent_suite("会议服务器与终端协议闭环")
    allure.dynamic.suite("关键依赖流程")
    allure.dynamic.sub_suite(flow.priority)
    allure.dynamic.title(f"{flow.name}｜连续协议状态机")
    allure.dynamic.id(flow.flow_id)
    allure.dynamic.label("流程ID", flow.flow_id)
    allure.dynamic.description(
        f"**流程ID：** `{flow.flow_id}`  \n"
        f"**步骤数量：** {len(flow.steps)}  \n"
        f"**资源要求：** {flow.cleanup_requirement or '按场景统一清理'}"
    )
    allure.dynamic.parameter(
        "fm_dependency_flow", flow.flow_id, excluded=True,
        mode=allure.parameter_mode.HIDDEN)


def report_step(title: str):
    return allure.step(title) if allure is not None else nullcontext()


def attach_json(value: Any, name: str) -> None:
    if allure is None:
        return
    allure.attach(
        json.dumps(to_report_data(value), ensure_ascii=False, indent=2),
        name, allure.attachment_type.JSON)


def to_report_data(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        converted = {str(key): to_report_data(item) for key, item in value.items()}
        if isinstance(converted.get("raw_json"), str):
            try:
                converted["raw_json"] = json.dumps(
                    redact(json.loads(converted["raw_json"])), ensure_ascii=False)
            except (ValueError, TypeError):
                pass
        return redact(converted)
    if isinstance(value, (list, tuple, set)):
        return [to_report_data(item) for item in value]
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8").lstrip("\x00")
            return _SECRET_XML.sub(r"\1***REDACTED***\2", text)
        except UnicodeDecodeError:
            return f"二进制数据（{len(value)}字节）"
    return value


def event_evidence(events: Mapping[str, Any] | None) -> list[dict]:
    evidence = []
    for key, event in (events or {}).items():
        alias, _, event_index = key.partition(":")
        data = to_report_data(event)
        if isinstance(data, Mapping):
            evidence.append({
                "接收端": alias_name(alias),
                "事件序号": event_index,
                "消息类型": data.get("kind"),
                "cmdid": data.get("cmdid"),
                "协议名称": data.get("name"),
                "字段": data.get("fields"),
                "原始XML": data.get("raw_xml"),
                "接收时间": data.get("received_at"),
            })
            if data.get("transport") == "websocket_json":
                evidence[-1].pop("原始XML", None)
                evidence[-1].update({"传输协议":"WebSocket JSON", "原始JSON":data.get("raw_json"),
                                     "实际ErrorCode":data.get("error_code"),
                                     "连接编号":data.get("connection_id")})
        else:
            evidence.append({"接收端": alias_name(alias), "事件": data})
    return evidence


def protocol_response_evidence(
    response: Any, events: Mapping[str, Any] | None, *, response_expected: bool,
    error_code: int | None = None,
) -> dict:
    received = [item for item in event_evidence(events)
                if item.get("消息类型") == "response"]
    if response is None and not received:
        explanation = (
            "协议定义了同步Response，但本次没有可展示的返回数据"
            if response_expected else
            "该业务命令为Notify型协议，不定义同步Response；执行结果以实际Notify为准"
        )
    else:
        explanation = "已展示公开方法返回值及客户端实际收到的Response事件"
    return {
        "是否定义同步Response": response_expected,
        "公开方法实际返回值": to_report_data(response),
        "实际业务错误码": f"0x{error_code:04X}" if error_code is not None else None,
        "客户端收到的Response事件": received,
        "说明": explanation,
    }


def protocol_notify_evidence(events: Mapping[str, Any] | None) -> dict:
    received = [item for item in event_evidence(events)
                if item.get("消息类型") != "response"]
    return {
        "实际Notify数量": len(received),
        "各端实际Notify": received,
        "说明": ("以下消息均来自本用例事件游标之后"
                 if received else "该用例没有定义或收到异步Notify"),
    }


def _outbound_records(outbound: Mapping[str, Any] | None) -> list[dict]:
    records = []
    for alias, values in (outbound or {}).items():
        for value in values or []:
            item = dict(value)
            item["alias"] = alias
            records.append(item)
    return records


def actual_call_evidence(
    outbound: Mapping[str, Any] | None, *, actor: str, operation: str,
    fallback_params: Mapping[str, Any],
) -> dict:
    calls = [item for item in _outbound_records(outbound)
             if item.get("trace_type") == "public_call"]
    formatted = [{
        "执行端": alias_name(item.get("alias")),
        "统一操作名": item.get("operation"),
        "实际Python方法": item.get("method_name"),
        "实际参数": item.get("params"),
        "调用时间": item.get("called_at"),
        "关联XML发送ID": item.get("sent_command_ids", []),
        "关联HTTP请求ID": item.get("http_request_ids", []),
    } for item in calls]
    main = [item for item in formatted
            if item["统一操作名"] == operation and item["执行端"] == alias_name(actor)]
    return {
        "操作端": alias_name(actor),
        "业务操作": operation_name(operation),
        "公开方法": operation,
        "主业务实际参数": (main[-1]["实际参数"] if main
                           else dict(fallback_params)),
        "本步骤全部公开方法调用": formatted,
        "实际SDK公开调用": [{"执行端":alias_name(item.get('alias')),
                            "方法":item.get('method_name'),"参数":item.get('params'),
                            "调用时间":item.get('called_at')}
                           for item in _outbound_records(outbound) if item.get('trace_type')=='sdk_call'],
        "说明": ("参数已完成Runtime变量替换和Driver默认参数合并；敏感字段已脱敏"
                 if main else "未捕获Driver调用轨迹，展示Runtime变量替换后的调用参数"),
    }


def protocol_outbound_evidence(
    outbound: Mapping[str, Any] | None, *, actor: str, operation: str,
    request_cmdid: int | None,
) -> dict:
    wire = []
    unique = {}
    for item in _outbound_records(outbound):
        trace_type = item.get("trace_type")
        if trace_type == "xml_command":
            key = (trace_type, item.get("alias"), item.get("event_id"))
        elif trace_type == "json_command":
            key = (trace_type, item.get("alias"), item.get("event_id"))
        elif trace_type == "http_request":
            key = (trace_type, item.get("trace_id"), item.get("method"),
                   item.get("url"), item.get("sent_at"))
        else:
            continue
        current = unique.get(key)
        if current is None or (not current.get("operation") and item.get("operation")):
            unique[key] = item
    for item in sorted(unique.values(), key=lambda value: value.get("sent_at", 0)):
        trace_type = item.get("trace_type")
        if trace_type == "json_command":
            wire.append({"发送端":alias_name(item.get("alias")), "传输协议":"WebSocket JSON",
                         "cmdid":item.get("cmdid"), "字段":item.get("fields"),
                         "原始JSON":to_report_data({"raw_json":item.get("raw_json")}).get("raw_json"),
                         "连接编号":item.get("connection_id"), "发送时间":item.get("sent_at")})
        if trace_type == "xml_command":
            wire.append({
                "发送端": alias_name(item.get("alias")),
                "业务操作": operation_name(item.get("operation")) if item.get("operation") else None,
                "是否主业务协议": (
                    item.get("alias") == actor and item.get("operation") == operation and
                    (request_cmdid is None or item.get("cmdid") == request_cmdid)),
                "传输协议": f"{str(item.get('source', '')).upper()} XML",
                "cmdid": item.get("cmdid"),
                "协议名称": item.get("name"),
                "协议版本": item.get("ver"),
                "seq": item.get("seq"),
                "字段": item.get("fields"),
                "原始XML": item.get("raw_xml"),
                "发送时间": item.get("sent_at"),
            })
        elif trace_type == "http_request":
            wire.append({
                "发送端": alias_name(item.get("alias")),
                "业务操作": operation_name(item.get("operation")) if item.get("operation") else None,
                "是否主业务协议": (
                    item.get("alias") == actor and item.get("operation") == operation),
                "传输协议": "HTTP",
                "HTTP方法": item.get("method"),
                "URL": item.get("url"),
                "Content-Type": item.get("content_type"),
                "请求Body": item.get("body"),
                "发送时间": item.get("sent_at"),
            })
    return {
        "实际发送协议数量": len(wire),
        "实际发送协议": wire,
        "说明": ("均为本步骤游标之后由SDK记录的真实上行请求；Authorization和敏感字段不进入报告"
                 if wire else "本步骤没有产生可观察的XML或HTTP上行请求"),
    }
