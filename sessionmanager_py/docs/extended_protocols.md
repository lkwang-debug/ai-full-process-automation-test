# 扩展 Conf 协议封装与真实环境回归

0914 新增 `relay` 用例及独立 PaaS 邀请接收端，参见
[邀请与 Relay 使用说明](invitation_and_relay_0914.md)。Relay 已在 7.154 真机通过；
邀请接收端仍需 PaaS 配置才能完成真机验证。下面保留 9 月 7 日的历史结果。

更新日期：2026-09-07。

本轮按 `4u_fmserver` 的 `ConfMsgProcessor.cpp`、`LocalRoom.cpp`、`Room.cpp` 实现补齐客户端公开 API 和测试，不修改服务器代码。按最新验收约定，自定义数据用例只检查功能投递与资源清理，不再执行后续会话健康检查；其他用例保留原检查。不把缺失的必要响应或通知判为成功。

## 验收标准（2026-09-07 更新）

| 验收项 | 通过条件 | 失败条件 |
| --- | --- | --- |
| 公共数据投递 | A 发 6572 后，A/B 均收到本次请求对应的 6672；SrcUserID、UserID、PublicData 正确且两端一致 | 任一必需接收端未收到，或字段、内容不符 |
| XML 保留字符还原 | A/B 的 PublicData 均正确还原原始 `<&>` 等内容 | 任一端内容损坏、重复转义或截断 |
| 私有数据投递 | 目标 B 收到本次 6672 PrivateData，内容正确；非目标 A 不收到私有通知 | 目标未收到、内容错误或发给非目标端。发送端已断线时，无法充分验证在线非目标端隔离性，需注明验证限制 |
| 会话健康 | 不纳入 `custom_data`、`custom_private` 用例，不执行后续能力查询 | 不因后续会话状态单独将已完成的数据投递判为失败；其他用例的健康检查不变 |
| 资源清理（独立项） | 客户端已关闭，对应临时会议关闭成功 | 清理失败或未确认 |

`custom_data` 只发送公共数据，要求 A/B 均收到；`custom_private` 只发送私有数据，要求 B 收到且 A 不收到该私有通知。两者分别运行，不在公共用例的同一会话继续发送私有数据。投递断言及资源清理均通过即视为本次数据用例通过；不额外检查是否还能查询用户能力。通知缺失、内容不符、私有通知发给非目标端和清理失败仍会失败。

取消会话健康检查是验收范围调整，不表示服务器断线问题已经修复，也不表示公共与私有数据同包发送已经通过。

用户提供的临时会议 `1000000044` 日志按此标准为：公共数据投递通过、`<&>` 还原通过、清理通过；A 的 `disconnects=['conf']` 和后续查询异常属于独立的会话健康失败。历史上 A 未收到 6672 的其他执行，仍应记录公共数据投递失败，不能用这一次成功覆盖。

`fm_extended_protocol_test.py` 已移除自定义数据函数内部及外层运行器的会话健康断言。`custom_private` 满足投递条件后可打印 `[PASS]`，不再出现 `private notify received, but health check failed`。修改前的真实结果保留在 [2026-09-07 全量回归记录](regression_acceptance_20260907.md)，其中历史 `[FAIL]` 不代表当前脚本仍采用相同口径。

修改后已在 `192.168.7.154`、Conf `2.0` 单独复测 `custom_private`：B 收到正确私有数据，输出 `[PASS] custom_private`、`failures=[]`、退出码 0，临时会议 `1000000111` 关闭成功。本地全量为 `228 passed, 2 skipped`。没有检查或宣称修复数据送达后的会话状态。

## 历史验证结论

真实环境为 `192.168.7.154:1089`，Conf 协议 `2.0`；HTTP 使用同服务器 `28080` 端口。账号使用配置文件中的 A7/A8，测试期间没有修改这两个配置文件。

10 类能力中，9 类完成本轮真实信令闭环；`6572/6672` 的封装已补齐，但当前服务器处理后会关闭操作端 Conf，会话健康检查失败，不能宣称全部修复。每个真机用例创建独立即时会议，结束后通过 HTTP 关闭；不在配置中的常驻 `10000` 会议上修改共享、分组或等候室状态。

最终整轮为 11 个用例、9 个通过、2 个失败（公开和私有自定义数据分别验证）；该轮临时会议 `1000000022` 至 `1000000032` 均已关闭。本地全量结果为 `220 passed, 2 skipped`，两项跳过分别因为未安装可选的 AES/SM4 依赖。

| 能力 | 实际投递契约 | 本次真实结果 | 用例名 |
| --- | --- | --- | --- |
| 6538/6638 多人数据共享 | 发起端收到结果；实际改变模式时向其他端广播；幂等设置只回复发起端 | 开启返回 `Result=0`，B 收到 `EnableMultiDataSharer=1`；重复开启有回复、B 无重复通知；已关闭恢复 | `multi_share` |
| 6544/6644、6545/6645 用户能力 | 单用户查询仅回请求端；按能力查询/订阅有分页和 `End`；取消订阅无回复 | 正确查询到 B 的能力及 NewVote 的 A/B 用户列表；订阅初始结果、取消订阅通过 | `user_features` |
| 6509/6609 扩展字段 | 按字段更新状态；一般为状态广播；语音私聊有申请和接受两个阶段 | `AudioOutMute`、`WBMarkState`、完整 `Info`、`PrivateTalk` 申请/接受均通过，A/B 收到对应状态；测试后恢复 | `extended_state` |
| 6509 -> 6646 -> 6546 修改自身状态 | A 请求 B 开启 VNC；仅 B 收到 6646；B 执行后回 6546，6546 无直接回复 | B 收到请求；拒绝上报保持会话可用；成功分支由 B 申请操作权并上报自身 6509，A/B 收到 `VNCState=2`；已关闭共享 | `self_state` |
| 6560/6660 能力重协商 | 请求端提交屏幕数和布局编码；6660 仅回请求端，可能另有布局推送 | A 返回 `Layout.Code=W1,WV4,WV9`，B 无 6660 | `renegotiate` |
| 6561/6661 H5 投票 | 广播默认排除发送端，也可指定目标；结束沿用服务器保存的接收端列表 | 整数投票 ID 的广播、定向开始和结束均通过；B 收到 6661，A 无对应广播回显 | `h5_vote` |
| 6565/6665/6666 分组配置 | 修改已有分组仅回请求端；6666 是独立的管理端成员状态通知 | 获得操作权 11 后创建、改名、修改已分配成员权限通过；A 收到真实 6666；删除分组通过 | `group_config` |
| 6667/6570/6670 等候室 | 6667 是入会等待路由；管理端在等候室子会话发 6570；6670 含逐用户 Result | 主会场 B 登录保持挂起；A/B 分别建立等候室连接；审批返回用户 `Result=0`，随后 B 主会场收到 6601 成功 | `waiting_room` |
| 6571/6671 语音字幕 | 订阅设置和翻译设置仅回请求端；不是字幕识别结果消息 | 开启字幕、开启英文翻译、关闭均返回成功；B 无 6671 | `asr_subtitle` |
| 6572/6672 自定义数据 | PublicData 广播给全体；PrivateData 仅发目标端；无独立请求回复 | 公共数据：A/B 都收到的执行按新口径记功能通过；A 未收到的执行仍记投递失败。私有数据 B 可收到，A 随后断线。会话健康失败独立保留 | `custom_data`、`custom_private` |

此处“通过”仅覆盖表中描述的信令流程。未验证真实屏幕捕获和媒体传输、H5 页面显示/用户实际投票、音频识别和字幕内容。等候室本次真机用例验证允许入会；拒绝、多用户混合审批及断网恢复不应被这次通过结果自动覆盖。能力查询多页、后续订阅更新及错配通知过滤由本地测试覆盖，本轮真机只出现一页。

## 手动复测

在项目目录执行：

```bash
cd /Users/areay/work/code/python/sessionmanager_py/sessionmanager_py

python3 examples/fm_extended_protocol_test.py \
  --config examples/multi_client.json \
  --http-config examples/http_api.json \
  --case all \
  --timeout 10
```

当前版本中，`custom_data` 和 `custom_private` 按上述投递条件判定 `[PASS]`/`[FAIL]`，不检查后续会话健康。公共数据若因 A 提前断线而缺少 A 的 6672，仍判投递失败。输出 `[CLEANUP]` 表示对应临时会议已关闭；清理失败仍使进程退出码为 1。

逐项执行时，把 `--case all` 替换为上表的用例名。例如只验证目标端自行处理状态：

```bash
python3 examples/fm_extended_protocol_test.py \
  --config examples/multi_client.json \
  --http-config examples/http_api.json \
  --case self_state \
  --timeout 10
```

单独验收私有数据投递（不检查后续会话健康）：

```bash
python3 examples/fm_extended_protocol_test.py \
  --config examples/multi_client.json \
  --http-config examples/http_api.json \
  --case custom_private \
  --timeout 10
```

不要同时运行多份这些测试：复用相同 A7/A8 身份可能触发账号/会话互踢。HTTP 配置与信令配置的 host 必须一致，脚本在不一致时拒绝建会。测试账号需要相应的创建会议、共享、分组和等候室管理权限；权限不足仍会失败，不会被跳过或忽略。

## 公开 API

以下方法同时适用于 `FmClient` 和 `FmTestClient`：

| 方法 | 说明 |
| --- | --- |
| `set_multi_data_sharer(enabled, force=False, timeout=None)` | 显式发送 Force，避免服务器缺省值带来的强制切换；返回 6638 字段 |
| `get_user_features(user_id=None, timeout=None)` | 默认查自己，返回 UserID/SupportFeatures；不存在的用户在当前服务器可能静默超时 |
| `query_feature_users(feature, subscribe=False, timeout=None)` | 返回 Feature、归一化 UserIDs 和原始 Pages；等待明确的 End 标志 |
| `unsubscribe_feature_users(feature)` | 发送 Type=2 后返回，不等待 6645 |
| `set_user_state(user_id=None, **state)` | 扩展 6509；默认修改自己，字段用下面说明中的 Python 参数名 |
| `reply_self_state_change(dst_user_id, vnc_state=..., error=None)` | 回 6546；dst_user_id 是 6646 的 SrcUserID；无回复、无自动同意或媒体操作 |
| `renegotiate_layout(layouts, screen_count=1, timeout=None)` | layouts 为逗号字符串或编码列表；返回 6660 的 Layout.Code |
| `send_h5_vote(vote_id, state=2, duration=1, user_ids=None, data=None)` | ID 为 0 到 2147483647 的整数；duration 单位分钟；state=2 开始、0 结束 |
| `modify_group_config(rooms, timeout=None)` | `rooms=[{"RoomID": ..., "Theme": ..., "User": [{"ID": ..., "Right": ...}]}]`；修改已有分组和已分配成员 |
| `change_group_state(room_ids, state, timeout=None)` | 新分组状态：0 停止/待开始、1 开始、2 删除；原 end_groups() 保持兼容 |
| `connect_related_room(room_info, timeout=None, **login_options)` | 根据 6624/6667 路由返回独立 Conf 客户端，不注销主会场；调用者负责关闭子客户端 |
| `allow_waiting_users(users, timeout=None)` | 在管理端等候室连接调用；`users=[{"UserID": ..., "Allow": 0或1}]`；逐项检查返回 UserInfo.Result |
| `set_asr_subtitle(enabled, translation=False, src_language=None, target_language=None, timeout=None)` | 开启翻译必须提供目标语言；成功仅表示服务器接受订阅设置 |
| `set_user_custom_data(user_id=None, public_data=None, private_data=None)` | 值为字符串，JSON 使用 json.dumps；空字符串清空，None 不修改；当前真机仍有服务端断线风险 |

这些方法的可选参数遵守函数签名，示例中的具名选项按关键字传入。

### 登录能力声明

`conf_login()` 新增 `support_features`、`screen_count`、`layouts`、`join_type`、`client_type`、`terminal_type`、`device_id` 参数。普通登录不默认强行开启全部新能力，避免改变旧用例的分组/等候室行为。

配置可添加如下可选段，账号内的同名段可覆盖全局选项：

```json
{
  "conf_login_options": {
    "support_features": ["AbilityRenegotiation", "NewVote", "GroupMeeting", "MoveInWaitingRoom", "VNCRemote"],
    "screen_count": 1,
    "layouts": ["W1", "WV4", "WV9"]
  }
}
```

扩展回归脚本在内存中设置这些能力，不写回配置文件。已有 `fm_single_case.py --method ...` 可以直接调用新增公开方法；等候室、多步确认等复杂流程请使用扩展回归脚本。

登录 6501 的 Layout 是顶层逗号字符串，而 6560 的 Layout 是包含 ScreenCount 和 Code 的容器。`W1/WV4/WV9` 是布局能力编码，不能拿分屏数或 VideoRelayID 替代。

### 6509 扩展字段与 6546 确认

新增的 Python 参数包括：`private_talk`、`audio_out_mute`、`info`、`wb_mark_state`、`enable_chat`、`group_state`、`online_state`、`record_state`、`speaker_state`、`camera_state`、`microphone_state`、`local_vp_state`。原 audio/video/data_state/manager_state/media_share_state/vnc_state 等字段保持兼容。

Info 是完整资料而非局部补丁。应保留 `Sex/SeatList/DisplayName/Tel/Mobile/EMail` 后再修改；不能只传 DisplayName，导致其他字段被清空。测试会先保存快照并在结束时恢复。

语音私聊方向示例：

```python
# A 向 B 申请，服务器将双方状态置为 WAITING=1。
a.set_user_state(b.room_user_id,
    private_talk={"DstUserID": a.room_user_id, "State": 2})

# B 接受后，服务器将双方状态置为 DONE=2。
b.set_user_state(private_talk={"DstUserID": a.room_user_id, "State": 2})

# B 结束或拒绝。
b.set_user_state(private_talk={"DstUserID": a.room_user_id, "State": 0})
```

远程 VNC 流程必须由目标端显式参与：A 发 6509 请求 B 开启，B 收 6646；B 完成本地操作、申请屏幕共享操作权 4 并上报自己的 6509 状态，最后发送 6546 处理结果。服务器当前 6546 处理仅记录结果，不再产生“6546 的 6646 回复”，也不会替 B 启动媒体或提交 VNC 状态。

### 接收与分页

新增回调为 `on_multi_data_sharer`、`on_feature_users`、`on_modify_self_state`、`on_h5_vote`、`on_group_user_state`、`on_waiting_room`、`on_user_custom_data`；`EventCollector` 同时提供对应的队列和统一的原始事件日志。

有直接回复的方法返回回复字段，统一事件分类为 response；独立通知分类为 notify。同一个 6638 对发起端可以是 response，对观察端是 notify，不应把两端断言写成一样。

查询能力列表等待 6645 的 End，不靠固定静默时长结束。无 seq 时使用 Feature 或 UserID/SrcUserID 辅助过滤，避免其他能力的订阅推送冒充当前查询回复。服务器未提供 seq 时，同一能力的并发查询/订阅更新仍有歧义，应串行使用；重新查询前先取消已有同名订阅。

6665 的 User 可能是单个容器内多个 ID/Right 字段，接口保留原始结构；6666 的 RoomUserInfo/User 也应按重复 XML 节点处理，不能假设永远是一个对象。

等候室 6667 不等于入会成功。主会场 6501 应保持等待，同时建立子会场会话，直到审批后真正收到主会场 6601。若登录等待超时，`ConfLoginWaiting` 会保留 `notification_cmdid` 和 `fields`；不要在同一条待审批连接盲目重发 6501。`SrvAuthCode` 不是主席密码，也不应写入 RoomPassword。

## 6572 仍需服务器修复

真实私有数据复测证据：

```text
A: recent=[], disconnects=['conf']
B: 6672 PrivateData 正确，随后 6605 UserID=1000000007
后续 A 能力查询失败：conf session not established / 0x1004
```

本地服务器代码存在对应的返回值问题：

1. `server/ConfService/LocalRoom.cpp:9521` 的 `OnSetUserCustomData()` 返回业务结果码，成功为 `WPROTOCOL_RESULT_SUCCESS`，即 0。
2. `server/ConfService/WConfService.cpp:2748` 原样传递该结果。
3. `server/ConfService/ConfMsgProcessor.cpp:1591` 把该值赋给 BOOL `bResult`。公共数据成功后 `!bResult` 成立，提前跳出；私有数据成功也最终返回 FALSE。
4. `server/ConfService/WConfService.cpp:1326` 检查 `!bRet`，将处理失败视为异常并关闭会话。通知可能已经发出，也可能发送端还未收到就断开。应分别检查必需接收端是否完整收到 6672，以及通知后的会话健康。

建议服务器侧区分“业务结果码”和“是否保留会话”：以独立变量接收结果码，成功时应让 ProcessMsg 返回 TRUE；公共数据成功后继续处理同一请求里的私有数据。业务失败的错误通知契约需另外明确，不能拿非零错误码直接当 TRUE 使用。

本轮未修改这部分 C++ 代码。客户端保持正确字段和投递范围，自定义数据测试不再执行健康检查，不增加自动重连重试。上述服务器风险作为已知问题保留；修复部署后可另做 PublicData、PrivateData、同包两种数据以及后续会话健康的稳定性专项验证。

## 本地测试

```bash
python3 -m pytest -q tests/test_extended_protocols.py
python3 -m pytest -q
```

新增测试包含真实方向的 mock 集成、登录布局与重协商布局的不同结构、整数投票 ID、分页 End、订阅后续通知、无回复取消/确认、通知分类、等候室多连接审批和逐用户错误结果。Mock 不模拟真实媒体引擎或服务器全部权限；本地通过不能替代真机验收。
