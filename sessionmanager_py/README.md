# sessionmanager_py

0922 更新：[6637 / 6647 / 6654 修复、真机结果及逐项命令](docs/notification_protocols_20260922.md)。
本包已合入 0914 后续的交互协议和确认方法；保留 0922 的 6639 通知与分组配置查询。

framecore 协议栈的 Python 实现，分两层：

- **会话层** `framecore_session`：对应 C++ 库 `framecore/sessionmanager`，TCP 可靠会话
  （`SESSIONTYPE_RELIABLE`）+ `XmlMsgIOPack` 信封，负责握手 / 序号 / 累计 ACK / 心跳 / 断线重连。
- **业务层** `fm_client`：在会话层之上封装 **4u_fmserver**（视频会议）业务协议——
  登录 → 入会 → 会中控场，用 XML 命令（`<cmd id="...">`）按 `application_id` 路由。

两层相互独立：`fm_client` 通过 import 复用 `framecore_session`，但代码、目录、测试都分开。

> **测试人员请看** [`docs/业务层设计文档.md`](docs/业务层设计文档.md) —— 设计思路、
> 方案选型、调用方式、API 速查和测试写法（不含会话层内部实现）。端到端框架规划见
> [`docs/端到端AI自动化测试方案.md`](docs/端到端AI自动化测试方案.md)。

扩展能力的公开 API、协议投递方向及 7.154 真机逐项复测命令见
[`docs/extended_protocols.md`](docs/extended_protocols.md)。覆盖多人共享、能力查询、
目标端状态确认、H5 投票、分组、等候室、语音字幕和自定义数据。

2026-09-16 补充：[聊天审批、交互语言、远程音视频确认及人脸点名](docs/interactive_protocols_20260916.md)。
两版本均已提供公开发送方法、通知回调和独立测试脚本；文档区分 SDK 本地验证与服务器验收结果。
2026-09-22 补充：[审批拒绝和交互语言的确认方法与复测](docs/interaction_confirmation_20260922.md)。

0914 新增：[`docs/invitation_and_relay_0914.md`](docs/invitation_and_relay_0914.md)
说明独立 PaaS 在线连接、接收后台邀请后自动入会的最小 demo，以及
`send_relay_message()` 的公开接口和真机复测命令。
`front_authenticate()` 本身不会登录 PaaS，不能单独用于接收后台邀请。

一键获取 PaaS 配置并执行真实邀请入会测试：

```bash
python3 examples/fm_invited_join_auto.py --invite-method both
```

默认读取 `examples/multi_client.json`、`http_api.json` 和 `paas_bootstrap.json`，
为第二个账号生成 `paas_online.json` 后测试。仓库仅提供脱敏的*.example.json模板；真实配置须在本地另行准备。
新环境按 [一键配置说明](docs/invitation_and_relay_0914.md#一键生成配置并测试推荐)
准备 `paas_bootstrap.json`。加 `--generate-only` 可仅生成并验证在线登录。

---

## 一、会话层 `framecore_session`

| 模块 | 职责 |
|------|------|
| `packets` | 所有会话报文 `pack`/`unpack`，5 字节包头（大端、4 位 type/version 位域） |
| `framer` | TCP 长度前缀分帧，处理粘包 / 半包 |
| `security` | 负载加密：NONE / XOR / AES-ECB / SM4（固定密钥） |
| `session` | TCP 可靠会话状态机：握手、序号、累计 ACK、心跳、断线重连续传 |
| `message` | 应用层信封 `XmlMsgIOPack`（对齐 C++ `CXmlMsgIOPack`）：1 字节压缩标志 + (zlib/原始) 业务报文 |

### 安装

核心库仅依赖 Python 标准库。可选加密：

```bash
pip install pycryptodome   # 启用 AES-ECB
pip install gmssl          # 启用 SM4
```

NONE 与 XOR 始终可用。

### 快速使用

```python
from framecore_session import TcpReliableSession, SessionConfig, SessionListener

class MyListener(SessionListener):
    def on_created(self): print("created")
    def on_data(self, data): print("recv", data)
    def on_closed(self): print("closed")

session = TcpReliableSession(MyListener(), SessionConfig(application_id=0x1234))
if session.connect("127.0.0.1", 7000):
    session.send(b"hello")
    session.close()
```

也可直接传入与 C++ `CreateSession(szServerAddrLink, ...)` 一致的地址链。初连时按顺序
尝试 TCP 候选，只有完成 `SessionAck` 握手才算成功；TCP 失败、握手超时或服务端拒绝
都会继续尝试下一个候选。重连时轮换候选：

```python
session.connect("TCP:10.0.0.1:7000;TCP:10.0.0.2:7000")
```

支持 IPv4、域名和 `V6TCP:[::1]:7000`。可靠会话忽略地址链中的 UDP 项；
没有可用 TCP 项时抛出 `ValueError`。

### 机制要点

- **握手**：客户端发 `SESSIONREQ`（dst=0），服务端回 `SESSIONACK`（选定加密方式 + 本端会话 ID）。
- **数据**：可靠包 = 包头 + `seqnum`(uint16) + 负载（按协商加密）；每个包缓存于未确认队列直到收到累计 ACK。
- **累计 ACK**：收够 `>=3` 个包或距上次 ACK `>=2000ms` 时发 `DATAACK`。
- **心跳**：每 5s 发 `HEARTBEAT`，对端回 `HEARTBEATACK`；超时切快心跳，再超时则断链重连。
- **断线重连续传**：重连发 `SESSIONREREQ`（带最后收到的序号），双方据此释放已确认包并重发未确认包。
- **关闭**：`close()` 发 `SESSIONBYE` 并关闭连接。

> 会话层序号 / ACK 位于 TCP 之上，服务于应用层背压、断线续传与统一 UDP/KCP 代码路径，不做网络层重传。

### `SessionConfig` 主要字段

| 字段 | 默认 | 说明 |
|------|------|------|
| `application_id` | 0 | 目标应用 ID（握手要约、业务路由） |
| `heartbeat_interval_ms` | 5000 | 慢速心跳间隔 |
| `connect_timeout_ms` | 15000 | 初始连接总超时，覆盖整条地址链 |
| `tcp_connect_timeout_ms` | 5000 | 单个 TCP 建连超时 |
| `handshake_timeout_ms` | 5000 | 单个节点等待 `SessionAck` 的超时 |
| `enable_reconnect` | True | 是否启用断线重连 |
| `reconnect_timeout_ms` | 60000 | 重连总超时 |
| `offered_security_types` | (0,1,2,3) | 握手要约的加密方式 |

---

## 二、业务层 `fm_client`（4u 会议）

在 `framecore_session` 之上封装 4u_fmserver 会议协议。

### 协议要点

- **传输**：framecore 可靠会话，握手协商 NONE/XOR/AES-ECB/SM4，并用 `application_id`
  路由到不同服务。这些是旧协议兼容机制，不等价于 TLS；跨不可信网络时应使用 TLS/VPN。
- **信封**：`XmlMsgIOPack`（复用会话层）。
- **业务体**：XML `<cmd id="..." ver="..." seq="..."><字段 val="..."/></cmd>`（UTF-8，字段在子元素 `val=` 属性）。
- **三域 application_id**：Entrance=`8`、Front=`20`、Conf=`11`（单机部署共用同一端口 1089，靠 app_id 路由）。
- **请求/回复配对**：Front 域 `REP = REQ + 1`；Conf/Entrance 域 `REP = REQ + 100`。
- ⚠️ 与 `cp_server_protocol.md`（cp-server 裸 JSON）是**不同产品线**，命令不通用。

### 支持的命令

**Entrance（app=8，+100）**

| 方法 | 命令 | 用途 |
|---|---|---|
| `entrance_get_config()` | 1000→1100 | 下发配置中心地址（可选引导） |

**Front（app=20，+1）**

| 方法 | 命令 | 用途 |
|---|---|---|
| `front_get_auth_type()` | 30014→30015 | 查认证类型 |
| `front_authenticate(u,pwd)` | 30000→30001 | 用户鉴权（仅登录，不进房） |
| `front_roomlist()` | 30002→30003 | 会议室列表（含 RoomID/RoomNodeID） |
| `front_discover_node()` | 30002 | **取本地 RoomNodeID**（服务端 DB 异常 0x1002 也能取到） |
| `front_getroomid(会议号)` | 30012→30013 | 会议号换算真实 RoomID |
| `front_login(...)` | 30004→30005 | 进房，拿 Token/Conf 地址/RoomUserID |
| `front_login_by_meeting_no(u,pwd,会议号)` | 30012→30013 + 30004→30005 | 会议号入会（换算 RoomID 后进房） |
| `front_login_by_invite_code(u,pwd,邀请码)` | 30004→30005 | 邀请码入会（`real_room_id` 为真实房） |
| `front_login_guest(u,pwd,room_id)` | 30000→30001 + 30004→30005 | 访客匿名入会（UserType=0，需 VerifyMode=3 房） |
| `front_roomlist_classified()` | 30002→30003 | 列表 + 给每个房标 `MeetingType`（固定/预约/即时） |
| `front_logout()` | 30008→30009 + 30010→30011 | 退房 + 注销 |

**Conf（app=11，+100）**

| 方法 | 命令 | 用途 |
|---|---|---|
| `conf_login()` | 6501→6601 | 登录会议室（自动发 INITCOMPLETE 6517） |
| `chat(text, dst_user_id=0, color=None, font=None)` | 6512 | 文字消息；`font`按协议生成`Font` XML属性 |
| `mic_on/off/request()` `camera_on/off()` `set_audio/video_state()` | 6509 | 开麦 / 关麦 / 申请发言 / 开关摄像头 |
| `start_record()` / `pause_record()` / `stop_record()` | 6510 | 开启 / 暂停 / 结束录制（ServerRecordStatus 2/1/0） |
| `lock_room()` / `unlock_room()` `set_conf_state(...)` | 6510 | 锁房 / 会议状态（EnableChat/EnableRecord…） |
| `kick_user(uid)` | 6506 | 踢人 |
| `close_room(reason,desc)` | 6507 | 关闭会议室（主持） |
| `report_av_info(audio,video)` | 6508 | 本地音视频设备上报 |
| `apply_host(chair_pwd=...)` / `release_host()` | 6511 | 申请 / 放弃主持人；申请密码必填 |
| `mute_all()` | 6514 | 全场静音（主持） |
| `conf_get_offline_users(refresh=False)` | 6503→6603 | 查本会议离线/历史成员；主线每次 Conf 登录首次查询，后续返回缓存；`refresh=True` 仅探测重复查询能力 |
| `conf_get_dept_info()` | 6528→6628 | 企业组织架构（⚠️ 服务端 handler 可能被注释，best-effort） |
| `conf_get_room_config()` | 6534→6634 | 会议配置（含 9 位 `InviteCode`） |
| `conf_get_contacts()` | 6601 缓存 + 6503 | 通讯录快照：组织架构 + 本地用户 + 离线成员 |
| `conf_invite_call(callees)` | 6741→6742 | 邀请电话/H323 联系人入会（终端呼叫子块，**+1** 配对） |
| `conf_hangup(callee_nums)` | 6743→6744 | 挂断已邀请呼叫 |
| `conf_query_call_list()` | 6746→6747 | 查已邀请拨号列表 |
| `create_group(rooms)` / `enter_group` / `end_groups` / `bring_to_main` | 6523/6524/6525/6526 | 分组会议；进入/返回结果在目标端收 6624/6626 |
| `request_right(type)` / `release_right(type)` | 6530→6630 | 申请/释放操作权（白板/共享/录制等，`RIGHT_TYPE_*`） |
| `rename_meeting(name)` | 6527→6627 | 改会议室名 |
| `set_subtitle(data,duration)` / `close_subtitle()` | 6515 / 6550→6650 | 会场字幕 设/关 |
| `send_sysmsg(sys_msg_id=...,dst_user_id=...)` | 6518→6618 | 发送服务器预配置消息；目标端收 6618 |
| `send_file(dst_uids,file_info)` | 6516 | 发文件通知（广播 6616） |
| `recall_msg(msg_id, timeout=None)` | 6568→6668 | 按`MsgID`撤回聊天消息（广播6669，字段为`MsgID/SrcUserID`） |
| `set_window_state(..., full_video_id=None, full_video_media_id=None, valid_full_mode=None)` | 6513 | 上报视图及全屏状态（广播6613） |
| `set_user_audio/video(uid,state)` / `set_layout(..., full_video_id=None, full_video_media_id=None, valid_full_mode=None)` / `set_role(...)` | 6509/6513/6511 | 控制指定用户音视频、布局、全屏成员和角色 |
| `set_role_permissions(...)` / `set_user_permissions(...)` | 6532/6535 | 修改角色或指定用户的功能权限，广播 6632/6635 |
| `set_room_data(items)` | 6519 | 设置会场/课堂自定义数据，广播 6619 |
| `get_av_mix_params()` / `set_av_mix_params(...)` | 6520/6521 | 查询/设置 AV 合成编码参数 |
| `set_video_subtitle_params(...)` | 6522 | 设置视频画面日期/用户名字幕参数，广播 6622 |
| `start_aux_stream(...)` / `stop_aux_stream()` / `configure_polling(...)` | 6509/6533 | 媒体辅流需 audio/video share ID；VNC 开启状态为 2 |
| `invite_terminals(sns_list)` | 6741→6742 | 邀请 SNS/H323 终端；呼叫状态由 6745 推送 |
| `allow_knock(uid,allow)` | 6504 | 主持人答复敲门 |
| `conf_logout()` | 6505 | 离开会 |

**推送（S→C → `FmListener`）**

| 回调 | cmdid |
|---|---|
| `on_user_enter(users)` | 6602 |
| `on_user_leave(uid)` | 6605 |
| `on_user_kicked(kick_id,uid,...)` | 6606 | 当前产品统一的被踢通知；HTTP `USER_REMOVE` 时 `KickID=-2` |
| `on_room_closed(reason,desc,...)` | 6607 |
| `on_av_info(fields)` | 6608 |
| `on_user_state_changed(fields)` | 6609（含开麦/摄像头/Right 变更） |
| `on_conf_state_changed(fields)` | 6610（含录制状态 ServerRecordStatus） |
| `on_chair_right_ret(fields)` | 6611（申请主持失败） |
| `on_chat(src,dst,text,msg_id)` | 6612 |
| `on_silent(fields)` | 6614 |
| `on_call_state_notify(fields)` | 6745 | 邀请呼叫状态（CallState/ReleaseCause/CalleeNum） |
| `on_knock(uid,name,fields)` / `on_wait_open_door()` | 6604 / 6617 | 敲门通知 / 等待开门 |
| `on_subtitle(fields)` | 6615 | 会场字幕 |
| `on_send_file(src,file_info,fields)` | 6616 | 他人发文件通知 |
| `on_window_state(fields)` | 6613 | 视图/布局转发 |
| `on_role_permission(roles,fields)` | 6631 | 角色权限配置 |
| `on_system_message(fields)` | 6618 | 系统文字/窗口/WEB/主题消息 |
| `on_group_enter(fields)` / `on_main_enter(fields)` | 6624/6626 | 观察端进入分组/返回主会通知 |
| `on_subtitle_closed(fields)` | 6650 | 字幕关闭通知 |
| `on_room_data(items,fields)` / `on_video_subtitle(fields)` | 6619/6622 | 会场数据/视频字幕参数 |
| `on_chat_check_result(fields)` | 6629 | 聊天审批结果 |
| `on_role_permission_changed(fields)` / `on_user_permission_changed(uid,fields)` | 6632/6635 | 角色/用户权限变更 |
| `on_system_notify(fields)` / `on_multicast_state(state,fields)` | 6636/6651 | 兼容解析；6636仅主服务器到节点，6651当前代码未实现，终端测试均不执行、不计阻塞 |
| `on_server_record_error(error,fields)` | 6636 | 提取 `ServerRecord.Error` 的专用回调 |
| `on_face_rollcall_started/result/cancelled(fields)` | 6657/6658/6659 | 人脸点名生命周期 |
| `on_relay_message(messages,fields)` | 6700 | 远程控制/云台/快速点名等中转消息 |
| `on_chat_recalled(fields)` | 6669 | 聊天撤回广播 |
| `on_web_kicked(uid,fields)` / `on_web_room_closed(fields)` / `on_web_notify(fields)` | 6652/6653/6654 | `6652` 仅兼容历史 Web 踢人通知；当前 HTTP 请出走 `6606`；`6653/6654` 为 Web 关房/通知 |

### 快速使用

```python
from fm_client import FmClient, FmListener

class L(FmListener):
    def on_user_state_changed(self, f): print("状态", f)
    def on_conf_state_changed(self, f): print("会议状态", f)
    def on_chat(self, s, d, t, m=None): print(f"聊天 {s}: {t}")

c = FmClient("192.168.7.154", 1089, L())
c.front_login("A7", "<configure-locally>", room_id=10000, room_app_id=11, room_node_id="396593")
c.conf_login(user_name="A7")     # verify_mode/room_password 自动从 front_login 继承
c.mic_on()                       # 开麦
c.camera_on()                    # 开摄像头
c.start_record()                 # 开启录制
c.chat("hello")
c.stop_record()
c.conf_logout(); c.front_logout(); c.close()
```

Front 与 Conf 分开部署时无需手工改地址：`front_login()` 会保存回复中的全部
`Server/SrvAddr`，`conf_login()` 按 C++ 客户端行为把它们拼成地址链后创建 app=11 会话。
Front 本身也可直接使用地址链：

```python
c = FmClient("TCP:10.0.0.1:1089;TCP:10.0.0.2:1089", listener=L())
```

### 入会前置：拿到 RoomNodeID

`LOGINROOM` 必须传服务器本地节点 ID（`RoomNodeID`，VARCHAR 非固定）。两种方式：

```bash
# 方式一：客户端自动发现（推荐，即使 ROOMLIST 返 0x1002 也能取到）
python -m fm_client.demo --host 192.168.7.154 --user A7 --password 'your-password' --discover-node
# → 本地 RoomNodeID = '396593'  →  export FM_ROOM_NODE=396593

# 方式二：查 DB
SELECT NodeID FROM t_nodeinfo WHERE IsLocal=1;
```

### 会前功能说明（即时/预约会议、通讯录、列表分类）

**即时会议 / 预约会议**：4u_fmserver 终端 XML 信令里没有“创建会议”命令
（Front 域终端命令最大到 30015）。创建、取消、平台用户邀请和布局锁定走 Web 后台 HTTP API，
由 `WebApiClient` 或 `FmClient.attach_http()` 调用；只测试信令时也可以直接使用已存在的房间
执行 `front_login(room_id=...)`。

**加入会议变体**（均复用 `LOGINROOM 30004`，无独立命令）：
- 会议号入会 `front_login_by_meeting_no`：`GETROOMID(30012)` 换算 RoomID → 进房
- 邀请码入会 `front_login_by_invite_code`：邀请码当 RoomID，服务端走 `t_invitecode` 解析，`real_room_id` 为真实房
- 访客匿名入会 `front_login_guest`：`UserType=0`，目标房须 `VerifyMode=3`

**通讯录**：4u 没有"全公司联系人搜索"命令，通讯录语义是**本会议成员 + 企业组织架构**：
- 组织架构 + 本地用户联系人：`conf_login` 的 6601 已下发（缓存在 `dept_info` / `user_info`）
- 离线成员：`conf_get_offline_users` 首次走 6503，当前 Conf 登录内后续调用复用缓存；在线成员走 6602 推送（listener 维护名册）
- 组织架构独立查：`conf_get_dept_info`(6528，⚠️ 服务端 handler 可能被注释)
- 一站式快照：`conf_get_contacts()` → `{depts, local_user, offline_users}`
- **通讯录邀请**= 邀请电话/H323 终端入会（`conf_invite_call` MAKECALL 6741），不是给在线同事弹通知

**会议室列表分类**：协议**不下发** `RoomType`/`MeetingType`。`front_roomlist_classified()` 在客户端按
`RoomID` 区间（即时 `[10^9, 0x70000000)`）+ `HopeStart/End` 时间（预约）推算，给每个 `RoomInfo` 打
`MeetingType` 标签（`fixed` / `reserved` / `instant`）。

### demo CLI

```bash
cd sessionmanager_py/sessionmanager_py
python -m fm_client.demo --mock                    # 内置 mock 自测
python -m fm_client.demo --list-rooms              # 拉房间列表
python -m fm_client.demo --list-rooms-classified   # 列表 + 标固定/预约/即时
python -m fm_client.demo --discover-node           # 取 RoomNodeID
python -m fm_client.demo --get-room-id 10000

# 加入会议变体（替代默认 room_id 入会）
python -m fm_client.demo --login-by-meeting-no 10000
python -m fm_client.demo --login-by-invite-code 123456789
python -m fm_client.demo --login-guest             # 访客匿名入会（需 VerifyMode=3 房）

# 入会后查通讯录 / 邀请（自动 登录→入会→执行→停留观察推送→退出）
python -m fm_client.demo --contacts                # 通讯录快照（部门+本地用户+离线成员）
python -m fm_client.demo --offline-users           # 离线/历史成员(6503)
python -m fm_client.demo --dept-info               # 组织架构(6528，可能无响应)
python -m fm_client.demo --room-config             # 会议配置/邀请码(6534)
python -m fm_client.demo --invite-call 13800000000,13900000000 --stay 10   # 邀请电话入会(MAKECALL)
python -m fm_client.demo --invite-call 8001 --call-type 2                 # H323 终端

python -m fm_client.demo --addr 'TCP:10.0.0.1:1089;TCP:10.0.0.2:1089'
python -m fm_client.demo                           # 登录→入会→聊天→开麦→退出
```

demo 所有参数支持 `FM_*` 环境变量默认值，设一次后裸跑：

```bash
export FM_HOST=192.168.7.154 FM_PORT=1089 FM_USER=A7 FM_PASSWORD=<configure-locally> \
       FM_ROOM_ID=10000 FM_ROOM_NODE=396593 FM_ROOM_APP_ID=11
# 或者用 FM_ADDR 替代 FM_HOST + FM_PORT：
export FM_ADDR='TCP:192.168.7.154:1089;TCP:192.168.7.155:1089'
python -m fm_client.demo
```

---

## 三、测试与诊断

以下命令均从 Python 工程目录执行：

```bash
cd /Users/areay/work/code/c++/public_framecore/sessionmanager_py/sessionmanager_py
```

真实服务器脚本读取 `examples/multi_client.json` 和 `examples/http_api.json`。运行前应替换
host、账号、房间和 HTTP 鉴权配置；不要把真实密码、Token 或 app_secret 提交到版本库。
`base_url` 通常应包含 `/api`，例如 `https://server:28080/api`。`verify_tls=false` 只应用于
受控测试环境中的自签证书。

`multi_client.json` 可配置 Conf 版本、入会稳定等待和真机协议参数：

```json
{
  "conf_protocol_version": "2.0",
  "initialization_wait": 2.0,
  "case_params": {
    "sys_msg_id": 6,
    "audio_share_id": 1,
    "video_share_id": 1
  }
}
```

该值会统一用于 `6501` 登录和登录后的全部 Conf 命令；Front(app=20) 仍使用自己的
`1.2`，不受影响。省略配置时默认 `1.2`，以兼容原有调用。根据当前 fmserver 源码，
`1.x` 是旧 3.x 客户端兼容模式，`2.x` 开启大会议角色/操作权逻辑，`3.x` 为新产品
预留，`4.x` 在远程打开音视频时增加用户确认；服务器当前声明版本为 `4.0`。

`initialization_wait`只用于双端测试连接完成后的初始化稳定期。`case_params.sys_msg_id`
必须存在于服务器 Message 配置中；消息正文和类型不能由客户端任意指定。

真实联调建议先用 `2.0` 验证 `6530 -> 6630`：服务端只在 Conf 主版本不小于 2 时
返回操作权响应。版本配置不是能力协商，不能启用服务器中未实现的 handler，例如当前
源码中已注释的 `6528 -> 6628`。`6534` 请求则会默认携带 `<InviteCode/>` 查询项。

### 3.1 离线全量测试

不依赖真实服务器，使用内置 `MockFmServer`：

```bash
python3 -m pip install -e '.[test]'            # 首次运行时安装包和 pytest
python3 -m pytest -q                           # 当前：167 passed, 2 skipped
python3 -m pytest tests/test_fm_client.py -v   # 业务层
python3 -m pytest tests/test_session.py -v     # 会话层
python3 -m pytest tests/test_received_events.py -v  # 统一下行事件
```

两个 skipped 是未安装的 AES/SM4 可选依赖，不影响 NONE/XOR、会话和会议业务测试。

### 3.2 HTTP 建会到信令退会闭环

该脚本会通过 HTTP **创建一个即时会议**，再通过 Front/Conf 信令入会、设置字幕并退会：

```bash
python3 examples/fm_http_lifecycle.py \
  examples/http_api.json examples/multi_client.json
```

这是有服务端副作用的真机测试。脚本不会自动删除新建会议；需要时应由测试环境或后台清理。

### 3.3 双模拟端广播闭环

A 发送业务命令，B 验证服务器广播，两端必须使用不同账号：

```bash
python3 examples/fm_two_clients.py examples/multi_client.json
```

### 3.4 完整信令生命周期诊断

执行 Front 登录、Conf 入会、会中操作、Conf 退出和 Front 注销：

```bash
python3 examples/fm_lifecycle_test.py examples/multi_client.json
```

### 3.5 HTTP API 端点调用

Python 不支持 `<roomId>`、`<userId>` 这种尖括号占位符；直接复制会在请求发出前得到
`SyntaxError`。下面的示例可以直接执行，并使用建会响应中的 `roomId`。`FM_HTTP_USER_ID`
必须是 Web 后台的平台用户 ID，不是 Conf 会话里的 `RoomUserID`。

```bash
export FM_HTTP_USER_ID=1001

python3 - <<'PY'
import os

from fm_client.http_api import WebApiClient

api = WebApiClient.from_config("examples/http_api.json")
created = api.create_instant_meeting(meeting_name="http-api-test", user_name="A7")
room_id = int(created["data"]["roomId"])
user_id = int(os.environ["FM_HTTP_USER_ID"])

print("create:", created)
print("invite:", api.invite_users(room_id, [user_id]))
print("lock layout:", api.lock_layout(room_id, True))
print("unlock layout:", api.lock_layout(room_id, False))
PY
```

这些调用会真实创建会议、邀请用户并修改布局锁。如果只想操作已有会议，可删除
`create_instant_meeting()`，然后使用 `room_id = int(os.environ["FM_HTTP_ROOM_ID"])`。

常见异常：`HttpApiError` 表示后台返回业务错误码；`HttpApiError(-1)` 通常表示地址、端口、
TLS 或网络不可达；`KeyError: data/roomId` 表示目标版本的建会响应结构与当前约定不同，应先
打印完整响应核对。HTTP 401/403 应检查 app_key、app_secret、服务器时间和 token 算法。

### 3.6 会议控制接口 demo

`fm_meeting_control_demo.py` 使用 A 操作端和 B 观察端验证开发侧公开的会议控制接口。
每个信令操作都由 A 发起，并由 B 使用统一 `ReceivedEvent` 验证服务器的新广播，而不是只检查
调用过程是否抛异常。

```bash
# 离线验证全部 9 个接口；不需要配置或真实服务器
python3 examples/fm_meeting_control_demo.py --mock

# 真机验证核心信令接口；A/B 必须是不同账号，A 需具备相应会控权限
python3 examples/fm_meeting_control_demo.py \
  --config examples/multi_client.json
```

真机模式默认跳过有外部副作用的两项邀请。确认测试数据后，才显式启用：

```bash
# 平台用户邀请：user ID 是 Web 后台的平台用户 ID，不是 RoomUserID
python3 examples/fm_meeting_control_demo.py \
  --config examples/multi_client.json \
  --http-config examples/http_api.json \
  --invite-user-id 1001

# SNS/H323 终端邀请；可重复传入 --terminal-sns
python3 examples/fm_meeting_control_demo.py \
  --config examples/multi_client.json \
  --terminal-sns 8001

# 锁会敲门完整时序：B 收 6617、A 收 6604、允许后 B 完成 6601
python3 examples/fm_locked_join_test.py \
  --config examples/multi_client.json \
  --timeout 10
```

覆盖接口：`set_user_audio`、`set_user_video`、`set_layout`、`set_role`、
`invite_users`、`invite_terminals`、`start_aux_stream`、`stop_aux_stream` 和
`configure_polling`。脚本会关闭本次启用的音视频、角色和辅流状态，并删除本次创建的轮询；
`set_layout` 会修改当前会场布局，真机验证应使用专用测试会议。脚本最终输出
`passed/failed/skipped` 汇总，任意检查失败时退出码为 1。

布局、媒体/VNC 辅流和视频轮询是排他性操作。demo 会在执行前通过 6530 自动申请对应操作权，
并在操作后释放；如果权利正被其他客户端占用，会直接报告服务端错误（通常为
`0x211E RIGHTOCCUPIED`）。

### 3.7 交互式诊断和能力探针

```bash
python3 examples/fm_pre_meeting_test.py  # 会前列表、通讯录、邀请、挂断
python3 examples/fm_probe_new_cmds.py    # 探测目标 fmserver 支持的扩展 cmdid
python3 examples/fm_record_test.py       # 会中音视频、录制、锁房
```

不同版本 fmserver 支持的命令集不同。探针结果应作为目标环境的能力边界；某个版本不支持
扩展 cmdid，不代表 Python 封装没有实现。

### 3.8 测试辅助层

`FmTestClient` 自带线程安全的 `EventCollector`、typed 返回和统一下行事件。推荐在动作前
记录事件游标，再等待游标之后的 Response/Notify，避免初始化或历史事件误匹配：

```python
mark = observer.events.mark()
operator.mic_on()

event = observer.events.wait_event(
    after=mark,
    source="conf",
    cmdid=6609,
    kind="notify",
    match={"UserID": operator.room_user_id_int, "Audio.State": 2},
    timeout=10,
)
assert event is not None
```

点路径中的索引兼容 XML 重复节点的两种解析形态。例如 `DataItem.0.ItemID`
既可匹配 `DataItem` 列表，也可匹配真机只下发一个节点时的单对象结构；
`ReceivedEvent.fields` 仍保留原始解析结果，不会因匹配而改写。

| 提供 | 说明 |
|---|---|
| `FmTestClient` | `FmClient` 的测试封装，提供 typed 返回和 `*_int` 状态 |
| `ReceivedEvent` | 保存 source、kind、cmdid、seq、request_cmdid、fields、原始 XML 和时间 |
| `EventCollector.received` | 保存 Response、Notify、未知消息和解析失败事件 |
| `mark()` / `wait_event()` | 使用事件游标等待新的统一下行事件 |
| `assert_no_event()` | 异常场景中断言观察端没有收到错误的成功广播 |
| `wait_for_*()` | 聊天、入会、录制、呼叫等常用事件的兼容便捷接口 |

### 3.9 单接口/单协议逐项验证

`fm_single_case.py` 每次只执行一个用例或公开方法。先查看可用项：

```bash
python3 examples/fm_single_case.py list
python3 examples/fm_single_case.py list --channel conf
python3 examples/fm_single_case.py list --channel http
```

单个 Conf 用例可先跑 Mock，再使用相同用例名跑真机：

```bash
python3 examples/fm_single_case.py conf --mock --case user_audio

python3 examples/fm_single_case.py conf \
  --config examples/multi_client.json \
  --case user_audio \
  --timeout 10
```

Front 房间列表和节点发现是两个不同的验证结论：

```bash
# 严格验证房间列表：30003 Result 非 0 时失败
python3 examples/fm_single_case.py conf \
  --config examples/multi_client.json --case front_room_list

# 只验证 RoomNodeID：即使列表返回 0x1002，只要携带 RoomNodeID 仍可通过
python3 examples/fm_single_case.py conf \
  --config examples/multi_client.json --case front_discover_node
```

`front_room_list` 返回 `0x1002 EXCEPTION` 通常表示 Front 服务的房间列表
数据库/存储过程异常，不是 Python 解析或 A/B 入会失败。

内置 Conf 用例会检查发送端 Response 或 B 端 Notify，并对音视频、
角色、辅流、轮询等状态做尽力清理。布局/辅流/轮询会自动申请和释放 6530 操作权。

需要业务参数时使用 `--params` JSON：

```bash
python3 examples/fm_single_case.py conf \
  --config examples/multi_client.json \
  --case terminal_call \
  --params '{"terminal_sns":"8001"}' \
  --allow-destructive
```

通用 Conf 模式可直接调用任意公开方法。支持
`$operator.room_user_id`、`$observer.room_user_id`、`$operator.local_user_id`、
`$observer.local_user_id` 和 `$room_id` 占位符：

```bash
python3 examples/fm_single_case.py conf \
  --config examples/multi_client.json \
  --method set_user_audio \
  --args '["$observer.room_user_id",2]' \
  --expect-cmdid 6609 \
  --expect-fields '{"UserID":"$observer.room_user_id","Audio.State":2}' \
  --cleanup-method set_user_audio \
  --cleanup-args '["$observer.room_user_id",0]'
```

单个 HTTP API：

```bash
python3 examples/fm_single_case.py http \
  --http-config examples/http_api.json \
  --method list_departments \
  --kwargs '{"page_number":1,"page_size":20}' \
  --expect '{"code":0}'
```

HTTP 创建、编辑、邀请、会控、拉流和订阅变更，以及 Conf 的 `kick_user`、
`close_room`、权限/终端呼叫等有外部影响的项，需使用专用测试数据并显式传入
`--allow-destructive`。成功退出码为 0，失败退出码为 1，超时时会打印游标之后最近 10 条事件。

---

## 四、目录

```
sessionmanager_py/
├── framecore_session/          # 会话层
│   ├── __init__.py constants.py exceptions.py
│   ├── address.py                            # TCP 地址链解析
│   ├── packets.py framer.py security.py session.py message.py
├── fm_client/                  # 业务层（4u 会议）
│   ├── __init__.py xmlcmd.py errcodes.py _helpers.py testing.py
│   ├── entrance.py             # Entrance 域 (app=8)
│   ├── front.py                # Front 域 (app=20)
│   ├── conf.py                 # Conf 域 (app=11)
│   ├── client.py               # FmClient 核心：共享状态 + 会话机械 + 推送分发 + attach_http
│   ├── http_api.py             # Web 后台 HTTP 客户端（建会/取消/邀请/布局锁定等信令外接口）
│   ├── ops/                    # 各域操作 mixin（混入 FmClient，平铺 API）
│   │   ├── front_ops.py        #   登录 / 加入会议变体 / 列表 / 发现 / 注销
│   │   ├── conf_ops.py         #   会中控场（聊天/音视频/录制/主席/踢人…）
│   │   ├── contacts_ops.py     #   通讯录查询（离线成员/组织架构/配置/快照）
│   │   ├── call_ops.py         #   通讯录邀请（MAKECALL 呼叫电话/H323 入会）
│   │   ├── meeting_ops.py      #   扩展会议控制（分组/操作权/字幕/改名/撤回/视图…）
│   │   ├── meeting_control_ops.py  # 会议控制补齐（布局/角色/辅流/轮询/邀请·建会，方案§4）
│   │   └── entrance_ops.py     #   Entrance 配置下发
│   ├── mockserver.py           # 内置 mock fmserver
│   └── demo.py                 # CLI demo
├── examples/
│   ├── session_test.py         # 会话层 loopback/client/server
│   ├── fm_record_test.py       # 业务层 录制/媒体 真机测试（会中，交互式）
│   ├── fm_pre_meeting_test.py  # 业务层 列表分类/通讯录/邀请 真机测试（会前，交互式菜单）
│   ├── fm_probe_new_cmds.py    # 扩展命令真机探针（逐条独立登录，报告支持情况）
│   ├── fm_two_clients.py       # 双账号多端闭环（读 JSON 配置）
│   ├── fm_lifecycle_test.py    # 入会→操作→退会 真机诊断
│   ├── fm_http_lifecycle.py    # HTTP 建会→信令入会→操作→退会
│   ├── fm_meeting_control_demo.py  # 9 个会议控制接口的 A 操作+B 观察闭环
│   ├── fm_single_case.py       # 单个 Conf/Front 用例或 HTTP 方法逐项验证
│   ├── multi_client.json       # 多端配置样例（host+账号+房间）
│   └── http_api.json           # Web API 配置样例（base_url+app_key+app_secret）
├── tests/
│   ├── test_address.py test_packets.py test_framer.py test_security.py test_session.py
│   ├── test_message.py test_xmlcmd.py test_fm_client.py test_multi_client.py
│   └── test_meeting_control.py test_http_api.py test_received_events.py test_single_case_cli.py test_errcodes.py
├── pyproject.toml              # 安装与 pytest 配置
├── conftest.py                 # sys.path 引导 + fm/fm_mock_server fixture
├── docs/业务层设计文档.md        # 测试人员向设计文档
└── README.md
```

---

## 五、后续

已实现：会话层 + 信封 + **4u 会议业务层**（Entrance/Front/Conf 三域）。
- 登录→入会→会中：音视频/录制/主席/踢人/会议状态/静音/设备上报。
- 会前：加入会议三变体（会议号/邀请码/访客）、会议室列表分类推算（固定/预约/即时）。
- 通讯录：组织架构(6601/6528)、离线成员(6503)、会议配置(6534)、通讯录快照、
  邀请电话/H323 入会(MAKECALL 6741 + 呼叫状态推送 6745 + 挂断/查列表)。
- 会议控制：布局、角色、辅流、轮询、分组、操作权、字幕、文件通知和消息撤回。
- Web 后台：即时/预约会议创建、取消、平台用户邀请和布局锁定（需 `attach_http`）。
- 测试事件：统一 `ReceivedEvent`、事件游标、Response/Notify 关联和负向断言。

> 即时/预约会议的创建不在终端 XML 信令层；`WebApiClient` 通过 Web 后台 HTTP API 实现。

`WebApiClient` 已覆盖 `docs/api.html` 中的业务端点：部门和用户管理、会议室创建/修改/授权/查询、
录制纪要查询与导出、会议控制操作、拉流和事件订阅。常用接口提供语义化方法；原始响应仍统一返回
`{code, msg, data}` 字典。会议纪要/转写导出使用 `download_*()` / `export_*()` 返回原始字节，
不会按 JSON 解析。底层 `request()`、`request_bytes()`、`control()` 仍可用于服务端新增字段或操作。

测试侧可按项目需要完善：
- `MeetingTestRuntime`、Excel 执行器和 Allure 报告属于测试编排层，不由本 SDK 实现；
  SDK 只提供公开业务接口、`FmTestClient`、统一事件对象和可运行 demo。
- 分阶段登录：公开 `front_enter_room()` 和组合式 `join_meeting()` 结果对象。
- 独立服务：AV/媒体流、白板、文档和真实文件传输不属于当前信令模拟范围。
- 真机矩阵：不同 fmserver 版本对 6528/6530/6534/6568 等命令的实现不同，需要持续探测。
- 电话网关：MAKECALL/H323 的最终效果需要真实网关环境验证。
