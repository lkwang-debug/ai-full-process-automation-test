"""4u Web 后台 HTTP API 客户端 —— 给"4u 终端信令里没有"的接口用（建会/取消/邀请/布局锁定）。

这些接口（创建预约/即时会议、取消会议、邀请平台用户、布局锁定）在 4u 终端 XML 信令里
**不存在**，走 Web 后台 REST API（见 docs/api.html）。本模块封装：
  - JWT 鉴权 token 生成（SHA256(header+payload+secret)，详见 api.html「鉴权相关说明」）
  - 统一 POST/GET 请求（带 Authorization 头、JSON 体、{code,msg,data} 响应解析）
  - 具体业务端点方法（create_instant_meeting / create_reserved_meeting /
    cancel_reserved_meeting / invite_users / lock_layout / ...）

仅用标准库（urllib），不引入 requests。

配置：base_url + app_key + app_secret + timeout + verify_tls，可写在 JSON 里
（见 examples/http_api.json），或 ``WebApiClient.from_config(path)`` 加载。
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger("fm_client.http")

# 鉴权算法 header（固定 SHA256）
_HEADER_JSON = '{"alg": "SHA256"}'
_TOKEN_REFRESH_AHEAD = 60.0   # 距过期还剩这么多秒就提前刷新（token 有效期 10 分钟）


def _camel_key(name: str) -> str:
    """把 Python 参数名转换为 Web API 使用的 lowerCamelCase。"""
    if "_" not in name:
        return name
    head, *tail = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _camel_fields(fields: Dict) -> Dict:
    """只转换当前对象的键；嵌套协议对象由调用方按文档提供。"""
    return {_camel_key(key): value for key, value in fields.items()}


def gen_token(app_key: str, app_secret: str, timestamp_ms: Optional[int] = None) -> str:
    """按 api.html 鉴权说明生成 JWT token：

        header  = base64('{"alg": "SHA256"}')
        payload = base64('{ "key": "<appKey>", "timestamp": <ms>}')
        sig     = sha256(header + payload + secret).hexdigest()
        token   = header.payload.sig

    实测与文档示例 payload base64 逐字节一致。token 有效期 10 分钟。
    """
    ts = int(timestamp_ms if timestamp_ms is not None else time.time() * 1000)
    header_b64 = base64.b64encode(_HEADER_JSON.encode()).decode()
    payload_json = '{ "key": "%s", "timestamp": %d}' % (app_key, ts)
    payload_b64 = base64.b64encode(payload_json.encode()).decode()
    sig = hashlib.sha256((header_b64 + payload_b64 + app_secret).encode()).hexdigest()
    return f"{header_b64}.{payload_b64}.{sig}"


class HttpApiError(Exception):
    """Web API 业务错误（响应 code != 成功码）。携带 code/msg。"""

    def __init__(self, code: int, message: str, payload: Any = None) -> None:
        self.code = code
        self.message = message
        self.payload = payload
        super().__init__(f"[{code}] {message}")


class WebApiClient:
    """4u Web 后台 HTTP API 客户端。配置在 JSON（base_url/app_key/app_secret/...）。"""

    def __init__(self, base_url: str, app_key: str, app_secret: str, *,
                 timeout: float = 10.0, verify_tls: bool = True,
                 success_code: int = 0) -> None:
        self.base_url = base_url.rstrip("/")
        self.app_key = app_key
        self.app_secret = app_secret
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.success_code = success_code
        self._token: Optional[str] = None
        self._token_ts: float = 0.0
        self._trace_lock = threading.Lock()
        self._next_request_trace_id = 1
        self._request_traces = []

    def mark_requests(self) -> int:
        """返回HTTP请求轨迹游标；Authorization永不进入轨迹。"""
        with self._trace_lock:
            return self._next_request_trace_id - 1

    def snapshot_requests(self, *, after: int = 0) -> list:
        with self._trace_lock:
            return [copy.deepcopy(item) for item in self._request_traces
                    if item["trace_id"] > after]

    def _record_request(self, method: str, url: str, body: Any) -> None:
        with self._trace_lock:
            trace_id = self._next_request_trace_id
            self._next_request_trace_id += 1
            self._request_traces.append({
                "trace_id": trace_id, "protocol": "HTTP",
                "method": method, "url": url,
                "body": copy.deepcopy(body), "sent_at": time.time(),
                "content_type": "application/json",
            })

    # ---- 配置加载 ----
    @classmethod
    def from_config(cls, path_or_dict) -> "WebApiClient":
        """从 JSON 文件路径或已加载的 dict 建客户端。

        支持两种结构：扁平 ``{base_url, app_key, app_secret, ...}``，或带 ``"http"`` 子节。"""
        if isinstance(path_or_dict, dict):
            cfg = path_or_dict
        else:
            with open(path_or_dict, encoding="utf-8") as f:
                cfg = json.load(f)
        if isinstance(cfg.get("http"), dict):
            cfg = cfg["http"]
        return cls(
            base_url=cfg["base_url"],
            app_key=cfg["app_key"],
            app_secret=cfg["app_secret"],
            timeout=float(cfg.get("timeout", 10.0)),
            verify_tls=bool(cfg.get("verify_tls", True)),
            success_code=int(cfg.get("success_code", 0)),
        )

    # ---- token（带过期刷新，10 分钟有效，提前 60s 刷）----
    def _current_token(self) -> str:
        now = time.time()
        if self._token is None or now - self._token_ts > 600 - _TOKEN_REFRESH_AHEAD:
            self._token = gen_token(self.app_key, self.app_secret)
            self._token_ts = now
        return self._token

    # ---- 底层请求 ----
    def request(self, method: str, path: str, body: Any = None, *,
                params: Optional[Dict] = None) -> Dict:
        """发请求，返回解析后的响应 dict（一般含 code/msg/data）。code != 成功码抛 HttpApiError。"""
        url = self.base_url + path
        if params:
            from urllib.parse import urlencode
            url += ("&" if "?" in url else "?") + urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", self._current_token())
        self._record_request(method, url, body)
        ctx = None if self.verify_tls else ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            logger.warning("HTTP %s %s -> %s %s", method, url, exc.code, raw[:200])
        except urllib.error.URLError as exc:
            raise HttpApiError(-1, f"网络错误 {method} {url}: {exc}") from exc
        try:
            result = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as exc:  # noqa: BLE001
            raise HttpApiError(-1, f"响应非 JSON: {raw[:200]!r}") from exc
        if isinstance(result, dict) and "code" in result:
            code = result.get("code")
            if str(code) != str(self.success_code):   # code 是字符串 "0" 表成功
                raise HttpApiError(code, result.get("msg") or result.get("message") or "",
                                   result.get("data"))
        return result

    def request_bytes(self, method: str, path: str, body: Any = None, *,
                      params: Optional[Dict] = None) -> bytes:
        """发起请求并返回原始响应字节。

        会议纪要/转写导出接口可能返回 Markdown、PDF 或其他文件，不能走
        ``request()`` 的 JSON 解码路径。业务错误仍按 JSON ``code`` 字段转换为
        :class:`HttpApiError`。
        """
        url = self.base_url + path
        if params:
            from urllib.parse import urlencode
            url += ("&" if "?" in url else "?") + urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", self._current_token())
        self._record_request(method, url, body)
        ctx = None if self.verify_tls else ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                result = json.loads(raw.decode("utf-8"))
            except Exception:
                result = None
            if isinstance(result, dict) and "code" in result:
                raise HttpApiError(result.get("code"),
                                   result.get("msg") or result.get("message") or "",
                                   result.get("data")) from exc
            raise HttpApiError(exc.code, f"HTTP {exc.code}: {raw[:200]!r}") from exc
        except urllib.error.URLError as exc:
            raise HttpApiError(-1, f"网络错误 {method} {url}: {exc}") from exc

    def download(self, path: str, *, params: Optional[Dict] = None) -> bytes:
        """下载文件型 HTTP 响应。"""
        return self.request_bytes("GET", path, params=params)

    def get(self, path, params=None):
        return self.request("GET", path, params=params)

    def post(self, path, body=None):
        return self.request("POST", path, body=body)

    def put(self, path, body=None):
        return self.request("PUT", path, body=body)

    def delete(self, path, body=None):
        return self.request("DELETE", path, body=body)

    # ---- 统一会控接口（/v1/control/{roomId}，operate+param 调度）----
    def control(self, room_id, operate: str, param: Dict) -> Dict:
        """POST /api/v1/control/{roomId}  {operate, param} —— 邀请/布局/锁定等会控操作。"""
        return self.post(f"/v1/control/{room_id}", {"operate": operate, "param": param})

    # ---- 1. 创建即时会议（POST /v1/room/createInstant）----
    def create_instant_meeting(self, *, meeting_name: str, user_name: str,
                               meeting_type: int = 1, meeting_template: Optional[int] = None,
                               verify_mode: Optional[int] = None, **extra) -> Dict:
        """创建即时会议。返回 data 含 ``roomId``/``inviteCode``/``nodeId``（后续入会句柄）。

        meeting_type: 0=带会议室 / 1=即时 / 2=即时_立即开会。"""
        body: Dict = {"meetingName": meeting_name, "userName": user_name, "meetingType": meeting_type}
        if meeting_template is not None:
            body["meetingTemplate"] = meeting_template
        if verify_mode is not None:
            body["verifyMode"] = verify_mode
        body.update(extra)
        return self.post("/v1/room/createInstant", body)

    # ---- 2. 创建预约会议（两步：addRoomInfo 建房 → reservation 标预约）----
    def add_room(self, *, room_name: str, verify_mode="3", call_bitrate: int = 0,
                 meeting_minutes: bool = False, terminal_re_invite: bool = False, **extra) -> Dict:
        """POST /v1/room/addRoomInfo 建会议室，返回 data.roomId。"""
        body: Dict = {"roomName": room_name, "verifyMode": str(verify_mode), "callBitrate": call_bitrate,
                      "meetingMinutes": meeting_minutes, "terminalReInvite": terminal_re_invite}
        body.update(extra)
        return self.post("/v1/room/addRoomInfo", body)

    def reserve_room(self, *, room_id, hope_start_time: str, hope_end_time: str, **extra) -> Dict:
        """POST /v1/room/reservation 把已有会议室标记为预约（hopeStartTime/hopeEndTime: yyyy-MM-dd HH:mm:ss）。"""
        return self.post("/v1/room/reservation",
                         {"roomId": room_id, "hopeStartTime": hope_start_time,
                          "hopeEndTime": hope_end_time, **extra})

    def create_reserved_meeting(self, *, room_name: str, hope_start_time: str, hope_end_time: str,
                                verify_mode="3", **extra) -> Dict:
        """预约会议 = add_room + reserve_room 两步。返回 {roomId, addRoom, reservation}。"""
        add = self.add_room(room_name=room_name, verify_mode=verify_mode, **extra)
        data = add.get("data") if isinstance(add, dict) else None
        room_id = data.get("roomId") if isinstance(data, dict) else None
        if room_id is None:
            raise HttpApiError(-1, "addRoomInfo 未返回 roomId", add)
        resv = self.reserve_room(room_id=room_id, hope_start_time=hope_start_time,
                                 hope_end_time=hope_end_time)
        return {"roomId": room_id, "addRoom": add, "reservation": resv}

    # ---- 3. 取消会议（editRoomStatus 软禁用 / delRoomInfo 硬删除）----
    def cancel_reserved_meeting(self, room_id, *, hard: bool = False, **extra) -> Dict:
        """取消会议。hard=False→editRoomStatus(status=0) 软禁用；hard=True→delRoomInfo 删除。"""
        if hard:
            return self.post("/v1/room/delRoomInfo", {"roomId": room_id, **extra})
        return self.post("/v1/room/editRoomStatus", {"roomId": room_id, "status": "0", **extra})

    # ---- 4. 邀请（/v1/control/{roomId}）----
    def invite_users(self, room_id, user_ids, *, name: str = "会控用户", **extra) -> Dict:
        """邀请平台内部用户入会（operate=INVITE_USER）。data.errors 为失败的 userId 列表。"""
        return self.control(room_id, "INVITE_USER", {"name": name, "users": list(user_ids), **extra})

    def invite_terminals_http(self, room_id, call_info, *, call_type: int = 1, **extra) -> Dict:
        """邀请电话/H323 终端入会（operate=INVITE_TERMINAL）——HTTP 版。

        call_type: 1=电话 / 2=H323；call_info: [{calleeNumber, calleeNickname, calleeUserId}]。
        （信令版见 ``client.invite_terminals()`` / ``conf_invite_call()``。）"""
        return self.control(room_id, "INVITE_TERMINAL",
                            {"callType": call_type, "callInfo": call_info, **extra})

    # ---- 5. 布局 / 会议锁定（/v1/control/{roomId}）----
    def lock_layout(self, room_id, lock: bool = True, *, user_id=0, **extra) -> Dict:
        """布局锁定/解锁（operate=LOCK_LAYOUT）。lock: True=锁/False=解。"""
        return self.control(room_id, "LOCK_LAYOUT",
                            {"userId": user_id, "lock": "1" if lock else "0", **extra})

    def lock_meeting(self, room_id, lock: bool = True, **extra) -> Dict:
        """整个会议锁定/解锁（operate=MEETING_LOCK，阻止新成员入会）。"""
        return self.control(room_id, "MEETING_LOCK", {"lock": 1 if lock else 0, **extra})

    def set_layout_http(self, room_id, layout: Dict, **extra) -> Dict:
        """会议中改布局（operate=OP_LAYOUT）——layout 含 mode/full/dataActive/videoRelayId/videoInfo 等。"""
        return self.control(room_id, "OP_LAYOUT", {"layout": layout, **extra})

    # ---- 部门 ----
    def edit_department(self, *, depart_id, depart_name, parent_depart_id, **extra) -> Dict:
        return self.post("/v1/department/edit", {
            "departId": depart_id, "departName": depart_name,
            "parentDepartId": parent_depart_id, **extra})

    def list_departments(self, *, depart_id=None, depart_name=None,
                         page_number=None, page_size=None, code=None, **extra) -> Dict:
        body = {"departId": depart_id, "departName": depart_name,
                "pageNumber": page_number, "pageSize": page_size, "code": code}
        body.update({k: v for k, v in extra.items()})
        return self.post("/v1/department/list", {k: v for k, v in body.items() if v is not None})

    def add_department(self, *, depart_name, parent_depart_id, **extra) -> Dict:
        return self.post("/v1/department/add", {
            "departName": depart_name, "parentDepartId": parent_depart_id, **extra})

    def delete_department(self, depart_id, **extra) -> Dict:
        return self.post("/v1/department/delete", {"departId": depart_id, **extra})

    # ---- 用户 ----
    def edit_user(self, *, user_name, admin_role, depart_id=None, **fields) -> Dict:
        body = {"adminRole": admin_role, "userName": user_name, **fields}
        if depart_id is not None:
            body["departId"] = depart_id
        return self.post("/v1/user/edit", body)

    def delete_user(self, user_name: str, **extra) -> Dict:
        return self.post("/v1/user/del", {"userName": user_name, **extra})

    def add_users_batch(self, *, user_name, password, number, password_type=0,
                        depart_id=None, admin_role=None, public_data=None, **extra) -> Dict:
        body = {"userName": user_name, "password": password, "number": number,
                "passwordType": password_type, **extra}
        for key, value in (("departId", depart_id), ("adminRole", admin_role),
                           ("publicData", public_data)):
            if value is not None:
                body[key] = value
        return self.post("/v1/user/addBatch", body)

    def add_users_batch_copy(self, *, user_name, password, number, password_type=0,
                             depart_id=None, admin_role=None, public_data=None, **extra) -> Dict:
        """调用 api.html 中保留的重复 addBatch 端点。"""
        body = {"userName": user_name, "password": password, "number": number,
                "passwordType": password_type, **extra}
        for key, value in (("departId", depart_id), ("adminRole", admin_role),
                           ("publicData", public_data)):
            if value is not None:
                body[key] = value
        return self.post("/v1/user/addBatch_1779263480333", body)

    def add_user(self, *, user_name, password, password_type=0, **fields) -> Dict:
        return self.post("/v1/user/add", {
            "userName": user_name, "password": password,
            "passwordType": password_type, **_camel_fields(fields)})

    def list_users(self, **filters) -> Dict:
        return self.post("/v1/user/list", _camel_fields(filters))

    def user_online(self, user_name: str) -> Dict:
        return self.get(f"/v1/user/online/{user_name}")

    # ---- 录制、会议纪要与导出 ----
    def download_meeting_summary(self, group_id, **params) -> bytes:
        return self.download(f"/v1/room/summary/{group_id}", params=params or None)

    def list_room_record_summaries(self, room_id, **params) -> Dict:
        return self.get("/v1/room/record-summaries/room",
                        params={"roomId": room_id, **_camel_fields(params)})

    def list_record_summaries(self, **params) -> Dict:
        return self.get("/v1/room/record-summaries",
                        params=_camel_fields(params) or None)

    def export_meeting_summary(self, group_id, **params) -> bytes:
        return self.download(f"/v1/room/record-summaries/{group_id}/summary/export",
                             params=params or None)

    def export_transcript(self, group_id, **params) -> bytes:
        return self.download(f"/v1/room/record-summaries/{group_id}/transcript/export",
                             params=params or None)

    def delete_record_summaries(self, group_ids, **extra) -> Dict:
        return self.delete("/v1/room/record-summaries",
                           {"groupIds": list(group_ids), **extra})

    def get_record_summary(self, group_id, **params) -> Dict:
        return self.get(f"/v1/room/record-summaries/{group_id}",
                        params=_camel_fields(params) or None)

    def list_record_participants(self, group_id, **params) -> Dict:
        return self.get(f"/v1/room/record-summaries/{group_id}/participants",
                        params=_camel_fields(params) or None)

    def list_record_tasks(self, group_id, **params) -> Dict:
        return self.get(f"/v1/room/record-summaries/{group_id}/record-tasks",
                        params=_camel_fields(params) or None)

    def list_records(self, group_id, **params) -> Dict:
        return self.get(f"/v1/room/record-summaries/{group_id}/records",
                        params=_camel_fields(params) or None)

    def get_record_share(self, group_id, **params) -> Dict:
        return self.get(f"/v1/room/record-summaries/{group_id}/share",
                        params=_camel_fields(params) or None)

    def delete_record_tasks(self, record_ids, **extra) -> Dict:
        return self.delete("/v1/room/record-summaries/records",
                           {"recordIds": list(record_ids), **extra})

    def get_web_control_url(self, room_id, **params) -> Dict:
        return self.get(f"/v1/room/{room_id}/web-control", params=params or None)

    def get_meeting_summary(self, **params) -> Dict:
        return self.get("/v1/room/summary", params=params or None)

    def edit_record_settings(self, *, room_id, **settings) -> Dict:
        return self.post("/v1/room/editRoomRecordSettings",
                         {"roomId": room_id, **_camel_fields(settings)})

    def query_record_settings(self, room_id, **params) -> Dict:
        return self.get("/v1/room/queryRoomRecordSettings",
                        params={"roomid": room_id, **_camel_fields(params)})

    # ---- 会议室管理 ----
    def batch_add_rooms(self, rooms) -> Dict:
        """批量新增会议室；api.html 定义请求体为对象数组。"""
        return self.post("/v1/room/batchAddRoomInfo", list(rooms))

    def authorize_user_room(self, *, user_name, room_user_str) -> Dict:
        return self.post("/v1/room/authRoom", {
            "userName": user_name, "roomUserStr": room_user_str})

    def authorize_room_users(self, *, room_id, room_user_str, auth_type="1") -> Dict:
        return self.post("/v1/room/authUser", {
            "roomId": room_id, "roomUserStr": room_user_str,
            "authType": auth_type})

    def kick_out_user(self, *, room_id, user_field_type, user_field_value,
                      app_back_notify=None, operator=None) -> Dict:
        body = {"roomId": room_id, "userFieldType": user_field_type,
                "userFieldValue": user_field_value}
        if app_back_notify is not None:
            body["appBackNotify"] = app_back_notify
        if operator is not None:
            body["operator"] = operator
        return self.post("/v1/room/kickOut", body)

    def kick_out_user_by_id(self, room_id, user_id, **extra) -> Dict:
        """按用户 ID 请出用户的便捷包装（文档底层字段仍是 userField*）。"""
        return self.post("/v1/room/kickOut", {
            "roomId": room_id, "userFieldType": 1,
            "userFieldValue": str(user_id), **extra})

    def delete_room(self, room_id, **extra) -> Dict:
        return self.post("/v1/room/delRoomInfo", {"roomId": room_id, **extra})

    def clean_room(self, room_id, **extra) -> Dict:
        return self.post("/v1/room/cleanRoom", {"roomId": room_id, **extra})

    def edit_room_status(self, room_id, status, **extra) -> Dict:
        return self.post("/v1/room/editRoomStatus", {
            "roomId": room_id, "status": status, **extra})

    def update_room_layout(self, *, room_id, layout_code=None, **extra) -> Dict:
        body = {"roomId": room_id, **extra}
        if layout_code is not None:
            body["layoutCode"] = layout_code
        return self.post("/v1/room/layout", {
            **body})

    def edit_room_info(self, *, room_id, **fields) -> Dict:
        return self.post("/v1/room/editRoomInfo",
                         {"roomId": room_id, **_camel_fields(fields)})

    def update_room_permission(self, *, room_id, room_mode, room_permission=None, **extra) -> Dict:
        body = {"roomId": room_id, "roomMode": room_mode, **extra}
        if room_permission is not None:
            body["roomPermission"] = room_permission
        return self.post("/v1/room/permission", body)

    def make_room_routine(self, *, room_id, **fields) -> Dict:
        return self.post("/v1/room/routine",
                         {"roomId": room_id, **_camel_fields(fields)})

    def make_room_reservation(self, *, room_id, hope_start_time, hope_end_time, **extra) -> Dict:
        return self.post("/v1/room/reservation", {
            "roomId": room_id, "hopeStartTime": hope_start_time,
            "hopeEndTime": hope_end_time, **extra})

    def make_room_fixed(self, *, room_id, **fields) -> Dict:
        return self.post("/v1/room/fixed",
                         {"roomId": room_id, **_camel_fields(fields)})

    def set_room_default_setting(self, *, room_id, **fields) -> Dict:
        return self.post("/v1/room/defaultSetting",
                         {"roomId": room_id, **_camel_fields(fields)})

    def list_rooms_http(self, **filters) -> Dict:
        return self.post("/v1/room/roomList", _camel_fields(filters))

    def get_room_login_addr(self, **params) -> Dict:
        return self.get("/v1/room/loginAddr", params=_camel_fields(params) or None)

    def query_meeting_log(self, **filters) -> Dict:
        return self.post("/v1/room/meetingLog", _camel_fields(filters))

    def list_online_rooms(self, **filters) -> Dict:
        return self.post("/v1/room/onlineRoom", _camel_fields(filters))

    def query_room_log(self, **filters) -> Dict:
        return self.post("/v1/room/roomLog", _camel_fields(filters))

    def get_room_user_num(self, room_id, **extra) -> Dict:
        return self.post("/v1/room/userNum",
                         {"roomId": room_id, **_camel_fields(extra)})

    def check_user_in_room(self, room_id, user_name, **extra) -> Dict:
        return self.post("/v1/room/userInRoom", {
            "roomId": room_id, "userName": user_name, **_camel_fields(extra)})

    def list_user_rooms(self, user_name: str, **params) -> Dict:
        return self.get(f"/v1/room/roomList/{user_name}",
                        params=_camel_fields(params) or None)

    def list_room_participants(self, room_id, **filters) -> Dict:
        return self.post("/v1/room/participant",
                         {"roomId": room_id, **_camel_fields(filters)})

    def list_authorized_users(self, room_id, **filters) -> Dict:
        return self.post("/v1/user/roomUserList",
                         {"roomId": room_id, **_camel_fields(filters)})

    def send_room_message(self, *, room_id, user_field_type, user_field_value,
                          app_back_notify=None, operator=None, **extra) -> Dict:
        """POST /v1/room/sendMsg -> target Conf connection receives 6654.

        user_field_type: 1=user ID, 2=username; join multiple values with '#'.
        Supply app_back_notify (sent as appBackNotify); the server requires it.
        Targets must already be in the meeting. HTTP success alone does not
        confirm delivery: observe 6654 with UserID/operator/appBackNotify.
        Existing camelCase fields passed through **extra remain supported.
        """
        if app_back_notify is not None:
            extra["appBackNotify"] = app_back_notify
        if operator is not None:
            extra["operator"] = operator
        return self.post("/v1/room/sendMsg", {
            "roomId": room_id, "userFieldType": user_field_type,
            "userFieldValue": user_field_value, **_camel_fields(extra)})

    # ---- 会控语义方法（统一 POST /control/{roomId}） ----
    def close_meeting(self, room_id) -> Dict:
        return self.control(room_id, "CLOSE", {})

    def mute_all(self, room_id) -> Dict:
        return self.control(room_id, "MUTE_ALL", {})

    def send_subtitles(self, room_id, *, font_size, font_color, subtitle_type,
                       align, content, user_ids=None, **extra) -> Dict:
        param = {"fontSize": font_size, "fontColor": font_color,
                 "type": subtitle_type, "align": align, "content": content,
                 "userId": list(user_ids or [0]), **extra}
        return self.control(room_id, "SEND_SUBTITLES", param)

    def close_meeting_subtitle(self, room_id) -> Dict:
        return self.control(room_id, "CLOSE_SUBTITLE", {})

    def operate_record(self, room_id, state: int) -> Dict:
        return self.control(room_id, "OP_RECORD", {"state": state})

    def rename_meeting_http(self, room_id, room_name: str) -> Dict:
        return self.control(room_id, "MEETING_RENAME", {"roomName": room_name})

    def set_user_manager(self, room_id, user_id, state: int) -> Dict:
        return self.control(room_id, "OP_ROLE_MANAGER", {"userId": user_id, "state": state})

    def set_user_presenter(self, room_id, user_id, state: int) -> Dict:
        return self.control(room_id, "OP_ROLE_PRESENTER", {"userId": user_id, "state": state})

    def remove_user(self, room_id, user_id) -> Dict:
        """HTTP 请出用户；当前产品向目标端下发 6606（KickID=-2）。"""
        return self.control(room_id, "USER_REMOVE", {"userId": user_id})

    def control_user_audio(self, room_id, user_id, *, device_id: int, state: int) -> Dict:
        return self.control(room_id, "USER_AUDIO_CONTROL", {
            "userId": user_id, "audioState": {"deviceId": device_id, "state": state}})

    def control_user_video(self, room_id, user_id, *, device_id: int, state: int) -> Dict:
        return self.control(room_id, "USER_VIDEO_CONTROL", {
            "userId": user_id, "videoState": {"deviceId": device_id, "state": state}})

    def rename_user(self, room_id, user_id, nickname: str) -> Dict:
        return self.control(room_id, "USER_RENAME", {"userId": user_id, "nickname": nickname})

    def query_user_info(self, room_id, *, user_id=None, user_name=None) -> Dict:
        param = {}
        if user_id is not None:
            param["userId"] = user_id
        if user_name is not None:
            param["userName"] = user_name
        return self.control(room_id, "USER_INFO", param)

    def list_meeting_users(self, room_id) -> Dict:
        return self.control(room_id, "USER_LIST", {})

    def query_meeting_info(self, room_id) -> Dict:
        return self.control(room_id, "MEETING_INFO", {})

    def add_pull_stream(self, room_id, *, user_name, leave_mode, protocol_param, **extra) -> Dict:
        return self.control(room_id, "PULL_STREAM_ADD", {
            "userName": user_name, "leaveMode": leave_mode,
            "protocolParam": protocol_param, **extra})

    def delete_pull_stream(self, room_id, protocol_param, **extra) -> Dict:
        return self.control(room_id, "PULL_STREAM_DEL", {
            "protocolParam": protocol_param, **extra})

    # ---- 会控事件订阅 ----
    def add_subscription(self, room_id, event=None, address=None, durable=False, **extra) -> Dict:
        body = {"durable": durable, **_camel_fields(extra)}
        if event is not None:
            body["event"] = event
        if address is not None:
            body["address"] = address
        return self.post(f"/v1/control/{room_id}/subscriptions", body)

    def delete_subscription(self, room_id, event=None, **extra) -> Dict:
        body = _camel_fields(extra)
        if event is not None:
            body["event"] = event
        return self.delete(f"/v1/control/{room_id}/subscriptions", body)

    def list_subscriptions(self, **params) -> Dict:
        return self.get("/v1/control/subscriptions", params=params or None)
