"""Redact credentials and session secrets before attaching diagnostics."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SENSITIVE_KEYS = {
    "password", "passwd", "pwd", "app_secret", "appsecret", "secret",
    "token", "authorization", "access_token", "refreshtoken", "refresh_token",
}


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).replace("-", "_").lower()
    compact = normalized.replace("_", "")
    return (
        normalized in SENSITIVE_KEYS or
        compact in SENSITIVE_KEYS or
        normalized.endswith(("_password", "_passwd", "_pwd", "_secret",
                             "_token", "_auth_code", "_check_code")) or
        compact.endswith(("password", "passwd", "secret", "token",
                          "pwd", "authcode", "checkcode"))
    )


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {
            key: "***REDACTED***" if _is_sensitive_key(key)
            else redact(item)
            for key, item in value.items()
        }
        # SDK管理员接口把密码作为第一个位置参数，需按公开方法契约脱敏。
        method=str(value.get('method_name',value.get('方法','')))
        if method=='HST.applyManagerRight':
            parameter_key='params' if 'params' in result else '参数'
            args=result.get(parameter_key)
            if isinstance(args,list) and args:
                result[parameter_key]=['***REDACTED***',*args[1:]]
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value,str) and value.lstrip().startswith(('{','[')):
        try:
            parsed=json.loads(value)
            masked=redact(parsed)
            if masked!=parsed:
                return json.dumps(masked,ensure_ascii=False)
        except (ValueError,TypeError):
            pass
    if isinstance(value, str) and value.startswith(("ws://", "wss://", "http://", "https://")):
        parsed = urlsplit(value)
        if parsed.query:
            query = [(key, "***REDACTED***" if _is_sensitive_key(key) else item)
                     for key, item in parse_qsl(parsed.query, keep_blank_values=True)]
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    return value
