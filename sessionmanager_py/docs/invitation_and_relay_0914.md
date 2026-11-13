# 0914：被邀请端在线入会与 Relay

更新日期：2026-09-14。代码、测试脚本及本文均位于 `sessionmanager_py_0914` 包内。

## 修复结论

最初提出的两个 SDK 公开能力缺口，均已修复，并在 **192.168.7.154、Conf 2.0、
A7/A8** 环境中完成对应的真实闭环验证，可以按下表验收范围关闭原缺陷。

| 原问题 | 已完成的修复 | 真实验证结果 | 结论 |
| --- | --- | --- | --- |
| 可以发送后台邀请，但没有“被邀请端在线、接收邀请并自动入会”的实现 | 新增 `PaasOnlineClient`、接收端 demo 和一键配置/测试脚本；通过独立 PaaS 连接接收 `14002`，取通知中的邀请码，提交 `14003` 并完成 Front/Conf 入会 | 最新用户复测：`invite_users` 在会议 `1000000143` 通过，`invite_contacts` 在会议 `1000000144` 通过；两次均收到真实邀请并进入目标会议，清理成功；`cases=2, failures=[]` | 已修复，两个邀请入口均通过 |
| Relay 服务端已支持转发，但 SDK 缺少有效的公开上行方法 | 新增 `send_relay_message()`，由 SDK 构造外层 `6700`、来源/目标和内层 msg，支持字段树及 XML 转义 | 此前 2026-09-14 真机验证：A→B 内层 `6718`，B→A 内层 `6719`；来源/目标、字段、中文及 `<&>"` 正确，A/B 后续能力查询通过；用户复测会议 `1000000137` 已清理 | 已修复，最小定向往返闭环通过 |

配套的 `paas_online.json` 空文件导致 `JSONDecodeError` 也已修复：
不存在或空白的输出文件可直接重新生成，输入配置错误会指出具体文件及行、列。
本次用户日志中，配置生成和 PaaS 登录也再次通过。

这里的“两项已修复”针对以上具体缺口。邀请的两个 HTTP 入口已做真实入会验证；
Relay 的真机验收覆盖定向往返，不将该结果扩大为所有内层业务、广播、批量目标、
长期稳定性或其他服务器版本都已验证。邀请接收端的自动断线重连、运行中 token 刷新
不在本次实现范围内。最新邀请证据来自本次用户提供的日志；Relay 证据来自此前真机日志，
本次文档更新未重新发起服务器测试。

## 两项问题的直接复测命令

进入项目目录；其他机器解压后进入同名包目录即可：

```bash
cd /Users/areay/work/code/python/sessionmanager_py/sessionmanager_py_0914
```

先测试接收邀请并入会。当前本机配置已经准备好，这条命令会自动刷新 PaaS 配置，
再分别测试 `invite_users` 和 `invite_contacts`，无需先运行 `--generate-only`：

```bash
python3 examples/fm_invited_join_auto.py \
  --invite-method both --timeout 10 --wait 30
```

预期两次出现 `[RECEIVED] 14002`、`[PASS] invited_join` 和对应的
`[CLEANUP] temporary_room=... closed`，最终为 `summary: cases=2, failures=[]`。
每次运行的会议号、seq_id 会变化，不应硬编码为本次记录中的数值。

再单独测试 Relay：

```bash
python3 examples/fm_extended_protocol_test.py \
  --config examples/multi_client.json \
  --http-config examples/http_api.json \
  --case relay --timeout 10
```

预期 `[PASS] relay`，输出中 `B_received.msg.id=6718`、`A_received.msg.id=6719`，
随后临时会议关闭，最终为 `summary: cases=1, failures=[]`。
Relay 使用已入会的 Conf 连接，不需要 `paas_online.json` 或 PaaS 在线服务登录。

| 验收项 | 必须满足的条件 |
| --- | --- |
| 邀请前在线 | B 收到 PaaS `10001 result=0`，尚未加入 Conf；仅 `front_authenticate()` 不足以接收邀请 |
| 邀请真实到达 | B 收到 `14002`，通知的邀请码与本次创建的会议一致；HTTP 返回成功本身不能判通过 |
| 接受并入会 | B 发送 `14003` 后完成 `30005/6601`，进入正确会议；自动用例要求 A 收到 B 的 `6602` |
| 入会后可用 | B 的能力查询成功且 PaaS 保持在线；`[PASS] invited_join` 之前已检查这些条件 |
| Relay 定向投递 | B 收到 A 的 `6700/msg 6718`，来源、目标及嵌套/重复字段正确；定向消息在检查窗口内不回显给 A |
| Relay 回包及会话 | B 通过公开方法发送 `6700/msg 6719`，A 收到正确来源/目标、Probe 和 Result；A/B 后续能力查询成功 |
| 清理及总结果 | 所创建的临时会议关闭成功，脚本退出码为 0，最终 failures 为空；清理失败同样不能判通过 |

`--generate-only` 的成功只代表“配置生成及在线登录”通过，日志中的
`invitation was not tested` 是正常说明；完整邀请结论以 `--invite-method both` 为准。

## 验证状态

| 项目 | 状态 |
| --- | --- |
| Relay 公开发送 | 已实现 `send_relay_message()`；SDK 构造外层 6700 和内层 msg，测试侧不拼 XML |
| Relay 真机定向往返 | 2026-09-14 在 192.168.7.154、Conf 2.0 通过：A7→A8 内层 6718，A8→A7 内层 6719；嵌套字段、中文及 `<&>"` 正确；A/B 后续能力查询正常；开发复测会议 1000000135、用户复测会议 1000000137 均已关闭 |
| 接收邀请 → 接受 → 入会 | 已实现 `PaasOnlineClient` 和最小 demo。本地测试覆盖 PaaS 脚本化响应、真实本地 Front/Conf TCP 入会，以及邀请丢失时不能通过 |
| PaaS 真实在线登录 | 2026-09-14 在 192.168.7.154 通过：A8（1000000008）收到 BASE 10001 result=0，尚未加入 Conf 即可保持在线 |
| 真实 invite_users 邀请入会 | 最新用户复测：B 收到 GS 14002（seq_id=8），提交 14003 后完成 30005/6601，room_user_id=1000000008；自动用例中 A 的 6602、B 能力查询及 PaaS 在线检查通过；临时会议 1000000143 已关闭 |
| 真实 invite_contacts 邀请入会 | 最新用户复测：B 收到 GS 14002（seq_id=9），接受并进入正确会议；同样通过入会通知及后续状态检查；临时会议 1000000144 已关闭 |
| 一键生成并测试 | 2026-09-14 在 192.168.7.154 运行 `fm_invited_join_auto.py --invite-method both`：重新获取 A8 凭据和 CP 地址，验证在线并生成配置；invite_users / invite_contacts 均通过，`summary: cases=2, failures=[]`；临时会议 1000000141 / 1000000142 均已关闭 |
| 空输出文件重新生成 | 2026-09-14 复现 `paas_online.json` 为 0 字节时的 JSONDecodeError；修复后在 192.168.7.154 执行 `--generate-only`，重新生成 A8 配置并收到 `10001 result=0`；本次未发起邀请 |
| 最新用户一键复测 | `--generate-only` 获得 A8 新凭据和 CP 地址并登录成功；随后 `--invite-method both` 返回 `summary: cases=2, failures=[]`，会议 1000000143 / 1000000144 均已清理 |

引入一键配置脚本前的本地全量记录：`python3 -B -m pytest -q` → `268 passed, 2 skipped`。
两项跳过为当前解释器未安装可选 AES/SM4 依赖。

新增一键脚本后执行 `python3 -B -m pytest -q tests/test_online_setup.py tests/test_online.py`
→ 最新 `43 passed`。覆盖按 Front 身份获取凭据、配置中心和 CP HTTP 链路、设备标识复用、
认证/配置错误、失败时保留旧配置、生成/手动/双入口模式及测试失败退出码；
另覆盖空输出重新生成、空输入拒绝、JSON 错误文件路径及行列提示。

## front_authenticate() 是否足够

不够。真实终端通过独立的 PaaS/FSP 在线连接接收邀请。
`front_authenticate()` 只完成 Front 用户认证，不登录 PaaS。
`connect_client(conf_login=False)` 还会执行 Front LOGINROOM，也不是被邀请端在线接口。

真实客户端 `client_src/MeetingFrontFrame` 的流程：

1. `WFQueryPaasAuthInfo.cpp` 获取用户 PaaS appId/token；`MeetingFrontDataContainer.cpp`
   准备设备标识、终端互斥分类等参数。
2. `WFQueryPaasAccess.cpp` 查询 Client Proxy；`WFLoginPaas.cpp` 调用
   `GetPaasOnline()->Login(...)`，等待 `OnOnline()`。
3. `MeetingFrontModel.cpp` 注册 `GetUserOnline()->AddNotify(...)`，在
   `OnInviteIncome(inviterUserId, inviteId, InviteData)` 收到邀请。
4. `AcceptInvite(..., true)` 提交接受，再通过 `InviteData.inviteCode` 启动入会。

新的 Python 接收端直接实现服务端 Client Proxy JSON 协议，不依赖原生 DLL。

| 阶段 | 连接/协议 | 关键数据 |
| --- | --- | --- |
| 在线登录 | Client Proxy，session app=1；`BASE 10000/10001` | app_id、token、user_id、client_guid、protocol_version、mutex_type、os_type |
| 收到邀请 | 同一 PaaS 连接，`GS 14002` | seq_id、邀请者 user_id、group_id、extend_info |
| 解析目标 | extend_info 为 JSON 字符串或对象 | method=InviteUser，params.inviteCode 为九位邀请码；group_id 不是 Front RoomID |
| 接受/拒绝 | `GS 14003`，原 seq_id/group_id；operate=0/1 | 无独立业务 Response，发送成功不代表入会成功 |
| 取消/拆线 | `GS 14008/14004` | 已取消邀请不可再接受；重复通知不重复入会 |
| Front 进入目标 | 已认证 Front，`30004/30005` | 把通知的邀请码作为 RoomID，获得 Token、RoomUserID、RealRoomID、Conf 地址 |
| Conf 入会 | `6501/6601`，再 `6517` | B 成功入会；自动双端用例还要求 A 收到 B 的 6602 |

PaaS 直接发送 UTF-8 JSON，没有 Conf 的 `XmlMsgIOPack` 压缩前缀。
连接心跳由 `TcpReliableSession` 处理；最小实现断线后明确失败，需要重新登录，
不自动刷新 token 或抢占在线终端。通知线程只负责收包/排队，入会在调用线程执行。

协议依据：`fsp_sss_stream/cpp/client_proxy/` 中的 `base_processor.cpp`、
`gcs_processor.cpp`、`session_msg_processor.cpp`、`context_def.h`，以及
`4u_fmserver/server/ConfService/AsyncReqMgr.cpp` 的 InviteUser 数据构造。

## 一键生成配置并测试（推荐）

当前本机 7.154 环境已准备好服务凭据配置，直接在 `sessionmanager_py_0914` 执行：

```bash
python3 examples/fm_invited_join_auto.py
```

脚本默认使用自己所在目录下的 `multi_client.json`、`http_api.json`、
`paas_bootstrap.json`，生成或更新同目录下的 `paas_online.json`，然后运行
`invite_users` 邀请入会闭环。默认被邀请端为 `accounts[1]`，操作端为 `accounts[0]`。
它不依赖开发机的 C++ 源码目录，打包后的 SDK 也可使用。

同时测试两种邀请入口：

```bash
python3 examples/fm_invited_join_auto.py \
  --invite-method both --timeout 10 --wait 30
```

执行顺序：

1. 用所选账号登录 Front，获取真实 `LocalUserID`，随即注销 Front；不预先入会。
2. 从 `http_api.json` 的 `base_url` 取得配置中心的协议、主机和端口，
   调用 PaaS 认证接口获取该用户的新 `appId/token` 及终端互斥分类。
3. 查询 `FSP_ACCESS`，再解析真实 CP TCP 地址。
4. 验证 PaaS 登录 `10001 result=0`，成功后才写入配置文件；同账号、同 appId
   保留原有设备标识，默认 `force_login=0`。获取或登录失败保留原配置。
5. 创建临时会议、发起邀请、等待 B 的真实 `14002`，接受后完成 Front/Conf 入会，
   检查 A 的 `6602`、B 的能力查询和在线状态，最后退出并关闭临时会议。

输出为 `[IDENTITY] → [DISCOVERED] → [CHECK] → [CONFIG] → [TEST] →
[ONLINE] → [RECEIVED] → [PASS] invited_join → [CLEANUP]`。
两种入口都通过时，末行是 `summary: cases=2, failures=[]`。
配置生成成功不代表邀请通过；邀请或清理失败都会以非零退出码结束。

仅生成配置并验证在线登录，不发邀请、不创建会议：

```bash
python3 examples/fm_invited_join_auto.py --generate-only
```

`paas_online.json` 是生成结果，不需要提前创建或填入内容；文件不存在、为空或仅有空白
都可以直接生成。空文件没有可复用的设备标识，会生成新的 `client_guid`。
仍需确保三个输入文件 `multi_client.json`、`http_api.json`、`paas_bootstrap.json`
是有效的 JSON 对象。输入文件损坏，或非空输出文件存在 JSON 语法错误时，脚本会
明确提示绝对路径及错误行、列；不会打印密码或 token，也不会覆盖该文件。

自动生成配置，然后等待你在后台手动邀请 A8：

```bash
python3 examples/fm_invited_join_auto.py --manual --wait 60 --stay 30
```

显式指定配置文件或被邀请账号：

```bash
python3 examples/fm_invited_join_auto.py \
  --config examples/multi_client.json \
  --http-config examples/http_api.json \
  --bootstrap-config examples/paas_bootstrap.json \
  --online-config examples/paas_online.json \
  --account-index 1 --invite-method both --timeout 10 --wait 30
```

`paas_bootstrap.json` 是一次性准备的**部署服务凭据**，与自动生成的用户 token 不同：

| 字段 | 用途 |
| --- | --- |
| client_id、client_secret | PaaS `paasAuthInfo` 接口的 Basic 认证；不是 Web HTTP API 的 app_key/app_secret |
| config_center_key | 配置中心查询 FSP_ACCESS 的 appkey |
| config_center_version | 配置中心查询版本，默认 `03.15.02.03` |
| protocol_version | PaaS 登录协议，默认 `1.0.0`，与 Conf 2.0 独立 |
| config_center_url（可选） | 配置中心独立部署或存在路径前缀时设置；默认使用 HTTP 配置的协议、主机和端口 |
| terminal_type、os_type（可选） | 覆盖模拟端类型；默认 macOS 使用 MAC 分类，其他桌面系统使用 PC，从认证响应取互斥值 |

换环境时同步修改 `multi_client.json` 和 `http_api.json`，两者主机必须一致；
服务凭据若不同，也更新 `paas_bootstrap.json`。脚本每次都按新环境和新账号获取参数，
不会拿原 A8 的 token 冒充其他用户。PaaS 在线失败时不会通过降低协议版本或强制抢占来重试。

分发到新的测试机器时，保留 `paas_bootstrap.example.json` 和
`paas_online.example.json` 模板；由该部署维护方提供服务凭据并另存为
`paas_bootstrap.json`，之后只需运行一键命令。当前本机这份已填好，无需重复填写。
真实 `paas_bootstrap.json` 和 `paas_online.json` 均不应进入通用分发包；
`.gitignore` 不会自动让 `tar` 排除它们，打包时需显式排除。

## PaaS 配置（手动方式）

复制 `examples/paas_online.example.json` 为本机 `examples/paas_online.json`，填写
**被邀请端**真实参数。占位值不能用于登录。

2026-09-14 已为当前本机的 `examples/paas_online.json` 填入 **7.154 / A8** 的真实配置，
可直接运行下方命令，无需再复制模板覆盖。凭据来自该环境正式
`/meeting-resource-server/open/paasAuthInfo?userId=1000000008` 认证接口；
CP 地址由 `/fmapi/servers/addresses` 查询 FSP_ACCESS，再调用返回的接入地址获得，
实际为 `TCP:192.168.7.154:1089;TCP:192.168.7.154:28101;`。
PaaS 协议为 `1.0.0`，互斥分类为认证结果 `exclusionGroup.MAC=1`，
`os_type=macOS`，`force_login=0`，使用本机稳定设备标识。
这份组合已通过上述真实登录及两种邀请入会测试。

本机真实配置已加入 `.gitignore`，文件权限为仅当前用户读写。
`.gitignore` 不会阻止 `tar` 打包：分发通用 SDK 时请排除 `examples/paas_online.json`，
保留 `examples/paas_online.example.json`。换环境、换账号或凭据失效时需重新获取，
当前接收端不会在运行中自动刷新 PaaS token；重新运行一键脚本会重新获取。

| 配置 | 来源 |
| --- | --- |
| cp_address | `TCP:实际CP地址:实际端口;`，来自终端 RequestCPAddress 结果；不是 Front 的地址，也不是 FSP_ACCESS HTTP URL |
| app_id、token | 对应用户当前有效的 PaaS appId/token；不是 HTTP app_key/app_secret，也不是 Front 会议 Token |
| user_id | 被邀请账号的平台 ID，必须对应 Front LocalUserID |
| client_guid | 模拟端稳定设备标识，对应终端 deviceId |
| protocol_version | 实际终端的 PaaS 登录版本，与 Conf 2.0 独立，不要为了绕过登录随意降级 |
| mutex_type | 参考认证结果 terminalInfo 中对应 PC 的值及实际终端登录报文 |
| os_type | 实际终端登录参数中的值 |
| registered_user、force_login | 注册账号为 1；force_login 默认 0，不自动抢占已在线真实终端 |

部署若要求 company_id、domain、service_id、service_type、sdk_type、media_source、
extend_info，也可按终端实际登录参数配置。`PaasOnlineClient` 本身只使用已有配置；
一键脚本通过正式认证/配置中心接口获取 token 和地址，不自行签发 token。
不要将真实 token 提交到仓库或打印到测试报告。

## 接收端最小代码

```python
from fm_client import FmTestClient, PaasOnlineClient
from fm_client.testing import load_config

cfg = load_config("examples/multi_client.json")
account = cfg["accounts"][1]
b = FmTestClient(cfg["host"], cfg.get("port", 1089), conf_protocol_version="2.0")
online = PaasOnlineClient.from_config("examples/paas_online.json", timeout=10)
try:
    b.front_authenticate(account["username"], account["password"])
    online.login()  # 等待 10001 result=0，此时尚未入会
    invitation = online.wait_invitation(timeout=60)
    online.accept_and_join(invitation, b,
        room_node_id=cfg.get("room_node_id"), user_name=account["username"], timeout=10)
    print("已入会", b.real_room_id or b.room_id, b.room_user_id)
finally:
    online.close()
    b.close()
```

目标只取真实邀请的 inviteCode，不从配置 room_id 推测。若用户拒绝，使用
`online.respond_invitation(invitation, accept=False)`。

在 **0914 项目目录**单独启动接收端：

```bash
python3 examples/fm_invited_join_demo.py \
  --config examples/multi_client.json \
  --online-config examples/paas_online.json \
  --account-index 1 --timeout 10 --wait 60 --stay 30
```

看到 `[ONLINE]` 后在后台邀请该用户。预期为
`[ONLINE] → [RECEIVED] 14002 → [PASS] invited_join`。
此模式只退出模拟端，不关闭后台会议；`--stay` 为成功后继续留会秒数。

自动双端复测，分别执行两条；A=accounts[0]，B=accounts[1]。每次创建并清理临时会议：

```bash
python3 examples/fm_invited_join_demo.py \
  --config examples/multi_client.json \
  --online-config examples/paas_online.json \
  --http-config examples/http_api.json \
  --invite-method invite_users --timeout 10 --wait 30

python3 examples/fm_invited_join_demo.py \
  --config examples/multi_client.json \
  --online-config examples/paas_online.json \
  --http-config examples/http_api.json \
  --invite-method invite_contacts --timeout 10 --wait 30
```

通过条件：HTTP 成功且无失败用户；B 收到真实 14002；邀请码属于本次会议；
B 提交 14003，完成 30005/6601；A 收到 B 的 6602；B 能查询能力且 PaaS 在线；
临时会议清理成功。任一步失败都不能仅凭 HTTP 成功判通过。

## Relay 公开方法

```python
a.send_relay_message(b.room_user_id, [
    {"id": 6718, "fields": {"Text": "中文<&>", "Item": [{"ID": 1}, {"ID": 2}]}},
])
b.send_relay_message(a.room_user_id, [{"id": 6719, "fields": {"Result": 0}}])
```

- SrcUserID 自动取当前 Conf room_user_id。messages 必须是非空 `{id, fields}` 列表，
  fields 可省略；id 是 `<msg id="...">` 属性。
- fields 沿用字段树：字典嵌套、列表重复、嵌套元素属性用 `@属性名`；SDK 转义 XML。
  接收后的标量通常为字符串，事件 raw_xml 保存原报文。
- dst_user_id 为主目标；dst_user_ids 为额外目标，构造成 DstUserList，自动去重。
- dst_user_id=0 广播，服务端排除源用户及内部辅助端；不能同时指定额外目标。
- 上下行都为 6700，没有 6800 Response。内层应答由目标模拟端主动发送。
- 保留 `on_relay_message(messages, fields)` 及 `events.wait_event(cmdid=6700)`。

```bash
python3 examples/fm_extended_protocol_test.py \
  --config examples/multi_client.json \
  --http-config examples/http_api.json \
  --case relay --timeout 10
```

用例验证 A→B 6718、B→A 6719 的来源/目标、内层 ID/字段和定向不回显，保留
A/B 后续会话健康断言；清理失败也算失败。此前自定义数据用例的验收范围不变。
