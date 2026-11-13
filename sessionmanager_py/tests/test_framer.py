from framecore_session import PacketFramer, ProtocolError
from framecore_session.packets import Heartbeat, SessionBye


def _two_packets_concat():
    return Heartbeat(1).to_bytes() + SessionBye(1).to_bytes()


def test_feed_returns_nothing_for_partial_header():
    framer = PacketFramer()
    assert framer.feed(b"\x00") == []


def test_feed_assembles_single_packet():
    framer = PacketFramer()
    raw = Heartbeat(1).to_bytes()
    assert framer.feed(raw) == [raw]


def test_feed_splits_concatenated_packets():
    framer = PacketFramer()
    concat = _two_packets_concat()
    assert framer.feed(concat) == [Heartbeat(1).to_bytes(), SessionBye(1).to_bytes()]


def test_feed_handles_split_across_calls():
    framer = PacketFramer()
    concat = _two_packets_concat()
    assert framer.feed(concat[:3]) == []
    out = framer.feed(concat[3:])
    assert out == [Heartbeat(1).to_bytes(), SessionBye(1).to_bytes()]


def test_feed_rejects_oversize_packet():
    framer = PacketFramer()
    try:
        framer.feed(b"\x00\x01" + b"\x00" * 3 + b"x")
    except ProtocolError:
        return
    raise AssertionError("expected ProtocolError")


def test_reset_clears_buffer():
    framer = PacketFramer()
    framer.feed(b"\x00")
    framer.reset()
    assert framer.feed(Heartbeat(1).to_bytes()) == [Heartbeat(1).to_bytes()]
