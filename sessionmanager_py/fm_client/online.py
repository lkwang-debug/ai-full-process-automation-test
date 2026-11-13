"""Invitation receiver for the PaaS Client Proxy JSON protocol (session app=1).

This connection is independent of Front/Conf. Protocol source: fsp_sss_stream
client_proxy/{base_processor,gcs_processor,session_msg_processor}.cpp.
It carries raw UTF-8 JSON, without the Conf XmlMsgIOPack prefix.
"""
from __future__ import annotations

import copy
import json
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from framecore_session import SessionConfig, SessionListener, TcpReliableSession


class OnlineError(RuntimeError):
    """PaaS authentication, connection, or invitation failure."""


@dataclass(frozen=True)
class OnlineInvitation:
    seq_id: int
    inviter_user_id: str
    group_id: str
    invite_code: int
    params: dict = field(repr=False)
    raw: dict = field(repr=False)

    @property
    def key(self):
        return self.inviter_user_id, self.seq_id

    @classmethod
    def from_message(cls, message):
        if message.get("business") != "GS" or message.get("id") != 14002:
            raise ValueError("expected GS invitation 14002")
        extension = message.get("extend_info")
        if isinstance(extension, str):
            extension = json.loads(extension)
        if not isinstance(extension, dict) or extension.get("method") != "InviteUser":
            raise ValueError("14002 is not a meeting InviteUser invitation")
        params = extension.get("params")
        if not isinstance(params, dict):
            raise ValueError("InviteUser.params must be an object")
        code = str(params.get("inviteCode", ""))
        if not re.fullmatch(r"[1-9][0-9]{8}", code):
            raise ValueError("invitation has no valid nine-digit params.inviteCode")
        seq = message.get("seq_id")
        if isinstance(seq, bool) or not isinstance(seq, int) or not 0 <= seq <= 0x7fffffff:
            raise ValueError("invalid invitation seq_id")
        for key in ("user_id", "group_id"):
            if not isinstance(message.get(key), str) or not message[key]:
                raise ValueError("invitation is missing " + key)
        return cls(seq, message["user_id"], message["group_id"], int(code),
                   copy.deepcopy(params), copy.deepcopy(message))


class PaasOnlineClient(SessionListener):
    """Keep an invited terminal online, receive invitations, and accept/join.

    Credentials are PaaS app_id/token, not HTTP app_key/app_secret or a Front
    password. Heartbeats are provided by TcpReliableSession. Automatic reconnect
    is disabled: a lost online session must be authenticated again explicitly.
    Blocking methods must run on the caller thread, never a session callback.
    """

    def __init__(self, config, *, timeout=10):
        self.config = copy.deepcopy(config)
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        for key in ("cp_address", "app_id", "token", "user_id", "client_guid",
                    "protocol_version", "mutex_type", "os_type"):
            if not isinstance(config.get(key), str) or not config[key]:
                raise ValueError("PaaS config requires nonempty " + key)
        if not re.fullmatch(r"[0-9a-z]{32}", config["app_id"]):
            raise ValueError("PaaS app_id must be 32 lowercase letters/digits")
        if not re.fullmatch(r"[0-9a-zA-Z_-]{1,128}", config["user_id"]):
            raise ValueError("invalid PaaS user_id")
        if not 3 <= len(config["token"]) <= 512:
            raise ValueError("invalid PaaS token length")
        self.user_id = config["user_id"]
        self._condition = threading.Condition(threading.RLock())
        self._request_lock = threading.Lock()
        self._responses = {}
        self._invitations = deque()
        self._pending = {}
        self._seen = set()
        self._events = deque(maxlen=200)
        self._failure = None
        self._online = False
        self._session = None

    @classmethod
    def from_config(cls, path_or_dict, **kwargs):
        if not isinstance(path_or_dict, dict):
            with open(path_or_dict, encoding="utf-8") as stream:
                path_or_dict = json.load(stream)
        return cls(path_or_dict.get("paas", path_or_dict), **kwargs)

    @property
    def is_online(self):
        with self._condition:
            return self._online and self._failure is None

    def snapshot_events(self):
        """Independent PaaS events; these are not Front/Conf XML events."""
        with self._condition:
            return copy.deepcopy(list(self._events))

    def _fail(self, reason):
        with self._condition:
            self._failure = reason
            self._online = False
            self._pending.clear()
            self._condition.notify_all()

    def on_closed(self):
        self._fail("PaaS connection closed")

    def on_create_failed(self, reason):
        self._fail("PaaS connection could not be established")

    def on_data(self, data):
        try:
            message = json.loads(data.rstrip(b"\x00").decode("utf-8"))
            if not isinstance(message, dict):
                raise ValueError("expected JSON object")
            cmdid = message.get("id")
            business = message.get("business")
            with self._condition:
                self._events.append(copy.deepcopy(message))
                if business == "BASE" and cmdid in (10001, 10003):
                    self._responses[cmdid] = message
                elif (business == "BASE" and cmdid == 10004) or (business == "PS" and cmdid == 12007):
                    if cmdid == 12007:
                        self._send({"business": "PS", "id": 12008})
                    self._fail("PaaS forced offline (cmdid=" + str(cmdid) + ")")
                elif business == "GS" and cmdid == 14002:
                    invitation = OnlineInvitation.from_message(message)
                    if invitation.key not in self._seen:
                        self._seen.add(invitation.key)
                        self._pending[invitation.key] = invitation
                        self._invitations.append(invitation)
                elif business == "GS" and cmdid in (14004, 14008):
                    self._pending.pop((message.get("user_id"), message.get("seq_id")), None)
                self._condition.notify_all()
        except (ValueError, TypeError, UnicodeError):
            # Malformed/unrelated invitations must never fall back to a room
            # from the test config or automatically enter an arbitrary group.
            with self._condition:
                self._events.append({"error": "invalid PaaS message or unsupported invitation"})
                self._condition.notify_all()

    def _send(self, message):
        if self._session is None or not self._session.send(
                json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")):
            raise OnlineError("PaaS send failed for cmd " + str(message["id"]))

    def _request(self, message, expected, timeout):
        with self._request_lock:
            deadline = time.monotonic() + timeout
            with self._condition:
                self._responses.pop(expected, None)
                self._send(message)
                while expected not in self._responses:
                    if self._failure:
                        raise OnlineError(self._failure)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"PaaS cmd {message['id']} no response {expected} within {timeout}s")
                    self._condition.wait(remaining)
                result = self._responses.pop(expected)
                if str(result.get("result")) != "0":
                    raise OnlineError(f"PaaS cmd {expected} result={result.get('result')}")
                return result

    def login(self):
        if self._session is not None:
            raise OnlineError("close the existing PaaS session before logging in again")
        with self._condition:
            self._failure = None
            self._online = False
            self._responses.clear()
            self._invitations.clear()
            self._pending.clear()
            self._seen.clear()
        self._session = TcpReliableSession(self, SessionConfig(application_id=1,
            enable_reconnect=False, connect_timeout_ms=int(self.timeout * 1000)))
        try:
            if not self._session.connect(self.config["cp_address"]):
                raise OnlineError("PaaS Client Proxy connection failed")
            keys = ("app_id", "token", "user_id", "client_guid", "protocol_version",
                    "company_id", "mutex_type", "os_type", "force_login", "registered_user",
                    "media_source", "extend_info", "sdk_type", "domain", "service_id", "service_type")
            payload = {key: self.config[key] for key in keys if key in self.config}
            payload.setdefault("force_login", 0)
            payload.setdefault("registered_user", 1)
            payload.update(business="BASE", id=10000)
            result = self._request(payload, 10001, self.timeout)
            with self._condition:
                if self._failure:
                    raise OnlineError(self._failure)
                self._online = True
            return result
        except BaseException:
            self.close()
            raise

    def wait_invitation(self, *, timeout=None):
        """Return a received invitation; cancellation/duplicates do not join."""
        timeout = self.timeout if timeout is None else float(timeout)
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                if not self.is_online:
                    raise OnlineError(self._failure or "call PaaS login() first")
                while self._invitations:
                    invitation = self._invitations.popleft()
                    if self._pending.get(invitation.key) is invitation:
                        return invitation
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no PaaS invitation 14002 within {timeout}s")
                self._condition.wait(remaining)

    def respond_invitation(self, invitation, *, accept=True):
        """Submit GS 14003, operate=0 accept/1 reject. No business reply follows.

        A successful send alone does not prove that the invitee entered a room.
        """
        with self._condition:
            if not self.is_online:
                raise OnlineError(self._failure or "PaaS is offline")
            if self._pending.get(invitation.key) is not invitation:
                raise OnlineError("invitation expired, cancelled, already answered, or belongs to another client")
            self._send({"business": "GS", "id": 14003, "seq_id": invitation.seq_id,
                        "group_id": invitation.group_id, "operate": 0 if accept else 1,
                        "extend_info": ""})
            del self._pending[invitation.key]

    def accept_and_join(self, invitation, client, *, room_node_id=None,
                        room_app_id=11, room_password=None, timeout=None, **conf_options):
        """Accept a real received invitation, then Front 30004 and Conf 6501.

        client must already be Front-authenticated as the invited PaaS user.
        Target comes ONLY from 14002.params.inviteCode, never a configured room.
        """
        if str(client.local_user_id) != self.user_id:
            raise OnlineError("Front identity must match the invited PaaS user_id")
        if client._conf is not None or client.room_user_id is not None:
            raise OnlineError("invitee must not already be joining or inside a meeting")
        timeout = self.timeout if timeout is None else timeout
        if room_node_id is None:
            room_node_id = client.front_discover_node(timeout=timeout)
        if not room_node_id:
            raise OnlineError("no Front node available for invitation routing")
        self.respond_invitation(invitation, accept=True)
        front = client.front_join_room(invitation.invite_code, room_node_id=room_node_id,
            room_app_id=room_app_id, room_password=room_password, timeout=timeout)
        conf = client.conf_login(timeout=timeout, **conf_options)
        return {"front": front, "conf": conf}

    def logout(self):
        if self.is_online:
            try:
                return self._request({"business": "BASE", "id": 10002}, 10003, self.timeout)
            finally:
                self.close()
        self.close()

    def close(self):
        session, self._session = self._session, None
        self._fail("PaaS connection closed")
        if session is not None:
            session.close()

