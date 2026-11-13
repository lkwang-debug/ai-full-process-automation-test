from __future__ import annotations

import zlib

from .exceptions import ProtocolError

XMLMSG_PACKTYPE_NONE = 0x00
XMLMSG_PACKTYPE_ZLIB = 0x01

ZLIB_MINPACKSIZE = 256
XMLMSG_BUFFERSIZE = 64 * 1024


class XmlMsgIOPack:
    def __init__(self, pack_type: int = XMLMSG_PACKTYPE_ZLIB) -> None:
        if pack_type not in (XMLMSG_PACKTYPE_NONE, XMLMSG_PACKTYPE_ZLIB):
            raise ValueError(f"unsupported pack type {pack_type}")
        self.pack_type = pack_type

    def pack(self, data: bytes) -> bytes:
        data = bytes(data)
        if len(data) >= XMLMSG_BUFFERSIZE:
            raise ProtocolError("message exceeds buffer size")
        if self.pack_type == XMLMSG_PACKTYPE_ZLIB and len(data) > ZLIB_MINPACKSIZE:
            return bytes((XMLMSG_PACKTYPE_ZLIB,)) + zlib.compress(data)
        return bytes((XMLMSG_PACKTYPE_NONE,)) + data

    def unpack(self, data: bytes) -> bytes:
        if len(data) < 1:
            raise ProtocolError("message payload too short")
        flag = data[0]
        body = data[1:]
        if flag == XMLMSG_PACKTYPE_ZLIB:
            try:
                decompressor = zlib.decompressobj()
                plain = decompressor.decompress(body, XMLMSG_BUFFERSIZE)
            except zlib.error as exc:
                raise ProtocolError(f"zlib decompress failed: {exc}") from exc
            if (len(plain) >= XMLMSG_BUFFERSIZE or decompressor.unconsumed_tail
                    or decompressor.unused_data or not decompressor.eof):
                raise ProtocolError("zlib payload exceeds buffer size or is incomplete")
            return plain
        if flag == XMLMSG_PACKTYPE_NONE:
            return body
        raise ProtocolError(f"unknown pack type {flag}")
