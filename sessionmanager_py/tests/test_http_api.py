"""4u Web 后台 HTTP API 客户端测试（token 生成 / 请求 / MeetingControlOps 委托）。"""
from __future__ import annotations

import base64
import hashlib
import json

import pytest

from fm_client import FmError, ProtocolNotInSignaling
from fm_client import http_api
from fm_client.http_api import HttpApiError, WebApiClient, gen_token


# ----------------------------- token 生成 -----------------------------
def test_gen_token_format():
    t = gen_token("4QY08Ky", "my-secret", 1633680255057)
    h, p, sig = t.split(".")
    assert base64.b64decode(h) == b'{"alg": "SHA256"}'
    # payload base64 与 api.html 文档示例逐字节一致
    assert p == "eyAia2V5IjogIjRRWTA4S3kiLCAidGltZXN0YW1wIjogMTYzMzY4MDI1NTA1N30="
    assert sig == hashlib.sha256((h + p + "my-secret").encode()).hexdigest()  # SHA256(header+payload+secret)
    assert len(sig) == 64


# ----------------------------- request（mock urlopen）---------------------------
class _FakeResp:
    def __init__(self, body): self._b = body
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _fake_urlopen(body_obj, capture=None):
    payload = json.dumps(body_obj).encode()

    def _u(req, timeout=None, context=None):
        if capture is not None:
            capture["req"] = req
            capture["context"] = context
        return _FakeResp(payload)
    return _u


def test_request_success_sets_auth_and_returns_data(monkeypatch):
    c = WebApiClient("https://host/api", "key", "secret", verify_tls=False)
    cap = {}
    monkeypatch.setattr(http_api.urllib.request, "urlopen",
                        _fake_urlopen({"code": "0", "msg": "SUCCESS", "data": {"roomId": 123}}, cap))
    r = c.post("/v1/room/createInstant", {"meetingName": "m"})
    assert r["data"]["roomId"] == 123
    req = cap["req"]
    assert req.has_header("Authorization")          # 带鉴权头
    token = req.get_header("Authorization")
    assert token.count(".") == 2                    # JWT 三段
    assert cap["context"] is not None               # verify_tls=False → 传了 unverified context
    traces = c.snapshot_requests(after=0)
    assert traces == [{
        "trace_id": 1, "protocol": "HTTP", "method": "POST",
        "url": "https://host/api/v1/room/createInstant",
        "body": {"meetingName": "m"}, "sent_at": traces[0]["sent_at"],
        "content_type": "application/json",
    }]
    assert "Authorization" not in traces[0]


def test_request_error_code_raises(monkeypatch):
    c = WebApiClient("https://host/api", "key", "secret")
    monkeypatch.setattr(http_api.urllib.request, "urlopen",
                        _fake_urlopen({"code": "1003", "msg": "token expired", "data": None}))
    with pytest.raises(HttpApiError) as exc:
        c.get("/v1/user/online/x")
    assert exc.value.code == "1003" and "token expired" in exc.value.message


def test_from_config_dict_and_file(tmp_path):
    cfg = {"base_url": "https://h/api", "app_key": "k", "app_secret": "s", "verify_tls": False}
    assert WebApiClient.from_config(cfg).base_url == "https://h/api"
    # 带 'http' 子节
    assert WebApiClient.from_config({"http": cfg}).app_key == "k"
    p = tmp_path / "h.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    assert WebApiClient.from_config(str(p)).app_secret == "s"


# ----------------------------- MeetingControlOps 委托 ---------------------------
class _StubHttp:
    def __init__(self): self.calls = []
    def create_instant_meeting(self, **k): self.calls.append(("instant", k)); return {"data": {"roomId": 9}}
    def lock_layout(self, room_id, lock, **k): self.calls.append(("lock", (room_id, lock))); return {"code": "0"}
    def invite_users(self, room_id, uids, **k): self.calls.append(("invite", (room_id, uids))); return {"data": {"result": 0}}


def test_stub_raises_when_no_http(fm):
    """未 attach_http 时，5 个信令外接口必须报 ProtocolNotInSignaling（不伪装成功）。"""
    fm.front_login("a", "p", room_id=100001, room_node_id="1"); fm.conf_login(user_name="a")
    with pytest.raises(ProtocolNotInSignaling):
        fm.create_instant_meeting(meeting_name="m", user_name="a")
    with pytest.raises(ProtocolNotInSignaling):
        fm.lock_layout(True)
    with pytest.raises(ProtocolNotInSignaling):
        fm.invite_users([1])


def test_delegates_to_http_when_attached(fm):
    """attach_http 后，信令外接口委托给 WebApiClient；不再报错。"""
    fm.front_login("a", "p", room_id=100001, room_node_id="1"); fm.conf_login(user_name="a")
    stub = _StubHttp()
    fm.attach_http(stub)                            # 附加（也接受 JSON 路径/dict/WebApiClient）
    assert fm.http_api is stub

    r = fm.create_instant_meeting(meeting_name="m", user_name="a")
    assert r["data"]["roomId"] == 9 and stub.calls[0][0] == "instant"

    fm.lock_layout(False)
    assert stub.calls[-1] == ("lock", (fm.room_id, False))   # 用当前会议 room_id

    fm.invite_users([1, 2])
    assert stub.calls[-1] == ("invite", (fm.room_id, [1, 2]))


def test_request_bytes_returns_binary_download(monkeypatch):
    c = WebApiClient("https://host/api", "key", "secret")
    captured = {}

    def fake(req, timeout=None, context=None):
        captured["url"] = req.full_url
        return _FakeResp(b"%PDF-1.7\\x00binary")

    monkeypatch.setattr(http_api.urllib.request, "urlopen", fake)
    assert c.download("/v1/room/summary/g-1", params={"format": "pdf"}) == b"%PDF-1.7\\x00binary"
    assert captured["url"].endswith("/v1/room/summary/g-1?format=pdf")


def test_api_html_business_wrappers_send_documented_routes(monkeypatch):
    calls = []

    def fake_request(self, method, path, body=None, *, params=None):
        calls.append((method, path, body, params))
        return {"code": "0", "data": {}}

    def fake_bytes(self, method, path, body=None, *, params=None):
        calls.append((method, path, body, params))
        return b"file"

    monkeypatch.setattr(WebApiClient, "request", fake_request)
    monkeypatch.setattr(WebApiClient, "request_bytes", fake_bytes)
    api = WebApiClient("https://host/api", "key", "secret")

    api.edit_department(depart_id=1, depart_name="研发", parent_depart_id=0)
    api.list_departments(page_number=1)
    api.add_department(depart_name="测试", parent_depart_id=1)
    api.delete_department(2)
    api.edit_user(user_name="u1", admin_role="2")
    api.delete_user("u1")
    api.add_users_batch(user_name="qa", password="password1", number=2)
    api.add_user(user_name="u2", password="password1")
    api.list_users(search_key="qa")
    api.user_online("u1")
    api.download_meeting_summary("g1")
    api.list_room_record_summaries(100)
    api.list_record_summaries(user_id=1)
    api.export_meeting_summary("g1")
    api.export_transcript("g1")
    api.delete_record_summaries(["g1"])
    api.get_record_summary("g1")
    api.list_record_participants("g1")
    api.list_record_tasks("g1")
    api.list_records("g1")
    api.get_record_share("g1")
    api.delete_record_tasks(["r1"])
    api.get_web_control_url(100, user_id=1)
    api.get_meeting_summary(begin_time="2026-01-01", end_time="2026-01-02")
    api.edit_record_settings(room_id=100, auto_record=1)
    api.query_record_settings(100)
    api.batch_add_rooms([{"roomName": "r", "roomMode": "fixed", "maxUserCount": 10}])
    api.authorize_user_room(user_name="u1", room_user_str="100,2")
    api.authorize_room_users(room_id=100, room_user_str="u1,2")
    api.kick_out_user(room_id=100, user_field_type=1, user_field_value="7")
    api.delete_room(100)
    api.clean_room(100)
    api.edit_room_status(100, "0")
    api.update_room_layout(room_id=100, layout_code="layout_square_4")
    api.edit_room_info(room_id=100, room_name="new")
    api.update_room_permission(room_id=100, room_mode="custom")
    api.make_room_routine(room_id=100, cycle_flag="1")
    api.make_room_reservation(room_id=100, hope_start_time="s", hope_end_time="e")
    api.make_room_fixed(room_id=100)
    api.set_room_default_setting(room_id=100, use_default_flag="1")
    api.list_rooms_http(page_number=1)
    api.get_room_login_addr()
    api.query_meeting_log(depart_id=1, start_time="s", end_time="e")
    api.list_online_rooms(page_number=1)
    api.query_room_log(depart_id=1, start_time="s", end_time="e")
    api.get_room_user_num(100)
    api.check_user_in_room(100, "u1")
    api.list_user_rooms("u1")
    api.list_room_participants(100)
    api.list_authorized_users(100)
    api.send_room_message(room_id=100, user_field_type=1, user_field_value="7")
    api.close_meeting(100)
    api.mute_all(100)
    api.send_subtitles(100, font_size=2, font_color=255, subtitle_type=0,
                       align=0, content="hello")
    api.close_meeting_subtitle(100)
    api.operate_record(100, 2)
    api.rename_meeting_http(100, "new")
    api.set_user_manager(100, 7, 2)
    api.set_user_presenter(100, 7, 2)
    api.remove_user(100, 7)
    api.control_user_audio(100, 7, device_id=0, state=2)
    api.control_user_video(100, 7, device_id=1, state=2)
    api.rename_user(100, 7, "new")
    api.query_user_info(100, user_id=7)
    api.list_meeting_users(100)
    api.query_meeting_info(100)
    api.add_pull_stream(100, user_name="stream", leave_mode="0", protocol_param={"streamType": "rtsp"})
    api.delete_pull_stream(100, {"streamType": "rtsp"})
    api.add_subscription(100, "USER", "http://127.0.0.1:8088/events")
    api.delete_subscription(100, "USER_ENTER")
    api.list_subscriptions(room_id=100)

    assert len(calls) >= 70
    assert calls[-1] == ("GET", "/v1/control/subscriptions", None, {"room_id": 100})
    assert any(path == "/v1/user/addBatch" and body["number"] == 2 for _, path, body, _ in calls)
    assert any(path == "/v1/room/batchAddRoomInfo" and isinstance(body, list)
               for _, path, body, _ in calls)
    assert any(path == "/v1/room/authUser" and body["roomUserStr"] == "u1,2"
               for _, path, body, _ in calls)
    assert any(
        method == "POST"
        and path == "/v1/control/100"
        and body == {"operate": "USER_REMOVE", "param": {"userId": 7}}
        for method, path, body, _ in calls
    )
