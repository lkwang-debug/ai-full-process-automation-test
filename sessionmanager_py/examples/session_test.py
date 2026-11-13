#!/usr/bin/env python3
"""framecore 会话层发包/收包测试程序。

模式:
  loopback            内置 echo 服务端 + 客户端自测（默认）
  server --port P     运行 echo 服务端（可用于跨机/真实客户端测试）
  client --host --port 连接服务端发包并采集回包

用法示例:
  python examples/session_test.py loopback --count 200 --size 512
  python examples/session_test.py server --port 7000
  python examples/session_test.py client --host 127.0.0.1 --port 7000 --appid 1
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    TcpReliableSession,
    parse_packet,
)
from framecore_session import message as msgio
from framecore_session.constants import SESSIONTYPE_RELIABLE, WSESSION_SECURITY_NONE

PROTOCOL_VERSION = 3


class EchoServer:
    """服务端会话协议的最小实现：握手 -> 回显 DATA -> 回复心跳/重连。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, server_session_id: int = 0xBEEF) -> None:
        self._lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._lsock.bind((host, port))
        self._lsock.listen(16)
        self.host, self.port = self._lsock.getsockname()[:2]
        self._server_sid = server_session_id
        self._stop = threading.Event()
        self._conns: list[socket.socket] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)

    def start(self) -> int:
        self._thread.start()
        return self.port

    def stop(self) -> None:
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

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._lsock.accept()
            except OSError:
                break
            with self._lock:
                self._conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        framer = PacketFramer()
        client_sid = 0
        last_recv = 0xFFFF
        send_seq = 0
        try:
            while not self._stop.is_set():
                try:
                    data = conn.recv(8192)
                except OSError:
                    break
                if not data:
                    break
                try:
                    frames = framer.feed(data)
                except Exception:
                    break
                for raw in frames:
                    try:
                        pkt = parse_packet(raw)
                    except Exception:
                        continue
                    if isinstance(pkt, SessionReq):
                        client_sid = pkt.src_session_id
                        self._send(conn, SessionAck(
                            dst_session_id=client_sid, ack=0,
                            security_type=WSESSION_SECURITY_NONE,
                            src_session_id=self._server_sid, ack_seqnum=0).to_bytes())
                    elif isinstance(pkt, SessionReReq):
                        client_sid = pkt.src_session_id
                        self._send(conn, SessionAck(
                            dst_session_id=client_sid, ack=0,
                            security_type=WSESSION_SECURITY_NONE,
                            src_session_id=self._server_sid,
                            ack_seqnum=last_recv).to_bytes())
                    elif isinstance(pkt, DataPacket):
                        last_recv = pkt.seqnum
                        self._send(conn, DataAck(
                            dst_session_id=client_sid, ack_seqnum=last_recv).to_bytes())
                        self._send(conn, DataPacket(
                            dst_session_id=client_sid, seqnum=send_seq,
                            payload=pkt.payload).to_bytes())
                        send_seq = (send_seq + 1) & 0xFFFF
                    elif isinstance(pkt, Heartbeat):
                        self._send(conn, HeartbeatAck(dst_session_id=client_sid).to_bytes())
                    elif isinstance(pkt, SessionBye):
                        return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _send(conn: socket.socket, raw: bytes) -> None:
        try:
            conn.sendall(raw)
        except OSError:
            pass


class _Collector(SessionListener):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._received: list[bytes] = []
        self.created = threading.Event()
        self.closed = threading.Event()

    def on_created(self) -> None:
        self.created.set()

    def on_closed(self) -> None:
        self.closed.set()

    def on_data(self, data: bytes) -> None:
        with self._lock:
            self._received.append(bytes(data))

    def snapshot(self) -> list[bytes]:
        with self._lock:
            return list(self._received)


def _build_payload(index: int, size: int) -> bytes:
    body = f"msg-{index:06d};".encode()
    if size <= len(body):
        return body[:size]
    return body + (b"0123456789ABCDEF" * ((size - len(body)) // 16 + 1))[:size - len(body)]


def _format_bytes(data: bytes, limit: int = 48) -> str:
    if len(data) <= limit:
        return data.hex()
    return data[:limit].hex() + f"...(+{len(data) - limit} bytes)"


def run_client(args: argparse.Namespace) -> int:
    collector = _Collector()
    config = SessionConfig(
        application_id=args.appid,
        connect_timeout_ms=int(args.connect_timeout * 1000),
        enable_reconnect=not args.no_reconnect,
    )
    session = TcpReliableSession(collector, config)

    print(f"[client] connecting {args.host}:{args.port} appid={args.appid} ...")
    start = time.monotonic()
    if not session.connect(args.host, args.port):
        print("[client] connect FAILED")
        return 1
    print(f"[client] connected, local_sid={session.local_session_id} "
          f"peer_sid={session.peer_session_id}")

    packer = msgio.XmlMsgIOPack()
    sent_payloads: list[bytes] = []
    sent_bytes = 0
    for i in range(args.count):
        if args.text is not None:
            payload = args.text.encode()
        else:
            payload = _build_payload(i, args.size)
        frame = payload if args.raw else packer.pack(payload)
        ok = session.send(frame)
        if not ok:
            print(f"[client] send #{i} blocked/failed (pending={session.pending_send_count})")
            time.sleep(0.01)
            continue
        sent_payloads.append(payload)
        sent_bytes += len(frame)

    print(f"[client] sent {len(sent_payloads)} messages, {sent_bytes} bytes on wire")

    deadline = time.monotonic() + args.linger
    while time.monotonic() < deadline:
        if args.expect_recv and len(collector.snapshot()) >= args.expect_recv:
            break
        time.sleep(0.02)

    elapsed = time.monotonic() - start
    received = collector.snapshot()
    session.close()

    print(f"[client] received {len(received)} data packets in {elapsed:.3f}s")
    matched = 0
    for idx, frame in enumerate(received):
        if args.raw:
            decoded = frame
            tag = "raw"
        else:
            try:
                decoded = packer.unpack(frame)
                tag = "ok"
            except Exception as exc:
                decoded = frame
                tag = f"unpack-fail:{exc}"
        if idx < len(sent_payloads) and decoded == sent_payloads[idx]:
            matched += 1
        if args.dump:
            preview = decoded.decode("utf-8", "replace") if tag == "ok" else _format_bytes(decoded)
            print(f"  recv[{idx}] ({tag}, {len(decoded)}B): {preview[:80]}")

    if not args.raw and sent_payloads:
        print(f"[client] round-trip matched {matched}/{min(len(sent_payloads), len(received))}")
    if sent_bytes and elapsed > 0:
        rate = sent_bytes / 1024 / elapsed
        print(f"[client] send throughput ~{rate:.1f} KiB/s, "
              f"{len(sent_payloads) / elapsed:.1f} msg/s")
    return 0


def run_loopback(args: argparse.Namespace) -> int:
    server = EchoServer("127.0.0.1", 0)
    server.start()
    print(f"[loopback] echo server on 127.0.0.1:{server.port}")
    try:
        client_args = argparse.Namespace(
            host="127.0.0.1", port=server.port, appid=args.appid,
            count=args.count, size=args.size, text=args.text, raw=args.raw,
            connect_timeout=5, no_reconnect=False, linger=args.linger,
            dump=args.dump, expect_recv=args.count)
        return run_client(client_args)
    finally:
        server.stop()


def run_server(args: argparse.Namespace) -> int:
    server = EchoServer("0.0.0.0", args.port)
    server.start()
    print(f"[server] echo server listening on 0.0.0.0:{server.port} (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[server] stopping")
    finally:
        server.stop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="framecore session send/recv test")
    sub = parser.add_subparsers(dest="mode", required=True)

    def add_client_args(p):
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, required=True)
        p.add_argument("--appid", type=int, default=0)
        p.add_argument("--count", type=int, default=10)
        p.add_argument("--size", type=int, default=512)
        p.add_argument("--text", default=None, help="send this text instead of generated payload")
        p.add_argument("--raw", action="store_true", help="skip XmlMsgIOPack envelope")
        p.add_argument("--linger", type=float, default=3.0, help="seconds to wait for recv")
        p.add_argument("--connect-timeout", type=float, default=5.0)
        p.add_argument("--no-reconnect", action="store_true")
        p.add_argument("--dump", action="store_true", help="print each received payload")
        p.add_argument("--expect-recv", type=int, default=0)

    p_client = sub.add_parser("client", help="connect to a server and send/recv")
    add_client_args(p_client)

    p_loop = sub.add_parser("loopback", help="in-process echo server + client self-test")
    p_loop.add_argument("--appid", type=int, default=0)
    p_loop.add_argument("--count", type=int, default=50)
    p_loop.add_argument("--size", type=int, default=512)
    p_loop.add_argument("--text", default=None)
    p_loop.add_argument("--raw", action="store_true")
    p_loop.add_argument("--linger", type=float, default=5.0)
    p_loop.add_argument("--dump", action="store_true")

    p_srv = sub.add_parser("server", help="run echo server")
    p_srv.add_argument("--port", type=int, default=0)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "client":
        return run_client(args)
    if args.mode == "loopback":
        return run_loopback(args)
    if args.mode == "server":
        return run_server(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
