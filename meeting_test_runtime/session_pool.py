"""Reusable external test runtimes for non-destructive cases."""
from __future__ import annotations

from typing import Callable, Dict

from .models import SessionKey
from .runtime import MeetingTestRuntime


RuntimeBuilder = Callable[[], MeetingTestRuntime]


class MeetingSessionPool:
    def __init__(self) -> None:
        self._sessions: Dict[SessionKey, MeetingTestRuntime] = {}

    def acquire(
        self, key: SessionKey, builder: RuntimeBuilder, *, isolated: bool = False,
    ) -> MeetingTestRuntime:
        if isolated:
            return builder()
        runtime = self._sessions.get(key)
        if runtime is None or not runtime.is_healthy():
            if runtime is not None:
                runtime.close_all()
            runtime = builder()
            self._sessions[key] = runtime
        return runtime

    def release(
        self, key: SessionKey, runtime: MeetingTestRuntime, *, isolated: bool = False,
    ) -> None:
        if isolated:
            runtime.close_all()
            return
        if not runtime.is_healthy():
            self._sessions.pop(key, None)
            runtime.close_all()

    def invalidate(self, key: SessionKey) -> None:
        runtime = self._sessions.pop(key, None)
        if runtime is not None:
            runtime.close_all()

    def invalidate_conflicts(self, key: SessionKey, *, include_exact: bool = True) -> int:
        """Close cached sessions which would reuse this meeting and its clients."""
        conflicts = [
            cached_key for cached_key in self._sessions
            if cached_key.room_id == key.room_id
            and set(cached_key.roles).intersection(key.roles)
            and (include_exact or cached_key != key)
        ]
        for cached_key in conflicts:
            self.invalidate(cached_key)
        return len(conflicts)

    def close_all(self) -> None:
        errors=[]
        for runtime in list(self._sessions.values()):
            try: runtime.close_all()
            except Exception as exc: errors.append(str(exc))
        self._sessions.clear()
        if errors: raise RuntimeError(f'会话池清理失败: {errors}')
