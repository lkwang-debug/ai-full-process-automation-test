# API 与业务协议封装说明

## 1. 范围和结论

本文说明 `sessionmanager_py` 已封装的 Web HTTP API、Entrance/Front
接入协议和 Conf 会中业务协议。基准文档为 `api文档` 和
`docs/ConfProtocol.xml`。

| 范围 | 当前状态 | 说明 |
|---|---|---|
| Web HTTP API | 已封装 | `api文档` 中的部门、用户、会议室、会控、录制纪要、拉流和订阅端点已有公开方法 |
| Entrance 协议 | 已封装 | 完成配置中心地址查询 |
| Front 协议 | 已封装 | 完成认证、房间列表、入房、退房和注销 |
| Conf 协议号和下行观察 | 已封装 | 本包 XML 的 92 个顶层 `cmdid` 均有常量或统一事件观察能力 |
| Conf 公开上行业务方法 | 大部分已封装 | 已补审批、交互语言、远程音视频确认和人脸信令；其余缺口以第 6 节为准 |

因此，不应笼统地表述为“所有业务协议全部封装完成”。
HTTP API 已按当前 `api文档` 完成；Conf 已覆盖常用业务和所有下行观察，
但仍有本文第 6 节列出的缺口。

2026-09-16 两版本新增接口、服务器前置条件与逐项命令见
[交互协议补充与验收说明](interactive_protocols_20260916.md)。

## 2. 代码分布

| 位置 | 职责 |
|---|---|
| `fm_client/http_api.py` | `WebApiClient`：HTTP 鉴权、JSON/文件请求和全部 REST 业务方法 |
| `fm_client/entrance.py` | Entrance cmdid、请求构造和响应配对 |
| `fm_client/front.py` | Front cmdid、请求构造和响应配对 |
| `fm_client/conf.py` | Conf cmdid、`PAIRS`、`PUSHES`、字段构造器和枚举常量 |
| `fm_client/ops/entrance_ops.py` | Entrance 公开业务方法 |
| `fm_client/ops/front_ops.py` | Front 认证、入房、列表和注销方法 |
| `fm_client/ops/conf_ops.py` | Conf 基础入会、成员、音视频和会议状态方法 |
| `fm_client/ops/meeting_ops.py` | 分组、权限、字幕、消息、轮询等扩展方法 |
| `fm_client/ops/meeting_control_ops.py` | 面向测试人员的高层会控方法和 HTTP 委托 |
| `fm_client/ops/contacts_ops.py` | 离线成员、组织架构和会议配置查询 |
| `fm_client/ops/call_ops.py` | 电话/H323 邀请、挂断和列表查询 |
| `fm_client/client.py` | `FmClient`、`FmListener`、`ReceivedEvent` 和协议分发 |
| `fm_client/testing.py` | `FmTestClient`、`EventCollector` 和事件等待/断言方法 |

## 3. Web HTTP API

入口：

```python
from fm_client.http_api import WebApiClient

api = WebApiClient.from_config("examples/http_api.json")
```

### 3.1 通用能力

| 能力 | 方法 |
|---|---|
| JWT 鉴权 | `gen_token()`、`WebApiClient.from_config()` |
| JSON 请求 | `request()`、`get()`、`post()`、`put()`、`delete()` |
| 文件下载 | `request_bytes()`、`download()` |
| 统一会控 | `control(room_id, operate, param)` |

### 3.2 部门和用户

| HTTP 路由 | 公开方法 |
|---|---|
| `POST /v1/department/add` | `add_department()` |
| `POST /v1/department/edit` | `edit_department()` |
| `POST /v1/department/list` | `list_departments()` |
| `POST /v1/department/delete` | `delete_department()` |
| `POST /v1/user/add` | `add_user()` |
| `POST /v1/user/addBatch` | `add_users_batch()` |
| `POST /v1/user/addBatch_1779263480333` | `add_users_batch_copy()` |
| `POST /v1/user/edit` | `edit_user()` |
| `POST /v1/user/del` | `delete_user()` |
| `POST /v1/user/list` | `list_users()` |
| `GET /v1/user/online/{userName}` | `user_online()` |
| `POST /v1/user/roomUserList` | `list_authorized_users()` |

### 3.3 会议室和会议查询

| HTTP 路由 | 公开方法 |
|---|---|
| `POST /v1/room/createInstant` | `create_instant_meeting()` |
| `POST /v1/room/addRoomInfo` | `add_room()` |
| `POST /v1/room/batchAddRoomInfo` | `batch_add_rooms()` |
| `POST /v1/room/editRoomInfo` | `edit_room_info()` |
| `POST /v1/room/delRoomInfo` | `delete_room()` |
| `POST /v1/room/cleanRoom` | `clean_room()` |
| `POST /v1/room/editRoomStatus` | `edit_room_status()` |
| `POST /v1/room/layout` | `update_room_layout()` |
| `POST /v1/room/permission` | `update_room_permission()` |
| `POST /v1/room/routine` | `make_room_routine()` |
| `POST /v1/room/reservation` | `reserve_room()`、`make_room_reservation()` |
| `POST /v1/room/fixed` | `make_room_fixed()` |
| `POST /v1/room/defaultSetting` | `set_room_default_setting()` |
| `POST /v1/room/authRoom` | `authorize_user_room()` |
| `POST /v1/room/authUser` | `authorize_room_users()` |
| `POST /v1/room/roomList` | `list_rooms_http()` |
| `GET /v1/room/roomList/{userName}` | `list_user_rooms()` |
| `GET /v1/room/loginAddr` | `get_room_login_addr()` |
| `POST /v1/room/meetingLog` | `query_meeting_log()` |
| `POST /v1/room/onlineRoom` | `list_online_rooms()` |
| `POST /v1/room/roomLog` | `query_room_log()` |
| `POST /v1/room/userNum` | `get_room_user_num()` |
| `POST /v1/room/userInRoom` | `check_user_in_room()` |
| `POST /v1/room/participant` | `list_room_participants()` |
| `POST /v1/room/sendMsg` | `send_room_message()` |
| `POST /v1/room/kickOut` | `kick_out_user()`、`kick_out_user_by_id()` |

组合方法：

- `create_reserved_meeting()`：`addRoomInfo` 后调用 `reservation`。
- `cancel_reserved_meeting()`：软取消调用 `editRoomStatus`，硬取消调用 `delRoomInfo`。

### 3.4 录制、会议纪要和导出

| HTTP 路由 | 公开方法 |
|---|---|
| `GET /v1/room/summary/{groupId}` | `download_meeting_summary()` |
| `GET /v1/room/summary` | `get_meeting_summary()` |
| `GET /v1/room/record-summaries/room` | `list_room_record_summaries()` |
| `GET /v1/room/record-summaries` | `list_record_summaries()` |
| `GET /v1/room/record-summaries/{groupId}` | `get_record_summary()` |
| `GET /v1/room/record-summaries/{groupId}/participants` | `list_record_participants()` |
| `GET /v1/room/record-summaries/{groupId}/record-tasks` | `list_record_tasks()` |
| `GET /v1/room/record-summaries/{groupId}/records` | `list_records()` |
| `GET /v1/room/record-summaries/{groupId}/share` | `get_record_share()` |
| `GET /v1/room/record-summaries/{groupId}/summary/export` | `export_meeting_summary()` |
| `GET /v1/room/record-summaries/{groupId}/transcript/export` | `export_transcript()` |
| `DELETE /v1/room/record-summaries` | `delete_record_summaries()` |
| `DELETE /v1/room/record-summaries/records` | `delete_record_tasks()` |
| `GET /v1/room/{roomId}/web-control` | `get_web_control_url()` |
| `POST /v1/room/editRoomRecordSettings` | `edit_record_settings()` |
| `GET /v1/room/queryRoomRecordSettings` | `query_record_settings()` |

### 3.5 会控、拉流和订阅

以下方法均调用 `POST /v1/control/{roomId}`：

| `operate` | 公开方法 |
|---|---|
| `INVITE_USER` | `invite_users()` |
| `INVITE_TERMINAL` | `invite_terminals_http()` |
| `LOCK_LAYOUT` | `lock_layout()` |
| `MEETING_LOCK` | `lock_meeting()` |
| `OP_LAYOUT` | `set_layout_http()` |
| `CLOSE` | `close_meeting()` |
| `MUTE_ALL` | `mute_all()` |
| `SEND_SUBTITLES` | `send_subtitles()` |
| `CLOSE_SUBTITLE` | `close_meeting_subtitle()` |
| `OP_RECORD` | `operate_record()` |
| `MEETING_RENAME` | `rename_meeting_http()` |
| `OP_ROLE_MANAGER` | `set_user_manager()` |
| `OP_ROLE_PRESENTER` | `set_user_presenter()` |
| `USER_REMOVE` | `remove_user()`；当前产品被请出端收 `6606`（`KickID=-2`），其他与会者收 `6605` |
| `USER_AUDIO_CONTROL` | `control_user_audio()` |
| `USER_VIDEO_CONTROL` | `control_user_video()` |
| `USER_RENAME` | `rename_user()` |
| `USER_INFO` | `query_user_info()` |
| `USER_LIST` | `list_meeting_users()` |
| `MEETING_INFO` | `query_meeting_info()` |
| `PULL_STREAM_ADD` | `add_pull_stream()` |
| `PULL_STREAM_DEL` | `delete_pull_stream()` |

| HTTP 路由 | 公开方法 |
|---|---|
| `POST /v1/control/{roomId}/subscriptions` | `add_subscription()` |
| `DELETE /v1/control/{roomId}/subscriptions` | `delete_subscription()` |
| `GET /v1/control/subscriptions` | `list_subscriptions()` |

### 3.6 FmClient 跨通道门面

`FmClient` / `FmTestClient` 可以先调用 `attach_http()` 关联
`WebApiClient`，再直接调用以下高层方法：

| 高层方法 | 实际通道 |
|---|---|
| `create_instant_meeting()` | HTTP `createInstant` |
| `create_reserved_meeting()` | HTTP `addRoomInfo + reservation` |
| `cancel_reserved_meeting()` | HTTP `editRoomStatus/delRoomInfo` |
| `invite_users()`、`invite_contacts()` | HTTP `INVITE_USER` |
| `lock_layout()` | HTTP `LOCK_LAYOUT` |

其余 HTTP 端点直接通过 `WebApiClient` 调用。

## 4. Entrance 和 Front 协议

### 4.1 Entrance（app=8）

| 协议 | 业务 | 公开方法 |
|---|---|---|
| `1000 -> 1100` | 查询配置中心地址 | `entrance_get_config()` |

### 4.2 Front（app=20）

| 协议 | 业务 | 公开方法 |
|---|---|---|
| `30000 -> 30001` | 用户认证 | `front_authenticate()`、`front_login()` |
| `30002 -> 30003` | 会议室列表/节点发现 | `front_roomlist()`、`front_roomlist_classified()`、`front_discover_node()` |
| `30004 -> 30005` | 加入房间 | `front_login()`、`front_login_by_invite_code()`、`front_login_guest()` |
| `30008 -> 30009` | 退出房间 | `front_logout()` |
| `30010 -> 30011` | Front 注销 | `front_logout()` |
| `30012 -> 30013` | 会议号换算 RoomID | `front_getroomid()`、`front_login_by_meeting_no()` |
| `30014 -> 30015` | 查询认证类型 | `front_get_auth_type()` |

## 5. Conf 会中业务协议（app=11）

状态说明：

- `完整`：已有公开上行业务方法，并已处理对应响应或通知。
- `部分`：常用字段已封装，但不是 XML 中每个字段都有显式参数。
- `仅下行`：该业务由服务器或 Web 侧主动触发，客户端只需观察。
- `缺少上行`：XML 定义了客户端请求，但尚无公开业务方法。

| 协议 | 业务 | 公开方法 / 下行回调 | 状态 |
|---|---|---|---|
| `6501/6601/6517` | 登录会场、初始化完成 | `conf_login()` | 完整 |
| `6602` | 用户进入 | `on_user_enter()` | 仅下行 |
| `6503/6603` | 离线成员/通讯录快照 | `conf_get_offline_users(refresh=False)`、`conf_get_contacts()`；主线每次 Conf 登录只请求一次，后续读缓存 | 完整 |
| `6504/6604/6617` | 敲门/等待开门 | `allow_knock()`、`on_knock()`、`on_wait_open_door()` | 完整 |
| `6505/6605` | 退出会场 | `conf_logout()`、`on_user_leave()` | 完整 |
| `6506/6606` | 踢出用户；当前 HTTP `USER_REMOVE` 也统一使用 `6606` | `kick_user()`、`remove_user()`、`on_user_kicked()` | 完整 |
| `6507/6607` | 关闭会议室 | `close_room()`、`on_room_closed()` | 完整 |
| `6508/6608` | 音视频设备信息 | `report_av_info()`、`on_av_info()` | 完整 |
| `6509/6609` | 用户音视频、角色、辅流及扩展状态 | 常用接口以及`set_user_state()`覆盖`PrivateTalk`、`AudioOutMute`、`Info`、`WBMarkState`；`on_user_state_changed()`接收 | 完整 |
| `6510/6610` | 会议状态、锁房、录制 | `set_conf_state()`、`lock_room()`、`unlock_room()`、`start_record()`、`pause_record()`、`stop_record()`、`on_conf_state_changed()` | 完整 |
| `6511/6611` | 申请/释放主持人 | `apply_host()`、`release_host()`、`on_chair_right_ret()` | 完整 |
| `6512/6612` | 公聊/私聊及字体 | `chat(font=...)`按XML属性发送、`on_chat()`接收 | 完整；群聊/私聊字体真机通过 |
| `6513/6613` | 窗口状态、会议布局及全屏成员 | `set_window_state()`、`set_layout()`支持`FullVideoID/FullVideoMediaID/ValidFullMode`、`on_window_state()`接收 | 完整；全屏字段真机通过 |
| `6514/6614` | 全场静音 | `mute_all()`、`on_silent()` | 完整 |
| `6515/6615` | 会场字幕 | `set_subtitle()`、`on_subtitle()` | 完整 |
| `6516/6616` | 文件发送通知 | `send_file()`、`on_send_file()` | 完整 |
| `6518/6618` | 系统消息 | `send_sysmsg()`、`on_system_message()` | 完整 |
| `6519/6619` | 会场自定义数据 | `set_room_data()`、`on_room_data()` | 完整 |
| `6520/6620` | 查询 AV 合成参数 | `get_av_mix_params()` | 完整 |
| `6521` | 设置 AV 合成参数 | `set_av_mix_params()` | 完整 |
| `6522/6622` | 视频字幕参数 | `set_video_subtitle_params()`、`on_video_subtitle()` | 完整 |
| `6523/6623` | 创建分组会议 | `create_group()` | 完整 |
| `6524/6624` | 进入分组会议 | `enter_group()`、`on_group_enter()` | 完整 |
| `6525/6625` | 结束分组会议 | `end_groups()` | 完整 |
| `6526/6626` | 返回主会议 | `bring_to_main()`、`on_main_enter()` | 完整 |
| `6527/6627` | 修改会议名称 | `rename_meeting()` | 完整 |
| `6528/6628` | 查询组织架构 | `conf_get_dept_info()` | 客户端完整；当前服务端 handler 已注释 |
| `6529/6629` | 聊天审批 | `review_chat_message()`、`on_chat_check_request()`、`on_chat_check_result()` | 已封装；拒绝分支服务端静默 |
| `6540/6640/6641` | 交互语言 | `set_interactive_language()`、`on_interactive_language()`、`on_room_languages()` | 已封装；部署能力待验收 |
| `6542/6642/6543/6643` | 远程音视频确认 | `request_remote_av()`、`respond_remote_av()`、`reply_remote_av()` | 已封装；不自动同意 |
| `6530/6630` | 申请/释放操作权 | `request_right()`、`release_right()` | 客户端完整；服务端要求 Conf 主版本 >= 2 |
| `6631` | 角色权限初始配置 | `on_role_permission()` | 仅下行 |
| `6532/6632` | 修改角色权限 | `set_role_permissions()`、`on_role_permission_changed()` | 完整 |
| `6533/6633` | 视频轮询 | `configure_polling()`、`on_video_polling()` | 完整 |
| `6534/6634` | 查询会议配置 | `conf_get_room_config()` | 完整；默认查询 `InviteCode` |
| `6535/6635` | 修改用户权限 | `set_user_permissions()`、`on_user_permission_changed()` | 完整 |
| `6636` | 操作权占用状态/历史系统通知 | `on_system_notify()`；兼容旧嵌套录制错误；当前协议仅主服务器到节点，终端测试不执行 | 仅下行 |
| `6637` | 录制异常 | `on_server_record_error(error, fields)` | 接收已修复；真机异常触发待确认 |
| `6647` | 等候用户列表 | `on_waiting_user_list(fields)` | 接收已补齐；真机未下发 |
| `6550/6650` | 关闭会场字幕 | `close_subtitle()`、`on_subtitle_closed()` | 完整 |
| `6555/6655` | 人脸服务路由 | `request_face_recognition()` | 信令完整；不执行图像识别 |
| `6556/6656/6657` | 开始人脸点名 | `start_face_rollcall()`、`on_face_rollcall_started()` | 信令完整 |
| `6558/6658` | 上报人脸点名结果 | `report_face_rollcall_result()`、`on_face_rollcall_result()` | 信令完整；需真实 FaceID |
| `6559/6659` | 取消人脸点名 | `cancel_face_rollcall()`、`on_face_rollcall_cancelled()` | 信令完整 |
| `6568/6668/6669` | 撤回聊天消息 | `recall_msg()`、`on_chat_recalled()` | 完整 |
| `6651` | 组播状态；当前代码未实现 | `on_multicast_state()`（兼容解析）；不执行、不计阻塞 | 仅下行 |
| `6652` | 历史/保留的 Web 踢出通知；当前请出使用目标端6606和其他成员6605 | `on_web_kicked()`（兼容解析）；不执行、不计阻塞 | 仅下行 |
| `6653` | Web 关闭会议室 | `on_web_room_closed()` | 仅下行 |
| `6654` | Web 通知 | `WebApiClient.send_room_message()`、`on_web_notify()` | 固定会议实测通过；动态会议 HTTP 1021 |
| `6700` | 服务器透明中转消息 | `send_relay_message()`、`on_relay_message()` | 完整；内层业务由端处理 |
| `6741/6742/6745` | 邀请电话/H323 终端 | `conf_invite_call()`、`invite_terminals()`、`on_call_state_notify()` | 完整 |
| `6743/6744` | 挂断终端呼叫 | `conf_hangup()` | 完整 |
| `6746/6747` | 查询终端呼叫列表 | `conf_query_call_list()` | 完整 |

## 6. 尚未完整封装的 Conf 能力

| 协议 | 尚缺少的公共能力 |
|---|---|

## 7. 统一下行事件

所有服务器下行均会转换为 `ReceivedEvent`，常用字段包括：

```text
event_id, source, cmdid, name, kind, seq, request_cmdid,
fields, raw_xml, raw_payload, received_at, received_monotonic,
decode_error
```

测试代码使用 `EventCollector` 观察事件：

```python
mark = observer.events.mark()
operator.set_user_audio(observer.room_user_id_int, 2)

event = observer.events.wait_event(
    after=mark,
    source="conf",
    cmdid=6609,
    kind="notify",
    match={"UserID": observer.room_user_id_int, "Audio.State": 2},
    timeout=5,
)
```

`wait_event()` 可观察没有专用 typed 回调的已知或未知 cmdid，因此
“没有专用 `on_xxx()`”不等于下行协议不可测。

## 8. 验证位置

| 位置 | 覆盖内容 |
|---|---|
| `tests/test_http_api.py` | HTTP 鉴权、路由、请求体和文件响应 |
| `tests/test_fm_client.py` | Entrance/Front/Conf 基础业务 |
| `tests/test_meeting_control.py` | 布局、角色、权限、辅流和轮询 |
| `tests/test_received_events.py` | `PAIRS`、`PUSHES` 和统一事件 |
| `tests/test_multi_client.py` | A 操作端和 B 观察端广播闭环 |
| `tests/test_single_case_cli.py` | 38 个内置单协议用例和通用调用模式 |
| `examples/fm_http_lifecycle.py` | HTTP 建会到信令退会的真机闭环 |
| `examples/fm_meeting_control_demo.py` | 会控公共方法的 Mock/真机验证 |
| `examples/fm_single_case.py` | 通过 `--case` 或 `--method` 逐项验证 Conf/HTTP |

本文的“已封装”表示客户端已有公开调用或观察入口；目标
fmserver 是否支持该命令，仍需在对应版本的真实环境中验证。
