# ConfProtocol 业务协议封装清单

基准：`docs/ConfProtocol.xml`，0922 补入 6637/6647 后共 92 个唯一顶层 `cmdid`。本文只统计 Conf
信令（app=11），不包含 Front、Entrance 和 HTTP API。
2026-09-16 新 XML 补充的审批、交互语言、远程音视频确认与人脸信令见
[新增接口与验收说明](interactive_protocols_20260916.md)，不包含在上述旧基准计数中。

## 1. 结论

当前尚未完成全部业务封装：

- 92 个协议号均已定义常量，并可通过统一事件 `events.wait_event(cmdid=...)` 观察下行。
- 常用会议业务已提供 `FmClient` / `FmTestClient` 公共方法。
- `6529/6540/6542/6543/6555/6556/6558/6559` 已提供公共上行业务方法；部署可用性需独立验收。
- `6700` 已提供 `send_relay_message()`；0914 真机定向往返及会话健康验证通过。
- `6509/6512/6513` 已覆盖常用业务，但部分协议字段仍只能使用底层构造器。

状态说明：`完整`表示已有公共业务方法；`部分`表示只覆盖常用字段；`仅下行`
表示只有回调和统一事件；`缺失`表示没有公共上行业务方法。

## 2. 已封装协议

| 协议 | 业务 | 公共方法 / 下行回调 | 状态 |
|---|---|---|---|
| 6501 / 6601 / 6517 | 登录会场、初始化完成 | `conf_login()` | 完整 |
| 6602 | 用户进入 | `on_user_enter()` | 仅下行 |
| 6503 / 6603 | 查询离线成员 | `conf_get_offline_users(refresh=False)`；主线每次 Conf 登录首次请求、后续读缓存 | 完整 |
| 6504 / 6604 / 6617 | 敲门处理 | `allow_knock()`；`on_knock()`；`on_wait_open_door()` | 完整 |
| 6505 / 6605 | 退出会场 | `conf_logout()`；`on_user_leave()` | 完整 |
| 6506 / 6606 | 踢出用户；当前 HTTP `USER_REMOVE` 也统一使用 6606 | `kick_user()`；`remove_user()`；`on_user_kicked()` | 完整 |
| 6507 / 6607 | 关闭会议室 | `close_room()`；`on_room_closed()` | 完整 |
| 6508 / 6608 | 音视频设备信息 | `report_av_info()`；`on_av_info()` | 完整 |
| 6509 / 6609 | 用户音视频、角色、辅流及扩展状态 | 常用接口以及`set_user_state()`覆盖`PrivateTalk`、`AudioOutMute`、`Info`、`WBMarkState`；`on_user_state_changed()`接收 | 完整 |
| 6510 / 6610 | 会议状态、锁房、录制 | `set_conf_state()`；`lock_room()`；`unlock_room()`；`start_record()`；`pause_record()`；`stop_record()`；`on_conf_state_changed()` | 完整 |
| 6511 / 6611 | 申请、释放主持人 | `apply_host()`；`release_host()`；`on_chair_right_ret()` | 完整 |
| 6512 / 6612 | 公聊、私聊及字体 | `chat(font=...)`按XML属性发送；`on_chat()`接收 | 完整；群聊/私聊字体真机通过 |
| 6513 / 6613 | 窗口状态、会议布局及全屏成员 | `set_window_state()`；`set_layout()`支持全屏字段；`on_window_state()`接收 | 完整；媒体ID 0/1及退出全屏真机通过 |
| 6514 / 6614 | 全场静音 | `mute_all()`；`on_silent()` | 完整 |
| 6515 / 6615 | 设置会场字幕 | `set_subtitle()`；`on_subtitle()` | 完整 |
| 6516 / 6616 | 文件发送通知 | `send_file()`；`on_send_file()` | 完整 |
| 6518 / 6618 | 系统消息 | `send_sysmsg()`；`on_system_message()` | 完整 |
| 6519 / 6619 | 会场自定义数据 | `set_room_data()`；`on_room_data()` | 完整 |
| 6520 / 6620 | 查询 AV 合成参数 | `get_av_mix_params()` | 完整 |
| 6521 | 设置 AV 合成参数 | `set_av_mix_params()` | 完整 |
| 6522 / 6622 | 视频字幕参数 | `set_video_subtitle_params()`；`on_video_subtitle()` | 完整 |
| 6523 / 6623 | 创建分组会议 | `create_group()` | 完整 |
| 6524 / 6624 | 进入分组会议 | `enter_group()`；`on_group_enter()` | 完整 |
| 6525 / 6625 | 结束分组会议 | `end_groups()` | 完整 |
| 6526 / 6626 | 返回主会议 | `bring_to_main()`；`on_main_enter()` | 完整 |
| 6527 / 6627 | 修改会议名称 | `rename_meeting()` | 完整 |
| 6528 / 6628 | 查询组织架构 | `conf_get_dept_info()` | 完整 |
| 6529 / 6629 | 聊天审批 | `set_chat_check()`；`review_chat_message()`；`on_chat_check_request()`；`on_chat_check_result()` | 已封装；服务端拒绝分支静默 |
| 6540 / 6640 / 6641 | 交互语言、会场语言配置 | `set_interactive_language()`；`on_interactive_language()`；`on_room_languages()` | 已封装；服务端能力待验收 |
| 6542 / 6642 / 6543 / 6643 | 远程音视频确认 | `request_remote_av()`；`respond_remote_av()`；`reply_remote_av()`；`on_remote_av_request/result()` | 已封装；不自动同意或采集媒体 |
| 6530 / 6630 | 申请、释放操作权 | `request_right()`；`release_right()` | 完整 |
| 6631 | 角色权限配置 | `on_role_permission()` | 仅下行 |
| 6532 / 6632 | 修改角色权限 | `set_role_permissions()`；`on_role_permission_changed()` | 完整 |
| 6533 / 6633 | 视频轮询 | `configure_polling()`；`on_video_polling()` | 完整 |
| 6534 / 6634 | 查询会议配置 | `conf_get_room_config()` | 完整 |
| 6535 / 6635 | 修改用户权限 | `set_user_permissions()`；`on_user_permission_changed()` | 完整 |
| 6636 | 操作权占用状态；兼容旧版系统通知；当前协议仅主服务器到节点 | `on_system_notify()`；终端测试不执行 | 仅下行 |
| 6637 | 录制异常 | `on_server_record_error()`；顶层 Error 优先，兼容 ServerRecord.Error | 接收已修复；真机异常触发待确认 |
| 6647 | 等候用户列表 | `on_waiting_user_list()`；`events.waiting_user_lists` 保留分页和 End | 接收已补齐；当前真机未下发 |
| 6550 / 6650 | 关闭会场字幕 | `close_subtitle()`；`on_subtitle_closed()` | 完整 |
| 6555 / 6655 | 视频人脸服务路由 | `request_face_recognition()` | 信令完整；需人脸服务 |
| 6556 / 6656 / 6657 | 开始人脸点名 | `start_face_rollcall()`；`on_face_rollcall_started()` | 信令完整 |
| 6558 / 6658 | 点名结果 | `report_face_rollcall_result()`；`on_face_rollcall_result()` | 信令完整；需真实 FaceID |
| 6559 / 6659 | 取消人脸点名 | `cancel_face_rollcall()`；`on_face_rollcall_cancelled()` | 信令完整 |
| 6651 | 组播状态；当前代码未实现 | `on_multicast_state()`（兼容解析）；不执行、不计阻塞 | 仅下行 |
| 6652 | 历史/保留的 Web 踢出通知；当前请出使用6606/6605 | `on_web_kicked()`（兼容解析）；不执行、不计阻塞 | 仅下行 |
| 6653 | Web 关闭会议室 | `on_web_room_closed()` | 仅下行 |
| 6654 | Web 通知 | `WebApiClient.send_room_message()` → `on_web_notify()` | 7.154 固定会议实测通过；动态会议 HTTP 1021 |
| 6741 / 6742 / 6745 | 邀请电话或 H323 终端、呼叫状态 | `conf_invite_call()`；`invite_terminals()`；`on_call_state_notify()` | 完整 |
| 6743 / 6744 | 挂断终端呼叫 | `conf_hangup()` | 完整 |
| 6746 / 6747 | 查询终端呼叫列表 | `conf_query_call_list()` | 完整 |
| 6700 | 透明中转 | `send_relay_message()`；`on_relay_message()` | 完整；内层业务由端处理 |

## 3. 未完整封装协议

| 协议 | 业务 | 当前能力 | 缺少的公共方法 |
|---|---|---|---|
| 6509 / 6609 | 用户状态 | 常用音视频、角色、辅流及扩展字段已通过`set_user_state()`封装和验证 | 无当前缺口 |
| 6512 / 6612 | 聊天 | 文本、目标用户、颜色及`Font`属性已封装并验证 | 无当前缺口 |
| 6513 / 6613 | 窗口状态 | 常用布局、视频位、定向用户及全屏字段已封装并验证 | 无当前缺口 |
