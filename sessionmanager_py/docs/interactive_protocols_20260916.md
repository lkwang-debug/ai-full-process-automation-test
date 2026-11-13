# 交互协议补充与验收说明（2026-09-16）

适用版本：`sessionmanager_py`、`sessionmanager_py_0914`；2026-09-22 已同步到 `sessionmanager_py_0922`。
下文已有真机记录属于原版本历史结果；本包本次验证见 [0922 验收记录](notification_protocols_20260922.md)。

2026-09-22 更新：[聊天拒绝与语言设置的确认接口、最新真机结果](interaction_confirmation_20260922.md)。

本次补齐 SDK 上行构造、公开方法、通知回调、事件保存及测试，不修改服务器代码，也不改变配置文件中的默认 Conf 版本。SDK 接口已补齐不等于部署环境的所有业务都已验收通过。

## 1. 新增公开接口与投递方向

下列方法可直接通过 `FmClient` / `FmTestClient` 调用，不需要测试侧拼 XML。

| 能力 | 公开方法 | 实际链路与回调 |
|---|---|---|
| 开关聊天审批 | `set_chat_check(enabled)` | 6510 `EnableChatMsgCheck` → 6610 |
| 审批聊天原消息 | `review_chat_message(message, approved=True/False)` | 审批人收到 6612 `NeedCheck=1` → 6529；同意后审批人收到 6629，普通用户收到获批的 6612 |
| 设置交互语言 | `set_interactive_language(language, user_id=None)` | 6540 → 6640；`on_interactive_language(fields)` |
| 接收会场语言配置 | 无需主动查询 | 6641 自动下发，可能分包，以 `End` 结束；`on_room_languages(fields)` |
| 申请远程音视频 | `request_remote_av(dst_user_id, audio_ids=None, video_ids=None, operation=0)` | A 发 6542 → B 收 6642；`on_remote_av_request(fields)` |
| 根据通知接受/拒绝 | `respond_remote_av(request, accept=True/False)` | B 用收到的 6642 字段回 6543 → A 收 6643；`on_remote_av_result(fields)` |
| 显式发送端侧处理结果 | `reply_remote_av(dst_user_id, result=..., audio_ids=None, video_ids=None, operation=0)` | 同上；Result=0 同意，非零拒绝 |
| 请求人脸服务路由 | `request_face_recognition(src_user_id, src_video_id, timeout=None)` | 6555 → 6655；返回服务地址、AppID、CheckCode 等 |
| 开始人脸点名 | `start_face_rollcall(title, countdown_seconds=30, timeout=None)` | 6556 → 6656，成功后另发 6657；`on_face_rollcall_started(fields)` |
| 上报点名结果 | `report_face_rollcall_result(rollcall_id, face_id)` | 6558 → 6658 广播；`on_face_rollcall_result(fields)` |
| 取消点名 | `cancel_face_rollcall(rollcall_id)` | 6559 → 6659 广播；`on_face_rollcall_cancelled(fields)` |

除表中的第一个位置参数外，可选参数按接口签名使用关键字传入。
新增审批回调 `on_chat_check_request(fields)` 只针对 `NeedCheck=1` 的 6612；已有 `on_chat(src, dst, text, msg_id)` 仍照常触发。

6529、6540、6542、6543、6558、6559 是发送型接口，返回 `None` 只表示调用完成，不能作为业务成功证据。
它们不注册成同步请求/响应配对，因为通知可能发给另一端、多个端或根本不返回。
6555、6556 保持同步请求/响应语义，非零 Result 抛出 `FmError`，原始返回仍可在统一事件中查看。

## 2. 关键使用条件

### 聊天审批

审批人需要 `CheckChat`，开关审批需要 `ConfigChatCheck`；普通发送者需要公聊权限且不能同时拥有审批权限。

`message` 必须来自真实收到的待审批 6612，包含 `SerialNumber/SrcUserID/DstUserID/Text`。SDK 保留原发送者、正文、颜色和字体等字段，不把审批人写成原发送者。Font 按 XML 属性编码，支持中文和 XML 保留字符。

当前本地主线 `LocalRoom.cpp::OnChatMsgCheckReq` 在拒绝分支直接返回，不广播 6629，也未完成与同意相同的待审批记录处理。SDK 能发送拒绝，但“发送后没有消息”不能证明拒绝业务已完成。2026-09-22 起，测试实际等待 6629；收到正确拒绝确认且未观察到正文投递时通过，超时则 FAIL，不再固定 BLOCKED。若产品要求拒绝也通知所有审批人并终结该审批，需要服务端补齐。

双端测试中普通发送者 B 声明 `ChatRecall`，因此获批后可以收到自己的 6612；没有该能力时服务器可能排除发送者，应增加第三个普通接收端观察。

### 远程音视频确认

确认链需要目标端 Conf 主版本至少为 4、调用者具备 `broadcast_others_audio/video` 权限、目标设备未打开，以及会议 `EnableForceOperateUserAV=0`。房控终端、强制模式、较老协议版本或已有申请状态可能走直接修改 6609 的分支。

新增测试默认仅在本次连接使用 Conf 5.0，不修改 JSON；会通过 6544/6644 检查测试结束后的会话可用性。音频 Conf 设备 ID 为 0，不能照抄新 XML 示例中的 1 或 HTTP 设备编号。视频需先通过 6508 上报有效设备。

`operation=0` 为请求广播，`operation=1` 为请求查看。下面是收到 6642 后由目标端显式决定的用法：

```python
mark = observer.events.mark()
operator.request_remote_av(observer.room_user_id, audio_ids=[0])
request = observer.events.wait_event(
    after=mark, source="conf", cmdid=6642, kind="notify", timeout=10
)
if request is None:
    raise AssertionError("未收到远程音频请求")
observer.respond_remote_av(request.fields, accept=True)
```

SDK 不自动代替目标端同意，也不启动麦克风或摄像头采集。通过确认信令及 6609 状态测试，不代表真实媒体流已经采集和播放。

### 交互语言

6540/6640 是交互语言设置，6641 是会场翻译配置，不能用已有 6571/6671 ASR 字幕设置结果替代。

本地服务器 `LocalRoom.cpp::OnUserSetInteractiveLanguage` 对应处理逻辑被注释，发送 6540 后静默不能算成功；需要核对部署版本是否启用该处理。测试 `language` 必须收到匹配用户和语言的真实 6640。
`room_languages` 从登录期事件开始收集 6641，以 End 判断列表完整性；未收到不是空列表成功。

### 人脸服务与点名

6555 仅请求服务路由，不会上传图像或执行识别；需有效视频设备及可用人脸服务。
6556 还需要会议数据库能创建点名记录；6558 需要真实数据库中的数字 FaceID，不是房间 UserID，也不是姓名。

新 XML 中 `6656 Result=10000` 的示例和“0 成功”的说明矛盾；SDK 按服务器实际 Result=0 成功处理。
`6558 FaceID=张三` 的示例不符合服务器整数字段读取方式；SDK 明确校验数字 ID。
点名结果信令通过也不能宣称摄像头识别算法已验收。

## 3. 手动验证命令

进入需要验证的包，例如：

```bash
cd /Users/areay/work/code/python/sessionmanager_py/sessionmanager_py_0922
# 验证另一版本时，进入 /Users/areay/work/code/python/sessionmanager_py/sessionmanager_py
```

确认 `examples/multi_client.json` 的两账号、Front 地址与 `examples/http_api.json` 的 HTTP 地址属于同一环境。脚本先验证两账号 Front 登录，再为每个用例单独创建临时会议并尝试关闭。失败不会修改固定会议配置；清理失败会明确报 FAIL。

先执行一项：

```bash
python3 examples/fm_interaction_protocol_test.py \
  --config examples/multi_client.json \
  --http-config examples/http_api.json \
  --case remote_audio_accept --timeout 10
```

逐项执行无需 FaceID 的用例：

```bash
for CASE in chat_approve chat_reject room_languages \
  remote_audio_accept remote_audio_reject \
  remote_video_accept remote_video_reject \
  face_recognition face_rollcall_start_cancel; do
  python3 examples/fm_interaction_protocol_test.py \
    --config examples/multi_client.json \
    --http-config examples/http_api.json \
    --case "$CASE" --timeout 10
done
```

语言代码必须是服务器支持的值，以下以 zh-CN 为例：

```bash
python3 examples/fm_interaction_protocol_test.py \
  --config examples/multi_client.json \
  --http-config examples/http_api.json \
  --case language --language zh-CN --timeout 10
```

完整点名需先将 FACE_ID 设置为真实人脸库中的数字 ID：

```bash
python3 examples/fm_interaction_protocol_test.py \
  --config examples/multi_client.json \
  --http-config examples/http_api.json \
  --case face_rollcall --face-id "${FACE_ID:?请设置真实人脸库ID}" --timeout 10
```

也可用 `--case all --language zh-CN --face-id "$FACE_ID"` 一次执行所有项目。聊天拒绝分支现在等待真实确认；当前部署静默时会报 FAIL。
退出码：0=全部实际通过；1=业务断言/请求/清理失败；2=前置环境或协议契约阻塞。脚本不会将缺通知、错误 Result 或无法清理处理成通过。

## 4. 本次验证记录

日期：2026-09-16。新增测试使用本地 socket 模拟服务验证报文、回调和测试断言，不作为真实服务器支持能力的证据。

| 验证项 | 结果 |
|---|---|
| 0914 版完整本地回归 | 327 passed，2 skipped |
| 原版完整本地回归 | 265 passed，2 skipped |
| 本次新增测试 | 每版 37 项通过；上方完整回归已包含这些测试 |
| 两包公开接口、报文编码和通知分发 | 已补齐；新增本地测试通过 |
| 接受/拒绝分支、目标用户和设备匹配 | 本地测试通过；缺失 6642/6643、错误目标等不会误判为 PASS |
| 中文、XML 保留字符、Font 属性 | 本地测试通过 |
| 服务端业务错误、清理失败、登录预检失败 | 保留错误，不报 PASS |
| 7.154 A7、A8 Front 登录 | 实际收到 30001 Result=6145 / 0x1801 UNAUTHORIZED |
| 新增协议真实业务链 | 被上述 Front 前置阻塞，本次未完成验收 |
| 人脸点名完整结果 | 还缺真实人脸库 FaceID，不使用伪造 ID 作为真机成功证据 |

两项跳过是当前 Python 环境缺少可选 AES/SM4 加密依赖，不属于本次新增协议测试。

当前本地服务器源码 `server/FrontService/UserRouteService.cpp::OnUserLoginReq` 在 `m_bLicense=false` 时返回 0x1801，应优先检查 7.154 的 License 授权及 Front 日志；这是源码定位线索，仍需以部署运行状态确认，不等于账号密码错误。

初轮在预检加入前创建了临时会议 1000000145～1000000154，关闭及后续会议查询均返回 HTTP 4014（room not exists or open）；没有拿到关闭成功确认。新增预检已在真机验证：登录失败直接退出，不再创建临时会议。

## 5. 文件入口

- [公开方法](../fm_client/ops/interaction_ops.py)
- [报文构造与参数校验](../fm_client/interaction.py)
- [独立测试脚本](../examples/fm_interaction_protocol_test.py)
- [本地回归测试](../tests/test_interaction_protocols.py)

本地回归命令：`python3 -B -m pytest -q`。当前包中的 `ConfProtocol.xml` 仍是原有基准；本补充文档覆盖新增 XML 提供的上述协议，不将原文中不合法的 XML 片段直接覆盖进去。
