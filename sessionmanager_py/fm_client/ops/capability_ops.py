"""Capability, targeted acknowledgement and extended collaboration protocols.

Delivery contracts follow ConfMsgProcessor.cpp and LocalRoom.cpp, not REQ+100
arithmetic. Media actions remain the caller's responsibility.
"""
from __future__ import annotations

from .. import conf as C


class CapabilityOps:
    def send_relay_message(self, dst_user_id, messages, *, dst_user_ids=None) -> None:
        """Send 6700 using this Conf identity, without waiting for a response.

        messages=[{'id': 6718, 'fields': {'Name': 'value'}}]. Nested fields and
        repeated fields use the usual XML field-tree dictionaries/lists; the SDK
        escapes values. Target 0 broadcasts to others. dst_user_ids adds targets
        to a nonzero primary recipient. Incoming messages use on_relay_message.
        """
        conf = self._require_conf()
        self._send(conf, C.ConfCmd.build_relay_message(
            self.room_user_id, dst_user_id, messages, dst_user_ids=dst_user_ids))

    def set_multi_data_sharer(self, enabled: bool, *, force: bool = False,
                              timeout=None) -> dict:
        """6538: requester receives 6638; others receive it only on a change."""
        rep = self._request(self._require_conf(), C.ConfCmd.build_multi_data_sharer(
            enabled=enabled, force=force), C.PAIRS, timeout,
            response_match=lambda cmd: str(cmd.get("SrcUserID")) == str(self.room_user_id))
        return rep.fields

    def get_user_features(self, user_id=None, *, timeout=None) -> dict:
        """6544/6644, requester only. Query an online user; absent users may time out."""
        uid = self.room_user_id if user_id is None else user_id
        rep = self._request(self._require_conf(), C.ConfCmd.build_user_features(uid),
                            C.PAIRS, timeout,
                            response_match=lambda cmd: str(cmd.get("UserID")) == str(uid))
        return rep.fields

    def query_feature_users(self, feature: str, *, subscribe: bool = False,
                             timeout=None) -> dict:
        """6545/6645: collect pages through End; later subscription updates are pushes.

        Returns Feature, UserIDs (string list), and unmodified Pages. A query for
        the same feature as an active subscription cannot be distinguished from
        its updates when the server omits seq; unsubscribe before re-querying.
        """
        pages = self._request_pages(self._require_conf(), C.ConfCmd.build_feature_users(
            feature, query_type=1 if subscribe else 0), C.PAIRS, timeout,
            complete=lambda cmd: "End" in cmd.fields,
            response_match=lambda cmd: cmd.get("Feature") == feature)
        ids = []
        for page in pages:
            ids.extend(uid for uid in str(page.get("UserID") or "").split(",") if uid)
        return {"Feature": feature, "UserIDs": ids, "Pages": [p.fields for p in pages]}

    def unsubscribe_feature_users(self, feature: str) -> None:
        """6545 Type=2 has no direct reply."""
        self._send(self._require_conf(), C.ConfCmd.build_feature_users(feature, query_type=2))

    def set_user_state(self, user_id=None, **state) -> None:
        """6509 extended state; keyword names follow build_change_user_state().

        Info is a complete snapshot (Sex/SeatList/DisplayName/Tel/Mobile/EMail):
        missing fields are cleared by the server. Remote VNC start sends 6646
        to the target; it does not immediately commit state or broadcast 6609.
        """
        self._send(self._require_conf(), C.ConfCmd.build_change_user_state(
            user_id=self.room_user_id if user_id is None else user_id, **state))

    def reply_self_state_change(self, dst_user_id, *, vnc_state: int, error=None) -> None:
        """6546 acknowledgement of 6646, no reply and no automatic media/state change.

        dst_user_id is 6646.SrcUserID. On success the target separately reports
        its own 6509 state after acquiring screen-share right and starting media.
        """
        self._send(self._require_conf(), C.ConfCmd.build_self_state_result(
            dst_user_id=dst_user_id, vnc_state=vnc_state, error=error))

    def renegotiate_layout(self, layouts, *, screen_count: int = 1, timeout=None) -> dict:
        """6560/6660 requester only; codes are server layout codes, not pane counts."""
        rep = self._request(self._require_conf(), C.ConfCmd.build_renegotiate_layout(
            screen_count=screen_count, layouts=layouts), C.PAIRS, timeout)
        return rep.fields

    def send_h5_vote(self, vote_id, *, state: int = 2, duration: int = 1,
                     user_ids=None, data=None) -> None:
        """6561 -> 6661: broadcast excludes sender, or delivery to explicit targets.

        vote_id is a signed 32-bit nonnegative integer, duration is in minutes.
        state=2 starts; state=0 ends using the server's saved recipients. data
        contains application-specific H5 fields, forwarded without reinterpretation.
        """
        self._send(self._require_conf(), C.ConfCmd.build_h5_vote(
            vote_id, state=state, duration=duration, user_ids=user_ids, data=data))

    def modify_group_config(self, rooms, *, timeout=None) -> dict:
        """6565/6665 requester only; GroupMeeting clients must hold right 11.

        Renames existing groups and changes rights of assigned members. 6666 is
        an independent manager notification. Preserve the server's nested User
        shape, including repeated ID/Right fields inside a single User element.
        """
        rep = self._request(self._require_conf(), C.ConfCmd.build_modify_group_config(rooms),
                            C.PAIRS, timeout)
        return rep.fields

    def allow_waiting_users(self, users, *, timeout=None) -> dict:
        """6570/6670, sent on the manager's WAITING-GROUP Conf session.

        users=[{"UserID": ..., "Allow": 0|1}]. Each returned UserInfo has its
        own Result; a received 6670 alone does not imply all admissions succeeded.
        """
        rep = self._request(self._require_conf(), C.ConfCmd.build_allow_waiting_users(users),
                            C.PAIRS, timeout)
        return rep.fields

    def set_asr_subtitle(self, enabled: bool, *, translation: bool = False,
                         src_language=None, target_language=None, timeout=None) -> dict:
        """6571/6671 requester-only setting, not proof that audio recognition ran."""
        rep = self._request(self._require_conf(), C.ConfCmd.build_asr_subtitle(
            enabled=enabled, translation=translation, src_language=src_language,
            target_language=target_language), C.PAIRS, timeout)
        return rep.fields

    def set_user_custom_data(self, user_id=None, *, public_data=None, private_data=None) -> None:
        """6572 -> 6672: public data to everyone; private data only to target.

        Values are strings (json.dumps for JSON); empty string clears a value.
        No response is guaranteed to the operator for another user's private data.
        """
        self._send(self._require_conf(), C.ConfCmd.build_user_custom_data(
            self.room_user_id if user_id is None else user_id,
            public_data=public_data, private_data=private_data))
