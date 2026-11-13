# Web SDK测试Demo与关键流程运行说明

Demo使用根目录现有1.8.18 SDK，不修改其源码；Python通过浏览器公开门面调用业务，真实JSON帧由Playwright采集。第一阶段50条关键流程和第二阶段独立Web工作簿分别维护，不修改原FM Excel。

## 环境

配置统一放在tests/e2e/config/test_env.ini。Web账号沿用operator、observer，Web入口使用web_sdk.ces_addr。验收集群由fm_server.host和web_sdk.allowed_conf_hosts指定；混合会话遵循Front实际路由，只连接白名单内的地址，不自动扩大集群范围。

browser_channel=chrome使用本机Chrome；也可指定msedge。若使用Playwright自带Chromium，需要先安装浏览器并将channel留空。不要同时运行同一批A/B账号的其他测试。

自动化默认`[web_sdk] headless=true`，浏览器在后台无窗口运行，不抢占用户桌面。只有主动调试页面时才改为false；手工启动Demo页面不影响自动化默认值。屏幕共享仍通过真实SDK采集专用测试页，不以虚假成功替代。

媒体使用测试虚拟摄像头/麦克风。桌面共享自动选择标题为WebSDK E2E Share Source的专用页，不选择个人桌面；不验收媒体画面质量。白板通过真实PaaS资源创建/关闭。

## 运行

从外部测试工程根目录使用PowerShell：

```powershell
$env:PYTHONUTF8='1'
python -m pip install -r requirements-test.txt
# 单独启动可手工检查的Demo，不自动登录
python tools/serve_web_demo.py
# 自动化本地门禁（另开终端，或关闭Demo服务后运行）
pytest -q tests --ignore=tests/e2e --ignore=tests/web_e2e
pytest -q sessionmanager_py/tests
# 只读服务发现，不登录账号、不创建会议
python tools/probe_web_sdk.py
# 明确开启真机测试，默认关闭
$env:WEB_RUN_REAL='1'
# 仅第一阶段50条关键流程
pytest tests/web_e2e/test_key_flows.py -q --maxfail=1 --alluredir=reports/allure-web-key-新批次
# 仅第二阶段扩展用例
pytest tests/web_e2e/test_full_cases.py -q --maxfail=1 --alluredir=reports/allure-web-full-新批次
# 第二阶段扩展 + 原50条门禁，使用一个新的结果目录
pytest tests/web_e2e -q --maxfail=1 --alluredir=reports/allure-web-combined-新批次 --junitxml=reports/web-combined-新批次.xml
```

独立`tests/web_e2e/data/web_key_flows.xlsx`是原50条关键流程入口，具体公开方法和字段契约在`web_key_scenarios.py`；JSON仅保留初版模板来源。

第二阶段入口为`tests/web_e2e/data/web_e2e_cases.xlsx`：功能用例、终端配置、参数域、依赖流程、FM复用审计、Conf信令检查和业务方法检查共7个Sheet。2026-10-14设计223条，其中213条已实现，10条登记接口或契约缺口；缺口保留为跳过，不计为通过。原214条定义保留，新增9条游客重新入会。依赖流程Sheet中的387个步骤是实际执行输入，通过已评审的步骤白名单及完整性契约调用，不接受任意方法名或XML。每个逻辑场景、终端组合和依赖流程分别可追溯。

Excel可维护媒体、身份、角色、接受/拒绝等已支持参数，不填密码或自造XML。用例ID保持稳定；新增场景先明确SDK公开方法、前置、接收方向及断言，再扩展加载器和执行器。本地校验会拒绝未注册步骤、不合法参数和强行把未实现场景改为可执行。流程必须保留场景要求的核心步骤、顺序、对象、参数与变量依赖；删掉核心操作、错目标、空结果核验都不能执行。参数修改时需同步实际步骤；调整业务顺序需先修改受评审的场景契约。`tools/create_web_full_workbook.py`用于生成模板，`--refresh-generated`会重建生成文件，不能用于覆盖人工维护而未保存的用例。

可按P0先跑：`pytest tests/web_e2e -q -m p0`。箭头不能直接用于pytest `-k`表达式，可用流程ID或完整带引号节点。

所有会议由HTTP创建并注册关闭/删除；客户端先退出、会议再清理。P0失败或异常断会停止批次；真实验收建议始终加--maxfail=1，先定位再从头复跑。

## 日志与维护

- Allure包含初始化、公开Python/SDK方法实际参数、实际JSON/XML/PaaS帧、结果和逐项清理。
- web_key_acceptance_manifest.json绑定代码、两份Excel及SDK指纹，并保存setup/call/teardown实际结果和后台运行配置，不含INI密码。
- 本地返回与Response/Notify分开；JSON不转换成伪XML，认证字段在报告中脱敏。
- 支持范围以Web需求MD为准。共享内部6513不等于Web本地布局跟随。
- 新参数先检查SDK公开入口，再修改封装及关键流程；不能私自发送自造协议绕过SDK。
- 第二阶段报告分别统计扩展用例、原50条门禁、缺口和未执行项，不把历史多次通过结果拼成一个全量通过结果。

游客使用SDK公开匿名入会方法，不借账号登录冒充游客。会议模板的会控类型对应CES的`standard`。当前standard默认参会人共享权限与本阶段“允许普通游客共享”的前置不同，因此会控游客共享场景仅在独立会议中，通过公开角色权限方法设置mentee的CreateAppshare/CreateWhiteboard，并核对实际6632与SDK有效权限；不把游客升级为主持或管理员，不修改公共模板。报告必须保留该前置。

调试按同一会议ID、用户ID和操作时间关联185的FMWebProxy及185/186的FMServer日志，使用实际测试日期目录。资料查询为SDK缓存时，不要求服务器生成一条不存在的查询请求。日志只能辅助定位，不能替代接收端真实消息断言。

白板ID比较遵循SDK的32位整数表示：PaaS返回的完整mediaId保持原样，6513/6613中的白板数值ID允许有符号与无符号表示对应。仅比较数据区域ID=1、同一Item的Type=1、本次真实发送6513中的Pos及本次白板ID；原关键流程仍校验Pos=0，重开场景按真实发送位置关联。零值、越界值、其他资源或其他类型不能匹配。通用字段比较不变，Allure保留原始请求/通知，并单独展示ID比较依据。白板创建19001及关闭19003、适用的PaaS取消发布/会议共享停止、操作权释放、连接健康和资源清理均须验收。

2026-10-14补强规则与真实复测进度见[实施记录](../reports/web_assertion_hardening_20261014.md)。新断言会揭示旧规则漏判，不得把旧PASS直接转成新规则通过。OAuth错误体由浏览器原请求的Chrome Fetch观察，不重发或修改响应；若网关只返回统一HTML，不以任意400冒充明确业务原因。
