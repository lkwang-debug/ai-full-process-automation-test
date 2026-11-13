from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import ClassVar, Tuple

from .constants import (
    DATAACK_TYPE_LAST,
    HEADER_SIZE,
    SESSIONPACKET_TYPE_DATA,
    SESSIONPACKET_TYPE_DATAACK,
    SESSIONPACKET_TYPE_HEARTBEAT,
    SESSIONPACKET_TYPE_HEARTBEATACK,
    SESSIONPACKET_TYPE_MAX,
    SESSIONPACKET_TYPE_PING,
    SESSIONPACKET_TYPE_PINGREP,
    SESSIONPACKET_TYPE_SESSIONACK,
    SESSIONPACKET_TYPE_SESSIONBYE,
    SESSIONPACKET_TYPE_SESSIONREQ,
    SESSIONPACKET_TYPE_SESSIONREREQ,
    SESSIONPROTOCOL_VERSION,
)
from .exceptions import ProtocolError

_HEADER = struct.Struct("!HHB")
HEADER_SIZE = _HEADER.size


def _type_version_byte(packet_type: int, version: int) -> int:
    return ((packet_type & 0x0F) << 4) | (version & 0x0F)


def _split_type_version(value: int) -> Tuple[int, int]:
    return (value >> 4) & 0x0F, value & 0x0F


def _require(data: bytes, body_len: int) -> None:
    """Validate that ``data`` carries at least HEADER_SIZE + body_len bytes.

    Guards every ``from_bytes`` against truncated packets so that a short
    buffer raises ProtocolError (handled by the caller) instead of leaking a
    struct.error that would kill the recv loop."""
    need = HEADER_SIZE + body_len
    if len(data) < need:
        raise ProtocolError(
            f"packet too short: need {need} bytes, have {len(data)}")


def pack_header(size: int, dst_session_id: int, packet_type: int, version: int) -> bytes:
    return _HEADER.pack(size, dst_session_id, _type_version_byte(packet_type, version))


def parse_header(buf: bytes) -> Tuple[int, int, int, int]:
    if len(buf) < HEADER_SIZE:
        raise ProtocolError("buffer smaller than packet header")
    size, dst, tvb = _HEADER.unpack_from(buf, 0)
    packet_type, version = _split_type_version(tvb)
    return size, dst, packet_type, version


def between_interval(value: int, start: int, end: int) -> bool:
    return (start <= value < end) or (end < start and (value >= start or value < end))


class Packet:
    packet_type: ClassVar[int] = 0

    def to_bytes(self) -> bytes:
        body = self._encode_body()
        return pack_header(HEADER_SIZE + len(body), self.dst_session_id,
                           self.packet_type, self.version) + body

    def _encode_body(self) -> bytes:
        return b""

    @classmethod
    def from_bytes(cls, data: bytes, dst_session_id: int, version: int) -> "Packet":
        raise NotImplementedError


@dataclass(frozen=True)
class SessionReq(Packet):
    packet_type: ClassVar[int] = SESSIONPACKET_TYPE_SESSIONREQ
    dst_session_id: int
    src_session_id: int
    application_id: int
    session_type: int
    security_types: Tuple[int, ...] = ()
    version: int = SESSIONPROTOCOL_VERSION

    def _encode_body(self) -> bytes:
        sec = bytes(self.security_types)
        if len(sec) > 0x0F:
            raise ProtocolError("too many security types")
        flags = (len(sec) << 4) | (self.session_type & 0x0F)
        return struct.pack("!BHH", flags, self.application_id, self.src_session_id) + sec

    @classmethod
    def from_bytes(cls, data, dst_session_id, version):
        _require(data, 5)
        flags, app_id, src = struct.unpack_from("!BHH", data, HEADER_SIZE)
        count = (flags >> 4) & 0x0F
        session_type = flags & 0x0F
        if len(data) < HEADER_SIZE + 5 + count:
            raise ProtocolError("SessionReq security list truncated")
        sec = tuple(data[HEADER_SIZE + 5:HEADER_SIZE + 5 + count])
        return cls(dst_session_id, src, app_id, session_type, sec, version)


@dataclass(frozen=True)
class SessionAck(Packet):
    packet_type: ClassVar[int] = SESSIONPACKET_TYPE_SESSIONACK
    dst_session_id: int
    ack: int
    security_type: int
    src_session_id: int
    ack_seqnum: int
    version: int = SESSIONPROTOCOL_VERSION

    def _encode_body(self) -> bytes:
        flags = ((self.security_type & 0x0F) << 4) | (self.ack & 0x0F)
        return struct.pack("!BHH", flags, self.src_session_id, self.ack_seqnum)

    @classmethod
    def from_bytes(cls, data, dst_session_id, version):
        _require(data, 5)
        flags, src, ack_seq = struct.unpack_from("!BHH", data, HEADER_SIZE)
        return cls(dst_session_id, flags & 0x0F, (flags >> 4) & 0x0F, src, ack_seq, version)


@dataclass(frozen=True)
class SessionBye(Packet):
    packet_type: ClassVar[int] = SESSIONPACKET_TYPE_SESSIONBYE
    dst_session_id: int
    version: int = SESSIONPROTOCOL_VERSION

    @classmethod
    def from_bytes(cls, data, dst_session_id, version):
        return cls(dst_session_id, version)


@dataclass(frozen=True)
class SessionReReq(Packet):
    packet_type: ClassVar[int] = SESSIONPACKET_TYPE_SESSIONREREQ
    dst_session_id: int
    src_session_id: int
    application_id: int
    session_type: int
    security_type: int
    ack_seqnum: int
    version: int = SESSIONPROTOCOL_VERSION

    def _encode_body(self) -> bytes:
        flags = ((self.security_type & 0x0F) << 4) | (self.session_type & 0x0F)
        return struct.pack("!BHHH", flags, self.application_id, self.src_session_id, self.ack_seqnum)

    @classmethod
    def from_bytes(cls, data, dst_session_id, version):
        _require(data, 7)
        flags, app_id, src, ack_seq = struct.unpack_from("!BHHH", data, HEADER_SIZE)
        return cls(dst_session_id, src, app_id, flags & 0x0F, (flags >> 4) & 0x0F, ack_seq, version)


@dataclass(frozen=True)
class DataPacket(Packet):
    packet_type: ClassVar[int] = SESSIONPACKET_TYPE_DATA
    dst_session_id: int
    seqnum: int
    payload: bytes = b""
    version: int = SESSIONPROTOCOL_VERSION

    def _encode_body(self) -> bytes:
        return struct.pack("!H", self.seqnum) + self.payload

    @classmethod
    def from_bytes(cls, data, dst_session_id, version):
        _require(data, 2)
        seqnum = struct.unpack_from("!H", data, HEADER_SIZE)[0]
        payload = bytes(data[HEADER_SIZE + 2:])
        return cls(dst_session_id, seqnum, payload, version)


@dataclass(frozen=True)
class DataAck(Packet):
    packet_type: ClassVar[int] = SESSIONPACKET_TYPE_DATAACK
    dst_session_id: int
    ack_seqnum: int
    ack_type: int = DATAACK_TYPE_LAST
    version: int = SESSIONPROTOCOL_VERSION

    def _encode_body(self) -> bytes:
        return struct.pack("!BH", self.ack_type & 0x0F, self.ack_seqnum)

    @classmethod
    def from_bytes(cls, data, dst_session_id, version):
        _require(data, 3)
        flags, ack_seq = struct.unpack_from("!BH", data, HEADER_SIZE)
        return cls(dst_session_id, ack_seq, flags & 0x0F, version)


@dataclass(frozen=True)
class Heartbeat(Packet):
    packet_type: ClassVar[int] = SESSIONPACKET_TYPE_HEARTBEAT
    dst_session_id: int
    version: int = SESSIONPROTOCOL_VERSION

    @classmethod
    def from_bytes(cls, data, dst_session_id, version):
        return cls(dst_session_id, version)


@dataclass(frozen=True)
class HeartbeatAck(Packet):
    packet_type: ClassVar[int] = SESSIONPACKET_TYPE_HEARTBEATACK
    dst_session_id: int
    version: int = SESSIONPROTOCOL_VERSION

    @classmethod
    def from_bytes(cls, data, dst_session_id, version):
        return cls(dst_session_id, version)


@dataclass(frozen=True)
class Ping(Packet):
    packet_type: ClassVar[int] = SESSIONPACKET_TYPE_PING
    dst_session_id: int
    dest_ip: int
    timestamp: int
    version: int = SESSIONPROTOCOL_VERSION

    def _encode_body(self) -> bytes:
        return struct.pack("!II", self.dest_ip, self.timestamp)

    @classmethod
    def from_bytes(cls, data, dst_session_id, version):
        _require(data, 8)
        dest_ip, timestamp = struct.unpack_from("!II", data, HEADER_SIZE)
        return cls(dst_session_id, dest_ip, timestamp, version)


@dataclass(frozen=True)
class PingRep(Ping):
    packet_type: ClassVar[int] = SESSIONPACKET_TYPE_PINGREP


_DECODERS = {
    SESSIONPACKET_TYPE_SESSIONREQ: SessionReq,
    SESSIONPACKET_TYPE_SESSIONACK: SessionAck,
    SESSIONPACKET_TYPE_SESSIONBYE: SessionBye,
    SESSIONPACKET_TYPE_SESSIONREREQ: SessionReReq,
    SESSIONPACKET_TYPE_DATA: DataPacket,
    SESSIONPACKET_TYPE_DATAACK: DataAck,
    SESSIONPACKET_TYPE_HEARTBEAT: Heartbeat,
    SESSIONPACKET_TYPE_HEARTBEATACK: HeartbeatAck,
    SESSIONPACKET_TYPE_PING: Ping,
    SESSIONPACKET_TYPE_PINGREP: PingRep,
}


def parse_packet(data: bytes) -> Packet:
    size, dst, packet_type, version = parse_header(data)
    if size < HEADER_SIZE:
        raise ProtocolError(f"packet size {size} smaller than header")
    if size != len(data):
        raise ProtocolError(f"packet size mismatch: header={size} actual={len(data)}")
    if packet_type > SESSIONPACKET_TYPE_MAX:
        raise ProtocolError(f"unknown packet type {packet_type}")
    decoder = _DECODERS.get(packet_type)
    if decoder is None:
        raise ProtocolError(f"unsupported packet type {packet_type}")
    return decoder.from_bytes(data, dst, version)
