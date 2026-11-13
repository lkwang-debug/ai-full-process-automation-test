# AI 全流程自动化测试

包含FM协议闭环、HTTP到FM交互、Web SDK自动化Demo、Excel业务用例和Allure报告能力。

## 项目组成

- `meeting_test_runtime/`：场景执行、客户端适配、资源管理和结果断言。
- `sessionmanager_py/`：随项目提供的会话层、FM客户端源码和本地Mock测试，不包含原内嵌Git元数据。
- `tests/e2e/data/`：FM协议用例工作簿。
- `tests/web_e2e/data/`：Web关键流程和扩展用例工作簿。
- `web_sdk_demo/`：浏览器Demo，使用根目录1.8.18 SDK。
- `tools/`：工作簿生成、协议审计、Demo服务和报告工具。

## 安装与配置

```powershell
python -m pip install -r requirements-test.txt
python -m pip install -e "./sessionmanager_py[aes]"
Copy-Item tests/e2e/config/test_env.example.ini tests/e2e/config/test_env.ini
```

已有真实配置不要覆盖。账号、密码、鉴权密钥及服务器地址只填写在本地INI或环境变量中。本地INI、客户端在线配置、历史报告、日志和缓存不提交到Git。

## 本地验证

```powershell
python -X utf8 -m pytest -q tests --ignore=tests/e2e --ignore=tests/web_e2e
python -X utf8 -m pytest -q sessionmanager_py/tests
```

默认不执行真实服务器场景。运行Web真机用例前需准备实际环境、账号和浏览器，并明确设置`WEB_RUN_REAL=1`；细节见[Web运行说明](web_sdk_demo/README.md)。FM协议和分层调用方式见[框架调用设计](测试框架调用设计.md)、[协议客户端说明](sessionmanager_py/README.md)。

启动本地Demo只提供页面，不会自动登录或入会：

```powershell
python tools/serve_web_demo.py
```

单独运行客户端示例时，将相应`*.example.json`复制为本地JSON并填写环境信息，避免直接使用空模板连接服务器。
