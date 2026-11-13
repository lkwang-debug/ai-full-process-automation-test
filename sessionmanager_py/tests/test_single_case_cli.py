from __future__ import annotations

import pytest

from examples import fm_single_case as single


@pytest.mark.parametrize("case_name", sorted(single.CONF_CASES))
def test_each_builtin_conf_case_runs_individually_with_mock(case_name, capsys):
    argv = ["conf", "--mock", "--case", case_name, "--timeout", "3"]
    case = single.CONF_CASES[case_name]
    if case.destructive:
        argv.append("--allow-destructive")
    if case_name == "terminal_call":
        argv.extend(["--params", '{"terminal_sns":"8001"}'])

    assert single.main(argv) == 0
    output = capsys.readouterr()
    assert f"[PASS] {case_name}" in output.out
    assert output.err == ""


def test_generic_conf_method_supports_references_event_and_cleanup(capsys):
    result = single.main([
        "conf", "--mock",
        "--method", "set_user_audio",
        "--args", '["$observer.room_user_id", 2]',
        "--expect-cmdid", "6609",
        "--expect-fields", '{"UserID":"$observer.room_user_id","Audio.State":2}',
        "--cleanup-method", "set_user_audio",
        "--cleanup-args", '["$observer.room_user_id", 0]',
        "--timeout", "3",
    ])

    assert result == 0
    assert "[PASS] set_user_audio" in capsys.readouterr().out


def test_server_fix_regression_uses_mainline_single_offline_query(capsys):
    assert single.main([
        "conf", "--mock", "--case", "server_fix_regression", "--timeout", "3",
    ]) == 0
    output = capsys.readouterr().out
    assert '"offline_users":' in output
    assert '"offline_users_repeat":' not in output


def test_offline_users_repeat_uses_mainline_session_cache(capsys):
    assert single.main([
        "conf", "--mock", "--case", "offline_users_repeat", "--timeout", "3",
    ]) == 0
    output = capsys.readouterr().out
    assert '"second_source": "conf-session-cache"' in output


def test_list_exposes_conf_cases_and_http_business_methods(capsys):
    assert single.main(["list"]) == 0
    output = capsys.readouterr().out
    assert "user_audio" in output
    assert "create_instant_meeting" in output
    assert "request" not in single._http_methods()


def test_http_single_method_checks_response(monkeypatch, capsys):
    class FakeApi:
        def list_departments(self, **kwargs):
            assert kwargs == {"page_number": 1}
            return {"code": "0", "data": {"total": 2}}

    monkeypatch.setattr(
        single.WebApiClient, "from_config", staticmethod(lambda _path: FakeApi()))

    assert single.main([
        "http",
        "--http-config", "unused.json",
        "--method", "list_departments",
        "--kwargs", '{"page_number":1}',
        "--expect", '{"code":0,"data.total":2}',
    ]) == 0
    assert "[PASS] list_departments" in capsys.readouterr().out


def test_http_state_change_requires_explicit_opt_in(monkeypatch, capsys):
    class FakeApi:
        def add_department(self, **_kwargs):
            raise AssertionError("must not be called without opt-in")

    monkeypatch.setattr(
        single.WebApiClient, "from_config", staticmethod(lambda _path: FakeApi()))

    assert single.main([
        "http",
        "--http-config", "unused.json",
        "--method", "add_department",
    ]) == 1
    assert "--allow-destructive" in capsys.readouterr().err


def test_destructive_case_requires_explicit_opt_in(capsys):
    assert single.main([
        "conf", "--mock", "--case", "close_room",
    ]) == 1
    assert "--allow-destructive" in capsys.readouterr().err
