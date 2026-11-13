"""Confirmation helpers for the SDK's FmTestClient.

These observe real notifications without registering them as direct responses.
The send-only methods on FmClient keep their original behavior.
"""
import math

from . import conf as C


class NotificationTimeout(TimeoutError):
    """A command was sent but a matching notification was not observed.

    This does not establish that the server lacks support or that no state was
    changed. Event metadata is retained without printing chat text or tokens.
    """

    def __init__(self, request_cmdid, cmdid, expected, timeout, recent, session_open):
        self.request_cmdid = request_cmdid
        self.cmdid = cmdid
        self.expected = dict(expected)
        self.timeout = timeout
        self.recent = list(recent)
        self.session_open = session_open
        super().__init__(
            f"cmd {request_cmdid} sent; no matching notify {cmdid} within {timeout}s; "
            f"expected={self.expected}; recent={self.recent}; "
            f"observer_conf_open={session_open}; server result remains unconfirmed"
        )


class InteractionTestOps:
    def _confirm_interaction(self, action, *, observer, request_cmdid, cmdid, match, timeout):
        timeout = float(self._request_timeout if timeout is None else timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self._require_conf()
        observer._require_conf()
        if (str(self.real_room_id or self.room_id) != str(observer.real_room_id or observer.room_id)
                or self.room_srv_node_id != observer.room_srv_node_id):
            raise ValueError("observer must be in the same Conf room")
        mark = observer.events.mark()
        action()
        event = observer.events.wait_event(
            after=mark, source="conf", cmdid=cmdid, kind="notify",
            match=match, timeout=timeout)
        if event is not None:
            return dict(event.fields)
        recent = [(e.cmdid, e.kind) for e in observer.events.snapshot_received(after=mark)
                  if e.source == "conf"][-20:]
        try:
            observer._require_conf()
            session_open = True
        except RuntimeError:
            session_open = False
        raise NotificationTimeout(request_cmdid, cmdid, match, timeout, recent, session_open)

    def review_chat_message_and_wait(self, message, *, approved, timeout=None):
        """Send 6529 and wait on this reviewer for a matching 6629.

        Match SerialNumber, CheckResult and CheckUserID. Silence raises
        NotificationTimeout for both approval and rejection. This verifies the
        acknowledgement only; message delivery and rejection finality require
        separate business assertions. Never call from a receive callback.
        """
        cmd = C.ConfCmd.build_chat_check(message, approved=approved)
        return self._confirm_interaction(
            lambda: self.review_chat_message(message, approved=approved),
            observer=self, request_cmdid=C.CHATCHECKREQ, cmdid=C.CHATCHECKRESULT,
            match={"SerialNumber": cmd.get("SerialNumber"),
                   "CheckResult": cmd.get("CheckResult"), "CheckUserID": self.room_user_id},
            timeout=timeout)

    def set_interactive_language_and_wait(self, language, *, user_id=None,
                                         observer=None, timeout=None):
        """Send 6540, observe 6640 on self or another FmTestClient in this room.

        Match the initiating user, target user and language; an unrelated 6640
        or a 6571/6671 ASR result cannot confirm this operation. Does not modify
        cached language locally or retry on silence. Use from the test thread.
        """
        uid = self.room_user_id if user_id is None else user_id
        cmd = C.ConfCmd.build_interactive_language(uid, language)
        receiver = self if observer is None else observer
        if not isinstance(receiver, InteractionTestOps) or not hasattr(receiver, "events"):
            raise ValueError("observer must be an FmTestClient")
        return self._confirm_interaction(
            lambda: self.set_interactive_language(language, user_id=uid),
            observer=receiver, request_cmdid=C.SETINTERACTIVELANGUAGE,
            cmdid=C.INTERACTIVELANGUAGENOTIFY,
            match={"SrcUserID": self.room_user_id,
                   "UserID": cmd.get("UserID"), "Language": language}, timeout=timeout)
