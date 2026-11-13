from pathlib import Path

import pytest

from meeting_test_runtime.config import TestEnvironmentConfig as EnvironmentConfig
from meeting_test_runtime.http_driver import WebApiServiceDriver
from meeting_test_runtime.models import ServiceProfile
from meeting_test_runtime.resource_manager import TestResourceManager as ResourceManager


def test_ini_config_reads_secrets_and_types(tmp_path):
    path = tmp_path / "env.ini"
    path.write_text(
        "[operator]\npassword = secret\n[http]\napp_secret = app-secret\nverify_tls = false\ntimeout = 12.5\n",
        encoding="utf-8")
    config = EnvironmentConfig(path)
    assert config.get("operator", "password") == "secret"
    assert config.get("http", "app_secret") == "app-secret"
    assert not config.getbool("http", "verify_tls")
    assert config.getfloat("http", "timeout") == 12.5
    assert config.get_protocol_version() == "2.0"


def test_destructive_cases_support_process_environment_override(tmp_path, monkeypatch):
    path = tmp_path / "env.ini"
    path.write_text(
        "[execution]\nrun_destructive_cases = false\n", encoding="utf-8")
    config = EnvironmentConfig(path)
    assert not config.run_destructive_cases()

    monkeypatch.setenv("FM_RUN_DESTRUCTIVE_CASES", "true")
    assert config.run_destructive_cases()


@pytest.mark.parametrize("value", ["0", "0.0", "v2", "2.x", "-1", ""])
def test_ini_config_rejects_invalid_conf_protocol_version(tmp_path, value):
    path = tmp_path / "env.ini"
    path.write_text(
        f"[fm_server]\nconf_protocol_version = {value}\n", encoding="utf-8")
    config = EnvironmentConfig(path)
    with pytest.raises(ValueError, match="conf_protocol_version"):
        config.get_protocol_version(fallback=value)


def test_http_driver_uses_explicit_whitelist(monkeypatch):
    calls = []
    class FakeApi:
        def create_instant_meeting(self, **params):
            calls.append(params); return {"code": 0}
    monkeypatch.setattr(
        "meeting_test_runtime.http_driver.WebApiClient.from_config",
        lambda config: FakeApi())
    driver = WebApiServiceDriver(ServiceProfile("HTTP", "web_api", {
        "base_url": "http://127.0.0.1", "app_key": "k", "app_secret": "s"}))
    assert driver.call("create_instant_meeting", {"meeting_name": "m"}) == {"code": 0}
    assert calls == [{"meeting_name": "m"}]
    with pytest.raises(ValueError, match="unregistered HTTP operation"):
        driver.call("request", {})


def test_resource_manager_cleans_in_reverse_and_continues_after_failure():
    manager = ResourceManager(); calls = []
    manager.register("first", calls.append, "first")
    manager.register("bad", lambda: (_ for _ in ()).throw(RuntimeError("bad")))
    manager.register("last", calls.append, "last")
    failures = manager.cleanup()
    assert calls == ["last", "first"]
    assert failures[0][0] == "bad"
