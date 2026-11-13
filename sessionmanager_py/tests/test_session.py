import socket
import threading
import time
from collections import deque

from framecore_session import (
    DataAck,
    DataPacket,
    Heartbeat,
    HeartbeatAck,
    PacketFramer,
    SessionAck,
    SessionBye,
    SessionConfig,
    SessionListener,
    SessionReReq,
    SessionReq,
    SessionState,
    TcpReliableSession,
    parse_packet,
)
from framecore_session.constants import WSESSION_SECURITY_NONE


def wait_for(condition, timeout=6.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return condition()


class _MiniServer:
    server_session_id = 0xBEEF

    def __init__(self):
        self._lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._lsock.bind(("127.0.0.1", 0))
        self._lsock.listen(8)
        self.port = self._lsock.getsockname()[1]
        self._lock = threading.Lock()
        self._conn = None
        self._client_sid = 0
        self._last_recv = 0xFFFF
        self._send_seq = 0
        self.received = deque()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            self._lsock.close()
        except OSError:
            pass
        with self._lock:
            conn = self._conn
            self._conn = None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass

    def drop_current(self):
        with self._lock:
            conn = self._conn
            self._conn = None
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass

    def send_to_client(self, payload, seq=None):
        with self._lock:
            conn = self._conn
            dst = self._client_sid
            if seq is None:
                seq = self._send_seq
                self._send_seq = (seq + 1) & 0xFFFF
        if conn is None:
            return
        pkt = DataPacket(dst_session_id=dst, seqnum=seq, payload=payload)
        try:
            conn.sendall(pkt.to_bytes())
        except OSError:
            pass
        return seq

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._lsock.accept()
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with self._lock:
            self._conn = conn
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        framer = PacketFramer()
        try:
            while not self._stop.is_set():
                try:
                    data = conn.recv(4096)
                except OSError:
                    break
                if not data:
                    break
                try:
                    frames = framer.feed(data)
                except Exception:
                    break
                for raw in frames:
                    self._handle(conn, parse_packet(raw))
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _handle(self, conn, pkt):
        if isinstance(pkt, SessionReq):
            self._client_sid = pkt.src_session_id
            ack = SessionAck(dst_session_id=pkt.src_session_id, ack=0,
                             security_type=WSESSION_SECURITY_NONE,
                             src_session_id=self.server_session_id, ack_seqnum=0)
            self._send(conn, ack.to_bytes())
        elif isinstance(pkt, SessionReReq):
            self._client_sid = pkt.src_session_id
            ack = SessionAck(dst_session_id=pkt.src_session_id, ack=0,
                             security_type=WSESSION_SECURITY_NONE,
                             src_session_id=self.server_session_id,
                             ack_seqnum=self._last_recv)
            self._send(conn, ack.to_bytes())
        elif isinstance(pkt, DataPacket):
            self._last_recv = pkt.seqnum
            self.received.append(bytes(pkt.payload))
            ack = DataAck(dst_session_id=self._client_sid, ack_seqnum=self._last_recv)
            self._send(conn, ack.to_bytes())
        elif isinstance(pkt, Heartbeat):
            self._send(conn, HeartbeatAck(dst_session_id=self._client_sid).to_bytes())
        elif isinstance(pkt, SessionBye):
            pass

    def _send(self, conn, raw):
        try:
            conn.sendall(raw)
        except OSError:
            pass


class _SilentHandshakeServer:
    def __init__(self):
        self._lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._lsock.bind(("127.0.0.1", 0))
        self._lsock.listen(8)
        self.port = self._lsock.getsockname()[1]
        self.accepted = threading.Event()
        self._stop = threading.Event()
        self._conns = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            self._lsock.close()
        except OSError:
            pass
        with self._lock:
            conns = list(self._conns)
            self._conns.clear()
        for conn in conns:
            try:
                conn.close()
            except OSError:
                pass

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._lsock.accept()
            except OSError:
                break
            with self._lock:
                self._conns.append(conn)
            self.accepted.set()
            threading.Thread(target=self._hold, args=(conn,), daemon=True).start()

    def _hold(self, conn):
        while not self._stop.wait(0.05):
            pass
        try:
            conn.close()
        except OSError:
            pass


class _CaptureListener(SessionListener):
    def __init__(self):
        self.created = threading.Event()
        self.reconnecting = threading.Event()
        self.reconnected = threading.Event()
        self.closed = threading.Event()
        self.send_ready = threading.Event()
        self.data = deque()
        self.fail_reasons = deque()

    def on_created(self):
        self.created.set()

    def on_create_failed(self, reason):
        self.fail_reasons.append(reason)

    def on_closed(self):
        self.closed.set()

    def on_data(self, data):
        self.data.append(bytes(data))

    def on_reconnecting(self):
        self.reconnecting.set()

    def on_reconnected(self):
        self.reconnected.set()

    def on_send_ready(self):
        self.send_ready.set()


class _CloseFromDataListener(_CaptureListener):
    def __init__(self):
        super().__init__()
        self.session = None
        self.close_errors = deque()

    def on_data(self, data):
        super().on_data(data)
        try:
            self.session.close()
        except Exception as exc:
            self.close_errors.append(exc)


def test_handshake_and_bidirectional_data():
    server = _MiniServer()
    server.start()
    try:
        listener = _CaptureListener()
        config = SessionConfig(application_id=1, connect_timeout_ms=3000)
        client = TcpReliableSession(listener, config, local_session_id=1234)

        assert client.connect("127.0.0.1", server.port) is True
        assert client.state is SessionState.CREATED
        assert client.peer_session_id == _MiniServer.server_session_id

        assert client.send(b"one")
        assert client.send(b"two")
        assert client.send(b"three")

        assert wait_for(lambda: len(server.received) >= 3)
        assert list(server.received)[-3:] == [b"one", b"two", b"three"]
        assert wait_for(lambda: client.pending_send_count == 0)

        server.send_to_client(b"hello")
        assert wait_for(lambda: bool(listener.data) and listener.data[-1] == b"hello")

        client.close()
        assert wait_for(lambda: client.state is SessionState.CLOSED)
    finally:
        server.stop()


def test_reconnect_after_drop():
    server = _MiniServer()
    server.start()
    try:
        listener = _CaptureListener()
        config = SessionConfig(application_id=1, connect_timeout_ms=3000,
                               reconnect_timeout_ms=8000)
        client = TcpReliableSession(listener, config, local_session_id=4321)

        assert client.connect("127.0.0.1", server.port) is True
        assert wait_for(lambda: listener.created.is_set())

        server.drop_current()

        assert wait_for(lambda: listener.reconnecting.is_set())
        assert wait_for(lambda: listener.reconnected.is_set(), timeout=10.0)
        assert client.state is SessionState.CREATED

        client.close()
    finally:
        server.stop()


def test_address_link_falls_back_to_second_tcp_endpoint():
    unavailable = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    unavailable.bind(("127.0.0.1", 0))
    unavailable_port = unavailable.getsockname()[1]
    unavailable.close()

    server = _MiniServer()
    server.start()
    try:
        listener = _CaptureListener()
        client = TcpReliableSession(
            listener, SessionConfig(application_id=1, connect_timeout_ms=3000))
        address_link = (
            f"TCP:127.0.0.1:{unavailable_port};TCP:127.0.0.1:{server.port}"
        )
        assert client.connect(address_link) is True
        assert client.connected_endpoint is not None
        assert client.connected_endpoint.port == server.port
        client.close()
    finally:
        server.stop()


def test_address_link_falls_back_when_first_endpoint_handshake_times_out():
    silent = _SilentHandshakeServer()
    server = _MiniServer()
    silent.start()
    server.start()
    try:
        listener = _CaptureListener()
        config = SessionConfig(
            application_id=1,
            connect_timeout_ms=3000,
            handshake_timeout_ms=200,
            tcp_connect_timeout_ms=500,
        )
        client = TcpReliableSession(listener, config)
        address_link = f"TCP:127.0.0.1:{silent.port};TCP:127.0.0.1:{server.port}"

        assert client.connect(address_link) is True
        assert silent.accepted.is_set()
        assert client.connected_endpoint is not None
        assert client.connected_endpoint.port == server.port
        client.close()
    finally:
        silent.stop()
        server.stop()


def test_duplicate_data_is_acknowledged_but_not_delivered_twice():
    server = _MiniServer()
    server.start()
    try:
        listener = _CaptureListener()
        client = TcpReliableSession(
            listener, SessionConfig(application_id=1, connect_timeout_ms=3000))
        assert client.connect("127.0.0.1", server.port)
        seq = server.send_to_client(b"once")
        assert wait_for(lambda: list(listener.data) == [b"once"])
        server.send_to_client(b"once", seq=seq)
        time.sleep(0.1)
        assert list(listener.data) == [b"once"]
        client.close()
    finally:
        server.stop()


def test_close_is_safe_from_receive_callback():
    server = _MiniServer()
    server.start()
    try:
        listener = _CloseFromDataListener()
        client = TcpReliableSession(
            listener, SessionConfig(application_id=1, connect_timeout_ms=3000))
        listener.session = client
        assert client.connect("127.0.0.1", server.port)
        server.send_to_client(b"close-now")
        assert wait_for(lambda: listener.closed.is_set())
        assert client.state is SessionState.CLOSED
        assert not listener.close_errors
    finally:
        server.stop()


def test_internal_disconnect_stops_background_threads():
    server = _MiniServer()
    server.start()
    try:
        listener = _CaptureListener()
        config = SessionConfig(
            application_id=1, connect_timeout_ms=3000, enable_reconnect=False)
        client = TcpReliableSession(listener, config)
        assert client.connect("127.0.0.1", server.port)
        server.drop_current()
        assert wait_for(lambda: listener.closed.is_set())
        assert wait_for(lambda: not client._send_thread.is_alive())
        assert wait_for(lambda: not client._timer_thread.is_alive())
        client.close()
    finally:
        server.stop()
