"""LIFO cleanup registry for HTTP-created test resources."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List


@dataclass
class CleanupAction:
    name: str
    callback: Callable[..., Any]
    args: tuple
    kwargs: dict


class TestResourceManager:
    def __init__(self) -> None:
        self._actions: List[CleanupAction] = []

    def register(self, name: str, callback: Callable[..., Any], *args, **kwargs) -> None:
        self._actions.append(CleanupAction(name, callback, args, kwargs))

    def cleanup(self) -> list[tuple[str, Exception]]:
        failures = []
        while self._actions:
            action = self._actions.pop()
            try:
                action.callback(*action.args, **action.kwargs)
            except Exception as exc:  # cleanup must continue
                failures.append((action.name, exc))
        return failures
