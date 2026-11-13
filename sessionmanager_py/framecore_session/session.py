from __future__ import annotations

import logging
import queue
import random
import socket
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Deque, List, Optional, Sequence, Tuple

from .address import SessionEndpoint, coerce_tcp_endpoints

from .constants import (
    ACK_BUFFER_BLOCK_PACKET_COUNT,
    ACK_COUNT_THRESHOLD,
    ACK_TIME_THRESHOLD_MS,
    DEFAULT_ACK_BUFFER_MAX_SIZE,
    FAST_HEARTBEAT_INTERVAL_MS,
    HEARTBEAT_ACK_TIMEOUT_MS,
    HEARTBEAT_TIMEOUT_FAILED_MS,
    MAX_CACHE_PACKET_COUNT,
    MAX_PACKET_SIZE,
    MAX_SESSIONID,
    MIN_SESSIONID,
    NORMAL_HEARTBEAT_INTERVAL_MS,
    RECONNECT_ATTEMPT_TIMEOUT_MS,
    RECONNECT_BACKOFF_MAX_MS,
    RECONNECT_BACKOFF_MS,
    SESSIONPROTOCOL_VERSION,
    SESSIONTYPE_RELIABLE,
    WSESSION_SECURITY_AES_ECB,
    WSESSION_SECURITY_NONE,
    WSESSION_SECURITY_SM4,
    WSESSION_SECURITY_XOR,
)
from .exceptions import (
    PayloadTooLargeError,
    ProtocolError,
    SecurityError,
    SessionStateError,
)
from .framer import PacketFramer
from .packets import (
    DataAck,
    DataPacket,
    Heartbeat,
    HeartbeatAck,
    Packet,
    SessionAck,
    SessionBye,
    SessionReReq,
    SessionReq,
    between_interval,
    parse_packet,
)
from .security import create_security, is_security_available

logger = logging.getLogger("framecore_session")

SEQ_MOD = 0x10000
DEFAULT_SECURITY_OFFER = (
    WSESSION_SECURITY_NONE,
    WSESSION_SECURITY_XOR,
    WSESSION_SECURITY_AES_ECB,
    WSESSION_SECURITY_SM4,
)

# Sentinel put on the send queue to make the send thread drain and exit.
_SHUTDOWN = object()

# (listener-event 名, 回调) —— 便于在回调抛异常时定位是哪个事件。
_Callback = Callable[[], None]
_Pending = List[Tuple[str, _Callback]]


class SessionState(Enum):
    IDLE = 0
    CREATING = 1
    CREATED = 2
    DISCONNECTED = 3
    RECONNECTING = 4
    CLOSED = 5


@dataclass
class SessionConfig:
    application_id: int
    heartbeat_interval_ms: int = NORMAL_HEARTBEAT_INTERVAL_MS
    fast_heartbeat_interval_ms: int = FAST_HEARTBEAT_INTERVAL_MS
    heartbeat_ack_timeout_ms: int = HEARTBEAT_ACK_TIMEOUT_MS
    heartbeat_timeout_failed_ms: int = HEARTBEAT_TIMEOUT_FAILED_MS
    ack_count_threshold: int = ACK_COUNT_THRESHOLD
    ack_time_threshold_ms: int = ACK_TIME_THRESHOLD_MS
    ack_buffer_max_size: int = DEFAULT_ACK_BUFFER_MAX_SIZE
    connect_timeout_ms: int = 15000
    handshake_timeout_ms: int = 5000
    enable_reconnect: bool = True
    reconnect_timeout_ms: int = 60000
    offered_security_types: Sequence[int] = DEFAULT_SECURITY_OFFER
    recv_buffer_size: int = 65536
    tcp_connect_timeout_ms: int = 5000


class SessionListener:
    def on_created(self) -> None:
        pass

    def on_create_failed(self, reason: str) -> None:
        pass

    def on_closed(self) -> None:
        pass

    def on_data(self, data: bytes) -> None:
        pass

    def on_reconnecting(self) -> None:
        pass

    def on_reconnected(self) -> None:
        pass

    def on_send_ready(self) -> None:
        pass

    def on_listener_error(self, event: str, exc: BaseException) -> None:
        """Invoked when a listener callback raises (``event`` 标识哪个回调，
        如 ``"on_data"``)。异常已记录日志；可重写以将其上抛（如让测试失败）。"""
        pass


@dataclass
class _SentPacket:
    seq: int
    raw: bytes


@dataclass(frozen=True)
class _QueuedSend:
    raw: bytes
    socket_generation: int


@dataclass(frozen=True)
class _ReconnectPlan:
    endpoint_index: int
    endpoint: SessionEndpoint
    rereq: bytes
    attempt: int


@dataclass(frozen=True)
class _InitialHandshake:
    ack: SessionAck
    extra_frames: Tuple[bytes, ...]


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


class TcpReliableSession:
    def __init__(
        self,
        listener: Optional[SessionListener] = None,
        config: Optional[SessionConfig] = None,
        *,
        local_session_id: Optional[int] = None,
    ) -> None:
        self._listener = listener or SessionListener()
        self._config = config or SessionConfig(application_id=0)
        self._local_sid = self._allocate_session_id(local_session_id)
        self._peer_sid = 0
        self._negotiated_version = SESSIONPROTOCOL_VERSION
        self._security = create_security(WSESSION_SECURITY_NONE)

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._state = SessionState.IDLE
        self._sock: Optional[socket.socket] = None
        self._framer = PacketFramer()
        self._endpoints: Tuple[SessionEndpoint, ...] = ()
        self._current_endpoint_index = 0
        self._socket_generation = 0

        # 发送侧：独立线程 + 无界队列，避免持锁 sendall 阻塞 recv/timer。
        self._send_queue: "queue.Queue[object]" = queue.Queue()
        self._send_thread: Optional[threading.Thread] = None
        self._recv_threads: List[threading.Thread] = []
        self._timer_thread: Optional[threading.Thread] = None
        self._created_event = threading.Event()

        self._send_seq = 0
        self._unacked: Deque[_SentPacket] = deque()
        self._ack_buffer_size = 0
        self._send_blocked = False

        self._last_recv_seq = 0xFFFF
        self._unack_count = 0
        self._last_ack_time = _now_ms()

        self._hb_interval = self._config.heartbeat_interval_ms
        self._last_hb_send = 0
        self._hb_ack_deadline: Optional[int] = None
        self._last_hb_ack = 0
        self._fast_hb = False

        self._disconnect_start = 0
        self._reconnect_attempt_start = 0
        self._last_reconnect_try = 0
        self._reconnect_attempts = 0
        self._reconnect_next_index = 0
        self._created_once = False
        self._closed_notified = False

    @property
    def state(self) -> SessionState:
        with self._lock:
            return self._state

    @property
    def local_session_id(self) -> int:
        return self._local_sid

    @property
    def peer_session_id(self) -> int:
        with self._lock:
            return self._peer_sid

    @property
    def connected_endpoint(self) -> Optional[SessionEndpoint]:
        with self._lock:
            if self._sock is None or not self._endpoints:
                return None
            return self._endpoints[self._current_endpoint_index]

    @property
    def pending_send_count(self) -> int:
        with self._lock:
            return len(self._unacked)

    @staticmethod
    def _allocate_session_id(value: Optional[int]) -> int:
        if value is not None:
            if not MIN_SESSIONID <= value <= MAX_SESSIONID:
                raise ValueError("local_session_id out of range")
            return value
        return random.randint(MIN_SESSIONID, MAX_SESSIONID)

    def connect(self, address: str, port: Optional[int] = None) -> bool:
        """Create the session using an address link or a legacy host/port pair.

        Address-link example: ``TCP:10.0.0.1:1089;TCP:10.0.0.2:1089``.
        TCP candidates are tried in order until one completes the session
        handshake.
        """
        endpoints = coerce_tcp_endpoints(address, port)
        deadline = time.monotonic() + self._config.connect_timeout_ms / 1000.0
        failure_reason = ""
        created = False
        created_sock: Optional[socket.socket] = None
        created_extra_frames: Tuple[bytes, ...] = ()
        with self._lock:
            if self._state != SessionState.IDLE:
                raise SessionStateError("session already started")
            self._endpoints = endpoints
            self._state = SessionState.CREATING
            self._created_event.clear()
            self._framer.reset()

        errors = []
        for index, endpoint in enumerate(endpoints):
            with self._lock:
                if self._state != SessionState.CREATING:
                    return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                errors.append("address-link connect timeout")
                break
            logger.debug("connecting to %s (appid=%s)", endpoint,
                         self._config.application_id)
            try:
                sock = self._open_socket(
                    endpoint.host, endpoint.port,
                    timeout=min(remaining, self._config.tcp_connect_timeout_ms / 1000.0))
            except OSError as exc:
                errors.append(f"{endpoint}: tcp connect failed: {exc}")
                continue

            with self._lock:
                if self._state != SessionState.CREATING:
                    self._close_plain_socket(sock)
                    return False
                self._sock = sock
                self._socket_generation += 1
                self._current_endpoint_index = index
                self._framer.reset()

            handshake_deadline = min(
                deadline,
                time.monotonic() + self._config.handshake_timeout_ms / 1000.0,
            )
            try:
                sock.settimeout(max(0.001, handshake_deadline - time.monotonic()))
                sock.sendall(self._session_req_bytes())
                handshake = self._wait_initial_handshake(sock, handshake_deadline)
                sock.settimeout(None)
            except (OSError, ProtocolError, struct.error, ValueError) as exc:
                errors.append(f"{endpoint}: handshake failed: {exc}")
                self._clear_initial_socket(sock)
                continue

            with self._lock:
                if self._state != SessionState.CREATING or sock is not self._sock:
                    self._close_plain_socket(sock)
                    return False
                if handshake.ack.ack != 0:
                    errors.append(f"{endpoint}: server rejected (ack={handshake.ack.ack})")
                    self._clear_initial_socket_locked()
                    continue
                try:
                    self._apply_ack_negotiation(handshake.ack)
                except SecurityError as exc:
                    errors.append(f"{endpoint}: {exc}")
                    self._clear_initial_socket_locked()
                    continue
                self._enter_created_locked()
                self._created_event.set()
                self._start_send_timer_threads()
                created = True
                created_sock = sock
                created_extra_frames = handshake.extra_frames
                break

        if not created:
            with self._lock:
                if self._state == SessionState.CREATING:
                    self._state = SessionState.CLOSED
                    self._created_event.set()
                    self._signal_stop()
                    self._close_socket_locked()
                    failure_reason = "connect failed: " + "; ".join(errors)
                else:
                    return False
            self._fire([("on_create_failed",
                         lambda reason=failure_reason: self._listener.on_create_failed(reason))])
            return False

        logger.info("session created: local=%s peer=%s security=%s version=%s",
                    self._local_sid, self._peer_sid,
                    self._security.security_type, self._negotiated_version)
        self._fire([("on_created", lambda: self._listener.on_created())])
        with self._lock:
            can_process_extra = (
                self._state == SessionState.CREATED
                and created_sock is not None
                and created_sock is self._sock
            )
        if can_process_extra:
            for raw in created_extra_frames:
                self._fire(self._process_raw(raw))
        with self._lock:
            if (self._state == SessionState.CREATED
                    and created_sock is not None
                    and created_sock is self._sock):
                self._start_recv_thread()
        return True

    def _clear_initial_socket(self, sock: socket.socket) -> None:
        with self._lock:
            if sock is self._sock:
                self._clear_initial_socket_locked()
                return
        self._close_plain_socket(sock)

    def _clear_initial_socket_locked(self) -> None:
        self._close_socket_locked()
        self._framer.reset()

    def _wait_initial_handshake(
        self,
        sock: socket.socket,
        deadline: float,
    ) -> _InitialHandshake:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("session handshake timeout")
            sock.settimeout(max(0.001, remaining))
            data = sock.recv(self._config.recv_buffer_size)
            if not data:
                raise ConnectionError("connection closed during handshake")
            frames = self._framer.feed(data)
            for offset, raw in enumerate(frames):
                packet = parse_packet(raw)
                if not isinstance(packet, SessionAck):
                    logger.warning("dropping pre-creation packet type=%s",
                                   type(packet).__name__)
                    continue
                if packet.dst_session_id != self._local_sid:
                    raise ProtocolError(
                        f"session ack for {packet.dst_session_id}, local={self._local_sid}")
                return _InitialHandshake(packet, tuple(frames[offset + 1:]))

    def send(self, data: bytes) -> bool:
        return self.send_array((data,))

    def send_array(self, datas: Sequence[bytes]) -> bool:
        with self._lock:
            if self._state not in (SessionState.CREATED, SessionState.RECONNECTING):
                return False
            if self._send_blocked:
                return False
            payload = b"".join(datas)
            ciphertext = self._security.encrypt(payload)
            seq = self._send_seq
            packet = DataPacket(self._peer_sid, seq, ciphertext,
                                version=self._negotiated_version)
            raw = packet.to_bytes()
            if len(raw) > MAX_PACKET_SIZE:
                raise PayloadTooLargeError(
                    f"encoded packet {len(raw)} exceeds max {MAX_PACKET_SIZE}")
            self._send_seq = (seq + 1) % SEQ_MOD
            # RECONNECTING 期间只入未确认队列、不真正发包：握手完成前发出的
            # 数据会被服务端丢弃，且会和重连后的 resend 重复。统一交给 resend。
            if self._state == SessionState.CREATED:
                self._enqueue_send(raw)
            self._unacked.append(_SentPacket(seq, raw))
            self._ack_buffer_size += len(raw)
            if self._is_ack_buffer_blocked():
                self._send_blocked = True
            if self._state == SessionState.CREATED:
                self._check_ack_locked()
            return True

    def close(self) -> None:
        bye: Optional[bytes] = None
        pending: _Pending = []
        with self._lock:
            if self._state == SessionState.IDLE:
                return
            if self._sock is not None and self._state == SessionState.CREATED:
                bye = SessionBye(self._peer_sid, version=self._negotiated_version).to_bytes()
            if self._state == SessionState.CREATING:
                self._created_event.set()
            self._state = SessionState.CLOSED
            pending = self._closed_notification_locked()
        # 让发送线程把 BYE（及已在队列的包）冲刷出去后再关 socket。
        if bye is not None:
            self._enqueue_send(bye)
        self._signal_stop()
        current = threading.current_thread()
        if self._send_thread is not None and self._send_thread is not current:
            self._send_thread.join(timeout=1.0)
        with self._lock:
            self._close_socket_locked()
        if self._timer_thread is not None and self._timer_thread is not current:
            self._timer_thread.join(timeout=1.0)
        for thread in self._recv_threads:
            if thread is not current:
                thread.join(timeout=1.0)
        self._fire(pending)

    def _open_socket(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
    ) -> socket.socket:
        if timeout is None:
            timeout = self._config.tcp_connect_timeout_ms / 1000.0
        sock = socket.create_connection((host, port), timeout=timeout)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(None)
            return sock
        except Exception:
            sock.close()
            raise

    @staticmethod
    def _close_plain_socket(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def _start_threads(self) -> None:
        self._start_send_timer_threads()
        self._start_recv_thread()

    def _start_send_timer_threads(self) -> None:
        self._send_thread = threading.Thread(
            target=self._send_loop, name="fc-send", daemon=True)
        self._send_thread.start()
        self._timer_thread = threading.Thread(
            target=self._timer_loop, name="fc-timer", daemon=True)
        self._timer_thread.start()

    def _start_recv_thread(self) -> None:
        self._recv_threads = [thread for thread in self._recv_threads if thread.is_alive()]
        thread = threading.Thread(target=self._recv_loop, name="fc-recv", daemon=True)
        self._recv_threads.append(thread)
        thread.start()

    def _enqueue_send(self, raw: bytes) -> bool:
        """把原始字节交由后台发送线程发送（非阻塞、不持锁做 I/O）。

        仅在会话正在关闭（stop 已置位）时返回 False。"""
        if self._stop_event.is_set():
            return False
        with self._lock:
            if self._sock is None:
                return False
            item = _QueuedSend(raw, self._socket_generation)
        self._send_queue.put(item)
        return True

    def _signal_stop(self) -> None:
        if not self._stop_event.is_set():
            self._stop_event.set()
            self._send_queue.put(_SHUTDOWN)

    def _send_loop(self) -> None:
        # 阻塞 get：close() 必投 _SHUTDOWN 唤醒，无需空闲轮询。
        while True:
            item = self._send_queue.get()
            if item is _SHUTDOWN:
                return
            if not isinstance(item, _QueuedSend):
                continue
            with self._lock:
                sock = self._sock
                generation = self._socket_generation
            if sock is None or item.socket_generation != generation:
                # Old-socket packets must never leak onto a reconnected socket.
                continue
            try:
                sock.sendall(item.raw)
            except OSError:
                logger.debug("send failed on socket %r", sock)
                self._handle_socket_closed(sock)

    def _supported_security_types(self) -> Tuple[int, ...]:
        return tuple(st for st in self._config.offered_security_types
                     if is_security_available(st))

    def _session_req_bytes(self) -> bytes:
        return SessionReq(
            dst_session_id=0,
            version=SESSIONPROTOCOL_VERSION,
            src_session_id=self._local_sid,
            application_id=self._config.application_id,
            session_type=SESSIONTYPE_RELIABLE,
            security_types=self._supported_security_types(),
        ).to_bytes()

    def _send_session_req(self) -> None:
        self._enqueue_send(self._session_req_bytes())

    def _close_socket_locked(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is not None:
            # shutdown 先于 close，确保对端阻塞中的 recv 立即醒来。
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _recv_loop(self) -> None:
        sock = self._sock
        while not self._stop_event.is_set():
            if sock is not self._sock:
                return
            try:
                data = sock.recv(self._config.recv_buffer_size)
            except OSError:
                break
            if not data:
                break
            try:
                frames = self._framer.feed(data)
            except ProtocolError:
                self._handle_socket_closed(sock)
                return
            for raw in frames:
                self._fire(self._process_raw(raw))
        self._handle_socket_closed(sock)

    def _process_raw(self, raw: bytes) -> _Pending:
        try:
            packet = parse_packet(raw)
        except (ProtocolError, struct.error, ValueError) as exc:
            # 未知/畸形包：记录后跳过，不再因单个坏包拆掉整条会话。
            # 兜底 struct.error/ValueError：即便 packets 层校验遗漏也不击杀 recv 线程。
            logger.warning("dropping malformed/unknown packet (%d bytes): %s",
                           len(raw), exc)
            return []
        with self._lock:
            if self._state == SessionState.CLOSED:
                return []
            if isinstance(packet, SessionAck):
                return self._handle_session_ack(packet)
            if self._state != SessionState.CREATED:
                return []
            if packet.dst_session_id != self._local_sid:
                logger.warning("dropping packet for session %s (local=%s)",
                               packet.dst_session_id, self._local_sid)
                return []
            if isinstance(packet, DataPacket):
                return self._handle_data(packet)
            if isinstance(packet, DataAck):
                return self._handle_data_ack(packet)
            if isinstance(packet, Heartbeat):
                return self._handle_heartbeat()
            if isinstance(packet, HeartbeatAck):
                self._mark_recv_liveness()
                return []
            if isinstance(packet, SessionBye):
                return self._handle_bye()
            return []

    def _handle_session_ack(self, packet: SessionAck) -> _Pending:
        if packet.dst_session_id != self._local_sid:
            return []
        if self._state == SessionState.CREATING:
            return self._complete_creation(packet)
        if self._state == SessionState.RECONNECTING:
            return self._complete_reconnect(packet)
        return []

    def _complete_creation(self, packet: SessionAck) -> _Pending:
        if packet.ack != 0:
            self._state = SessionState.CLOSED
            self._close_socket_locked()
            self._signal_stop()
            self._created_event.set()
            logger.info("session create rejected (ack=%s)", packet.ack)
            return [("on_create_failed",
                     lambda: self._listener.on_create_failed(f"server rejected (ack={packet.ack})"))]
        try:
            self._apply_ack_negotiation(packet)
        except SecurityError as exc:
            self._state = SessionState.CLOSED
            self._close_socket_locked()
            self._signal_stop()
            self._created_event.set()
            logger.warning("session security negotiation failed: %s", exc)
            return [("on_create_failed",
                     lambda reason=str(exc): self._listener.on_create_failed(reason))]
        self._enter_created_locked()
        self._created_event.set()
        logger.info("session created: local=%s peer=%s security=%s version=%s",
                    self._local_sid, self._peer_sid,
                    self._security.security_type, self._negotiated_version)
        return [("on_created", lambda: self._listener.on_created())]

    def _apply_ack_negotiation(self, packet: SessionAck) -> None:
        if packet.security_type not in self._supported_security_types():
            raise SecurityError(f"server selected unavailable security type {packet.security_type}")
        self._peer_sid = packet.src_session_id
        self._negotiated_version = min(SESSIONPROTOCOL_VERSION, packet.version)
        self._security = create_security(packet.security_type)

    def _enter_created_locked(self) -> None:
        # 不在此处 reset framer：重连场景下，新 socket 已在 _reconnect_execute
        # 里 reset 过，这里再 reset 会丢掉"已收到的部分下一帧"尾部字节。
        now = _now_ms()
        self._last_hb_send = now
        self._last_hb_ack = now
        self._hb_ack_deadline = None
        self._fast_hb = False
        self._hb_interval = self._config.heartbeat_interval_ms
        self._state = SessionState.CREATED
        self._created_once = True

    def _complete_reconnect(self, packet: SessionAck) -> _Pending:
        if packet.ack != 0:
            self._close_socket_locked()
            self._state = SessionState.DISCONNECTED
            self._reconnect_attempt_start = 0
            return []
        if packet.security_type != self._security.security_type:
            logger.warning("reconnect selected different security type %s (expected %s)",
                           packet.security_type, self._security.security_type)
            self._close_socket_locked()
            self._state = SessionState.DISCONNECTED
            self._reconnect_attempt_start = 0
            return []
        self._peer_sid = packet.src_session_id
        self._negotiated_version = min(SESSIONPROTOCOL_VERSION, packet.version)
        self._free_acked_locked(packet.ack_seqnum)
        resent = len(self._unacked)
        # 先进入 CREATED 再 resend：send_array/_enqueue_send 仅在 CREATED 真正发包
        self._enter_created_locked()
        self._resend_unacked_locked()
        self._reconnect_attempts = 0
        self._reconnect_next_index = self._current_endpoint_index
        if resent:
            # 重发未确认包属于 at-least-once 语义：若服务端实际已收可能产生重复。
            logger.info("session reconnected; resent %d unacked packet(s)", resent)
        else:
            logger.info("session reconnected")
        return [("on_reconnected", lambda: self._listener.on_reconnected())]

    def _handle_data(self, packet: DataPacket) -> _Pending:
        self._mark_recv_liveness()
        seq = packet.seqnum
        expected = (self._last_recv_seq + 1) % SEQ_MOD
        if seq != expected:
            logger.warning("out-of-order/duplicate data seq=%s (last=%s)",
                           seq, self._last_recv_seq)
            # Keep the cumulative ACK at the last contiguous sequence. This
            # suppresses duplicate delivery and asks the peer to resend a gap.
            raw = DataAck(self._peer_sid, self._last_recv_seq,
                          version=self._negotiated_version).to_bytes()
            self._enqueue_send(raw)
            return []
        try:
            payload = self._security.decrypt(packet.payload)
        except SecurityError as exc:
            logger.warning("data decrypt failed: %s", exc)
            return []
        self._last_recv_seq = seq
        self._unack_count += 1
        self._check_ack_locked()
        return [("on_data", lambda data=payload: self._listener.on_data(data))]

    def _handle_data_ack(self, packet: DataAck) -> _Pending:
        self._mark_recv_liveness()
        self._free_acked_locked(packet.ack_seqnum)
        pending: _Pending = []
        if self._send_blocked and not self._is_ack_buffer_blocked():
            self._send_blocked = False
            pending.append(("on_send_ready", lambda: self._listener.on_send_ready()))
        return pending

    def _handle_heartbeat(self) -> _Pending:
        self._mark_recv_liveness()
        raw = HeartbeatAck(self._peer_sid, version=self._negotiated_version).to_bytes()
        self._enqueue_send(raw)
        return []

    def _handle_bye(self) -> _Pending:
        logger.info("peer sent BYE; closing")
        self._state = SessionState.CLOSED
        self._close_socket_locked()
        self._signal_stop()
        return self._closed_notification_locked()

    def _closed_notification_locked(self) -> _Pending:
        if not self._created_once or self._closed_notified:
            return []
        self._closed_notified = True
        return [("on_closed", lambda: self._listener.on_closed())]

    def _free_acked_locked(self, ack_seq: int) -> None:
        if not self._unacked:
            return
        end = (self._unacked[0].seq + len(self._unacked)) % SEQ_MOD
        while self._unacked:
            front = self._unacked[0].seq
            if not between_interval(ack_seq, front, end):
                break
            sent = self._unacked.popleft()
            self._ack_buffer_size -= len(sent.raw)

    def _resend_unacked_locked(self) -> None:
        for sent in self._unacked:
            self._enqueue_send(sent.raw)

    def _check_ack_locked(self) -> None:
        now = _now_ms()
        threshold = self._config.ack_count_threshold
        if self._unack_count >= threshold or (
            self._unack_count > 0 and now - self._last_ack_time >= self._config.ack_time_threshold_ms
        ):
            raw = DataAck(self._peer_sid, self._last_recv_seq,
                          version=self._negotiated_version).to_bytes()
            if self._enqueue_send(raw):
                self._last_ack_time = now
                self._unack_count = 0

    def _is_ack_buffer_blocked(self) -> bool:
        return (
            (self._ack_buffer_size > self._config.ack_buffer_max_size
             or len(self._unacked) >= MAX_CACHE_PACKET_COUNT)
            and len(self._unacked) > ACK_BUFFER_BLOCK_PACKET_COUNT
        )

    def _mark_recv_liveness(self) -> None:
        self._last_hb_ack = _now_ms()
        self._hb_ack_deadline = None
        if self._fast_hb:
            self._fast_hb = False
            self._hb_interval = self._config.heartbeat_interval_ms

    def _timer_loop(self) -> None:
        while not self._stop_event.wait(0.1):
            now = _now_ms()
            pending: _Pending = []
            plan: Optional[_ReconnectPlan] = None
            with self._lock:
                state = self._state
                if state == SessionState.CREATED:
                    pending += self._heartbeat_tick_locked(now)
                    self._check_ack_locked()
                elif state in (SessionState.DISCONNECTED, SessionState.RECONNECTING):
                    if self._config.enable_reconnect:
                        pending, plan = self._reconnect_tick_locked(now)
                    else:
                        self._state = SessionState.CLOSED
                        self._signal_stop()
                        pending += self._closed_notification_locked()
            # 阻塞的 TCP connect 在锁外执行，避免卡住 close() 等锁持有者。
            if plan is not None:
                self._reconnect_execute(plan)
            self._fire(pending)

    def _heartbeat_tick_locked(self, now: int) -> _Pending:
        if now - self._last_hb_send >= self._hb_interval:
            raw = Heartbeat(self._peer_sid, version=self._negotiated_version).to_bytes()
            self._enqueue_send(raw)
            self._last_hb_send = now
            if self._hb_ack_deadline is None:
                self._hb_ack_deadline = now + self._config.heartbeat_ack_timeout_ms
        if self._hb_ack_deadline is not None and now >= self._hb_ack_deadline:
            if now - self._last_hb_ack >= self._config.heartbeat_timeout_failed_ms:
                self._hb_ack_deadline = None
                logger.info("heartbeat ack timeout; disconnecting")
                return self._disconnect_locked("heartbeat timeout")
            if not self._fast_hb:
                self._fast_hb = True
                self._hb_interval = self._config.fast_heartbeat_interval_ms
            self._hb_ack_deadline = None
        return []

    def _disconnect_locked(self, reason: str) -> _Pending:
        if self._state in (SessionState.DISCONNECTED, SessionState.RECONNECTING,
                           SessionState.CLOSED, SessionState.IDLE):
            return []
        previous = self._state
        self._close_socket_locked()
        self._framer.reset()
        if previous == SessionState.CREATING or not self._config.enable_reconnect:
            self._state = SessionState.CLOSED
            self._signal_stop()
            if previous == SessionState.CREATING:
                self._created_event.set()
                logger.info("create failed: %s", reason)
                return [("on_create_failed",
                         lambda: self._listener.on_create_failed(reason))]
            logger.info("session closed: %s", reason)
            return self._closed_notification_locked()
        self._state = SessionState.DISCONNECTED
        self._disconnect_start = _now_ms()
        self._reconnect_attempt_start = 0
        self._last_reconnect_try = 0
        self._reconnect_attempts = 0
        self._reconnect_next_index = self._current_endpoint_index
        logger.info("session disconnected: %s", reason)
        return [("on_reconnecting", lambda: self._listener.on_reconnecting())]

    def _reconnect_tick_locked(self, now: int) -> Tuple[_Pending, Optional[_ReconnectPlan]]:
        """在锁内决定是否发起一次重连；返回 (回调, 重连计划)。

        真正的阻塞 socket open 由 _reconnect_execute 在锁外执行。"""
        if now - self._disconnect_start > self._config.reconnect_timeout_ms:
            self._state = SessionState.CLOSED
            self._signal_stop()
            logger.info("reconnect timed out; closing")
            return self._closed_notification_locked(), None
        if self._state == SessionState.RECONNECTING:
            if (self._reconnect_attempt_start
                    and now - self._reconnect_attempt_start >= RECONNECT_ATTEMPT_TIMEOUT_MS):
                self._close_socket_locked()
                self._state = SessionState.DISCONNECTED
                self._reconnect_attempt_start = 0
            else:
                return [], None
        backoff = min(RECONNECT_BACKOFF_MS << min(self._reconnect_attempts, 16),
                      RECONNECT_BACKOFF_MAX_MS)
        if now - self._last_reconnect_try < backoff:
            return [], None
        if not self._endpoints:
            return [], None
        self._last_reconnect_try = now
        endpoint_index = self._reconnect_next_index % len(self._endpoints)
        self._reconnect_next_index = (endpoint_index + 1) % len(self._endpoints)
        endpoint = self._endpoints[endpoint_index]
        self._reconnect_attempts += 1
        rereq = SessionReReq(
            dst_session_id=self._peer_sid,
            version=self._negotiated_version,
            src_session_id=self._local_sid,
            application_id=self._config.application_id,
            session_type=SESSIONTYPE_RELIABLE,
            security_type=self._security.security_type,
            ack_seqnum=self._last_recv_seq,
        ).to_bytes()
        plan = _ReconnectPlan(endpoint_index, endpoint, rereq, self._reconnect_attempts)
        return [], plan

    def _reconnect_execute(self, plan: _ReconnectPlan) -> None:
        """锁外打开新 socket 并发出 SessionReReq，再回锁内登记状态。"""
        endpoint = plan.endpoint
        if self._stop_event.is_set():
            return
        try:
            sock = self._open_socket(endpoint.host, endpoint.port)
        except OSError:
            return
        try:
            sock.sendall(plan.rereq)
        except OSError:
            try:
                sock.close()
            except OSError:
                pass
            return
        with self._lock:
            if self._state == SessionState.CLOSED or self._stop_event.is_set():
                try:
                    sock.close()
                except OSError:
                    pass
                return
            self._sock = sock
            self._socket_generation += 1
            self._current_endpoint_index = plan.endpoint_index
            self._state = SessionState.RECONNECTING
            self._reconnect_attempt_start = _now_ms()
            self._framer.reset()
            self._start_recv_thread()
        logger.debug("reconnect attempt #%d to %s", plan.attempt, endpoint)

    def _handle_socket_closed(self, sock: socket.socket) -> None:
        pending: _Pending = []
        with self._lock:
            if sock is not self._sock:
                return
            if self._state in (SessionState.CLOSED, SessionState.IDLE):
                return
            if self._state == SessionState.CREATING:
                self._close_socket_locked()
                self._state = SessionState.CLOSED
                self._signal_stop()
                self._created_event.set()
                logger.info("create failed: connection closed during handshake")
                pending = [("on_create_failed",
                            lambda: self._listener.on_create_failed(
                                "connection closed during handshake"))]
            else:
                pending = self._disconnect_locked("connection closed")
        self._fire(pending)

    def _fire(self, pending: Sequence[Tuple[str, _Callback]]) -> None:
        for event, callback in pending:
            try:
                callback()
            except Exception as exc:
                logger.exception("listener callback %r raised", event)
                try:
                    self._listener.on_listener_error(event, exc)
                except Exception:
                    logger.exception("on_listener_error raised")
