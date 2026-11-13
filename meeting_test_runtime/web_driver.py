"""Adapter for the product-supported Web SDK operations, without touching FmTestClient."""
from __future__ import annotations

from .drivers import register_client_driver
from .web_client import WebTestClient
from .fm_driver import FmClientDriver
from urllib.parse import urlsplit


class WebClientDriver:
    client_type = "web"
    capabilities = {"received_event", "public_chat", "self_av", "remote_av", "presenter",
                    "manager", "screen_share", "whiteboard", "record", "user_info"}

    def __init__(self, profile, *, worker=None):
        self.profile = profile
        self.native_client = WebTestClient(profile, worker=worker)

    @property
    def identity(self):
        return self.native_client.identity

    @property
    def conf_protocol_version(self):
        # 4.0 is the actual ClientVer of this SDK, not a test-side rewrite.
        return "web-client-4.0/sdk-1.8.18"

    def join(self, room_id):
        return self.native_client.join(room_id)

    def call(self, operation, params=None):
        result = self.native_client.call(operation, params)
        self.native_client.refresh_identity()
        return result

    def mark_events(self):
        return self.native_client.events.mark()

    def wait_event(self, expectation, after, timeout):
        event = self.native_client.events.wait(expectation, after, timeout)
        if event is None:
            received = self.snapshot_events(after)
            raise AssertionError(f"Web event timeout: {expectation}; received={received}")
        return event

    def expect_no_event(self, expectation, after, timeout):
        self.native_client.events.assert_no(expectation, after, timeout)

    assert_no_event = expect_no_event

    def snapshot_events(self, after=0):
        return self.native_client.events.as_dicts(after)

    def mark_outbound(self):
        return self.native_client.mark_outbound()

    def snapshot_outbound(self, after=0):
        return self.native_client.snapshot_outbound(after)

    def reset(self, operation, params=None):
        inverse = {"mic_on":"mic_off", "camera_on":"camera_off", "start_record":"stop_record",
                   "request_presenter":"release_presenter", "request_manager":"release_manager",
                   "start_screen_share":"stop_screen_share", "start_whiteboard":"stop_whiteboard"}
        if operation in inverse:
            self.call(inverse[operation], params)
            return inverse[operation], dict(params or {})
        return None

    def is_healthy(self):
        return self.native_client.is_healthy()

    def close(self):
        self.native_client.close()


def register_web_driver():
    register_client_driver("web", WebClientDriver)


def _allowed_conf_servers(servers, allowed_hosts):
    """只选配置白名单内的实际地址，不把双栈别名当成必须连接的地址。"""
    allowed = {str(host).strip().lower() for host in allowed_hosts}
    selected = []
    discovered = []
    for server in servers:
        addresses = []
        for address in str(server.get("SrvAddr") or "").split(";"):
            address = address.strip()
            if not address:
                continue
            host = urlsplit("tcp://" + address.split(":", 1)[1]).hostname
            discovered.append(host)
            if host in allowed:
                addresses.append(address)
        if addresses:
            selected.append({**server, "SrvAddr": ";".join(addresses) + ";"})
    if not selected:
        raise RuntimeError(f"Front路由超出已配置测试集群: {discovered}")
    return selected


class CrossEndpointFmDriver(FmClientDriver):
    """Mixed sessions follow Front routing within the explicitly allowed cluster."""
    def join(self, room_id):
        account, endpoint=self.profile.account,self.profile.endpoint
        front=self.native_client.front_login(account['username'],account['password'],room_id=int(room_id),
            room_node_id=str(endpoint['room_node_id']),room_password=account.get('room_password'))
        self.native_client.conf_servers = _allowed_conf_servers(
            self.native_client.conf_servers, endpoint['allowed_conf_hosts'])
        conf=self.native_client.conf_login(user_name=account['username'],support_features=self._support_features())
        return {'front':front,'conf':conf}
