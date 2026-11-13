方案思路：

服务端开发将会议终端与会议服务器之间的底层TCP/XML协议，封装成测试可以直接调用的Python业务方法和事件对象，测试再把多个 FmTestClient 封装成 MeetingTestRuntime ，供pytest和Excel用例统一调用

## 1. 目标

本方案用于开发与测试联合评审，明确：

- `sessionmanager_py` 当前已经封装的能力。
- 本期仍需开发补齐的协议能力。
- 测试如何调用 `FmTestClient` 完成双模拟端协议闭环。
- 如何接入 `pytest + openpyxl + Excel + Allure` 自动化测试框架。

验证范围仅限协议层：

```text
模拟端 A 发送业务命令
-> 真实会议服务器处理
-> 模拟端 B 收到 XML Notify
-> 解析 cmdid 和 fields
-> 断言消息类型与字段
```

不验证真实终端的音视频采集、布局渲染、录制文件、辅流画面等执行效果。

覆盖范围：

- 会中：音视频、布局、角色、邀请、请出、录制、全体静音、会议锁定、字幕、关闭会议、辅流、轮询。
- 会前：加入会议、预约会议、即时会议、通讯录邀请、固定/预约/即时会议列表。
- 邀请：普通用户、通讯录用户、SNS 终端、H323。

## 2. sessionmanager_py 已封装能力

### 2.1 会话通信层

`framecore_session` 已封装：

- TCP 可靠连接。
- 会话创建、握手和关闭。
- 数据分包、组包和 ACK。
- 加密能力。
- 连接异常和重连处理。

测试代码不需要直接操作 TCP。

### 2.2 会议业务层

`FmClient/FmTestClient` 已封装：

- Entrance 接入发现和配置中心地址查询。  # `entrance_get_config()`
- Front 鉴权、登录、退出。  # `front_authenticate()`、`front_login()`、`front_logout()`
- 房间 ID、会议号、邀请码和访客入会。  # `front_login()`、`front_login_by_meeting_no()`、`front_login_by_invite_code()`、`front_login_guest()`
- Conf 登录、初始化和退出。  # `conf_login()`、`conf_logout()`；`conf_login(init_complete=True)` 自动发送初始化完成消息
- 会议室列表及固定/预约/即时会议分类。  # `front_roomlist()`、`front_roomlist_classified()`、`list_rooms()`
- 成员入会、离会和状态变化。  # `wait_for_user_enter()`、`events.wait_for_user_leave()`、`wait_for_user_state()`
- 本端麦克风、摄像头开关及申请发言。  # `mic_on()`、`mic_off()`、`mic_request()`、`camera_on()`、`camera_off()`
- 申请和释放主持人。  # `apply_host()`、`release_host()`
- 录制开始、暂停、停止。  # `start_record()`、`pause_record()`、`stop_record()`
- 会议锁定和解锁。  # `lock_room()`、`unlock_room()`
- 全体静音。  # `mute_all()`
- 字幕设置和关闭。  # `set_subtitle()`、`close_subtitle()`、`events.wait_for_subtitle()`
- 请出用户和关闭会议。  # `kick_user()`、`close_room()`
- 电话/H323 呼叫、挂断和状态查询。  # `conf_invite_call()`、`conf_hangup()`、`conf_query_call_list()`、`wait_for_call_state()`
- 通讯录、离线成员和组织信息查询。  # `conf_get_contacts()`、`conf_get_offline_users()`、`conf_get_dept_info()`、`offline_contacts()`、`contacts_snapshot()`
- 窗口状态上报。  # `set_window_state()`；接收事件使用 `events.window_states`
- XML 命令构造、发送、Response 匹配和错误码处理。  # `XmlCommand`、`_send()`、`_request()`、`FmError`（底层内部能力）

### 2.3 事件接收层

`FmClient` 已实现：

```text
TCP 收到数据
-> XmlCommand.from_bytes() 解析 XML
-> 区分 Response 与 Notify
-> _dispatch_push() 按 cmdid 分发
-> EventCollector 保存业务事件
```

`EventCollector` 当前可收集：

- 成员入会和离会。
- 音视频及用户状态。
- 请出和关会。
- 会议及录制状态。
- 全体静音。
- 字幕。
- 窗口状态。
- 角色权限。
- 呼叫状态。

测试可使用 `wait_for_*()`、`snapshot()`、`wait_until()` 和 `clear()` 等接口等待并断言服务器消息。

## 3. 测试流程

从协议层看，完整登录链路分为：

```text
Entrance接入发现（1000/响应cmdid待确认）
-> Front用户认证（30000/30001）
-> Front进入房间（30004/30005）
-> Conf登录会议（6501/6601）
-> 发送初始化完成消息
-> 其他会内成员收到USERENTER
```

`front_login()` 当前会连续执行用户认证和进入房间；`conf_login(init_complete=True)` 会在 Conf 登录成功后自动发送初始化完成消息。

```text
1. 创建 FmTestClient A（操作端）和 FmTestClient B（观察端）。
2. A 执行 front_login() 和 conf_login()。
3. B 执行 front_login() 和 conf_login()。
4. 确认 A、B 都已完成会议初始化。
5. 清空 A、B 的历史事件：events.clear()。
6. A 发送业务命令，例如 mic_on()。
7. 服务器接收并处理命令。
8. 服务器向相关会议成员广播 XML Notify。
9. A、B 分别接收 TCP 数据。
10. XmlCommand.from_bytes() 将 XML 解析成 cmdid 和 fields。
11. _dispatch_push() 根据 cmdid 分发给 EventCollector。
12. 检查 B 是否收到对应事件。
13. 按用户 ID、状态字段等内容断言。
14. 根据业务需要，再检查 A 是否也收到相同广播。
```

观察端 B 是主要断言对象；A 收到自广播只作为补充证据。

测试示例：

```python
operator.mic_on()                            #开启麦克风操作
               
events = observer.wait_for_user_state(       #等待用户状态变化
    user_id=operator.room_user_id_int,
    timeout=10,
)

assert events
#验证麦克风状态变为开启（状态值 "2"）
assert any(
    str(event.get("Audio", {}).get("State")) == "2"   
    for event in events
)
```

## 4. 待开发补齐接口

以下名称是测试侧建议的统一业务接口。具体 cmdid 和 XML 字段由开发根据正式协议实现。

```python
# 已完成Front认证后单独进入房间，便于分阶段测试30004/30005
front_enter_room(room_id, room_node_id, room_password=None, room_app_id=11)

# 主持人设置指定用户的音频状态
set_user_audio(user_id, state)

# 主持人设置指定用户的视频状态
set_user_video(user_id, state)

# 设置会议布局，可指定布局成员和目标接收用户
set_layout(layout_type, user_ids, target_user_ids=None)

# 设置或取消指定用户的主持人、管理员、主讲人等角色
set_role(user_id, role, enabled)

# 邀请普通平台用户进入会议
invite_users(user_ids)

# 从会议通讯录邀请用户进入会议
invite_contacts(user_ids)

# 根据 SNS 号码邀请硬件终端进入会议
invite_terminals(sns_list)

# 开始发送辅流
start_aux_stream()

# 停止发送辅流
stop_aux_stream()

# 新增、修改、暂停、恢复、停止或删除视频轮询
configure_polling(...)

# 创建预约会议，并返回会议 ID、会议号和邀请码等信息
create_reserved_meeting(...)

# 创建即时会议，并返回可供其他模拟端入会的信息
create_instant_meeting(...)

# 取消尚未开始的预约会议
cancel_reserved_meeting(...)
```

建议登录过程保存各阶段结果，便于测试断言和 Allure 定位：

```python
client.entrance_response
client.auth_response
client.room_response
client.conf_response
```

也可以提供组合结果：

```python
result = client.join_meeting(...)

result.entrance
result.authentication
result.room
result.conf
result.init_complete
```

`front_enter_room()` 应作为公开业务接口，避免测试直接调用当前私有方法 `_loginroom()`。

同时需要补齐相应的服务器下行协议解析：

- 普通用户和通讯录邀请 Notify。
- SNS 终端邀请及状态 Notify。
- 指定布局和布局锁定 Notify。
- 管理员、主讲人等角色变化 Notify。
- 辅流开始、切换和停止 Notify。
- 轮询配置及窗口切换 Notify。
- 预约、即时会议创建和状态变化 Response/Notify。

所有下行消息统一使用 `ReceivedEvent` 封装。原有六个字段保持不变，新增字段均有
默认值，兼容已有构造方式；解析失败的消息也会作为 `decode_error` 事件进入统一队列。

```python
@dataclass
class ReceivedEvent:
    source: str
    cmdid: Optional[int]
    name: str
    fields: dict
    raw_xml: bytes
    received_at: float
    event_id: int = 0
    kind: str = "unknown"  # response/notify/unknown/decode_error
    seq: Optional[int] = None
    request_cmdid: Optional[int] = None
    received_monotonic: float = 0.0
    decode_error: Optional[str] = None
    raw_payload: bytes = b""
```

事件由 `EventCollector.received` 统一保存。测试应在执行动作前调用 `mark()` 记录游标，
然后使用 `wait_event(after=游标, source=..., cmdid=..., kind=..., match=...)` 等待新事件，
避免历史消息误匹配。`assert_no_event()` 用于业务异常场景的负向断言。

这样测试可以直接按来源、消息类型、cmdid、请求关联和字段断言，并在 Allure 报告中
展示脱敏后的原始 XML。现有 typed 事件队列和 `wait_for_*()` 继续作为便捷兼容接口。

## 5. 测试框架调用设计

测试侧采用：

```text
pytest
+ openpyxl
+ Excel 数据驱动
+ Allure 报告
+ sessionmanager_py
```

各层职责：

```text
Excel
  维护业务场景、客户端角色、操作参数和预期事件
        |
pytest 场景执行器
  创建客户端、调用业务方法、等待事件、执行断言
        |
sessionmanager_py 适配层
  对 FmTestClient 进行测试侧统一封装
        |
FmTestClient
  登录、发命令、收 Response/Notify
        |
EventCollector
  保存服务器下行消息
        |
Allure
  展示步骤、参数、事件字段、原始 XML 和断言结果
```

测试侧增加一层轻量封装,用于管理和控制测试中的会议客户端行为

```python
class MeetingTestRuntime:
    def create_client(self, alias, account): ...  # 创建客户端实例，返回客户端对象或客户端ID
    def join(self, alias, roomid): ...  # 指定客户端加入会议室
    def call(self, alias, operation, params): ...  # 在客户端上执行操作，模拟用户的会议行为
    def clear_events(self, alias): ...  # 清空指定客户端的事件队列或缓存
    def wait_event(self, alias, event, match, timeout): ...  # 等待指定客户端收到特定事件
    def close_all(self): ...  # 关闭所有客户端连接
```

参数说明：

- `alias`：客户端别名，例如 `operator`、`observer`、`target`。
- `account`：账号信息，包含用户名、密码或 Token 等登录凭证。
- `roomid`：需要加入的会议室 ID。
- `operation`：操作名称，例如 `mic_on`、`mic_off`、`set_layout`。
- `params`：操作参数字典；无参数操作传空字典 `{}`。
- `event`：等待的事件名称，例如 `USER_STATE_CHANGED`。
- `match`：事件字段匹配条件，例如 `{"Audio.State": 2}`。
- `timeout`：等待事件的最长时间，单位为秒。

该封装负责：

- 按别名管理多个 `FmTestClient`。
- 统一完成 Front 和 Conf 登录。
- 将 Excel 中的业务动作映射到白名单方法。
- 保存用户 ID、会议 ID、会议号等运行变量。
- 等待指定客户端收到事件。
- 输出断言失败时的事件快照。
- 在用例结束后关闭全部连接。

Excel 不直接反射调用任意 Python 方法。测试执行器维护允许调用的业务操作白名单，开发补充新接口后，只需在白名单中注册一次。


例如：



## 6. 用例设计方向

测试用例按“一个业务功能一条完整协议链路”设计，不拆成互相依赖的零散用例。

主要场景：

- 登录会议：分别验证 Entrance 发现、Front 认证、Front 进房、Conf 登录和初始化完成，最终由观察端断言 `USERENTER`。
- 音视频：A 开关音视频，B 断言 `USERSTATECHANGED` 及用户、设备和 State。
- 布局：A 设置分屏和成员列表，B 断言布局 Notify 中的布局类型、成员及目标范围。
- 角色：A 设置或取消角色，B 断言目标用户及角色状态。
- 邀请：被邀请端先登录 Front，收到邀请 Notify 后入会，观察端收到 `USERENTER`。
- 请出：目标端收到请出消息，观察端收到成员离会消息。
- 录制、静音、锁定、字幕：A 发起操作，B 断言对应会议状态 Notify。
- 关会：A 关闭会议，B 断言关会消息。
- 辅流：A 开始、切换或停止辅流，B 断言辅流状态 Notify。
- 轮询：A 配置轮询，B 断言配置和窗口切换消息。
- 会前：创建预约/即时会议后查询列表，并使用返回的会议信息完成入会验证。

每次操作前清空历史事件或记录事件游标，避免旧消息导致误判。踢人、关会等破坏性场景使用独立会议。

### 登录会议专项正常场景

| 阶段 | 主要测试点 |
|---|---|
| Entrance发现 | 返回平台类型、配置中心地址和服务版本；返回地址可以继续连接 Front；单机部署可跳过 Entrance 直接连接 Front |
| Front认证 | 注册用户账号密码登录；已有 Token 登录；匿名用户分配 UserID；不同终端和客户端类型；退出后重新登录 |
| Front进房 | 房间 ID、会议号、邀请码和访客入会；正确会议密码；返回并保存 RoomUserID、Token、RoomSrvNodeID、UserRight 和会议服务地址 |
| Conf登录 | 固定/预约/即时会议登录；主持人、普通成员和匿名成员权限正确；返回会议配置和媒体/FSP地址；初始化完成后观察端收到 `USERENTER` |
| 完整链路 | Entrance发现 -> Front认证 -> Front进房 -> Conf登录 -> 初始化完成 -> 双端收发一条业务消息 -> Conf/Front退出 |

## 7. 业务异常用例设计

异常用例主要验证服务器是否正确拒绝非法业务操作，以及观察端是否没有收到错误的成功状态广播。不包含断网、服务器停机等环境故障。

通用验证流程：

```text
1. A 发送无权限、无效参数或状态冲突的业务命令。
2. 请求型命令检查 A 是否收到正确的错误码或失败 Response。
3. 通知型命令在协议提供错误 Notify 时检查错误信息。
4. 检查 B 未收到对应的成功状态广播。
5. 确认 A、B 连接正常，后续合法命令仍能正常收发。
```

主要业务异常测试点：

| 模块 | 业务异常测试点 |
|---|---|
| Entrance发现 | 配置中心地址为空或不可用；终端类型/客户端版本非法；请求超时；返回地址无法连接 Front |
| Front认证 | 用户不存在、禁用或删除；密码错误；Token为空、过期、伪造或与用户不匹配；非法终端/App类型；重复认证；认证失败后错误地保留 LocalUserID |
| Front进房 | 未认证直接进房；房间、节点或AppID无效；会议密码错误；会议未开始、已结束或锁定；邀请码失效；授权Token获取失败；30005缺少Token、RoomUserID或会议服务地址；重复进房 |
| Conf登录 | 未取得30005直接登录；Token为空、错误、过期或与房间/用户不匹配；会议服务节点错误；数据库用户校验失败；FSP地址获取失败；重复6501；6601缺少用户、权限或会议配置；登录失败未释放Token |
| 音视频 | 普通成员控制其他用户；目标用户不存在、已离会或被请出；非法状态或设备 ID；会议关闭后操作 |
| 布局 | 无权限设置；非法分屏数；窗口数与用户数冲突；重复或不存在用户；目标与排除用户冲突；布局锁定后修改 |
| 角色 | 错误主持密码；无权限修改他人角色；目标用户不存在或已离会；互斥角色冲突；重复设置或取消角色 |
| 邀请 | 邀请自己、已在会用户或重复邀请；邀请列表为空；用户、SNS或H323地址不存在；无邀请权限；会议锁定或关闭后邀请 |
| 请出 | 普通成员请出他人；请出自己或主持人；目标不存在或已离会；重复请出同一用户 |
| 录制 | 普通成员操作；未开始直接暂停/停止；录制中重复开始；停止后重复停止；录制权限被占用 |
| 静音和锁定 | 普通成员执行全体静音或锁定；重复锁定/解锁；会议关闭后操作 |
| 字幕 | 普通成员设置字幕；字幕为空或超长；持续时间非法；指定用户不存在；重复关闭字幕 |
| 关会 | 普通成员关闭会议；重复关闭；关会原因非法；关会后继续发送会议命令 |
| 辅流 | 无权限发送；多个成员争抢辅流权；未开始直接停止；辅流源非法；重复开始/停止；发送者离会后操作 |
| 轮询 | 无权限配置；轮询 ID 非法或重复；成员为空、重复或不存在；间隔/窗口非法；修改或删除不存在的轮询 |
| 预约/即时会议 | 无创建权限；预约时间或时长非法；名称为空或超长；重复创建；取消已开始/已取消会议；邀请成员无效 |

异常用例不能只通过“没有收到消息”判定成功。请求型命令优先断言明确的失败 Response；没有同步 Response 的通知型命令应结合错误 Notify、无成功广播和后续合法操作判断。

## 8. 开发与测试分工及验收

开发侧：

- 提供缺失业务命令的构造和发送接口。
- 提供对应 Response/Notify 的 cmdid 及字段解析。
- 将新 Notify 接入 `_dispatch_push()` 和 `EventCollector`。
- 保留原始 XML、cmdid、fields 和接收时间。
- 提供可连接真实服务器的账号及会议环境说明。

测试侧：

- 基于 pytest、openpyxl 和 Excel 实现数据驱动执行器。
- 封装多 `FmTestClient` 管理、登录、调用、等待和清理。
- 设计完整业务场景和协议字段断言。
- 设计业务异常场景，验证失败 Response/Notify 及无错误广播。
- 将发送、接收、原始 XML 及断言结果写入 Allure 报告。
- 建立按模块和用例 ID 执行的回归入口。

验收标准：

- 方案范围内的业务均有可调用的 `FmTestClient` 接口。
- 服务器下行消息均能被 `EventCollector` 识别和保存。
- 观察端 B 能收到并断言正确的 cmdid、目标 ID 和核心字段。
- 业务异常操作返回明确失败结果，且不会向观察端广播成功状态。
- 不以“发送方法未报错”代替接收端协议断言。
- 邀请、布局、角色、辅流、轮询、预约和即时会议等缺失能力补齐后，能够直接接入同一测试执行流程。
- Allure 报告能够定位发送端、接收端、操作参数、Response、Notify 和原始 XML。
