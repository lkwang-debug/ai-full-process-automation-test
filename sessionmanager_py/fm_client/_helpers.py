"""FmClient 核心与各域操作 mixin 共用的内部工具。"""
from __future__ import annotations

from typing import Any


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> list:
    """None/空串→[]，单值→[value]，list→原样（解析重复子元素时常见）。

    空串视作空列表：真机 6601 的 ``<DeptInfo>`` 在未配置组织架构时常解析成 ``{'Dept': ''}``
    或空串，不应当成一个"空部门"。"""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def _dept_list(dept_info: Any) -> list:
    """6601 的 DeptInfo 是 {Dept: [...]} 容器（或裸 Dept）；归一成 list。"""
    if isinstance(dept_info, dict):
        return _as_list(dept_info.get("Dept"))
    return _as_list(dept_info)
