import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from examples import fm_invited_join_auto as setup


@pytest.fixture
def discovery(fm_mock_server):
    state = {"requests": [], "auth_code": 1, "cp_result": "TCP:127.0.0.1:50003;"}
    service = {"client_id": "test-service", "client_secret": "test-service-secret",
               "config_center_key": "test-config-key", "terminal_type": "PC"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.do_GET()

        def do_GET(self):
            parsed = urlsplit(self.path)
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"]))) if self.headers.get("Content-Length") else None
            state["requests"].append((self.command, parsed, dict(self.headers), body))
            if parsed.path == "/meeting-resource-server/open/paasAuthInfo":
                result = {"resCode": state["auth_code"], "resMessage": "SECRET must not be printed",
                    "result": {"appId": "a" * 32, "token": "fresh-test-token",
                               "exclusionGroup": {"PC": 7}}}
            elif parsed.path == "/fmapi/servers/addresses":
                result = {"code": 0, "result": [{"type": "UNRELATED", "addresses": [{"url": "ignored"}]},
                    {"type": "FSP_ACCESS", "addresses": [{"url": state["base"] + "/ngproxy/http/access/server/address"}]}]}
            else:
                result = {"code": 0, "result": state["cp_result"]}
            data = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["base"] = f"http://127.0.0.1:{server.server_port}"
    cfg = {"host": fm_mock_server.host, "port": fm_mock_server.port,
           "accounts": [{"username": "A", "password": "a"}, {"username": "B", "password": "b"}]}
    api = setup.WebApiClient(state["base"] + "/api", "http-key", "http-secret")
    try:
        yield cfg, api, service, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_discovery_uses_authenticated_identity_and_separate_credentials(discovery, fm_mock_server, capsys, monkeypatch):
    cfg, api, service, state = discovery
    # Production Front keeps an account's ID across logins; the generic mock
    # otherwise allocates a different ID for every connection.
    monkeypatch.setattr(fm_mock_server, "_alloc_uid", lambda: 1001)
    generated = setup.generate_config(cfg, api, service, timeout=1)
    assert generated["user_id"] == "1001"  # Front result, never hardcoded A8.
    assert generated["token"] == "fresh-test-token"
    assert generated["cp_address"] == state["cp_result"]
    assert generated["mutex_type"] == "7" and generated["force_login"] == 0
    auth, access, cp = state["requests"]
    assert parse_qs(auth[1].query) == {"userId": [generated["user_id"]]}
    assert auth[2]["Authorization"] == "Basic " + base64.b64encode(b"test-service:test-service-secret").decode()
    assert access[0] == "POST" and access[3]["appkey"] == "test-config-key"
    assert "Authorization" not in access[2] and "Authorization" not in cp[2]
    assert parse_qs(cp[1].query) == {"appType": ["3"], "appId": ["a" * 32]}
    ids = [cmd.cmdid for _, cmd in fm_mock_server.received_commands]
    assert 30000 in ids and 30010 in ids and 30004 not in ids and 6501 not in ids
    second = setup.generate_config(cfg, api, service, previous=generated, timeout=1)
    assert second["client_guid"] == generated["client_guid"]
    monkeypatch.setattr(fm_mock_server, "_alloc_uid", lambda: 1002)
    unrelated = setup.generate_config(cfg, api, service, previous=generated, timeout=1)
    assert unrelated["client_guid"] != generated["client_guid"]
    output = capsys.readouterr().out
    assert "fresh-test-token" not in output and "test-service-secret" not in output


@pytest.mark.parametrize("failure", ["credentials", "wrong_host", "auth", "tcp"])
def test_discovery_fails_at_actual_stage_without_leaking_payload(discovery, failure):
    cfg, api, service, state = discovery
    if failure == "credentials":
        service["client_secret"] = "REPLACE_WITH_SECRET"
        match = "requires client_secret"
    elif failure == "wrong_host":
        cfg["host"] = "wrong.example"
        match = "hosts must match"
    elif failure == "auth":
        state["auth_code"] = -1
        match = "PaaS auth: resCode=-1"
    else:
        state["cp_result"] = "invalid-secret-server-payload"
        match = "invalid TCP address"
    with pytest.raises(setup.BootstrapError, match=match) as exc:
        setup.generate_config(cfg, api, service, timeout=1)
    assert "SECRET" not in str(exc.value) and "invalid-secret-server-payload" not in str(exc.value)
    if failure in ("credentials", "wrong_host"):
        assert not state["requests"]


def write_inputs(tmp_path, discovery):
    cfg, api, service, state = discovery
    configs = {"multi.json": cfg, "http.json": {"base_url": api.base_url, "app_key": api.app_key,
        "app_secret": api.app_secret}, "bootstrap.json": service}
    for name, value in configs.items():
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    output = tmp_path / "online.json"
    args = ["--config", str(tmp_path / "multi.json"), "--http-config", str(tmp_path / "http.json"),
            "--bootstrap-config", str(tmp_path / "bootstrap.json"), "--online-config", str(output), "--timeout", "1"]
    return args, output


@pytest.mark.parametrize("mode", ["generate", "both", "manual", "failed_test"])
def test_cli_generates_then_runs_requested_demo_and_propagates_failure(discovery, tmp_path, monkeypatch, capsys, mode):
    args, output = write_inputs(tmp_path, discovery)
    calls = []
    monkeypatch.setattr(setup.PaasOnlineClient, "login", lambda self: {"result": 0})

    def demo(argv):
        # Output must already exist when the receiver starts.
        assert json.loads(output.read_text())["token"] == "fresh-test-token"
        calls.append(argv)
        return 1 if mode == "failed_test" else 0

    monkeypatch.setattr(setup.fm_invited_join_demo, "main", demo)
    switches = {"generate": ["--generate-only"], "both": ["--invite-method", "both"],
                "manual": ["--manual"], "failed_test": []}[mode]
    assert setup.main(args + switches) == (1 if mode == "failed_test" else 0)
    assert len(calls) == {"generate": 0, "both": 2, "manual": 1, "failed_test": 1}[mode]
    if mode == "both":
        assert [call[call.index("--invite-method") + 1] for call in calls] == ["invite_users", "invite_contacts"]
    if mode == "manual":
        assert "--http-config" not in calls[0]
    if os.name == "posix":
        assert output.stat().st_mode & 0o777 == 0o600
    printed = capsys.readouterr().out
    assert "fresh-test-token" not in printed and "test-service-secret" not in printed
    if mode == "generate":
        assert "invitation was not tested" in printed


def test_failed_login_preserves_previous_config_and_does_not_invite(discovery, tmp_path, monkeypatch, capsys):
    args, output = write_inputs(tmp_path, discovery)
    old = '{"token":"old-working-token"}\n'
    output.write_text(old, encoding="utf-8")

    def denied(self):
        raise setup.BootstrapError("PaaS login refused")

    monkeypatch.setattr(setup.PaasOnlineClient, "login", denied)
    calls = []
    monkeypatch.setattr(setup.fm_invited_join_demo, "main", lambda argv: calls.append(argv))
    assert setup.main(args) == 1
    assert output.read_text() == old and calls == []
    assert "[CONFIG]" not in capsys.readouterr().out
    assert not list(tmp_path.glob(".paas-online-*"))


def test_refuses_to_overwrite_input_config(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{}")
    with pytest.raises(SystemExit):
        setup.main(["--config", str(path), "--online-config", str(path)])
    assert path.read_text() == "{}"


@pytest.mark.parametrize("contents", ["", " \n\t"])
@pytest.mark.parametrize("login_ok", [True, False])
def test_empty_output_is_regenerated_only_after_successful_login(discovery, tmp_path, monkeypatch, capsys, contents, login_ok):
    args, output = write_inputs(tmp_path, discovery)
    output.write_text(contents, encoding="utf-8")

    def login(self):
        # The old file is untouched until the new credentials work.
        assert output.read_text() == contents
        if not login_ok:
            raise setup.BootstrapError("PaaS login refused")
        return {"result": 0}

    monkeypatch.setattr(setup.PaasOnlineClient, "login", login)
    assert setup.main(args + ["--generate-only"]) == (0 if login_ok else 1)
    if login_ok:
        assert json.loads(output.read_text())["token"] == "fresh-test-token"
    else:
        assert output.read_text() == contents
    printed = capsys.readouterr().out
    assert "[INFO] empty output " + str(output) in printed
    assert "fresh-test-token" not in printed


@pytest.mark.parametrize("filename", ["multi.json", "http.json", "bootstrap.json", "online.json"])
def test_invalid_config_reports_exact_file_without_values_or_network(discovery, tmp_path, capsys, filename):
    args, output = write_inputs(tmp_path, discovery)
    path = tmp_path / filename
    invalid = '{\n"secret": "test-private-value",\n}'
    path.write_text(invalid, encoding="utf-8")
    assert setup.main(args + ["--generate-only"]) == 1
    printed = capsys.readouterr().out
    assert "invalid JSON in " + str(path) in printed
    assert "line 3, column 1" in printed
    assert "test-private-value" not in printed
    assert path.read_text() == invalid
    assert discovery[3]["requests"] == []


@pytest.mark.parametrize("filename", ["multi.json", "http.json", "bootstrap.json"])
def test_empty_required_input_is_an_error(discovery, tmp_path, capsys, filename):
    args, output = write_inputs(tmp_path, discovery)
    path = tmp_path / filename
    path.write_text("", encoding="utf-8")
    assert setup.main(args + ["--generate-only"]) == 1
    printed = capsys.readouterr().out
    assert "invalid JSON in " + str(path) in printed
    assert "line 1, column 1" in printed
    assert not output.exists() and not discovery[3]["requests"]
