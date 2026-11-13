"""Python facade over a browser-hosted real Web SDK, with an owning async worker."""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from typing import Any
from urllib.parse import parse_qsl

from .models import EventExpectation
from .redaction import redact
from .web_protocol import WebEventJournal

ROOT = Path(__file__).resolve().parents[1]


class WebSdkError(RuntimeError):
    def __init__(self, message, *, detail=None, origin="sdk"):
        self.detail = detail
        self.origin = origin
        self.code = detail.get("result") if isinstance(detail, dict) else None
        super().__init__(message)


class DemoServer:
    """Expose only the demo assets and the explicitly configured SDK file."""
    def __init__(self, sdk_path=None):
        sdk = Path(sdk_path or ROOT / "hst-rtc-sdk-1.8.18.es.unmin.js").resolve()
        if not sdk.is_file():
            raise FileNotFoundError(sdk)
        routes = {"/": ROOT / "web_sdk_demo/index.html", "/app.js": ROOT / "web_sdk_demo/app.js",
                  "/sdk.js": sdk, "/share_source.html": ROOT / "web_sdk_demo/share_source.html"}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = routes.get(self.path.split("?", 1)[0])
                if path is None:
                    self.send_error(404)
                    return
                body = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8"
                                 if path.suffix == ".js" else "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        self.sdk_hash = hashlib.sha256(sdk.read_bytes()).hexdigest()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class BrowserWorker:
    """All Playwright objects stay on this thread; callbacks run during pytest waits."""
    def __init__(self, *, sdk_path=None, headless=True, ignore_https_errors=False, browser_channel="chrome"):
        self.server = DemoServer(sdk_path)
        self.headless = headless
        self.ignore_https_errors = ignore_https_errors
        self.browser_channel = browser_channel
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="web-sdk-browser", daemon=True)
        self.thread.start()
        try:
            self.submit(self._start(), 40)
        except BaseException:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
            self.server.close()
            raise

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        if pending:
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.close()

    def submit(self, coroutine, timeout=30):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise

    async def _start(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless,
            channel=self.browser_channel or None,
            args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                  "--auto-select-tab-capture-source-by-title=WebSDK E2E Share Source",
                  "--autoplay-policy=no-user-gesture-required"])

    async def _shutdown(self):
        await self.browser.close()
        await self.playwright.stop()

    def close(self):
        try:
            self.submit(self._shutdown(), 15)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
            self.server.close()


class WebTestClient:
    """One independent browser context. Only reviewed public operations are callable."""
    OPERATIONS = frozenset({"init", "login", "join", "join_guest", "approve_self_av", "get_identity", "get_auth_state", "get_devices", "get_users",
        "get_user_info", "get_authenticated_profile", "get_capabilities", "get_effective_permissions", "mic_on", "mic_off", "camera_on", "camera_off",
        "chat", "request_presenter", "release_presenter", "request_manager", "release_manager",
        "request_remote_av", "respond_remote_av", "close_remote_av", "start_record", "stop_record",
        "start_screen_share", "stop_screen_share", "start_whiteboard", "stop_whiteboard", "leave", "close"})

    def __init__(self, profile, *, worker=None):
        self.profile = profile
        self.events = WebEventJournal()
        self._lock = threading.RLock()
        self._outbound = []
        self._sequence = 0
        self._connection = 0
        self._active_connections = set()
        self._critical_connections = set()
        self._proxy_connections = set()
        self._closed = False
        self._joined = False
        self._unexpected_disconnect = False
        self._identity = {}
        self._owns_worker = worker is None
        self.worker = worker or BrowserWorker(
            sdk_path=profile.endpoint.get("sdk_path"),
            headless=bool(profile.endpoint.get("headless", True)),
            ignore_https_errors=bool(profile.endpoint.get("ignore_https_errors", False)),
            browser_channel=profile.endpoint.get("browser_channel", "chrome"))
        try:
            self.worker.submit(self._open(), 30)
        except BaseException:
            if self._owns_worker:
                self.worker.close()
            raise

    async def _open(self):
        self.context = await self.worker.browser.new_context(
            ignore_https_errors=self.worker.ignore_https_errors,
            permissions=["microphone", "camera"], viewport={"width":1280,"height":800})
        await self.context.add_init_script("""(() => {
          window.__captureDiagnostics=[];
          const devices=navigator.mediaDevices;
          if (!devices?.getDisplayMedia) return;
          const original=devices.getDisplayMedia.bind(devices);
          devices.getDisplayMedia=(constraints) => {
            const entry={at:Date.now()/1000,constraints,phase:'requested'};
            window.__captureDiagnostics.push(entry);
            return original(constraints).then(stream => {
              entry.phase='acquired';entry.video=stream.getVideoTracks().map(t=>({state:t.readyState,settings:t.getSettings()}));
              return stream;
            }, error => {
              entry.phase='rejected';entry.error={name:error.name,message:error.message};throw error;
            });
          };
        })();""")
        self.share_page = await self.context.new_page()
        await self.share_page.goto(self.worker.server.url + "/share_source.html")
        self.page = await self.context.new_page()
        self.page.on("websocket", self._socket)
        self.page.on("pageerror", self._page_error)
        self.page.on("request", self._http_request)
        self.page.on("response", self._http_response)
        # Chromium可观察CORS阻止SDK读取的真实HTTP状态；不替SDK重新发请求。
        self._network_urls={}
        self._cdp=await self.context.new_cdp_session(self.page)
        self._cdp.on("Network.requestWillBeSent", lambda data:self._network_urls.update(
            {data["requestId"]:data["request"]["url"]}))
        self._cdp.on("Network.responseReceivedExtraInfo", self._http_status_extra)
        await self._cdp.send("Network.enable")
        self._cdp.on('Fetch.requestPaused',self._auth_response)
        # Read the browser's own auth error body before CORS hides it from JS.
        # Continue the original response unchanged; do not resend/fulfill/edit HTTP.
        await self._cdp.send('Fetch.enable',{'patterns':[{'urlPattern':'*/oauth2/token*','requestStage':'Response'}]})
        await self.page.goto(self.worker.server.url)
        await self.page.wait_for_function("window.webTest !== undefined", timeout=15000)

    def _page_error(self, error):
        self.events.append(source="browser", cmdid=None, fields={"error":str(error)},
                           raw_json="", kind="browser_error")

    def _http_status_extra(self, data):
        status=int(data.get("statusCode",0))
        url=self._network_urls.get(data.get("requestId"),"")
        if status>=400 and url and not url.startswith(self.worker.server.url):
            self.events.append(source="http",cmdid=None,fields={"url":url,"status":status,
                "body_unavailable":True,"evidence_source":"Chrome Network.responseReceivedExtraInfo"},
                raw_json="",kind="response")

    async def _auth_response(self, data):
        request_id=data['requestId'];url=data.get('request',{}).get('url','')
        try:
            status=int(data.get('responseStatusCode',0))
            if status>=400:
                captured=await self._cdp.send('Fetch.getResponseBody',{'requestId':request_id})
                raw=captured['body']
                if captured.get('base64Encoded'):raw=base64.b64decode(raw).decode('utf-8','replace')
                try:body=json.loads(raw)
                except ValueError:body={'text':raw[:1500]}
                self.events.append(source='http',cmdid=None,fields=redact({'url':url,'status':status,'body':body,
                    'evidence_source':'Chrome Fetch.getResponseBody (original response unchanged)'}),raw_json='',kind='response')
        except Exception as exc:
            self.events.append(source='browser',cmdid=None,fields={'auth_body_capture_error':type(exc).__name__},raw_json='',kind='observation_error')
        finally:
            await self._cdp.send('Fetch.continueRequest',{'requestId':request_id})

    def _http_request(self, request):
        if request.url.startswith(self.worker.server.url):
            return
        body=request.post_data
        try:
            body=json.loads(body) if body else None
        except ValueError:
            body=dict(parse_qsl(body)) if body and '=' in body else body
        self._record({"trace_type":"http_request", "method":request.method,
            "url":request.url,"request_body":body,"sent_at":time.time(),
            "sent_monotonic":time.monotonic()})

    async def _http_response(self, response):
        if response.url.startswith(self.worker.server.url):
            return
        try:
            body=await response.text()
            try: fields=json.loads(body)
            except ValueError: fields={"text":body[:1000]}
            self.events.append(source="http",cmdid=None,fields={"url":response.url,
                "status":response.status,"body":fields},raw_json="",kind="response")
        except Exception:
            self.events.append(source="http",cmdid=None,fields={"url":response.url,
                "status":response.status,"body_unavailable":True},raw_json="",kind="response")

    def _socket(self, socket):
        with self._lock:
            self._connection += 1
            connection = self._connection
            self._active_connections.add(connection)
        socket.on("framereceived", lambda payload: self._received(payload,connection))
        socket.on("framesent", lambda payload: self._sent(payload, connection, socket.url))
        socket.on("close", lambda *_: self._disconnected(connection, socket.url))
        socket.on("socketerror", lambda error: self.events.append(source="connection", cmdid=None,
            fields={"error":str(error)}, raw_json="", kind="socket_error", connection_id=connection))

    def _disconnected(self, connection, url):
        with self._lock:
            self._active_connections.discard(connection)
            if self._joined and not self._closed and connection in self._critical_connections:
                self._unexpected_disconnect = True
        self.events.append(source="connection", cmdid=None, fields={"url":url}, raw_json="",
                           kind="disconnect", connection_id=connection)

    def _received(self,payload,connection):
        event=self.events.append_frame(payload,connection)
        if event.source in {'conf','web_proxy'} or (event.source=='paas' and event.cmdid==10001):
            with self._lock:
                self._critical_connections.add(connection)
                if event.source in {'conf','web_proxy'}:
                    self._proxy_connections.add(connection)

    def _sent(self, payload, connection, url):
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="replace")
        try:
            message = json.loads(payload)
        except (TypeError, ValueError):
            message = {}
        self._record({"trace_type":"json_command", "source":"conf" if "CmdID" in message else "paas",
                      "cmdid":message.get("CmdID", message.get("id")),
                      "fields":copy.deepcopy(message.get("Data", message)),
                      "raw_json":str(payload), "connection_id":connection,
                      "url":url, "sent_at":time.time(), "sent_monotonic":time.monotonic()})

    def _record(self, value):
        with self._lock:
            self._sequence += 1
            value["event_id"] = self._sequence
            self._outbound.append(value)
            return self._sequence

    async def _invoke(self, operation, params):
        if operation == "start_screen_share":
            # 无头模式也需要激活承载SDK的页以满足浏览器采集前置；没有OS窗口，不抢桌面焦点。
            await self.page.bring_to_front()
            mark=await self.page.evaluate("window.webTest.nativeMark()")
            await self.page.evaluate("request => {window.webTest.captureRequest=request;}",
                                     {"operation":operation,"params":params})
            await self.page.locator("#capture-trigger").click()
            result=await self.page.evaluate("async () => { try {return {ok:true,value:await window.webTest.capturePromise};} catch(e) {return {ok:false,error:{message:e.message??e.detail?.message??e.detail?.msg??'SDK共享拒绝',origin:e.origin??'sdk',detail:e.detail??{message:e.message,name:e.name}}};}}")
            result['sdk_calls']=await self.page.evaluate("mark=>window.webTest.nativeSnapshot(mark)",mark)
            return result
        return await self.page.evaluate("async request => { const mark=window.webTest.nativeMark(); try { const value=await window.webTest.invoke(request.operation,request.params); return {ok:true,value,sdk_calls:window.webTest.nativeSnapshot(mark)}; } catch(e) { const detail=e.detail ?? (e instanceof Error ? {name:e.name,message:e.message,code:e.code} : e); return {ok:false,error:{message:e.message ?? 'SDK拒绝',origin:e.origin ?? 'sdk',detail},sdk_calls:window.webTest.nativeSnapshot(mark)}; }}",
                                        {"operation":operation,"params":params})

    def call(self, operation, params=None):
        __tracebackhide__ = True  # 登录参数不得进入pytest异常参数展示。
        if operation not in self.OPERATIONS:
            raise ValueError(f"Web产品不支持操作: {operation}")
        values = dict(params or {})
        trace = {"trace_type":"public_call", "operation":operation, "method_name":"WebTestClient.call",
                 "params":copy.deepcopy(values), "called_at":time.time()}
        self._record(trace)
        if operation in {"leave", "close"}:
            with self._lock:
                self._joined = False
        try:
            result = self.worker.submit(self._invoke(operation, values),
                float(self.profile.endpoint.get("operation_timeout", 30)))
            if operation == 'start_screen_share':
                async def capture_diagnostics():
                    return await self.page.evaluate('window.__captureDiagnostics ?? []')
                trace['capture_diagnostics'] = self.worker.submit(capture_diagnostics(), 5)
            for actual in result.get('sdk_calls',[]):
                self._record({"trace_type":"sdk_call","operation":operation,
                              "method_name":"HST."+actual['method'],"params":actual['args'],
                              "called_at":actual['called_at']})
            if not result["ok"]:
                error = result["error"]
                raise WebSdkError(error["message"], detail=error.get("detail"), origin=error["origin"])
            trace["local_result"] = copy.deepcopy(result["value"])
            return result["value"]
        except Exception as exc:
            trace["error"] = str(exc)
            trace['error_detail']=redact(getattr(exc,'detail',None))
            raise

    def join(self, room_id):
        __tracebackhide__ = True
        endpoint, account = self.profile.endpoint, self.profile.account
        marks = self.events.mark()
        self.call("init", {"ces_addr":endpoint["ces_addr"], "timeout_ms":30000})
        if account.get("identity_type", "account") == "guest":
            result = self.call("join_guest", {"room_id":room_id,
                "display_name":account["display_name"],"room_password":account.get("room_password")})
        else:
            self.call("login", {"username":account["username"],"password":account["password"]})
            result = self.call("join", {"room_id":room_id,"username":account["username"],
                                       "room_password":account.get("room_password")})
        event = self.events.wait(EventExpectation(cmdid=6601, kind="response"), marks,
                                 float(endpoint.get("event_timeout", 10)))
        if event is None or str(event.error_code) != "0":
            raise AssertionError("Web未收到真实成功6601")
        actual_room=event.fields.get("RoomInfo", {}).get("RoomID")
        if str(actual_room) != str(room_id):
            raise AssertionError(f"Web实际6601会议与请求不符: {actual_room}, expected={room_id}")
        if account.get("identity_type", "account") == "guest":
            commands=[item for item in self.snapshot_outbound(0) if item.get("cmdid")==1000]
            if not commands or str(commands[-1].get("fields",{}).get("UserType"))!="0":
                raise AssertionError("游客未通过真实UserType=0匿名请求入会")
        roles=self.events.wait(EventExpectation(cmdid=6631,kind='notify'),marks,
                              float(endpoint.get('event_timeout',10)))
        features=self.events.wait(EventExpectation(cmdid=6639,kind='notify'),marks,
                                 float(endpoint.get('event_timeout',10)))
        if roles is None or not roles.fields.get('Role') or features is None or not features.fields:
            raise AssertionError('Web初始化缺少实际角色/产品能力消息')
        from .web_assertions import validate_initialization
        validate_initialization(roles.fields,features.fields)
        initialized=any(item.get('trace_type')=='json_command' and item.get('cmdid')==6517
                        for item in self.snapshot_outbound(0))
        if not initialized:
            raise AssertionError('Web未实际发送6517初始化完成请求')
        self._identity = self.call("get_identity")
        self._identity["room_id"] = int(room_id)
        if not self._identity.get("room_user_id"):
            raise AssertionError("Web未取得会议内用户ID")
        with self._lock:
            self._joined = True
        # Web初期保留原稳定窗口，不复用FM提前就绪策略。
        time.sleep(float(endpoint.get("join_settle_time", 2)))
        return {"sdk_result":result, "received_login":event.fields,
                "error_code":event.error_code, "identity":copy.deepcopy(self._identity)}

    @property
    def identity(self):
        return copy.deepcopy(self._identity)

    def refresh_identity(self):
        identity=self.call('get_identity') or {}
        self._identity.update(identity)
        if identity.get('joined') is False:
            with self._lock:self._joined=False
        return self.identity

    def mark_outbound(self):
        with self._lock:
            return self._sequence

    def snapshot_outbound(self, after=0):
        with self._lock:
            return copy.deepcopy([item for item in self._outbound if item["event_id"] > int(after)])

    def is_healthy(self):
        with self._lock:
            return (self._joined and not self._closed and not self._unexpected_disconnect
                    and bool(self._proxy_connections.intersection(self._active_connections)))

    def close(self):
        if self._closed:
            return
        self._closed = True
        errors = []
        try:
            self.call("close")
        except Exception as exc:
            errors.append(str(exc))
        finally:
            try:
                self.worker.submit(self.context.close(), 10)
            finally:
                self._joined = False
                if self._owns_worker:
                    self.worker.close()
        if errors:
            raise RuntimeError(f"Web退出失败: {errors}")

    def diagnostics(self):
        return redact({"sdk_version":"1.8.18", "sdk_sha256":self.worker.server.sdk_hash,
                       "browser_version":self.worker.browser.version,
                       "identity":self.identity, "events":self.events.as_dicts(),
                       "outbound":self.snapshot_outbound()})
