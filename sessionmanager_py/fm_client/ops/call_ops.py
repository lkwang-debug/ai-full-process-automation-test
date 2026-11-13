"""通讯录邀请操作 mixin：Conf 终端呼叫子块（TERMINAL_BASE=6740，REP = REQ + 1）。

混入 FmClient，复用其 _require_conf / _request 及登录态。
"邀请"=拨打呼叫把电话/H323 终端拉进当前会议，不是给在线同事弹通知（后者走服务端 HTTP）。
权威字段源：ConfProtocol.h 末尾 TERMINAL_BASE 段、PhoneAccessMsgCodec.cpp。
"""
from __future__ import annotations

from typing import List, Optional

from .. import conf as C
from .._helpers import _as_list


class CallOps:
    """邀请电话/H323 联系人入会、挂断、查呼叫列表。"""

    def conf_invite_call(
        self, callees: List[dict], *, call_type: int = C.CALLTYPE_PHONE,
        timeout: Optional[float] = None,
    ) -> dict:
        """邀请电话/H323 联系人入会(6741→6742)。callees 用 C.ConfCmd.call_info(...) 构造。

        返回 {OprResult, CallType, RemainCallCount, ReqCallInfo[...]}。CallType: 1=电话 2=H323。
        呼叫状态变化由 on_call_state_notify(6745) 推送送达。"""
        conf = self._require_conf()
        rep = self._request(conf, C.ConfCmd.build_makecall(
            call_type=call_type, callees=callees), C.PAIRS, timeout)
        assert rep is not None
        fields = rep.fields
        fields["ReqCallInfo"] = _as_list(fields.get("ReqCallInfo"))
        return fields

    def conf_hangup(
        self, callee_nums: List[str], *, call_type: int = C.CALLTYPE_PHONE,
        timeout: Optional[float] = None,
    ) -> dict:
        """挂断已邀请的呼叫(6743→6744)。"""
        conf = self._require_conf()
        rep = self._request(conf, C.ConfCmd.build_hangup(
            call_type=call_type, callee_nums=callee_nums), C.PAIRS, timeout)
        assert rep is not None
        return rep.fields

    def conf_query_call_list(
        self, *, call_type: int = C.CALLTYPE_PHONE,
        timeout: Optional[float] = None,
    ) -> dict:
        """查询已邀请拨号列表(6746→6747)。返回 {OprResult, CallType, CalleeInfo[...]}。"""
        conf = self._require_conf()
        rep = self._request(conf, C.ConfCmd.build_call_list(
            call_type=call_type, caller_user_id=self.room_user_id,
            room_id=self.room_id), C.PAIRS, timeout)
        assert rep is not None
        fields = rep.fields
        fields["CalleeInfo"] = _as_list(fields.get("CalleeInfo"))
        return fields
