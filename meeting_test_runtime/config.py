"""INI configuration for real-server end-to-end tests."""
from __future__ import annotations

import configparser
import os
import re
from pathlib import Path
from typing import Any, Optional


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "tests" / "e2e" / "config" / "test_env.ini"


class TestEnvironmentConfig:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or os.environ.get("FM_TEST_CONFIG", DEFAULT_CONFIG))
        self.parser = configparser.ConfigParser(interpolation=None)
        if not self.path.exists():
            raise FileNotFoundError(f"测试配置文件不存在: {self.path}")
        self.parser.read(self.path, encoding="utf-8-sig")

    def get(self, section: str, option: str, *, env: Optional[str] = None,
            fallback: Any = "") -> str:
        if env and os.environ.get(env) is not None:
            return os.environ[env]
        return self.parser.get(section, option, fallback=str(fallback)).strip()

    def getint(self, section: str, option: str, *, env: Optional[str] = None,
               fallback: int = 0) -> int:
        value = self.get(section, option, env=env, fallback=fallback)
        return int(value) if value else int(fallback)

    def getfloat(self, section: str, option: str, *, env: Optional[str] = None,
                 fallback: float = 0.0) -> float:
        value = self.get(section, option, env=env, fallback=fallback)
        return float(value) if value else float(fallback)

    def getbool(self, section: str, option: str, *, env: Optional[str] = None,
                fallback: bool = False) -> bool:
        value = self.get(section, option, env=env, fallback=str(fallback))
        return value.lower() in {"1", "true", "yes", "y", "是", "on"}

    def get_protocol_version(
        self, section: str = "fm_server", option: str = "conf_protocol_version",
        *, env: Optional[str] = "FM_CONF_PROTOCOL_VERSION", fallback: str = "2.0",
    ) -> str:
        """Read and validate a positive dotted numeric protocol version."""
        value = self.get(section, option, env=env, fallback=fallback)
        if not re.fullmatch(r"\d+(?:\.\d+)*", value):
            raise ValueError(
                f"invalid {section}.{option}: {value!r}; expected a positive version such as 2.0")
        if not any(int(part) > 0 for part in value.split(".")):
            raise ValueError(
                f"invalid {section}.{option}: {value!r}; version must be greater than zero")
        return value

    def missing(self, fields: list[tuple[str, str]]) -> list[str]:
        return [f"{section}.{option}" for section, option in fields
                if not self.get(section, option)]

    def get_host_list(self, section: str, option: str, *, fallback: str = '') -> list[str]:
        """Accept the INI's comma/semicolon host lists without changing routing."""
        value=self.get(section,option,fallback=fallback)
        return list(dict.fromkeys(host.strip() for host in re.split(r'[,;]',value) if host.strip()))

    def run_destructive_cases(self) -> bool:
        """Allow a one-process override without editing the shared INI file."""
        return self.getbool(
            "execution", "run_destructive_cases",
            env="FM_RUN_DESTRUCTIVE_CASES", fallback=False)
