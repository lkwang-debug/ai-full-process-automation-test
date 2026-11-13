"""Second-stage cases: explicit identities/templates, reviewed scenario whitelist."""
from dataclasses import dataclass, field
import json
from pathlib import Path

from .excel_loader import ExcelCaseLoader

SCENARIOS = {
    "login", "chat", "self_av", "mute", "role", "info", "remote", "direct", "screen", "whiteboard", "record",
    "lock", "kick", "close", "guest_login", "guest_av", "guest_role", "guest_share", "approval",
    "chat_roundtrip", "av_cycle", "rejoin", "guest_rejoin", "share_cycle", "mixed_control", "manager_wrong_password",
    "role_repeated", "chat_denied", "av_cancel", "fm_screen", "fm_whiteboard", "role_then_record",
    "remote_target_left", "remote_overlap", "login_invalid", "whiteboard_cycle", "info_membership", "remote_denied", "share_takeover", "share_contention",
}
PARAMETERS = {
    "chat": {"text"}, "self_av": {"kind"}, "role": {"role"}, "remote": {"kind", "accept", "role"},
    "direct": {"kind", "role"}, "record": {"role"}, "guest_av": {"kind"},
    "guest_role": {"role"}, "guest_share": {"kind"}, "approval": {"kind", "role", "decision"},
    "chat_roundtrip": {"text", "repeat"}, "av_cycle": {"kind"}, "share_cycle": {"kind"},
    "mixed_control": {"kind", "role", "path"}, "role_repeated": {"role"}, "av_cancel": {"kind"},
    "role_then_record": {"role"}, "remote_target_left": {"kind", "role"},
    "remote_overlap": {"kind"}, "login_invalid": {"failure"}, "remote_denied":{"kind"}, "guest_rejoin":{"guest_alias"},
}
MODULES = {
    "login": "登录与成员", "guest_login": "游客入会", "guest_av": "游客音视频", "approval": "本人申请与审批",
    "guest_role": "游客角色", "guest_share": "游客共享", "mixed_control": "混合成员会控",
    "chat_roundtrip": "双向群聊及广播", "chat": "群聊内容", "self_av": "本人音视频", "av_cycle": "音视频连续流程",
    "role": "角色申请放弃", "info": "资料查询", "remote": "远程确认", "direct": "远程直接开启",
    "screen": "桌面共享", "whiteboard": "白板共享", "record": "云录制权限", "mute": "静音接收",
    "lock": "锁会入会", "kick": "被请出", "close": "会议关闭", "rejoin": "重新入会", "guest_rejoin":"游客重新入会",
    "share_cycle": "共享接替", "manager_wrong_password": "管理员密码错误", "role_repeated": "角色重复申请",
    "chat_denied": "群聊权限拒绝", "av_cancel": "音视频申请取消", "fm_screen": "FM屏幕共享接收",
    "fm_whiteboard": "FM白板视图协议接收", "role_then_record": "角色变化与录制权限",
    "remote_target_left": "离会目标会控", "remote_overlap": "远程请求关联", "login_invalid": "入会业务异常",
    "whiteboard_cycle": "白板关闭再建", "info_membership": "成员缓存变化", "remote_denied":"普通成员会控权限",
    "share_takeover":"共享结束后接替", "share_contention":"共享争抢及接替",
}


@dataclass(frozen=True)
class WebFullCase:
    case_id: str
    name: str
    scenario: str
    combination: str
    identities: tuple[str, ...]
    template: str
    params: dict = field(default_factory=dict)
    priority: str = "P1"
    status: str = "IMPLEMENTED"
    reason: str = ""
    risk: str = "ISOLATED_STATEFUL"
    source_ids: tuple[str, ...] = ()
    case_type: str = "NORMAL"
    evidence: str = "PROTOCOL"
    steps: tuple = ()

    @property
    def types(self):
        return tuple(self.combination.replace("+", "->").split("->"))

    def spec(self):
        return {"id": self.case_id, "run_id": self.case_id, "name": self.name, "priority": self.priority,
                "scenario": self.scenario, "combination": self.combination, "params": self.params,
                "identities":self.identities,"template":self.template}


def validate_case(case):
    if case.scenario not in SCENARIOS or case.template not in {"BASE", "discussion", "training", "control", "CONFIRM", "DIRECT"}:
        raise ValueError(f"{case.case_id}: 未注册场景或会议模板")
    if case.combination not in {"fm->web", "web->fm", "web->web", "web->fm+web", "web->web+web"}:
        raise ValueError(f"{case.case_id}: 未支持终端组合")
    if len(case.identities) != len(case.types) or any(x not in {"account", "guest"} for x in case.identities):
        raise ValueError(f"{case.case_id}: 客户端身份与拓扑不一致")
    if any(kind == "fm" and identity != "account" for kind, identity in zip(case.types, case.identities)):
        raise ValueError("本阶段只实现Web游客")
    if case.priority not in {"P0", "P1", "P2"} or case.status not in {"IMPLEMENTED", "SKIP_BLOCKED", "SKIP_ENV"}:
        raise ValueError("无效执行级别/状态")
    if case.risk not in {"SAFE_REUSABLE", "ISOLATED_STATEFUL", "DESTRUCTIVE_ISOLATED"}:
        raise ValueError("无效隔离规则")
    if set(case.params) != PARAMETERS.get(case.scenario, set()):
        raise ValueError(f"{case.case_id}: 参数不符合公开场景白名单")
    for key, values in {"kind": {"audio", "video", "screen", "whiteboard"}, "role": {"presenter", "manager", "attendee"},
                        "decision": {"approve", "cancel", "reject"}, "path": {"accept", "reject", "direct"},
                        "failure": {"account", "password", "room"}}.items():
        if key in case.params and case.params[key] not in values:
            raise ValueError(f"{case.case_id}: 非法{key}")
    if "accept" in case.params and not isinstance(case.params["accept"], bool):
        raise ValueError("accept必须是JSON布尔值")
    if "repeat" in case.params and not isinstance(case.params["repeat"], bool):
        raise ValueError("repeat必须是JSON布尔值")
    if "text" in case.params and not isinstance(case.params["text"], str):
        raise ValueError("聊天正文必须是字符串")
    if 'kind' in case.params:
        allowed={'screen','whiteboard'} if case.scenario in {'guest_share','share_cycle'} else {'audio','video'}
        if case.params['kind'] not in allowed:raise ValueError('媒体类型不适用于本场景')
    if 'role' in case.params and case.scenario!='record' and case.params['role']=='attendee':raise ValueError('本操作角色必须是主持或管理员')
    if case.evidence not in {'PROTOCOL','CACHE','CACHE_AND_PROTOCOL','SDK_LOCAL'}:raise ValueError('无效证据类型')
    if case.scenario == 'guest_rejoin':
        alias=case.params['guest_alias']
        if alias not in ('operator','observer') or case.identities[('operator','observer').index(alias)]!='guest':
            raise ValueError('游客重新入会必须操作真实游客端')
    if case.scenario=='fm_whiteboard' and (case.combination!='fm->web' or case.evidence!='PROTOCOL'):
        raise ValueError('FM白板视图必须以FM公开方法发送并由Web接收协议')
    if case.scenario=='remote_target_left' and (case.combination!='web->web' or case.evidence!='SDK_LOCAL'):
        raise ValueError('离会目标场景只验收Web公开门面的本地拒绝')
    if case.scenario=='remote_overlap' and (case.combination!='web->web+web' or case.template!='CONFIRM'):
        raise ValueError('重叠邀请必须使用三Web端及需要同意的会议模板')
    if case.status != "IMPLEMENTED" and not case.reason:
        raise ValueError("跳过必须有具体原因")
    if case.status=='IMPLEMENTED' and case.scenario=='approval' and case.params['decision']=='reject':
        raise ValueError('缺口场景没有正式执行契约，不能直接改状态启用')
    from .web_full_steps import validate_flow_contract
    validate_flow_contract(case)
    return case


def load_web_full_cases(path):
    from dataclasses import replace
    loader=ExcelCaseLoader(Path(path));flows={}
    for row in loader._read_sheet('依赖流程'):
        flows.setdefault(str(row['流程ID']),[]).append(row)
    cases = []; seen = set()
    functional_rows=loader._read_sheet('功能用例')
    all_ids={str(row['用例ID']) for row in functional_rows}
    for row in functional_rows:
        if not ExcelCaseLoader._enabled(row.get("是否执行")) and row.get("用例状态") == "IMPLEMENTED":
            continue
        case = WebFullCase(
            case_id=str(row["用例ID"]), name=str(row["场景名称"]), scenario=str(row["统一操作名"]),
            combination=str(row["终端组合"]), identities=tuple(str(row["入会身份"]).split(",")),
            template=str(row["会议模板"]), params=json.loads(row.get("参数JSON") or "{}"),
            priority=str(row["执行级别"]), status=str(row["用例状态"]), reason=str(row.get("缺口原因") or ""),
            risk=str(row["环境风险"]), source_ids=tuple(str(row.get("来源FM用例ID") or "").split(",")) if row.get("来源FM用例ID") else (),
            case_type=str(row["用例类型"]), evidence=str(row["证据类型"]))
        rows=sorted(flows.get(case.case_id,[]),key=lambda r:int(r['步骤序号']))
        if rows:
            if [int(r['步骤序号']) for r in rows]!=list(range(1,len(rows)+1)):raise ValueError('流程步骤必须连续')
            case=replace(case,steps=tuple({'actor':str(r['执行端']),'operation':str(r['统一操作名']),
                                         'params':json.loads(r['参数JSON'] or '{}')} for r in rows))
        from .web_full_steps import reviewed_steps
        if reviewed_steps(case) and not rows:raise ValueError('依赖流程缺少实际执行步骤')
        validate_case(case)
        if case.case_id in seen:
            raise ValueError(f"重复用例ID: {case.case_id}")
        seen.add(case.case_id); cases.append(case)
    if set(flows)-all_ids:raise ValueError('流程引用不存在的功能用例')
    return cases
