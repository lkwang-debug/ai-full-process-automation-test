from __future__ import annotations

import struct
import threading
from typing import List

from .constants import HEADER_SIZE, MAX_PACKET_SIZE
from .exceptions import ProtocolError

_SIZE_FIELD = struct.Struct("!H")


class PacketFramer:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self._lock = threading.Lock()

    def feed(self, data: bytes) -> List[bytes]:
        if not data:
            return []
        with self._lock:
            self._buffer.extend(data)
            packets: List[bytes] = []
            while len(self._buffer) >= HEADER_SIZE:
                size = _SIZE_FIELD.unpack_from(self._buffer, 0)[0]
                if size < HEADER_SIZE:
                    raise ProtocolError(f"invalid packet size {size}")
                if size > MAX_PACKET_SIZE:
                    raise ProtocolError(f"packet size {size} exceeds maximum")
                if len(self._buffer) < size:
                    break
                packets.append(bytes(self._buffer[:size]))
                del self._buffer[:size]
            return packets

    def reset(self) -> None:
        with self._lock:
            self._buffer.clear()
