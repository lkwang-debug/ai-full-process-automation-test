from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


_TCP_PROTOCOLS = {"TCP", "V6TCP"}


@dataclass(frozen=True)
class SessionEndpoint:
    """One TCP candidate from a framecore server address link."""

    protocol: str
    host: str
    port: int

    def __str__(self) -> str:
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return f"{self.protocol}:{host}:{self.port}"


def parse_tcp_address_link(address_link: str) -> Tuple[SessionEndpoint, ...]:
    """Parse ``TCP:host:port;TCP:host2:port2`` into ordered candidates.

    Reliable sessions only use TCP, matching the C++ session manager. Other
    transport entries in a mixed address link are ignored.
    """
    if not isinstance(address_link, str) or not address_link.strip():
        raise ValueError("server address link must be a non-empty string")

    endpoints = []
    seen = set()
    for raw_item in address_link.split(";"):
        item = raw_item.strip()
        if not item:
            continue
        protocol, sep, authority = item.partition(":")
        protocol = protocol.strip().upper()
        if not sep or not protocol:
            raise ValueError(f"invalid server address item: {item!r}")
        if protocol not in _TCP_PROTOCOLS:
            continue

        host, port_text = _split_host_port(authority.strip(), item)
        try:
            port = int(port_text, 10)
        except ValueError as exc:
            raise ValueError(f"invalid port in server address item: {item!r}") from exc
        if not 1 <= port <= 65535:
            raise ValueError(f"port out of range in server address item: {item!r}")

        endpoint = SessionEndpoint(protocol, host, port)
        key = (host.lower(), port)
        if key not in seen:
            endpoints.append(endpoint)
            seen.add(key)

    if not endpoints:
        raise ValueError("server address link contains no TCP endpoint")
    return tuple(endpoints)


def coerce_tcp_endpoints(address: str, port: Optional[int] = None) -> Tuple[SessionEndpoint, ...]:
    """Accept either a C++-style address link or the legacy host/port pair."""
    if port is None:
        return parse_tcp_address_link(address)
    if not isinstance(address, str) or not address.strip():
        raise ValueError("host must be a non-empty string")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be in range 1..65535")
    host = address.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    protocol = "V6TCP" if ":" in host else "TCP"
    return (SessionEndpoint(protocol, host, port),)


def _split_host_port(authority: str, original: str) -> Tuple[str, str]:
    if authority.startswith("["):
        close = authority.find("]")
        if close < 0 or close + 1 >= len(authority) or authority[close + 1] != ":":
            raise ValueError(f"invalid IPv6 server address item: {original!r}")
        host = authority[1:close].strip()
        port_text = authority[close + 2:].strip()
    else:
        host, sep, port_text = authority.rpartition(":")
        host = host.strip()
        port_text = port_text.strip()
        if not sep:
            raise ValueError(f"missing port in server address item: {original!r}")
    if not host or not port_text:
        raise ValueError(f"invalid server address item: {original!r}")
    return host, port_text
