"""Driver registry for TestClient packages supplied by development teams."""
from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional, Protocol, Set

from .models import ClientProfile


class TestClientDriver(Protocol):
    """Minimum contract implemented by each test-side client wrapper."""

    client_type: str
    capabilities: Set[str]

    @property
    def identity(self) -> Mapping[str, Any]: ...

    def join(self, room_id: int) -> Mapping[str, Any]: ...

    def call(self, operation: str, params: Optional[dict] = None) -> Any: ...

    def mark_events(self) -> int: ...

    def wait_event(
        self, expectation: Any, after: int, timeout: float,
    ) -> Any: ...

    def expect_no_event(
        self, expectation: Any, after: int, timeout: float,
    ) -> None: ...

    def snapshot_events(self, after: int = 0) -> list: ...

    def mark_outbound(self) -> Any: ...

    def snapshot_outbound(self, after: Any) -> list: ...

    def reset(self, operation: str, params: Optional[dict] = None) -> None: ...

    def is_healthy(self) -> bool: ...

    def close(self) -> None: ...


DriverFactory = Callable[[ClientProfile], TestClientDriver]
_DRIVER_FACTORIES: Dict[str, DriverFactory] = {}


def register_client_driver(
    client_type: str, factory: DriverFactory, *, replace: bool = False,
) -> None:
    """Register a test-side wrapper without changing the native TestClient."""
    key = client_type.strip().lower()
    if not key:
        raise ValueError("client_type cannot be empty")
    if key in _DRIVER_FACTORIES and not replace:
        raise ValueError(f"client driver already registered: {key}")
    _DRIVER_FACTORIES[key] = factory


def create_client_driver(profile: ClientProfile) -> TestClientDriver:
    key = profile.client_type.strip().lower()
    try:
        factory = _DRIVER_FACTORIES[key]
    except KeyError as exc:
        raise ValueError(f"unregistered client type: {key}") from exc
    return factory(profile)


def registered_client_types() -> Set[str]:
    return set(_DRIVER_FACTORIES)
