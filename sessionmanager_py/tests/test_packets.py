from framecore_session import packets
from framecore_session.constants import (
    DATAACK_TYPE_LAST,
    SESSIONPACKET_TYPE_DATA,
    SESSIONPACKET_TYPE_DATAACK,
    SESSIONPACKET_TYPE_HEARTBEAT,
    SESSIONPACKET_TYPE_SESSIONACK,
    SESSIONPACKET_TYPE_SESSIONREQ,
    SESSIONTYPE_RELIABLE,
    WSESSION_SECURITY_NONE,
)


def test_session_req_wire_format_matches_reference():
    req = packets.SessionReq(
        dst_session_id=0,
        src_session_id=0x0005,
        application_id=0x1234,
        session_type=SESSIONTYPE_RELIABLE,
        security_types=(0, 1, 2, 3),
    )
    assert req.to_bytes() == bytes.fromhex("000E 0000 13 40 1234 0005 00010203".replace(" ", ""))


def test_session_ack_wire_format_matches_reference():
    ack = packets.SessionAck(
        dst_session_id=0x0005,
        ack=0,
        security_type=WSESSION_SECURITY_NONE,
        src_session_id=0x000A,
        ack_seqnum=0,
    )
    assert ack.to_bytes() == bytes.fromhex("000A 0005 23 00 000A 0000".replace(" ", ""))


def test_data_packet_wire_format_matches_reference():
    pkt = packets.DataPacket(dst_session_id=0x000A, seqnum=0, payload=b"AB")
    assert pkt.to_bytes() == bytes.fromhex("0009 000A 03 0000 4142".replace(" ", ""))


def test_data_ack_wire_format_matches_reference():
    ack = packets.DataAck(dst_session_id=0x0005, ack_seqnum=0)
    assert ack.to_bytes() == bytes.fromhex("0008 0005 53 01 0000".replace(" ", ""))


def test_roundtrip_all_packet_types():
    samples = [
        packets.SessionReq(0, 5, 0x1234, SESSIONTYPE_RELIABLE, (0, 1, 2, 3)),
        packets.SessionAck(0x0005, 0, WSESSION_SECURITY_NONE, 0x000A, 0),
        packets.SessionBye(0x000A),
        packets.SessionReReq(0x000A, 5, 0x1234, SESSIONTYPE_RELIABLE, WSESSION_SECURITY_NONE, 7),
        packets.DataPacket(0x000A, 42, b"hello"),
        packets.DataAck(0x0005, 42),
        packets.Heartbeat(0x000A),
        packets.HeartbeatAck(0x000A),
        packets.Ping(0, 0xC0A80101, 12345),
        packets.PingRep(0, 0xC0A80101, 12345),
    ]
    for original in samples:
        parsed = packets.parse_packet(original.to_bytes())
        assert type(parsed) is type(original)
        assert parsed.to_bytes() == original.to_bytes()


def test_parse_packet_rejects_size_mismatch():
    raw = packets.Heartbeat(1).to_bytes()
    tampered = raw[:-1]
    try:
        packets.parse_packet(tampered)
    except packets.ProtocolError:
        return
    raise AssertionError("expected ProtocolError")


def test_type_version_byte_encoding():
    req = packets.SessionReq(0, 1, 1, 0, ())
    assert req.to_bytes()[4] == (SESSIONPACKET_TYPE_SESSIONREQ << 4) | 3
    ack = packets.SessionAck(0, 0, 0, 0, 0)
    assert ack.to_bytes()[4] == (SESSIONPACKET_TYPE_SESSIONACK << 4) | 3
    hb = packets.Heartbeat(0)
    assert hb.to_bytes()[4] == (SESSIONPACKET_TYPE_HEARTBEAT << 4) | 3


def test_between_interval_handles_wraparound():
    assert packets.between_interval(3, 0, 10) is True
    assert packets.between_interval(0, 0, 10) is True
    assert packets.between_interval(10, 0, 10) is False
    assert packets.between_interval(65535, 65530, 4) is True
    assert packets.between_interval(2, 65530, 4) is True
    assert packets.between_interval(5, 65530, 4) is False
