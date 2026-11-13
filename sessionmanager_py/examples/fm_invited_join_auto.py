"""Generate real PaaS credentials, check online login, then test invited join.

Uses only this SDK and local JSON configuration; no C++ checkout is required.
The deployment's PaaS service credentials are separate from HTTP API keys.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import platform
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit, urlunsplit
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fm_client import FmClient, PaasOnlineClient, WebApiClient
from framecore_session.address import parse_tcp_address_link
from examples import fm_invited_join_demo


class BootstrapError(RuntimeError):
    """A configuration/discovery failure, without credential-bearing payloads."""


def read_config(path, *, generated_output=False):
    """Read a JSON object with file-specific diagnostics, never echoing values.

    The generated output is optional: a missing/empty file has no device ID to
    reuse. Nonempty malformed files and all invalid inputs remain errors.
    """
    path = Path(path).resolve()
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if generated_output:
            return {}
        raise BootstrapError(f"config file not found: {path}") from None
    except UnicodeError:
        raise BootstrapError(f"config file is not valid UTF-8: {path}") from None
    except OSError as exc:
        raise BootstrapError(f"cannot read config file {path}: {exc.strerror}") from None
    if generated_output and not contents.strip():
        print(f"[INFO] empty output {path}; will generate a new configuration", flush=True)
        return {}
    try:
        result = json.loads(contents)
    except json.JSONDecodeError as exc:
        raise BootstrapError(f"invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}") from None
    if not isinstance(result, dict):
        raise BootstrapError(f"config file must contain a JSON object: {path}")
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward service credentials to a redirected endpoint.
        return None


def http_json(url, *, stage, timeout, verify_tls=True, body=None, headers=None):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise BootstrapError(stage + ": invalid HTTP endpoint")
    request = urllib.request.Request(url,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})})
    context = ssl.create_default_context() if verify_tls else ssl._create_unverified_context()
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise BootstrapError(f"{stage}: HTTP {exc.code}") from None
    except (OSError, ValueError) as exc:
        raise BootstrapError(f"{stage}: {type(exc).__name__}; check endpoint/connectivity/JSON response") from None
    if not isinstance(result, dict):
        raise BootstrapError(stage + ": expected a JSON object")
    return result


def response_result(message, *, stage, key="code", success=0):
    if str(message.get(key)) != str(success):
        # Do not echo a server message/body that may contain tokens.
        code = message.get(key)
        code = str(code) if str(code).lstrip("-").isdigit() else "missing/invalid"
        raise BootstrapError(f"{stage}: {key}={code}")
    if "result" not in message:
        raise BootstrapError(stage + ": missing result")
    return message["result"]


def generate_config(cfg, api, service, *, account_index=1, previous=None, timeout=10):
    """Authenticate the selected Front user and discover fresh PaaS credentials."""
    for key in ("client_id", "client_secret", "config_center_key"):
        value = service.get(key)
        if not isinstance(value, str) or not value or value.startswith("REPLACE_WITH_"):
            raise BootstrapError("paas_bootstrap.json requires " + key)
    parts = urlsplit(api.base_url)
    if parts.scheme not in ("http", "https") or parts.hostname != cfg["host"]:
        raise BootstrapError("HTTP and Front config hosts must match")
    accounts = cfg.get("accounts") or []
    if not 0 <= account_index < len(accounts):
        raise BootstrapError("account-index is not in config.accounts")
    center = service.get("config_center_url") or urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    center_parts = urlsplit(center)
    if (center_parts.scheme not in ("http", "https") or not center_parts.hostname
            or center_parts.query or center_parts.fragment or center_parts.username or center_parts.password):
        raise BootstrapError("invalid config_center_url")
    center = center.rstrip("/")
    client = FmClient(cfg["host"], int(cfg.get("port", 1089)), request_timeout=timeout)
    try:
        account = accounts[account_index]
        client.front_authenticate(account["username"], account.get("password", ""), timeout=timeout)
        user_id = str(client.local_user_id)
        if not user_id or user_id == "None":
            raise BootstrapError("Front did not return LocalUserID")
    finally:
        try:
            client.front_logout(exit_room=False, timeout=timeout)
        finally:
            client.close()
    print(f"[IDENTITY] server={cfg['host']}; account-index={account_index}; user={user_id}", flush=True)
    options = {"timeout": timeout, "verify_tls": api.verify_tls}
    auth = base64.b64encode((service["client_id"] + ":" + service["client_secret"]).encode()).decode()
    auth_info = response_result(http_json(
        center + "/meeting-resource-server/open/paasAuthInfo?" + urlencode({"userId": user_id}),
        stage="PaaS auth", headers={"Authorization": "Basic " + auth}, **options),
        stage="PaaS auth", key="resCode", success=1)
    if not isinstance(auth_info, dict):
        raise BootstrapError("PaaS auth: expected result object")
    app_id, token = auth_info.get("appId"), auth_info.get("token")
    if not isinstance(app_id, str) or not isinstance(token, str):
        raise BootstrapError("PaaS auth: missing appId/token")
    system = platform.system()
    terminal = service.get("terminal_type") or ("MAC" if system == "Darwin" else "PC")
    groups = auth_info.get("exclusionGroup") or auth_info.get("terminalInfo") or {}
    mutex = groups.get(terminal) if isinstance(groups, dict) else None
    if mutex is None or isinstance(mutex, (bool, dict, list)) or not str(mutex):
        raise BootstrapError("PaaS auth: missing terminal category; check terminal_type in paas_bootstrap.json")
    access = response_result(http_json(center + "/fmapi/servers/addresses", stage="FSP_ACCESS",
        body={"appkey": service["config_center_key"],
              "version": service.get("config_center_version", "03.15.02.03"), "type": "FSP_ACCESS"},
        **options), stage="FSP_ACCESS")
    if not isinstance(access, list):
        raise BootstrapError("FSP_ACCESS: expected result list")
    addresses = []
    for item in access:
        if not isinstance(item, dict) or item.get("type") != "FSP_ACCESS":
            continue
        for entry in item.get("addresses") or []:
            address = entry.get("url") if isinstance(entry, dict) else None
            if isinstance(address, str) and address and address not in addresses:
                addresses.append(address)
    if not addresses:
        raise BootstrapError("FSP_ACCESS: no access address returned")
    cp_address = None
    last_error = None
    for address in addresses:
        try:
            if address.upper().startswith(("TCP:", "V6TCP:")):
                candidate = address
            else:
                parsed = urlsplit(address)
                path = parsed.path.rstrip("/")
                if not path.endswith("/access/server/address"):
                    path += "/access/server/address"
                query = parsed.query + ("&" if parsed.query else "") + urlencode({"appType": 3, "appId": app_id})
                url = urlunsplit((parsed.scheme, parsed.netloc, path, query, ""))
                candidate = response_result(http_json(url, stage="CP address", **options), stage="CP address")
            parse_tcp_address_link(candidate)
            cp_address = candidate
            break
        except (BootstrapError, ValueError) as exc:
            # Parser exceptions can contain a server payload; keep only safe diagnostics.
            last_error = str(exc) if isinstance(exc, BootstrapError) else "CP address: invalid TCP address link"
    if cp_address is None:
        raise BootstrapError(last_error or "CP address: discovery failed")
    previous = previous or {}
    guid = previous.get("client_guid") if (previous.get("user_id") == user_id
        and previous.get("app_id") == app_id) else None
    if not isinstance(guid, str) or not guid or guid.startswith("REPLACE_WITH_"):
        guid = str(uuid.uuid4())
    generated = {"_说明": "自动获取的真实 PaaS 配置；包含用户 token，请勿随通用 SDK 分发。",
        "_server": cfg["host"], "cp_address": cp_address, "app_id": app_id, "token": token,
        "user_id": user_id, "client_guid": guid,
        "protocol_version": service.get("protocol_version", "1.0.0"),
        "mutex_type": str(mutex), "os_type": service.get("os_type") or
            {"Darwin": "macOS", "Windows": "windows"}.get(system, "linux"),
        "registered_user": 1, "force_login": 0}
    for key in ("company_id", "domain", "service_id", "service_type", "sdk_type", "media_source", "extend_info"):
        if key in service:
            generated[key] = service[key]
    PaasOnlineClient(generated, timeout=timeout)  # Validate before login or file replacement.
    print(f"[DISCOVERED] user={user_id}; CP={cp_address.rstrip(';')}; fresh token obtained (hidden)", flush=True)
    return generated


def save_config(path, config):
    """Replace only the destination, atomically, with mode 0600 on POSIX."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".paas-online-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    examples = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(examples / "multi_client.json"))
    parser.add_argument("--http-config", default=str(examples / "http_api.json"))
    parser.add_argument("--bootstrap-config", default=str(examples / "paas_bootstrap.json"),
                        help="deployment service credentials; see paas_bootstrap.example.json")
    parser.add_argument("--online-config", default=str(examples / "paas_online.json"), help="generated output")
    parser.add_argument("--account-index", type=int, default=1, help="invitee; default accounts[1]")
    parser.add_argument("--invite-method", choices=("invite_users", "invite_contacts", "both"), default="invite_users")
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--wait", type=float, default=30)
    parser.add_argument("--stay", type=float, default=0)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--generate-only", action="store_true", help="generate and verify online login without inviting")
    mode.add_argument("--manual", action="store_true", help="generate then wait for a manual backend invitation")
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.wait <= 0 or args.stay < 0:
        parser.error("timeout/wait must be positive; stay must be nonnegative")
    if not (args.generate_only or args.manual) and args.account_index == 0:
        parser.error("automatic inviter uses accounts[0]; select a different invitee")
    output = Path(args.online_config).resolve()
    if output in {Path(p).resolve() for p in (args.config, args.http_config, args.bootstrap_config)}:
        parser.error("online-config output must differ from input files")
    try:
        if not Path(args.bootstrap_config).is_file():
            raise BootstrapError("missing paas_bootstrap.json; copy paas_bootstrap.example.json and fill deployment service credentials")
        cfg = read_config(args.config)
        api = WebApiClient.from_config(read_config(args.http_config))
        api.timeout = args.timeout
        service = read_config(args.bootstrap_config)
        previous = read_config(output, generated_output=True)
        previous = previous.get("paas", previous)
        if not isinstance(previous, dict):
            raise BootstrapError(f"paas section must contain a JSON object: {output}")
        generated = generate_config(cfg, api, service, account_index=args.account_index,
                                    previous=previous, timeout=args.timeout)
        online = PaasOnlineClient(generated, timeout=args.timeout)
        try:
            online.login()
            print("[CHECK] PaaS 10001 result=0", flush=True)
        finally:
            online.close()
        save_config(output, generated)
        print(f"[CONFIG] saved {output}; credentials hidden", flush=True)
        if args.generate_only:
            print("[PASS] config generation and online login; invitation was not tested", flush=True)
            return 0
        methods = ("invite_users", "invite_contacts") if args.invite_method == "both" else (args.invite_method,)
        if args.manual:
            methods = ("manual",)
        failures = []
        for method in methods:
            demo_args = ["--config", args.config, "--online-config", str(output),
                "--account-index", str(args.account_index), "--timeout", str(args.timeout),
                "--wait", str(args.wait), "--stay", str(args.stay)]
            if not args.manual:
                demo_args += ["--http-config", args.http_config, "--invite-method", method]
            print(f"[TEST] {method}", flush=True)
            if fm_invited_join_demo.main(demo_args) != 0:
                failures.append(method)
        print(f"summary: cases={len(methods)}, failures={failures}", flush=True)
        return int(bool(failures))
    except Exception as exc:
        print(f"[FAIL] setup/test: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
