import zlib

import pytest

from framecore_session import ProtocolError
from framecore_session.message import (
    XMLMSG_PACKTYPE_NONE,
    XMLMSG_PACKTYPE_ZLIB,
    ZLIB_MINPACKSIZE,
    XmlMsgIOPack,
)

SMALL = b"<msg type='ping'/>"
LARGE = b"<msg type='bulk'>" + b"x" * (ZLIB_MINPACKSIZE * 4) + b"</msg>"


def test_small_message_not_compressed():
    packer = XmlMsgIOPack()
    envelope = packer.pack(SMALL)
    assert envelope[0] == XMLMSG_PACKTYPE_NONE
    assert envelope[1:] == SMALL
    assert packer.unpack(envelope) == SMALL


def test_large_message_zlib_compressed():
    packer = XmlMsgIOPack()
    envelope = packer.pack(LARGE)
    assert envelope[0] == XMLMSG_PACKTYPE_ZLIB
    assert envelope[1:] == zlib.compress(LARGE)
    assert packer.unpack(envelope) == LARGE


def test_boundary_at_minpacksize_stays_raw():
    packer = XmlMsgIOPack()
    data = b"x" * ZLIB_MINPACKSIZE
    envelope = packer.pack(data)
    assert envelope[0] == XMLMSG_PACKTYPE_NONE


def test_pack_type_none_never_compresses():
    packer = XmlMsgIOPack(XMLMSG_PACKTYPE_NONE)
    envelope = packer.pack(LARGE)
    assert envelope[0] == XMLMSG_PACKTYPE_NONE
    assert packer.unpack(envelope) == LARGE


def test_unpack_rejects_unknown_flag():
    packer = XmlMsgIOPack()
    with pytest.raises(ProtocolError):
        packer.unpack(bytes((0x05,)) + b"abc")


def test_unpack_rejects_too_short():
    packer = XmlMsgIOPack()
    with pytest.raises(ProtocolError):
        packer.unpack(b"")


def test_empty_message_roundtrip():
    packer = XmlMsgIOPack()
    assert packer.unpack(packer.pack(b"")) == b""


def test_unpack_rejects_oversized_zlib_output():
    packer = XmlMsgIOPack()
    envelope = bytes((XMLMSG_PACKTYPE_ZLIB,)) + zlib.compress(b"x" * (64 * 1024))
    with pytest.raises(ProtocolError):
        packer.unpack(envelope)


def test_cross_check_with_pure_zlib():
    packer = XmlMsgIOPack()
    envelope = bytes((XMLMSG_PACKTYPE_ZLIB,)) + zlib.compress(LARGE)
    assert packer.unpack(envelope) == LARGE
