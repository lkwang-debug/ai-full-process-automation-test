# 0922 SDK 合并与通知协议验收

本文件随 `sessionmanager_py_0922` 发包，记录 2026-09-22 的修复、实际验证边界及手动命令。
本轮只修改 0922 包；没有修改 0914、服务器源码、服务部署或配置文件。

## 本包已合入的更新

- 0914 后续新增的聊天审批、交互语言、远程音视频确认、人脸识别路由和点名接口，以及对应回调、测试和文档。
- `review_chat_message_and_wait()`、`set_interactive_language_and_wait()`、`NotificationTimeout`。超时保持失败，不虚构服务端确认。
- XML 中三处 `var` 改为 `val`；同步 mock 对 `val` 的读取。
- 原有 PaaS 被邀请入会与 Relay 代码已与 0914 相同；保留 0922 独有的 `6639` 产品能力通知、`conf_get_room_config(group_room_config=True)`。
- 修正合入单测对异步初始化消息的假设：旧审批不能计入新观察窗口，但新的 `6602` 入会通知可以正常出现。

## 三条通知的修复和验收结果

环境：使用本包 `examples/multi_client.json` 和 `examples/http_api.json`，服务器 `192.168.7.154`，Conf `2.0`，两个配置账号作为操作端 A、观察端 B。

| 项目 | SDK 状态 | 本轮真实结果 | 验收结论 |
|---|---|---|---|
| 6637 录制异常 | 登记为 Notify；顶层 Error 触发 `on_server_record_error()`；兼容 6637/ServerRecord.Error 和旧版 6636/ServerRecord.Error | 普通 `start_record()` 成功，A/B 收到 6610 ServerRecordStatus=2；10 秒没有 6637 | 接收缺陷已修复；实际异常通知尚未验证，BLOCKED |
| 6647 等候用户列表 | 登记为 Notify；新增 `on_waiting_user_list()` 和 `events.waiting_user_lists`；保留所有字段、单条/多条用户及结束页 | B 收到 6667 并建立等候室连接；A/B 主会连接及 A/B 等候室连接均没有 6647 | SDK 接收已补齐；当前场景未下发，BLOCKED |
| 6654 Web 通知：固定会议 | `send_room_message()` 显式支持 `app_back_notify`、`operator`；原有 Notify 回调保留 | HTTP code=0，B 收到匹配 UserID/operator/appBackNotify；中文、`<&>` 和引号正确；A 未收到该定向消息；A/B 健康 | PASS |
| 6654 Web 通知：动态会议 | 请求由相同公开方法发出 | HTTP 1021：Conference room does not exist，未到达通知等待步骤 | FAIL；当前部署的 HTTP 路径/会议类型支持问题，SDK 不能伪装成功 |

本地完整测试：355 passed、2 skipped。包括旧接口回归、新旧录制错误格式、合法操作权通知不误报录制异常、等候列表分页及 End、6654 请求字段与接收回调、临时固定会议清理及失败退出码。
本地模拟报文只证明 SDK 接收和测试逻辑，不代表服务器产生了这些通知。

本轮临时动态会议 `1000000211`（Web）、`1000000212`（录制）、`1000000213`（等候室）均已关闭。
Web 固定会议复测创建的 `10008` 已关闭并删除。未关闭或删除配置中的会议室 `10000`。

## 修复后的公开接收方式

```python
from fm_client import FmListener, conf as C

class Listener(FmListener):
    def on_server_record_error(self, error, fields):
        # fields 保留原始布局，包括 RecordType、Error、Description。
        print("record error", error, fields)

    def on_waiting_user_list(self, fields):
        # 每个分包回调一次；UserList.End 是结束标记，不是空数据成功的猜测。
        print("waiting users page", fields)

    def on_web_notify(self, fields):
        print("web notify", fields)

# FmTestClient 也可用统一事件观察；mark 应取在触发动作之前。
# event = client.events.wait_event(after=mark, source="conf",
#     cmdid=C.SERVERRECORDERRORNOTIFY, kind="notify", timeout=10)
```

没有增加虚构的 `6647` 查询命令，也没有把 `6637/6647/6654` 加入请求/响应配对。
`allow_waiting_users()` 仍是 `6570/6670` 审批，不是列表查询。
`6636` 按新协议描述为操作权占用状态，旧版嵌套 `ServerRecord.Error` 仍保持兼容。

## 手动逐项复测

进入你解压后的 0922 包根目录：

```bash
cd /Users/areay/work/code/python/sessionmanager_py/sessionmanager_py_0922
```

6654 定向通知（自动创建固定会议，测试后关闭并删除）：

```bash
python3 examples/fm_notification_protocol_test.py --case web_notify --timeout 10
```

6637 观察（自动创建动态会议；普通开始录制，不制造资源耗尽）：

```bash
python3 examples/fm_notification_protocol_test.py --case record_error --timeout 10
```

普通启动成功而没有异常时应为 BLOCKED，不能判定“录制错误回调失败”。服务端若提供独立会议的可控异常触发方式，可使用 `--record-action observe --timeout 60`，在脚本打印的临时会议中配合触发。SDK 不自动执行服务故障注入。

6647 列表（自动建立主会、管理等候室和被等候端的连接）：

```bash
python3 examples/fm_notification_protocol_test.py --case waiting_users --timeout 10
```

通过条件是某条真实连接收到包含 B 的 6647 用户列表及该列表的 End；只有 6667、6602 或 6670 不算通过。

复现动态会议的 Web 路径问题：

```bash
python3 examples/fm_notification_protocol_test.py --case web_notify --web-room-type dynamic --timeout 10
```

一次运行本次三项，以及本地回归：

```bash
python3 examples/fm_notification_protocol_test.py --case all --timeout 10
python3 -m pytest -q
```

配置参数可显式加 `--config examples/multi_client.json --http-config examples/http_api.json`。
默认使用配置中的 Conf 版本；`--protocol-version` 只覆盖本次测试连接。HTTP 与信令必须指向同一主机。
退出码：0=全部通过；1=至少一项失败或清理失败；2=没有失败，但至少一项仍阻塞。BLOCKED 不计入通过。

## 后续需要服务端确认

1. 6637：新 `RecordService/RoomMsgConstructor.cpp::EncodeRecordErrorNotify` 已实现顶层错误报文，`ConfService/LocalRoom.cpp::ProcessRecordErrorNotify` 可定向或广播。需要稳定、可控且局限于临时会议的真实错误触发条件。普通录制权限拒绝或重复操作可能只产生 6610。
2. 6647：提供实际部署分支中的发送入口、触发时机、接收连接及分页契约。本地当前主线未找到 6647 发送代码；仅 XML 定义不能证明已经部署。
3. 6654：固定会议链路已通。若要求支持动态会议，需要修复 HTTP `/v1/room/sendMsg` 对动态会议的查询/路由，或明确该接口只支持固定会议。SDK 不自动转换会议类型、不吞掉 1021。
