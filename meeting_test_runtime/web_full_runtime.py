"""Owned meetings and account leases for the Web expansion, not the FM suite."""
import time
import uuid
from .models import ClientProfile, ServiceProfile
from .web_key_scenarios import WebKeyRuntime
from .web_protocol import field_values
from .allure_reporting import attach_json
from .web_key_scenarios import user, role


class WebMeetingFactory:
    def __init__(self, cfg):
        self.cfg=cfg; self.cached=None; self.cached_key=None

    def _key(self, case):
        return (case.combination, case.identities, case.template, tuple(
            self.cfg.get(section, "username") for section in ("operator", "observer", "invitee")))

    def acquire(self, case):
        reusable=case.risk=="SAFE_REUSABLE"
        key=self._key(case)
        if self.cached and (not reusable or key!=self.cached_key or not self.cached.is_healthy()):
            self.cached.close_all(); self.cached=None; self.cached_key=None
        if reusable and self.cached:
            return self.cached
        runtime=self.build(case)
        if reusable:
            self.cached=runtime; self.cached_key=key
        return runtime

    def release(self, case, runtime, failed=False):
        if runtime is self.cached and not failed and runtime.is_healthy():
            return
        try: runtime.close_all()
        finally:
            if runtime is self.cached:
                self.cached=None; self.cached_key=None

    def close(self):
        if self.cached: self.cached.close_all(); self.cached=None

    def build(self, case):
        from .http_driver import WebApiServiceDriver
        cfg=self.cfg
        runtime=WebKeyRuntime(event_timeout=cfg.getfloat("execution","event_timeout",fallback=10),quiet_timeout=2)
        runtime.full_case=case
        service=WebApiServiceDriver(ServiceProfile("WEB_FULL_HTTP","web_api",{
            "base_url":cfg.get("http","base_url"),"app_key":cfg.get("http","app_key"),"app_secret":cfg.get("http","app_secret"),
            "timeout":cfg.getfloat("http","timeout",fallback=10),"verify_tls":cfg.getbool("http","verify_tls",fallback=False)}))
        runtime.add_service("http_admin",service)
        # user-visible control mode corresponds to the documented standard template.
        mode={"discussion":"discussion","training":"training","control":"standard"}.get(case.template,"standard")
        try:
            created=service.native_client.add_room(room_name="web-full-"+case.case_id[-25:],verify_mode="3",maxUserCount=10,
                roomMode=mode,enableChairPwd="1",chairPassword=cfg.get("protocol","chair_password"),
                creatorName=cfg.get("http","user_name") or cfg.get("operator","username"),autoRecord=0)
            room_id=int(created["data"]["roomId"])
            runtime.register_cleanup(self.verify_deleted,service.native_client,room_id)
            runtime.register_cleanup(service.native_client.delete_room,room_id)
            runtime.register_cleanup(service.native_client.close_meeting,room_id)
            node=str(created["data"].get("nodeId") or cfg.get("meeting","room_node_id"))
            runtime.variables.update({"meeting.room_id":room_id,"env.chair_password":cfg.get("protocol","chair_password")})
            if case.template in {"CONFIRM","DIRECT"} or (case.template=="BASE" and case.scenario in {"chat","chat_roundtrip","self_av","av_cycle","mute","chat_denied"}):
                permissions={"broadcastOwnAudio":True,"broadcastOwnVideo":True,"operatePublicChat":case.scenario!='chat_denied'}
                if case.template in {"CONFIRM","DIRECT"}:
                    permissions["openUserAvRequiresRequest"]="true" if case.template=="CONFIRM" else "false"
                configured=service.native_client.update_room_permission(room_id=room_id,room_mode="custom",room_permission=permissions)
                assert str(configured.get("code"))=="0"
            for index,(kind,identity) in enumerate(zip(case.types,case.identities)):
                alias=("operator","observer","witness")[index];section=("operator","observer","invitee")[index]
                account={"identity_type":identity,"display_name":"guest-e2e-"+uuid.uuid4().hex[:12] if identity=="guest" else cfg.get(section,"display_name",fallback=alias)}
                if identity=="account":account.update(username=cfg.get(section,"username"),password=cfg.get(section,"password"))
                endpoint={"room_id":room_id,"room_node_id":node,"event_timeout":runtime.event_timeout,
                    "join_settle_time":cfg.getfloat("web_sdk","join_settle_time",fallback=2),
                    "operation_timeout":cfg.getfloat("web_sdk","operation_timeout",fallback=30),
                    "ces_addr":cfg.get("web_sdk","ces_addr"),"host":cfg.get("fm_server","host"),
                    "port":cfg.getint("fm_server","port",fallback=1089),"conf_protocol_version":"5.0" if case.scenario in {"remote","remote_overlap","mixed_control"} else cfg.get_protocol_version(),
                    "allowed_conf_hosts":cfg.get_host_list("web_sdk","allowed_conf_hosts"),"support_features":cfg.get("protocol","support_features")}
                runtime.create_client(alias,ClientProfile("FULL_"+alias,kind,account,endpoint))
            aliases=list(runtime.clients)
            # Account controller joins before the anonymous sender; aliases remain fixed for the case.
            order=sorted(aliases,key=lambda a: runtime.profiles[a].account["identity_type"]=="guest")
            for alias in order:
                if case.scenario=="lock" and alias=="observer":continue
                if case.scenario=="login_invalid" and alias=="operator":continue
                runtime.join(alias,room_id)
            if case.template=='control' and case.scenario=='guest_share':
                # standard默认参会人禁止共享。用户确认的测试前置是允许普通游客共享。
                # 仅本次独立会议的mentee权限改变，不升级游客角色，不改公共模板。
                from .web_driver import CrossEndpointFmDriver
                endpoint=dict(runtime.profiles['operator'].endpoint)
                account={'username':cfg.get('operator','username'),'password':cfg.get('operator','password')}
                setup=CrossEndpointFmDriver(ClientProfile('MEETING_SETUP','fm',account,endpoint))
                try:
                    setup.join(room_id)
                    setup.call('apply_host',{'chair_pwd':cfg.get('protocol','chair_password')})
                    mark=setup.mark_events()
                    setup.call('set_role_permissions',{'role_codes':'mentee','permissions':{'CreateAppshare':1,'CreateWhiteboard':1}})
                    from .models import EventExpectation
                    proof=setup.wait_event(EventExpectation(cmdid=6632,kind='notify',match={'RoleCode':'mentee'}),mark,runtime.event_timeout)
                    attach_json({'reason':'本测试要求普通游客共享，standard默认权限不同；仅设置独立会议mentee',
                        'permissions':{'CreateAppshare':1,'CreateWhiteboard':1},'event':proof,
                        'actual_calls_and_protocol':setup.snapshot_outbound({})},'共享允许的会控参会人模板前置')
                finally:setup.close()
                for alias in aliases:
                    if runtime.clients[alias].client_type=='web':
                        permissions=runtime.call(alias,'get_effective_permissions',{})
                        assert str(permissions.get('CreateAppshare'))=='1' and str(permissions.get('CreateWhiteboard'))=='1','共享配置未在SDK实际生效'
            for alias in aliases:
                if f"{alias}.room_user_id" not in runtime.variables:continue
                account=runtime.profiles[alias].account
                if account["identity_type"]=="guest":
                    uid=runtime.variables[alias+".room_user_id"]
                    peers=[a for a in aliases if a!=alias and f"{a}.room_user_id" in runtime.variables]
                    assert peers,"游客身份必须由独立端的成员消息证明"
                    for peer in peers:
                        from .web_key_scenarios import wait
                        wait(runtime,peer,{peer:0},6602,{"User.UserID":uid,"User.UserType":0})
            attach_json({"template":case.template,"api_room_mode":mode,"room_id":room_id,
                "identities":case.identities,"created":created},"独立会议模板与实际身份前置")
            return runtime
        except BaseException:
            attach_json(runtime.snapshot_all_events({}),"前置失败的实际服务器消息")
            runtime.close_all();raise

    @staticmethod
    def verify_deleted(api,room_id):
        response=api.list_rooms_http(room_id=room_id,page_number=1,page_size=10)
        assert str(response.get('code'))=='0','关闭删除后的会议查询失败'
        data=response.get('data')
        assert isinstance(data,dict) and isinstance(data.get('records'),list),'会议回读没有明确records列表'
        assert not data['records'],f'测试会议{room_id}删除后仍存在'
        attach_json({'checked_room_id':room_id,'actual_server_response':response},'会议清理后精确回读为空')
        return response


def clear_roles(runtime, alias):
    for label,key in (("manager","ManagerState"),("presenter","DataState")):
        if int(user(runtime,alias).get(key) or 0):role(runtime,alias,label,False)


def establish_role(runtime, alias, label):
    clear_roles(runtime,alias)
    role(runtime,alias,label,True)
    info=user(runtime,alias)
    assert int(info.get("DataState" if label=="presenter" else "ManagerState") or 0)==2
    assert int(info.get("ManagerState" if label=="presenter" else "DataState") or 0)==0
