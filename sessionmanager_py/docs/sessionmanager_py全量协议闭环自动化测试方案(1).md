# sessionmanager_py 全量协议闭环自动化测试方案

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
operator.mic_on()

events = observer.wait_for_user_state(
    user_id=operator.room_user_id_int,
    timeout=10,
)

assert events
assert any(
    str(event.get("Audio", {}).get("State")) == "2"
    for event in events
)
```

## 4. 待开发补齐接口

以下名称是测试侧建议的统一业务接口。具体 cmdid 和 XML 字段由开发根据正式协议实现。

```python
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

同时需要补齐相应的服务器下行协议解析：

- 普通用户和通讯录邀请 Notify。
- SNS 终端邀请及状态 Notify。
- 指定布局和布局锁定 Notify。
- 管理员、主讲人等角色变化 Notify。
- 辅流开始、切换和停止 Notify。
- 轮询配置及窗口切换 Notify。
- 预约、即时会议创建和状态变化 Response/Notify。

所有下行消息已统一为 `ReceivedEvent`，包含原有的 source、cmdid、name、fields、
raw_xml、received_at，以及 event_id、kind、seq、request_cmdid、received_monotonic、
decode_error 和 raw_payload。事件统一保存在 `EventCollector.received`，测试使用
`mark()` + `wait_event(after=...)` 隔离历史消息，并可在 Allure 报告中展示脱敏后的
原始 XML。完整字段定义见《端到端AI自动化测试方案》。

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

测试侧增加一层轻量封装：

```python
class MeetingTestRuntime:
    def create_client(self, alias, account): ...
    def join(self, alias, room): ...
    def call(self, alias, operation, params): ...
    def clear_events(self, alias): ...
    def wait_event(self, alias, event, match, timeout): ...
    def close_all(self): ...
```

该封装负责：

- 按别名管理多个 `FmTestClient`。
- 统一完成 Front 和 Conf 登录。
- 将 Excel 中的业务动作映射到白名单方法。
- 保存用户 ID、会议 ID、会议号等运行变量。
- 等待指定客户端收到事件。
- 输出断言失败时的事件快照。
- 在用例结束后关闭全部连接。

Excel 不直接反射调用任意 Python 方法。测试执行器维护允许调用的业务操作白名单，开发补充新接口后，只需在白名单中注册一次。

## 6. 用例设计方向

测试用例按“一个业务功能一条完整协议链路”设计，不拆成互相依赖的零散用例。

主要场景：

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

## 7. 开发与测试分工及验收

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
- 将发送、接收、原始 XML 及断言结果写入 Allure 报告。
- 建立按模块和用例 ID 执行的回归入口。

验收标准：

- 方案范围内的业务均有可调用的 `FmTestClient` 接口。
- 服务器下行消息均能被 `EventCollector` 识别和保存。
- 观察端 B 能收到并断言正确的 cmdid、目标 ID 和核心字段。
- 不以“发送方法未报错”代替接收端协议断言。
- 邀请、布局、角色、辅流、轮询、预约和即时会议等缺失能力补齐后，能够直接接入同一测试执行流程。
- Allure 报告能够定位发送端、接收端、操作参数、Response、Notify 和原始 XML。

## 8. 约定

- 本方案用于开发与测试共同评审接口边界和测试方式。
- 不在方案中展开 Excel 字段级定义和逐条测试数据。
- 不包含异常用例专项设计。
- 具体 cmdid、XML 字段和错误码以开发提供的正式协议为准。
- 本期只验证协议闭环，不验证真实终端执行结果。
