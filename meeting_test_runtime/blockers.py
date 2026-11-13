"""Central registry for protocol operations that must not enter normal regression."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, FrozenSet, Optional


class BlockerKind(str, Enum):
    UNSUPPORTED_PROTOCOL = "UNSUPPORTED_PROTOCOL"
    MISSING_SERVER_HANDLER = "MISSING_SERVER_HANDLER"
    RISKY_PROTOCOL_PROBE = "RISKY_PROTOCOL_PROBE"
    MISSING_SESSIONMANAGER_API = "MISSING_SESSIONMANAGER_API"
    SERVER_RESPONSE_BLOCKED = "SERVER_RESPONSE_BLOCKED"
    MISSING_TRIGGER_CONTRACT = "MISSING_TRIGGER_CONTRACT"


@dataclass(frozen=True)
class ProtocolBlocker:
    kind: BlockerKind
    cmdids: FrozenSet[int]
    operations: FrozenSet[str]
    reason: str
    blocker_id: str = ""
    allow_automatic_execution: bool = False


BLOCKERS = (
    ProtocolBlocker(
        BlockerKind.UNSUPPORTED_PROTOCOL, frozenset(), frozenset({'pause_record'}),
        '用户2026-09-16确认暂停录制不属于本期执行范围；不生成用例、不计阻塞，保留公开接口。'),
    ProtocolBlocker(
        BlockerKind.UNSUPPORTED_PROTOCOL,
        frozenset({30012, 30013}),
        frozenset({"front_getroomid", "front_login_by_meeting_no"}),
        "当前产品已确认不使用30012/30013旧版本会议号换算协议；仅保留协议追溯，不要求环境提供meeting_no。"),
    ProtocolBlocker(
        BlockerKind.UNSUPPORTED_PROTOCOL, frozenset({30002, 30003}),
        frozenset({"front_roomlist", "front_roomlist_classified", "list_rooms"}),
        "当前产品仅3.15及部分历史终端使用30002/30003；本项目不再生成相关功能用例。"),
    ProtocolBlocker(
        BlockerKind.UNSUPPORTED_PROTOCOL, frozenset({6520, 6521, 6620}),
        frozenset({"get_av_mix_params", "set_av_mix_params"}),
        "当前产品会议服务不处理AV合成6520/6521/6620；删除功能用例，仅保留协议追溯。"),
    ProtocolBlocker(
        BlockerKind.UNSUPPORTED_PROTOCOL, frozenset({6528, 6628}),
        frozenset({"conf_get_dept_info"}),
        "当前产品已确认不再使用6528/6628；不生成执行用例，仅保留协议追溯。"),
    ProtocolBlocker(
        BlockerKind.UNSUPPORTED_PROTOCOL, frozenset({6653}), frozenset(),
        "当前产品HTTP关会统一下发6607，不再返回6653；6653仅保留协议追溯。"),
    ProtocolBlocker(
        BlockerKind.UNSUPPORTED_PROTOCOL, frozenset({6636}), frozenset(),
        "根协议明确6636暂只用于主服务器到节点，不属于会议终端闭环验收范围。"),
    ProtocolBlocker(
        BlockerKind.UNSUPPORTED_PROTOCOL, frozenset({6651}), frozenset(),
        "产品确认当前组播6651代码未实现；不生成执行用例、不计阻塞，仅保留协议追溯。"),
    ProtocolBlocker(
        BlockerKind.UNSUPPORTED_PROTOCOL, frozenset({6652}), frozenset(),
        "当前产品请出统一使用目标端6606和其他成员6605；6652仅保留历史兼容解析，不执行、不计阻塞。"),
    ProtocolBlocker(
        BlockerKind.UNSUPPORTED_PROTOCOL, frozenset({6647}), frozenset(),
        "产品确认当前服务器未实现且不使用6647；不生成执行用例，不计阻塞，仅保留协议追溯。"),
    ProtocolBlocker(
        BlockerKind.UNSUPPORTED_PROTOCOL, frozenset({6540, 6640}),
        frozenset({"set_interactive_language", "set_interactive_language_and_wait"}),
        "产品确认6540/6640当前会议服务未实现且协议未使用；不生成执行用例，不计阻塞，仅保留追溯。"),
    ProtocolBlocker(
        BlockerKind.UNSUPPORTED_PROTOCOL, frozenset({6641}), frozenset(),
        "产品确认6641当前加载入口被注释，所有会议均不发送；不生成执行用例，不计阻塞，仅保留协议追溯。"),
    ProtocolBlocker(
        BlockerKind.MISSING_TRIGGER_CONTRACT,
        frozenset({6555, 6556, 6558, 6559, 6655, 6656, 6657, 6658, 6659}),
        frozenset({"request_face_recognition", "start_face_rollcall",
                   "report_face_rollcall_result", "cancel_face_rollcall"}),
        "新版SDK已有公开方法，但人脸服务、真实视频设备、FaceID数据库及服务端协议链未更新；用户确认继续阻塞。"),
)


BLOCKERS_BY_OPERATION: Dict[str, ProtocolBlocker] = {
    operation: blocker for blocker in BLOCKERS for operation in blocker.operations
}
BLOCKERS_BY_CMDID: Dict[int, ProtocolBlocker] = {
    cmdid: blocker for blocker in BLOCKERS for cmdid in blocker.cmdids
}


def blocker_for_operation(operation: str) -> Optional[ProtocolBlocker]:
    return BLOCKERS_BY_OPERATION.get(operation)


def blocker_for_cmdid(cmdid: int) -> Optional[ProtocolBlocker]:
    return BLOCKERS_BY_CMDID.get(int(cmdid))
