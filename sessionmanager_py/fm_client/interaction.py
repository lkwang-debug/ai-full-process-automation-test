"""Field builders for approval, languages, AV consent and face signaling.

Wire fields follow the 2026-09-16 XML and ConfMsgProcessor.cpp. These builders
do not start media capture or connect to the face recognition service.
"""
from __future__ import annotations

import copy
import re

from .xmlcmd import XmlCommand


def protocol_int(value, name, minimum=0):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError(name + " must be an integer")
    number = int(value)
    if not minimum <= number <= 0x7fffffff:
        raise ValueError(name + " is outside the protocol range")
    return number


def flag(value, name):
    if value not in (True, False, 0, 1):
        raise ValueError(name + " must be boolean or 0/1")
    return int(value)


class InteractionBuilders:
    @classmethod
    def build_chat_check(cls, message, *, approved):
        """6529 preserves the original 6612 sender, serial, text and font."""
        if not isinstance(message, dict):
            raise ValueError("message must be the fields of a received 6612")
        fields = {key: copy.deepcopy(message[key]) for key in (
            "SrcUserType", "SrcLiveUserName", "SrcLiveUserId", "DstUserType",
            "DstLiveUserId", "Color", "Text") if key in message}
        for key in ("SerialNumber", "SrcUserID", "DstUserID"):
            fields[key] = protocol_int(message.get(key), key)
        if not isinstance(fields.get("Text"), str):
            raise ValueError("message.Text must be a string")
        if "Font" in message:
            font = message["Font"]
            if not isinstance(font, dict) or any(isinstance(v, (dict, list)) for v in font.values()):
                raise ValueError("message.Font must be an attribute dictionary")
            fields["Font"] = {"@" + k.lstrip("@"): v for k, v in font.items()}
        fields["CheckResult"] = flag(approved, "approved")
        return XmlCommand(6529, fields, ver=cls.ver)

    @classmethod
    def build_interactive_language(cls, user_id, language):
        if not isinstance(language, str) or not language.strip():
            raise ValueError("language must be a nonempty server-supported language code")
        return XmlCommand(6540, {"UserID": protocol_int(user_id, "user_id", 1),
                                "Language": language}, ver=cls.ver)

    @classmethod
    def _build_remote_av(cls, cmdid, src_user_id, dst_user_id, *,
                         audio_ids=None, video_ids=None, operation=0, result=None):
        fields = {"SrcUserID": protocol_int(src_user_id, "src_user_id", 1),
                  "DstUserID": protocol_int(dst_user_id, "dst_user_id", 1),
                  "Type": protocol_int(operation, "operation")}
        if fields["Type"] not in (0, 1):
            raise ValueError("operation must be 0 (broadcast) or 1 (view)")
        if fields["SrcUserID"] == fields["DstUserID"]:
            raise ValueError("remote AV target must be another user")
        for name, ids in (("Audio", audio_ids), ("Video", video_ids)):
            if ids is None:
                continue
            if not isinstance(ids, (list, tuple)) or not ids:
                raise ValueError(name + " IDs must be a nonempty list")
            values = list(dict.fromkeys(protocol_int(i, name + ".ID") for i in ids))
            if name == "Audio" and values != [0]:
                raise ValueError("Conf audio device ID must be 0 (not HTTP device ID 1)")
            fields[name] = [{"ID": i} for i in values]
        if not any(key in fields for key in ("Audio", "Video")):
            raise ValueError("at least one audio/video device is required")
        if result is not None:
            fields["Result"] = protocol_int(result, "result")
        return XmlCommand(cmdid, fields, ver=cls.ver)

    @classmethod
    def build_remote_av_request(cls, src_user_id, dst_user_id, **kwargs):
        return cls._build_remote_av(6542, src_user_id, dst_user_id, **kwargs)

    @classmethod
    def build_remote_av_reply(cls, src_user_id, dst_user_id, *, result, **kwargs):
        result = protocol_int(result, "result")
        return cls._build_remote_av(6543, src_user_id, dst_user_id, result=result, **kwargs)

    @classmethod
    def build_face_recognition(cls, src_user_id, src_video_id):
        return XmlCommand(6555, {"SrcUserID": protocol_int(src_user_id, "src_user_id", 1),
                                "SrcVideoID": protocol_int(src_video_id, "src_video_id")}, ver=cls.ver)

    @classmethod
    def build_face_rollcall_start(cls, title, countdown_seconds):
        if not isinstance(title, str) or not title.strip() or len(title.encode("utf-8")) > 255:
            raise ValueError("title must be nonempty and at most 255 UTF-8 bytes")
        return XmlCommand(6556, {"RollcallTitle": title,
            "CountdownTime": protocol_int(countdown_seconds, "countdown_seconds", 1)}, ver=cls.ver)

    @classmethod
    def build_face_rollcall_result(cls, rollcall_id, face_id):
        return XmlCommand(6558, {"RollcallID": protocol_int(rollcall_id, "rollcall_id", 1),
                                "FaceID": protocol_int(face_id, "face_id", 1)}, ver=cls.ver)

    @classmethod
    def build_face_rollcall_cancel(cls, rollcall_id):
        return XmlCommand(6559, {"RollcallID": protocol_int(rollcall_id, "rollcall_id", 1)}, ver=cls.ver)
