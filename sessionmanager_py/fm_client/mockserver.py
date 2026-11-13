"""内置 mock fmserver —— 用于离线测试与调试。

用 framecore 会话层（raw socket + PacketFramer）实现服务侧，按 SessionReq 的
application_id 路由到 Front(app=20)/Conf(app=11)，业务体用 XmlMsgIOPack + XML，
与真实 fmserver 线缆同构。支持多连接、握手、心跳、数据 ACK，并能回放：
  Front: USERLOGIN / LOGINROOM / EXITROOM / USERLOGOUT
  Conf : LOGINREQ(+推送 USERENTER) / INITCOMPLETE / CHATMSG / CHANGEUSERSTATE / LOGOUTREQ

真实 fmserver 测试请用 fm_client.demo 直连；本类供 pytest 与本地自测使用。
"""
from __future__ import annotations

import copy
import os
import socket
import threading

from framecore_session import (
    DataAck,
    DataPacket,
    Heartbeat,
    HeartbeatAck,
    PacketFramer,
    SessionAck,
    SessionReq,
    XmlMsgIOPack,
    parse_packet,
)
from framecore_session.constants import WSESSION_SECURITY_NONE

from . import conf as C
from . import entrance as E
from . import errcodes as ERR
from . import front as F
from .xmlcmd import XmlCommand
from ._helpers import _as_list


class MockFmServer:
    SERVER_SID = 0xBEEF
    _windows_port_lock = threading.Lock()
    _next_windows_port = 20000

    def __init__(self, host: str = "127.0.0.1", port: int = 0,
                 login_result: int = 0, roomlist_result: int = 0,
                 conf_address_link: str = "", ignored_cmdids=()) -> None:
        self._lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if port and os.name != "nt":
            self._lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if port == 0 and os.name == "nt":
            # This test host uses 1024..15000 as the outbound dynamic range.
            # Binding a listener with port=0 can select a previous client's
            # TIME_WAIT source port and make the next loopback connect fail
            # with WSAEADDRINUSE (10048). Use a monotonically selected range
            # outside the dynamic source ports for Windows mock servers.
            with self._windows_port_lock:
                for _ in range(25000):
                    candidate = self._next_windows_port
                    self.__class__._next_windows_port += 1
                    if self._next_windows_port > 45000:
                        self.__class__._next_windows_port = 20000
                    try:
                        self._lsock.bind((host, candidate))
                        break
                    except OSError:
                        continue
                else:
                    self._lsock.close()
                    raise OSError("no free Windows mock listener port in 20000..45000")
        else:
            self._lsock.bind((host, port))
        self._lsock.listen(8)
        self.host, self.port = self._lsock.getsockname()[:2]
        self.conf_address_link = conf_address_link or f"TCP:{self.host}:{self.port}"
        self.login_result = login_result
        # 模拟 ROOMLIST 房间列表 DB 异常：非 0 时只回 Result+RoomNodeID（仍带节点ID，不带回房列表）
        self.roomlist_result = roomlist_result
        self.ignored_cmdids = set(ignored_cmdids)
        self._stop = threading.Event()
        self._conns: list = []
        self._workers: list[threading.Thread] = []
        self._conf_peers: list = []        # [(user_id, send_cmd)]，广播/定向通知用
        self._capability_peers = {}
        self._multi_sharing = {}
        self._h5_votes = {}
        self._feature_subscribers = {}
        self._recalled_msg_ids = set()
        self._room_locked = False
        self._uid_counter = 1000           # 每个登录客户端分配唯一 uid（首个 1001，便于多端区分）
        self.received_app_ids: list = []
        self.received_commands: list = []
        self._av_mix_params = {
            "Audio": {"FecType": 1, "EncoderID": 6},
            "Video": {
                "Width": 320, "Height": 240, "FrameRate": 15,
                "EncoderID": 3, "EncoderMode": 0, "Bitrate": 30000,
                "VBRQuality": 50, "KeyFrameInterval": 30,
            },
        }
        self.av_mix_supported = True
        self.offline_users = [
            {"UserID": 3003, "Right": 2, "UserLevel": 0, "Name": "offline-1",
             "DisplayName": "离线甲", "DeptID": "2", "UserNodeID": "1",
             "Tel": "20000", "Mobile": "13900000000", "EMail": "a@example.com", "Sex": 1},
        ]
        self.departments = [
            {"DeptID": "1", "DeptName": "root", "ParentDeptID": "0", "NodeID": "1"},
            {"DeptID": "2", "DeptName": "dev", "ParentDeptID": "1", "NodeID": "1"},
        ]
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._lsock.close()
        except OSError:
            pass
        with self._lock:
            conns = list(self._conns)
            self._conns.clear()
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                c.close()
            except OSError:
                pass
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        with self._lock:
            workers = list(self._workers)
            self._workers.clear()
        for worker in workers:
            if worker.is_alive() and worker is not threading.current_thread():
                worker.join(timeout=1.0)

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._lsock.accept()
            except OSError:
                break
            with self._lock:
                self._conns.append(conn)
            worker = threading.Thread(target=self._serve, args=(conn,), daemon=True)
            with self._lock:
                self._workers.append(worker)
            worker.start()

    # ---- 多客户端广播支持（让"另一个模拟端"也能收到广播）----
    def _alloc_uid(self) -> int:
        with self._lock:
            self._uid_counter += 1
            return self._uid_counter

    def _register_conf_peer(self, user_id, send_cmd) -> None:
        with self._lock:
            if not any(peer_send is send_cmd for _, peer_send in self._conf_peers):
                self._conf_peers.append((str(user_id), send_cmd))

    def _unregister_conf_peer(self, send_cmd) -> None:
        with self._lock:
            self._capability_peers.pop(send_cmd, None)
            for subscribers in self._feature_subscribers.values():
                subscribers.discard(send_cmd)
            self._conf_peers = [
                peer for peer in self._conf_peers if peer[1] is not send_cmd
            ]

    def _broadcast(self, cmd: XmlCommand) -> None:
        """把广播型 +100 推送给所有已入会的 conf 客户端（含发送者，模拟真实服务器 fan-out）。"""
        with self._lock:
            peers = list(self._conf_peers)
        for _, send_cmd in peers:
            send_cmd(cmd)

    def _send_to_users(self, user_ids, cmd: XmlCommand, *, exclude=()) -> None:
        targets = {str(user_id) for user_id in user_ids}
        excluded = {str(user_id) for user_id in exclude}
        with self._lock:
            peers = list(self._conf_peers)
        for user_id, send_cmd in peers:
            if user_id in targets and user_id not in excluded:
                send_cmd(copy.deepcopy(cmd))

    def _serve(self, conn: socket.socket) -> None:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        framer = PacketFramer()
        packer = XmlMsgIOPack()
        client_sid = 0
        app_id = None
        last_recv = 0xFFFF
        box = {"send_seq": 0}
        ctx = {"uid": None}              # 本连接 USERLOGIN 分配的 uid（_front/_conf 共用）

        def send_raw(raw: bytes) -> None:
            try:
                conn.sendall(raw)
            except OSError:
                pass

        def send_cmd(cmd: XmlCommand) -> None:
            payload = packer.pack(cmd._relay_raw_xml if hasattr(cmd, "_relay_raw_xml") else cmd.to_bytes())
            pkt = DataPacket(dst_session_id=client_sid, seqnum=box["send_seq"], payload=payload)
            box["send_seq"] = (box["send_seq"] + 1) & 0xFFFF
            send_raw(pkt.to_bytes())

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
                    try:
                        pkt = parse_packet(raw)
                    except Exception:
                        continue
                    if isinstance(pkt, SessionReq):
                        client_sid = pkt.src_session_id
                        app_id = pkt.application_id
                        with self._lock:
                            self.received_app_ids.append(app_id)
                        send_raw(SessionAck(
                            dst_session_id=client_sid, ack=0,
                            security_type=WSESSION_SECURITY_NONE,
                            src_session_id=self.SERVER_SID, ack_seqnum=0).to_bytes())
                    elif isinstance(pkt, DataPacket):
                        last_recv = pkt.seqnum
                        send_raw(DataAck(
                            dst_session_id=client_sid, ack_seqnum=last_recv).to_bytes())
                        try:
                            plain = packer.unpack(pkt.payload)
                            cmd = XmlCommand.from_bytes(plain)
                            if cmd.cmdid == C.RELAYMESSAGE:
                                cmd._relay_raw_xml = plain
                        except Exception:
                            continue
                        with self._lock:
                            self.received_commands.append((app_id, cmd))
                        self._dispatch(app_id, cmd, send_cmd, ctx)
                    elif isinstance(pkt, Heartbeat):
                        send_raw(HeartbeatAck(dst_session_id=client_sid).to_bytes())
        finally:
            self._unregister_conf_peer(send_cmd)
            try:
                conn.close()
            except OSError:
                pass
            with self._lock:
                if conn in self._conns:
                    self._conns.remove(conn)

    def _dispatch(self, app_id, cmd: XmlCommand, send_cmd, ctx) -> None:
        if cmd.cmdid in self.ignored_cmdids:
            return
        if app_id == F.FRONT_APP_ID:
            self._front(cmd, send_cmd, ctx)
        elif app_id == C.CONF_APP_ID:
            self._conf(cmd, send_cmd, ctx)
        elif app_id == E.ENTRANCE_APP_ID:
            self._entrance(cmd, send_cmd)

    def _entrance(self, cmd: XmlCommand, send_cmd) -> None:
        if cmd.cmdid == E.CONFIGREQ:
            send_cmd(XmlCommand(E.CONFIGREP, {
                "PlatformType": "private",
                "ConfigCenterAddr": "https://127.0.0.1:8443",
            }, ver=E.ENTRANCE_VER, seq=cmd.seq))

    def _front(self, cmd: XmlCommand, send_cmd, ctx) -> None:
        if cmd.cmdid == F.USERLOGIN_REQ:
            ctx["uid"] = self._alloc_uid()
            send_cmd(XmlCommand(F.USERLOGIN_REP, {
                "Result": self.login_result,
                "LocalNodeID": "1", "LocalUserID": ctx["uid"], "BusinessName": "mock",
            }, ver=F.FRONT_VER, seq=cmd.seq))
        elif cmd.cmdid == F.LOGINROOM_REQ:
            if self.login_result != 0:
                send_cmd(XmlCommand(F.LOGINROOM_REP, {"Result": self.login_result},
                                    ver=F.FRONT_VER, seq=cmd.seq))
                return
            uid = ctx["uid"] or 1001
            send_cmd(XmlCommand(F.LOGINROOM_REP, {
                "Result": 0, "RoomNodeID": "1",
                "RoomID": cmd.get("RoomID"), "RealRoomID": cmd.get("RoomID"),
                "UserRight": 2, "RoomVerifyMode": 3, "Token": "mock-token-xyz",
                "RoomUserID": uid, "RoomSrvNodeID": "1$SID:11$AID:11",
                "Server": [{"SrvAddr": self.conf_address_link, "SrvName": "ConfSrv"}],
            }, ver=F.FRONT_VER, seq=cmd.seq))
        elif cmd.cmdid == F.EXITROOM_REQ:
            send_cmd(XmlCommand(F.EXITROOM_REP, {"Result": 0}, ver=F.FRONT_VER, seq=cmd.seq))
        elif cmd.cmdid == F.USERLOGOUT_REQ:
            send_cmd(XmlCommand(F.USERLOGOUT_REP, {"Result": 0}, ver=F.FRONT_VER, seq=cmd.seq))
        elif cmd.cmdid == F.ROOMLIST_REQ:
            if self.roomlist_result != 0:
                # 模拟真实 fmserver：列表 DB 异常(0x1002)，但回复仍带 RoomNodeID
                send_cmd(XmlCommand(F.ROOMLIST_REP, {
                    "Result": self.roomlist_result, "IsTopNode": 1, "RoomNodeID": "1",
                }, ver=F.FRONT_VER, seq=cmd.seq))
                return
            send_cmd(XmlCommand(F.ROOMLIST_REP, {
                "Result": 0, "RoomNodeID": "1",
                "RoomInfo": [
                    {"Name": "Room-A", "RoomID": 100001, "RoomAppID": 11,
                     "MaxUserCount": 100, "CurUserCount": 3,
                     "HopeStartTime": "0000-00-00 00:00:00", "HopeEndTime": "0000-00-00 00:00:00"},
                    {"Name": "Room-B", "RoomID": 100002, "RoomAppID": 11,
                     "MaxUserCount": 50, "CurUserCount": 0,
                     "HopeStartTime": "2026-07-13 09:00:00", "HopeEndTime": "2026-07-13 10:00:00"},
                    {"Name": "Room-Instant", "RoomID": 1000000001, "RoomAppID": 11,
                     "MaxUserCount": 20, "CurUserCount": 1,
                     "HopeStartTime": "0000-00-00 00:00:00", "HopeEndTime": "0000-00-00 00:00:00"},
                ],
            }, ver=F.FRONT_VER, seq=cmd.seq))
        elif cmd.cmdid == F.GETROOMID_REQ:
            send_cmd(XmlCommand(F.GETROOMID_REP, {
                "Result": 0, "RoomId": 100001, "RoomNodeID": "1",
            }, ver=F.FRONT_VER, seq=cmd.seq))
        elif cmd.cmdid == F.GETAUTHTYPE_REQ:
            send_cmd(XmlCommand(F.GETAUTHTYPE_REP, {
                "AuthType": "FSMeetingAuth",
            }, ver=F.FRONT_VER, seq=cmd.seq))

    def _extended_conf(self, cmd: XmlCommand, send_cmd, ctx) -> bool:
        """Model routing contracts, not external media engines or server permissions."""
        if cmd.cmdid == C.LOGINREQ:
            features = cmd.get("SupportFeatures") or ""
            if isinstance(features, dict):
                features = features.get("val") or ""
            ctx.update(uid=cmd.get("UserID"), room=cmd.get("RoomID"),
                       features=features)
            with self._lock:
                self._capability_peers[send_cmd] = dict(ctx)
                subscriptions = [(feature, list(subscribers))
                                 for (room, feature), subscribers in self._feature_subscribers.items()
                                 if room == ctx["room"] and feature in ctx["features"].split(",")]
            for feature, subscribers in subscriptions:
                for subscriber in subscribers:
                    subscriber(XmlCommand(C.FEATUREUSERSREP, {
                        "Feature": feature, "UserID": ctx["uid"], "End": {}}, ver=C.CONF_VER_LOGINREP))
            return False
        room = ctx.get("room")
        uid = str(ctx.get("uid"))
        with self._lock:
            peers = [(send, peer) for send, peer in self._capability_peers.items()
                     if peer.get("room") == room]

        def reply(cmdid, fields):
            send_cmd(XmlCommand(cmdid, fields, ver=C.CONF_VER_LOGINREP, seq=cmd.seq))

        if cmd.cmdid == C.RELAYMESSAGE:
            # Match server OnTransData: forward original bytes without parsing
            # and reserializing inner message attributes or unknown fields.
            dst = str(cmd.get("DstUserID"))
            extra = (cmd.get("DstUserList") or {}).get("UserID", [])
            extra = extra if isinstance(extra, list) else [extra]
            targets = {dst, *(str(item) for item in extra)}
            for send, peer in peers:
                peer_uid = str(peer["uid"])
                if (dst == "0" and peer_uid != str(cmd.get("SrcUserID"))) or peer_uid in targets:
                    send(copy.deepcopy(cmd))
            return True

        if cmd.cmdid == C.SETMULTIDATASHARER:
            enabled = cmd.get("EnableMultiDataSharer")
            with self._lock:
                previous = self._multi_sharing.get(room, "0")
                self._multi_sharing[room] = enabled
            fields = {"Result": 0, "SrcUserID": uid, "EnableMultiDataSharer": enabled,
                      "Force": cmd.get("Force")}
            if previous == enabled:
                reply(C.MULTIDATASHARERNOTIFY, fields)
            else:
                for send, _ in peers:
                    send(XmlCommand(C.MULTIDATASHARERNOTIFY, fields, ver=C.CONF_VER_LOGINREP))
        elif cmd.cmdid == C.QUERYUSERFEATURES:
            for _, peer in peers:
                if str(peer["uid"]) == str(cmd.get("UserID")):
                    reply(C.USERFEATURESREP, {"UserID": peer["uid"],
                                              "SupportFeatures": peer["features"]})
                    break
        elif cmd.cmdid == C.QUERYFEATUREUSERS:
            feature = cmd.get("Feature")
            query_type = str(cmd.get("Type"))
            with self._lock:
                subscribers = self._feature_subscribers.setdefault((room, feature), set())
                if query_type == "2":
                    subscribers.discard(send_cmd)
                    return True
                if query_type == "1":
                    subscribers.add(send_cmd)
            ids = [str(peer["uid"]) for _, peer in peers if feature in peer["features"].split(",")]
            # Small pages exercise the End marker rather than an idle-time heuristic.
            for index in range(0, max(1, len(ids)), 2):
                fields = {"Feature": feature, "UserID": ",".join(ids[index:index + 2])}
                if index + 2 >= len(ids):
                    fields["End"] = {}
                reply(C.FEATUREUSERSREP, fields)
        elif cmd.cmdid == C.MODIFYSELFSTATERESULT:
            pass  # 6546 is an acknowledgement, not a state update or request.
        elif cmd.cmdid == C.ABILITYRENEGOTIATION:
            reply(C.ABILITYRENEGOTIATIONREP, {"Layout": {"Code": cmd.get("Layout")["Code"]}})
        elif cmd.cmdid == C.H5VOTE:
            key = (room, str(cmd.get("ID")))
            with self._lock:
                if str(cmd.get("State")) == "2":
                    target = cmd.get("UserList") or {}
                    ids = _as_list(target.get("UserID"))
                    targets = {str(value) for value in ids}
                    if not targets or targets == {"0"}:
                        targets = {str(peer["uid"]) for _, peer in peers if str(peer["uid"]) != uid}
                    self._h5_votes[key] = targets
                else:
                    targets = self._h5_votes.pop(key, set())
            fields = {key: value for key, value in cmd.fields.items() if key != "UserList"}
            fields["Creator"] = uid
            for send, peer in peers:
                if str(peer["uid"]) in targets:
                    send(XmlCommand(C.H5VOTENOTIFY, fields, ver=C.CONF_VER_LOGINREP))
        elif cmd.cmdid == C.MODIFYGROUPCONFIG:
            reply(C.MODIFYGROUPCONFIGREP, {"RoomInfo": cmd.get("RoomInfo")})
        elif cmd.cmdid == C.SETASRSUBTITLE:
            reply(C.SETASRSUBTITLEREP, {"Result": 0, **cmd.fields})
        elif cmd.cmdid == C.SETUSERCUSTOMDATA:
            for field in ("PublicData", "PrivateData"):
                if field not in cmd.fields:
                    continue
                fields = {"SrcUserID": uid, "UserID": cmd.get("UserID"), field: cmd.get(field)}
                for send, peer in peers:
                    if field == "PublicData" or str(peer["uid"]) == str(cmd.get("UserID")):
                        send(XmlCommand(C.USERCUSTOMDATANOTIFY, fields, ver=C.CONF_VER_LOGINREP))
        else:
            return False
        return True

    def _conf(self, cmd: XmlCommand, send_cmd, ctx) -> None:
        if self._extended_conf(cmd, send_cmd, ctx):
            return
        if cmd.cmdid == C.LOGINREQ:
            ctx["uid"] = cmd.get("UserID")
            if self._room_locked and self._conf_peers:
                send_cmd(XmlCommand(C.WAITOPENDOOR, {}, ver=C.CONF_VER_LOGINREP))
                self._broadcast(XmlCommand(C.KNOCKNOTIFY, {
                    "UserID": ctx["uid"], "DisplayName": cmd.get("UserName"),
                }, ver=C.CONF_VER_LOGINREP))
                return
            self._register_conf_peer(ctx["uid"], send_cmd)  # 登记为可接收广播的会议成员
            send_cmd(XmlCommand(C.LOGINREP, {           # 6601 回复（仅本人）
                "Result": 0, "SupportFSP": 0,
                "UserSrvNodeID": "1$SID:11$AID:11", "SupportFeatures": "",
                "DeptInfo": {"Dept": [
                    {"DeptID": "1", "DeptName": "root", "ParentDeptID": "0", "NodeID": "1"},
                    {"DeptID": "2", "DeptName": "dev", "ParentDeptID": "1", "NodeID": "1"},
                ]},
                "UserInfo": {"Right": 2, "DisplayName": cmd.get("UserName"),
                             "UserLevel": 0, "DeptID": "2", "UserNodeID": "1", "SeatList": 0,
                             "Tel": "10000", "Mobile": "13800000000",
                             "EMail": "u@example.com", "Sex": 0},
            }, ver=C.CONF_VER_LOGINREP, seq=cmd.seq))
            # 6602 广播：新成员入会，会内所有成员（含自己）都收
            self._broadcast(XmlCommand(C.USERENTER, {"User": [{
                "UserID": cmd.get("UserID"), "Right": 2,
                "Name": cmd.get("UserName"), "DisplayName": cmd.get("UserName"),
            }]}, ver=C.CONF_VER_LOGINREP))
        elif cmd.cmdid == C.INITCOMPLETE:
            self._broadcast(XmlCommand(C.USERENTER, {"User": [{
                "UserID": 2002, "Right": 2, "Name": "other", "DisplayName": "other",
            }]}, ver=C.CONF_VER_LOGINREP))
        elif cmd.cmdid == C.CHATMSG:
            fields = {
                "SrcUserID": cmd.get("SrcUserID"), "DstUserID": cmd.get("DstUserID"),
                "Text": cmd.get("Text"), "MsgID": "m-1",
            }
            for key in ("Color", "Font"):
                if cmd.get(key) is not None:
                    fields[key] = cmd.get(key)
            self._broadcast(XmlCommand(C.CHATMSGNOTIFY, fields, ver=C.CONF_VER_LOGINREP))
        elif cmd.cmdid == C.CHANGEUSERSTATE:
            if cmd.get("PrivateTalk") is not None:
                talk = cmd.get("PrivateTalk")
                state = int(talk["State"])
                if state == 2 and str(cmd.get("UserID")) != str(ctx.get("uid")):
                    state = 1
                for target, partner in ((cmd.get("UserID"), talk["DstUserID"]),
                                        (talk["DstUserID"], cmd.get("UserID"))):
                    self._broadcast(XmlCommand(C.USERSTATECHANGED, {"UserID": target,
                        "PrivateTalk": {"DstUserID": partner, "State": state}}, ver=C.CONF_VER_LOGINREP))
                return
            if str(cmd.get("UserID")) != str(ctx.get("uid")) and str(cmd.get("VNCState")) == "2":
                self._send_to_users([cmd.get("UserID")], XmlCommand(C.MODIFYSELFSTATENOTIFY, {
                    "SrcUserID": ctx.get("uid"), "VNCState": 2,
                    "VNCAudioID": cmd.get("VNCAudioID") or 0}, ver=C.CONF_VER_LOGINREP))
                return
            # 回显所有状态子字段（音视频/主讲 DataState/管理员 ManagerState/辅流 MediaShare·VNC）
            fields = {"UserID": cmd.get("UserID")}
            for k in ("Audio", "Video", "DataState", "ManagerState", "Right",
                      "MediaShareState", "AudioShareID", "VideoShareID", "VNCState", "VNCAudioID",
                      "PrivateTalk", "AudioOutMute", "Info", "WBMarkState", "EnableChat",
                      "GroupState", "OnlineState", "RecordState", "SpeakerState", "CameraState",
                      "MicrophoneState", "LocalVPState"):
                if cmd.get(k) is not None:
                    fields[k] = cmd.get(k)
            self._broadcast(XmlCommand(C.USERSTATECHANGED, fields, ver=C.CONF_VER_LOGINREP))
        elif cmd.cmdid == C.CHANGEVIDEOPOLLINGREQ:
            vps = cmd.get("VideoPolling")
            if isinstance(vps, dict):
                vps = [vps]
            elif not isinstance(vps, list):
                vps = []
            for vp in vps:
                ul = vp.get("UserList") if isinstance(vp, dict) else None
                users = []
                if isinstance(ul, dict):
                    u = ul.get("UserID")
                    users = u if isinstance(u, list) else ([u] if u is not None else [])
                notify = {"@val": vp.get("val", 0) if isinstance(vp, dict) else 0,
                          "Operation": C.POLLING_NOTIFY_CHANGE,
                          "CurrentUserID": users[0] if users else 0,
                          "NextUserID": users[1] if len(users) > 1 else 0,
                          "Interval": (vp.get("Interval") if isinstance(vp, dict) else None) or 5,
                          "Screen": (vp.get("Screen") if isinstance(vp, dict) else None) or 0,
                          "Window": (vp.get("Window") if isinstance(vp, dict) else None) or 0}
                self._broadcast(XmlCommand(C.VIDEOPOLLINGNOITFY, {"VideoPolling": notify},
                                           ver=C.CONF_VER_LOGINREP))
        elif cmd.cmdid == C.LOGOUTREQ:
            self._broadcast(XmlCommand(C.USERLEAVE, {"UserID": cmd.get("UserID")}, ver=C.CONF_VER_LOGINREP))
        elif cmd.cmdid == C.KICKUSER:
            self._broadcast(XmlCommand(C.USERKICKED, {
                "KickID": ctx.get("uid") or 1001, "UserID": cmd.get("UserID")}, ver=C.CONF_VER_LOGINREP))
        elif cmd.cmdid == C.CLOSEROOM:
            self._broadcast(XmlCommand(C.ROOMCLOSED, {
                "Reason": cmd.get("Reason"), "Description": cmd.get("Description")},
                ver=C.CONF_VER_LOGINREP))
        elif cmd.cmdid == C.AVINFO:
            self._broadcast(XmlCommand(C.AVINFONOTIFY, dict(cmd.fields), ver=C.CONF_VER_LOGINREP))
        elif cmd.cmdid == C.CHANGECONFSTATE:
            if cmd.get("Lock") is not None:
                self._room_locked = str(cmd.get("Lock")) == "1"
            self._broadcast(XmlCommand(C.CONFSTATECHANGED, dict(cmd.fields), ver=C.CONF_VER_LOGINREP))
        elif cmd.cmdid == C.REQCHAIRRIGHT:
            right = 3 if str(cmd.get("ChairRight")) == "1" else 2
            self._broadcast(XmlCommand(C.USERSTATECHANGED, {"UserID": ctx.get("uid") or 1001, "Right": right},
                                ver=C.CONF_VER_LOGINREP))
        elif cmd.cmdid == C.SILENT:
            self._broadcast(XmlCommand(C.SILENTNOTIFY, {}, ver=C.CONF_VER_LOGINREP))
        # ---- 通讯录 / 组织架构 / 会议配置 ----
        elif cmd.cmdid == C.REQOFFLINES:
            users = copy.deepcopy(self.offline_users)
            if not users:
                send_cmd(XmlCommand(
                    C.OUTLINE, {"Result": 0}, ver=C.CONF_VER_LOGINREP, seq=cmd.seq))
            for offset in range(0, len(users), 60):
                send_cmd(XmlCommand(
                    C.OUTLINE,
                    {"Result": 0, "User": users[offset:offset + 60]},
                    ver=C.CONF_VER_LOGINREP,
                    seq=cmd.seq,
                ))
        elif cmd.cmdid == C.DEPTINFOREQ:
            depts = copy.deepcopy(self.departments)
            if not depts:
                send_cmd(XmlCommand(C.DEPTINFOREP, {
                    "Result": 0, "DeptEnd": "",
                }, ver=C.CONF_VER_LOGINREP, seq=cmd.seq))
            for offset in range(0, len(depts), 150):
                fields = {"Result": 0, "Dept": depts[offset:offset + 150]}
                if offset + 150 >= len(depts):
                    fields["DeptEnd"] = ""
                send_cmd(XmlCommand(
                    C.DEPTINFOREP, fields, ver=C.CONF_VER_LOGINREP, seq=cmd.seq))
        elif cmd.cmdid == C.GETROOMCONFIGREQ:
            send_cmd(XmlCommand(C.GETROOMCONFIGREP, {
                "Result": 0, "InviteCode": "100100100",
            }, ver=C.CONF_VER_LOGINREP, seq=cmd.seq))
        # ---- 终端呼叫子块（邀请电话/H323 入会）----
        elif cmd.cmdid == C.MAKECALL_REQ:
            call_type = cmd.get("CallType") or C.CALLTYPE_PHONE
            req_infos = cmd.get("ReqCallInfo")
            if isinstance(req_infos, dict):
                req_infos = [req_infos]
            elif not isinstance(req_infos, list):
                req_infos = []
            send_cmd(XmlCommand(C.MAKECALL_REP, {
                "OprResult": 0, "CallType": call_type, "RemainCallCount": 10,
                "ReqCallInfo": [{"CalleeNum": ri.get("CalleeNum"), "CalleeUserID": 5000 + i}
                                for i, ri in enumerate(req_infos)],
            }, ver=C.CONF_VER_LOGINREP, seq=cmd.seq))
            # 模拟呼叫状态推送：每个被叫→已接通
            for ri in req_infos:
                send_cmd(XmlCommand(C.CALLSTATE_NOTIFY, {
                    "CallType": call_type, "CalleeNum": ri.get("CalleeNum"),
                    "CallState": C.CALL_STATE_CONFIRMED, "ReleaseCause": 0,
                }, ver=C.CONF_VER_LOGINREP))
        elif cmd.cmdid == C.HANGUP_REQ:
            call_type = cmd.get("CallType") or C.CALLTYPE_PHONE
            send_cmd(XmlCommand(C.HANGUP_REP, {
                "OprResult": 0, "CallType": call_type,
            }, ver=C.CONF_VER_LOGINREP, seq=cmd.seq))
        elif cmd.cmdid == C.CALL_LIST_REQ:
            call_type = cmd.get("CallType") or C.CALLTYPE_PHONE
            send_cmd(XmlCommand(C.CALL_LIST_REP, {
                "OprResult": 0, "CallType": call_type,
                "CalleeInfo": [
                    {"CalleeNum": "13800000000", "CalleeUserID": 5000, "CallState": 2},
                ],
            }, ver=C.CONF_VER_LOGINREP, seq=cmd.seq))
        # ---- 扩展会议控制 ----
        elif cmd.cmdid == C.ALLOWKNOCK:
            pass  # 主持人答复敲门，无回复
        elif cmd.cmdid == C.CREATEGROUP:
            rooms_in = cmd.get("RoomInfo")
            if isinstance(rooms_in, dict):
                rooms_in = [rooms_in]
            elif not isinstance(rooms_in, list):
                rooms_in = []
            base = 0x70000000
            rooms_out = []
            for i, r in enumerate(rooms_in):
                rooms_out.append({
                    "RoomID": base + 1 + i, "Theme": r.get("Theme"),
                    "Creator": cmd.get("UserID") or "1001", "Duration": 0,
                    "MaxUserCount": r.get("MaxUserCount"), "EnableJoin": r.get("EnableJoin"),
                    "EnableLeave": r.get("EnableLeave"),
                })
            send_cmd(XmlCommand(C.GROUPINFO, {"RoomInfo": rooms_out},
                                ver=C.CONF_VER_LOGINREP, seq=cmd.seq))
            if "GroupMeeting" in ctx.get("features", "").split(","):
                send_cmd(XmlCommand(C.GROUPUSERSTATENOTIFY, {"RoomUserInfo": [
                    {"RoomID": room["RoomID"], "User": [
                        {**user, "State": 0} for user in _as_list(source.get("User"))]}
                    for room, source in zip(rooms_out, rooms_in)]}, ver=C.CONF_VER_LOGINREP))
        elif cmd.cmdid == C.ENTERGROUPREQ:
            users = cmd.get("User")
            if isinstance(users, dict):
                users = [users]
            target_ids = [user.get("ID") for user in (users or []) if isinstance(user, dict)]
            self._send_to_users(target_ids, XmlCommand(C.ENTERGROUPREP, {
                "SrcUserID": ctx.get("uid") or "1001",
                "RoomSrvNodeID": "1$SID:11$AID:11", "SrvAuthCode": "auth-mock",
                "RoomID": cmd.get("RoomID"), "Theme": "group-theme",
                "Right": 3, "Token": "group-token-mock", "SrvAddr": self.conf_address_link,
            }, ver=C.CONF_VER_LOGINREP))
        elif cmd.cmdid == C.CHANGEGROUPSTATEREQ:
            send_cmd(XmlCommand(C.CHANGEGROUPSTATEREP, {
                "RoomID": cmd.get("RoomID"), "State": cmd.get("State") or 0},
                                ver=C.CONF_VER_LOGINREP, seq=cmd.seq))
        elif cmd.cmdid == C.ENTERMAINREQ:
            user_ids = cmd.get("UserID")
            if not isinstance(user_ids, list):
                user_ids = [user_ids]
            self._send_to_users(user_ids, XmlCommand(
                C.ENTERMAINREP,
                {"SrcUserID": ctx.get("uid") or "1001"},
                ver=C.CONF_VER_LOGINREP,
            ))
        elif cmd.cmdid == C.OPERATIONRIGHTREQ:
            try:
                operation = int(cmd.get("Operation"))
                right_type = int(cmd.get("RightType"))
                right_index = int(cmd.get("RightIndex") or 0)
            except (TypeError, ValueError):
                operation, right_type, right_index = -1, -1, -1
            result = (0 if 0 <= operation <= 4 and 1 <= right_type <= 12
                      and right_index == 0 else ERR.INVALIDARG)
            send_cmd(XmlCommand(C.OPERATIONRIGHTREP, {
                "Result": result, "OccupiedUserID": "",
                "Operation": operation, "RightType": right_type,
                "RightIndex": right_index,
            }, ver=C.CONF_VER_LOGINREP, seq=cmd.seq))
        elif cmd.cmdid == C.CHANGEROLEPERMISSION:
            role_codes = cmd.get("RoleCode")
            if not isinstance(role_codes, list):
                role_codes = [role_codes]
            permissions = {
                key: value for key, value in cmd.fields.items()
                if key != "RoleCode"
            }
            for role_code in role_codes:
                self._broadcast(XmlCommand(
                    C.ROLEPERMISSIONCHANGED,
                    {"RoleCode": role_code, **permissions},
                    ver=C.CONF_VER_LOGINREP,
                ))
        elif cmd.cmdid == C.CHANGEUSERPERMISSION:
            self._broadcast(XmlCommand(
                C.USERPERMISSIONCHANGED,
                dict(cmd.fields),
                ver=C.CONF_VER_LOGINREP,
            ))
        elif cmd.cmdid == C.MEETINGRENAMEREQ:
            send_cmd(XmlCommand(C.MEETINGRENAMEREP, {"Name": cmd.get("Name")},
                                ver=C.CONF_VER_LOGINREP, seq=cmd.seq))
        elif cmd.cmdid == C.MEETINGCHAR:
            self._broadcast(XmlCommand(C.MEETINGCHARNOTIFY,
                                {"Data": cmd.get("Data"), "Duration": cmd.get("Duration")},
                                ver=C.CONF_VER_LOGINREP))  # 广播 6615
        elif cmd.cmdid == C.CLOSESUBTITLEREQ:
            self._broadcast(XmlCommand(
                C.CLOSESUBTITLEREP, {}, ver=C.CONF_VER_LOGINREP, seq=cmd.seq))
        elif cmd.cmdid == C.SYSMSGREQ:
            message_id = int(cmd.get("SysMsgID") or 0)
            messages = {
                6: (C.SYSMSG_TYPE_WINDOW,
                    "Setting failed. SVC encoding does not support setting"),
            }
            if message_id in messages:
                msg_type, message = messages[message_id]
                notify = XmlCommand(C.SYSMSG, {
                    "Type": msg_type, "Msg": message,
                }, ver=C.CONF_VER_LOGINREP)
                dst_user_id = cmd.get("DstUserID")
                if str(dst_user_id or "0") == "0":
                    with self._lock:
                        all_user_ids = [user_id for user_id, _ in self._conf_peers]
                    self._send_to_users(all_user_ids, notify, exclude=[ctx.get("uid")])
                else:
                    self._send_to_users([dst_user_id], notify)
        elif cmd.cmdid == C.SETROOMDATA:
            items = cmd.get("DataItem")
            if not isinstance(items, list):
                items = [items]
            notified = []
            for item in items:
                value = dict(item or {})
                value.setdefault("Duration", 0)
                notified.append(value)
            self._broadcast(XmlCommand(
                C.ROOMDATANOTIFY,
                {"DataItem": notified},
                ver=C.CONF_VER_LOGINREP,
            ))
        elif cmd.cmdid == C.GETAVMIXPARAMREQ:
            if self.av_mix_supported:
                with self._lock:
                    params = copy.deepcopy(self._av_mix_params)
            else:
                params = {"Result": ERR.UNSUPPORTED}
            send_cmd(XmlCommand(
                C.GETAVMIXPARAMREP,
                params,
                ver=C.CONF_VER_LOGINREP,
                seq=cmd.seq,
            ))
        elif cmd.cmdid == C.SETAVMIXPARAM:
            with self._lock:
                if str(cmd.get("EnableDefault")) in ("1", "True", "true"):
                    self._av_mix_params = {
                        "Audio": {"FecType": 1, "EncoderID": 6},
                        "Video": {
                            "Width": 320, "Height": 240, "FrameRate": 15,
                            "EncoderID": 3, "EncoderMode": 0,
                            "Bitrate": 30000, "VBRQuality": 50,
                            "KeyFrameInterval": 30,
                        },
                    }
                else:
                    for key in ("Audio", "Video"):
                        if isinstance(cmd.get(key), dict):
                            self._av_mix_params[key] = copy.deepcopy(cmd.get(key))
        elif cmd.cmdid == C.SETVIDEOSUBTITLE:
            self._broadcast(XmlCommand(
                C.VIDEOSUBTITLENOTIFY,
                dict(cmd.fields),
                ver=C.CONF_VER_LOGINREP,
            ))
        elif cmd.cmdid == C.SENDFILE:
            self._broadcast(XmlCommand(C.SENDFILENOTIFY, {
                "SrcUserID": cmd.get("SrcUserID") or "1001", "FileInfo": cmd.get("FileInfo"),
            }, ver=C.CONF_VER_LOGINREP))  # 广播 6616
        elif cmd.cmdid == C.RECALLMSGREQ:
            msg_id = cmd.get("MsgID")
            duplicate = msg_id in self._recalled_msg_ids
            if not duplicate:
                self._recalled_msg_ids.add(msg_id)
            send_cmd(XmlCommand(C.RECALLMSGREP, {
                "MsgID": msg_id, "Result": 1 if duplicate else 0},
                                ver=C.CONF_VER_LOGINREP, seq=cmd.seq))
            if not duplicate:
                self._broadcast(XmlCommand(C.RECALLCHATMSGNOTIFY, {
                    "MsgID": msg_id, "SrcUserID": ctx.get("uid")},
                    ver=C.CONF_VER_LOGINREP))  # 广播 6669
        elif cmd.cmdid == C.WNDSTATE:
            self._broadcast(XmlCommand(C.WNDSTATENOTIFY, dict(cmd.fields),
                                ver=C.CONF_VER_LOGINREP))  # 广播 6613
