"""Public interactive protocols; forwarding/notifications are send-only."""
from .. import conf as C
from ..interaction import flag


class InteractionOps:
    def set_chat_check(self, enabled):
        """6510 EnableChatMsgCheck; requires config_chat_check permission."""
        self.set_conf_state(enable_chat_check=flag(enabled, "enabled"))

    def review_chat_message(self, message, *, approved):
        """6529: message is received 6612 fields, including original sender/serial.

        Requires check_chat permission. 6629 goes to reviewers, not necessarily
        the message sender. Current mainline silently ignores a rejection.
        The return value only indicates the command was sent.
        """
        self._send(self._require_conf(), C.ConfCmd.build_chat_check(message, approved=approved))

    def set_interactive_language(self, language, *, user_id=None):
        """6540 -> 6640 notification; 6641 is independent room configuration.

        Current local server source has a disabled handler. Sending is not
        proof of support; observe actual 6640 on the deployed server.
        """
        self._send(self._require_conf(), C.ConfCmd.build_interactive_language(
            self.room_user_id if user_id is None else user_id, language))

    def request_remote_av(self, dst_user_id, *, audio_ids=None, video_ids=None, operation=0):
        """6542 -> target 6642. No direct reply. Target chooses when/how to reply.

        Consent requires target Conf >=4, valid devices, caller permissions and
        force-operation disabled. Older/forced paths can change 6609 directly.
        operation=0 broadcasts, 1 requests viewing. This does not capture media.
        """
        self._send(self._require_conf(), C.ConfCmd.build_remote_av_request(
            self.room_user_id, dst_user_id, audio_ids=audio_ids,
            video_ids=video_ids, operation=operation))

    def reply_remote_av(self, dst_user_id, *, result, audio_ids=None, video_ids=None, operation=0):
        """6543 -> original requester 6643. result=0 accept, nonzero reject.

        Call only after the terminal/user has decided. Nothing auto-accepts.
        """
        self._send(self._require_conf(), C.ConfCmd.build_remote_av_reply(
            self.room_user_id, dst_user_id, result=result, audio_ids=audio_ids,
            video_ids=video_ids, operation=operation))

    def respond_remote_av(self, request, *, accept):
        """Reply to actual 6642 fields, reversing identities and preserving devices."""
        accepted = flag(accept, "accept")
        if not isinstance(request, dict) or str(request.get("DstUserID")) != str(self.room_user_id):
            raise ValueError("6642 target must match this Conf user")
        devices = {}
        for key, arg in (("Audio", "audio_ids"), ("Video", "video_ids")):
            if key in request:
                entries = request[key] if isinstance(request[key], list) else [request[key]]
                if not entries or any(not isinstance(item, dict) or "ID" not in item for item in entries):
                    raise ValueError(key + " must contain device ID fields")
                devices[arg] = [item["ID"] for item in entries]
        self.reply_remote_av(request.get("SrcUserID"), result=0 if accepted else 1,
                             operation=request.get("Type"), **devices)

    def request_face_recognition(self, src_user_id, src_video_id, *, timeout=None):
        """6555/6655: allocate a face-service route for an existing video device.

        Returns Result/ServerDevID/ServerAddr/AppID/CheckCode. Does not perform
        image recognition. Nonzero Result raises FmError.
        """
        cmd = C.ConfCmd.build_face_recognition(src_user_id, src_video_id)
        rep = self._request(self._require_conf(), cmd, C.PAIRS, timeout,
            response_match=lambda r: str(r.get("SrcUserID")) == str(cmd.get("SrcUserID"))
            and str(r.get("SrcVideoID")) == str(cmd.get("SrcVideoID")))
        return rep.fields

    def start_face_rollcall(self, title, *, countdown_seconds=30, timeout=None):
        """6556/6656; successful start separately broadcasts 6657.

        Requires an available face service and meeting database.
        """
        rep = self._request(self._require_conf(),
            C.ConfCmd.build_face_rollcall_start(title, countdown_seconds), C.PAIRS, timeout)
        return rep.fields

    def report_face_rollcall_result(self, rollcall_id, face_id):
        """6558 -> 6658 broadcast for a valid face database ID and active rollcall.

        face_id is numeric, not a person's name. Sending a known ID tests this
        signaling step, not camera capture or biometric recognition.
        """
        self._send(self._require_conf(), C.ConfCmd.build_face_rollcall_result(rollcall_id, face_id))

    def cancel_face_rollcall(self, rollcall_id):
        """6559 -> 6659 broadcast. No independent response; inactive IDs may be silent."""
        self._send(self._require_conf(), C.ConfCmd.build_face_rollcall_cancel(rollcall_id))
