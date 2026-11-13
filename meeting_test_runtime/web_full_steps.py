"""Reviewed Excel step registry; no arbitrary Python reflection."""
from .web_protocol import matches_fields
from .web_key_scenarios import wait, no_event, device, av, user, role, execute_key_flow
from .web_full_runtime import clear_roles, establish_role
from .allure_reporting import attach_json, report_step, alias_name

ALLOWED_STEPS={"chat_send":{"text","save_as"},"chat_unique":{"variables"},
    "approval_prepare":{"role","kind"},"self_apply":{"kind"},"approval_decide":{"kind","decision"},"self_close":{"kind"},
    "mixed_prepare":{"role","path"},"remote_target":{"target","kind","path"},"mixed_check":{"kind","path"},
    "remote_close":{"target","kind","path"},"leave_member":set(),"rejoin_member":set(),
    "whiteboard_prepare":set(),"whiteboard_start":{"save_as"},"whiteboard_stop":{"variable"},"whiteboard_distinct":{"variables"},
    "record_role":{"role"},"release_role":{"role"},"record_denied":set()}
STEP_NAMES={"chat_send":"发送公共消息并核对全部对端","chat_unique":"核对连续消息标识互不复用", "approval_prepare":"准备独立审批角色和真实设备",
    "self_apply":"本人申请，核对等待态且未开启","approval_decide":"审批者批准或本人取消","self_close":"本人关闭音视频",
    "mixed_prepare":"取得单一角色并核对确认配置","remote_target":"控制指定目标并核对非目标","mixed_check":"核对两个目标同时处于预期状态",
    "remote_close":"关闭指定目标并核对非目标","leave_member":"成员主动退出","rejoin_member":"重建客户端并重新入会",
    "whiteboard_prepare":"明确取得主持角色作为白板生命周期前置","whiteboard_start":"创建白板并核对资源通知","whiteboard_stop":"关联本次白板关闭","whiteboard_distinct":"核对新旧白板标识隔离",
    "record_role":"仅指定角色录制启停","release_role":"放弃角色","record_denied":"普通成员录制明确拒绝"}


def reviewed_steps(case):
    rows=[]
    def add(actor,op,params=None):rows.append({'actor':actor,'operation':op,'params':params or {}})
    if case.scenario=='chat_roundtrip':
        names=[]
        for index in range(2 if case.params['repeat'] else 1):
            for actor in ('operator','observer','witness')[:len(case.types)]:
                variable=f'flow.chat.{actor}.{index}';names.append(variable)
                add(actor,'chat_send',{'text':'${case.text}','save_as':variable})
        add('operator','chat_unique',{'variables':names})
    elif case.scenario=='approval' and case.params['decision']!='reject':
        add('observer','approval_prepare',{'role':'${case.role}','kind':'${case.kind}'})
        add('operator','self_apply',{'kind':'${case.kind}'})
        add('observer','approval_decide',{'kind':'${case.kind}','decision':'${case.decision}'})
        if case.params['decision']=='approve':add('operator','self_close',{'kind':'${case.kind}'})
    elif case.scenario=='mixed_control':
        add('operator','mixed_prepare',{'role':'${case.role}','path':'${case.path}'})
        for target in ('observer','witness'):add('operator','remote_target',{'target':target,'kind':'${case.kind}','path':'${case.path}'})
        add('operator','mixed_check',{'kind':'${case.kind}','path':'${case.path}'})
        for target in ('observer','witness'):add('operator','remote_close',{'target':target,'kind':'${case.kind}','path':'${case.path}'})
    elif case.scenario in {'rejoin','guest_rejoin'}:
        actor=case.params['guest_alias'] if case.scenario=='guest_rejoin' else 'observer'
        add(actor,'leave_member');add(actor,'rejoin_member')
    elif case.scenario=='whiteboard_cycle':
        add('operator','whiteboard_prepare')
        for index in (0,1):
            add('operator','whiteboard_start',{'save_as':f'flow.board.{index}'})
            add('operator','whiteboard_stop',{'variable':f'flow.board.{index}'})
        add('operator','whiteboard_distinct',{'variables':['flow.board.0','flow.board.1']})
    elif case.scenario=='role_then_record':
        add('operator','record_role',{'role':'${case.role}'});add('operator','release_role',{'role':'${case.role}'});add('operator','record_denied')
    return rows


def validate_steps(steps,aliases):
    for step in steps:
        if set(step)!={'actor','operation','params'} or step['actor'] not in aliases:
            raise ValueError('步骤字段/执行端不合法')
        if step['operation'] not in ALLOWED_STEPS or set(step['params'])!=ALLOWED_STEPS[step['operation']]:
            raise ValueError('步骤必须属于已审核白名单且参数完整')
        fixed={'approval_prepare':'observer','approval_decide':'observer'}
        if step['operation'] not in {'chat_send','chat_unique','leave_member','rejoin_member'} and step['actor']!=fixed.get(step['operation'],'operator'):
            raise ValueError('执行端与步骤业务对象不一致')
        for key,values in {'kind':{'audio','video','${case.kind}'},'role':{'presenter','manager','${case.role}'},
                           'path':{'accept','reject','direct','${case.path}'},'decision':{'approve','cancel','${case.decision}'},
                           'target':set(aliases)-{'operator'}}.items():
            if key in step['params'] and step['params'][key] not in values:raise ValueError('流程参数不符合业务契约')
        for key in ('save_as','variable'):
            if key in step['params'] and not str(step['params'][key]).startswith('flow.'):raise ValueError('输出变量只能位于flow命名空间')
        if 'variables' in step['params'] and (not isinstance(step['params']['variables'],list) or any(
                not isinstance(v,str) or not v.startswith('flow.') for v in step['params']['variables'])):
            raise ValueError('流程变量列表不合法')


def validate_flow_contract(case):
    """Critical business steps cannot disappear or be replaced by unrelated allowed steps."""
    expected=reviewed_steps(case);actual=list(case.steps)
    aliases=('operator','observer','witness')[:len(case.types)]
    validate_steps(actual,aliases)
    if len(actual)!=len(expected):raise ValueError('流程核心步骤缺失或数量不符')
    def resolve(value):
        if isinstance(value,dict):return {k:resolve(v) for k,v in value.items()}
        if isinstance(value,list):return [resolve(v) for v in value]
        if isinstance(value,str) and value.startswith('${case.'):
            name=value[7:-1]
            if name not in case.params:raise ValueError('流程引用未定义用例参数')
            return case.params[name]
        return value
    normalized=[{'actor':s['actor'],'operation':s['operation'],'params':resolve(s['params'])} for s in actual]
    planned=[{'actor':s['actor'],'operation':s['operation'],'params':resolve(s['params'])} for s in expected]
    # Keep explicit, reviewed order/parameters. Business changes need a new reviewed contract,
    # not a row deletion which silently bypasses the case goal.
    if normalized!=planned:raise ValueError('流程顺序、对象、参数或变量依赖偏离业务契约')
    produced=set()
    for step in normalized:
        p=step['params']
        if 'save_as' in p:
            if p['save_as'] in produced:raise ValueError('流程输出变量重复覆盖')
            produced.add(p['save_as'])
        if 'variable' in p and p['variable'] not in produced:raise ValueError('流程消费未产生的资源')
        if 'variables' in p:
            if len(p['variables'])<2 or len(set(p['variables']))!=len(p['variables']) or set(p['variables'])!=produced:
                raise ValueError('流程结果核验不能为空、重复或遗漏输出')


def execute_steps(case,runtime):
    from .web_full_scenarios import assert_av,no_av,public_chat,remote_open,verify_remote_template,start_share,stop_share
    validate_flow_contract(case)
    for key,value in case.params.items():runtime.variables['case.'+key]=value
    validate_steps(case.steps,list(runtime.clients));runtime.variables['flow.devices']={}
    completed=[]
    for index,step in enumerate(case.steps,1):
        actor=step['actor'];op=step['operation'];p=runtime.resolve_variables(step['params'])
        with report_step(f'流程步骤{index}：{alias_name(actor)}{STEP_NAMES[op]}'):
            attach_json({'actor':actor,'operation':op,'parameters':p},'Excel步骤及变量替换后的实际参数')
            if op=='chat_send':runtime.variables[p['save_as']]=public_chat(runtime,actor,p['text'])
            elif op=='chat_unique':
                ids=[runtime.variables[name] for name in p['variables']]
                assert len(set(ids))==len(ids),'相同正文不能由旧消息或同一MsgID重复证明'
            elif op=='approval_prepare':
                establish_role(runtime,actor,p['role']);clear_roles(runtime,'operator')
                runtime.variables['flow.device']=device(runtime,'operator',p['kind'])
            elif op=='self_apply':
                marks=runtime.mark_all_events();av(runtime,actor,p['kind'],True,runtime.variables['flow.device'])
                assert_av(runtime,marks,actor,p['kind'],runtime.variables['flow.device'],1);no_av(runtime,marks,actor,p['kind'])
            elif op=='approval_decide':
                marks=runtime.mark_all_events();dev=runtime.variables['flow.device'];uid=runtime.variables['operator.room_user_id']
                if p['decision']=='approve':runtime.call(actor,'approve_self_av',{'user_id':uid,'kind':p['kind'],'dev_id':dev});state=2
                else:av(runtime,'operator',p['kind'],False,dev);state=0
                assert_av(runtime,marks,'operator',p['kind'],dev,state)
            elif op=='self_close':
                marks=runtime.mark_all_events();av(runtime,actor,p['kind'],False,runtime.variables['flow.device'])
                assert_av(runtime,marks,actor,p['kind'],runtime.variables['flow.device'],0)
            elif op=='mixed_prepare':
                establish_role(runtime,actor,p['role']);verify_remote_template(runtime,actor,p['path']=='direct')
                for target in ('observer','witness'):
                    clear_roles(runtime,target)
                    runtime.variables['flow.devices'][target]=device(runtime,target,case.params['kind'])
            elif op=='remote_target':
                from .web_assertions import av_state,assert_non_target
                target=p['target'];dev=runtime.variables['flow.devices'][target]
                other='witness' if target=='observer' else 'observer';other_dev=runtime.variables['flow.devices'][other]
                baseline=av_state(runtime,other,p['kind'],other_dev)
                marks=runtime.mark_all_events();remote_open(runtime,actor,target,p['kind'],p['path'],marks,dev)
                assert_non_target(runtime,marks,other,p['kind'],other_dev,baseline,actor)
            elif op=='mixed_check':
                if p['path']!='reject':
                    key='Audio' if p['kind']=='audio' else 'Video'
                    for target,dev in runtime.variables['flow.devices'].items():
                        assert matches_fields(user(runtime,target),{key+'.ID':dev,key+'.State':2}),'两个目标应同时开启且设备一致'
            elif op=='remote_close':
                from .web_assertions import av_state,assert_non_target
                if p['path']=='reject':
                    assert av_state(runtime,p['target'],p['kind'],runtime.variables['flow.devices'][p['target']])==0
                    completed.append({'step':index,'actor':actor,'operation':op,'reason':'拒绝后目标未开启，核验保持关闭'})
                    continue
                target=p['target'];dev=runtime.variables['flow.devices'][target];marks=runtime.mark_all_events()
                other='witness' if target=='observer' else 'observer';other_dev=runtime.variables['flow.devices'][other]
                baseline=av_state(runtime,other,p['kind'],other_dev)
                runtime.call(actor,'close_remote_av',{'user_id':runtime.variables[target+'.room_user_id'],'kind':p['kind'],'dev_id':dev})
                assert_av(runtime,marks,target,p['kind'],dev,0)
                assert_non_target(runtime,marks,other,p['kind'],other_dev,baseline,actor)
            elif op=='leave_member':
                uid=runtime.variables[actor+'.room_user_id'];marks=runtime.mark_all_events()
                runtime.call(actor,'leave' if runtime.clients[actor].client_type=='web' else 'conf_logout',{})
                from .web_assertions import assert_departed
                assert_departed(runtime,actor,uid,marks)
                runtime.variables['flow.departed_alias']=actor
            elif op=='rejoin_member':
                assert runtime.variables.get('flow.departed_alias')==actor,'未完成退出不能重建会话'
                runtime.replace_client(actor);marks=runtime.mark_all_events();out=runtime.mark_all_outbound()
                runtime.join(actor,runtime.variables['meeting.room_id'])
                for peer in runtime.clients:
                    if peer!=actor:
                        fields={'User.UserID':runtime.variables[actor+'.room_user_id']}
                        if case.scenario=='guest_rejoin':fields['User.UserType']=0
                        wait(runtime,peer,marks,6602,fields)
                if case.scenario=='guest_rejoin':
                    from .web_assertions import assert_sent
                    assert_sent(runtime,actor,out,1000,{'UserType':0})
            elif op=='whiteboard_prepare':establish_role(runtime,actor,'presenter')
            elif op=='whiteboard_start':
                from .web_assertions import assert_old_whiteboards_absent
                marks=runtime.mark_all_events();out=runtime.mark_all_outbound()
                result=start_share(runtime,actor,'whiteboard')
                old=[v['mediaId'] for n,v in runtime.variables.items() if n.startswith('flow.board.') and isinstance(v,dict)]
                assert_old_whiteboards_absent(runtime,actor,{**out,'events':marks},result['mediaId'],old)
                runtime.variables[p['save_as']]=result
            elif op=='whiteboard_stop':stop_share(runtime,actor,'whiteboard',runtime.variables[p['variable']])
            elif op=='whiteboard_distinct':
                ids=[runtime.variables[n]['mediaId'] for n in p['variables']]
                assert len(set(ids))==len(ids),'新建白板资源不能重复'
            elif op=='record_role':execute_key_flow({**case.spec(),'scenario':'record','params':{'role':p['role']}},runtime)
            elif op=='release_role':role(runtime,actor,p['role'],False)
            elif op=='record_denied':execute_key_flow({**case.spec(),'scenario':'record','params':{'role':'attendee'}},runtime)
            completed.append({'step':index,'actor':actor,'operation':op})
    assert len(completed)==len(case.steps),'流程必要业务阶段未全部完成'
    attach_json(completed,'全部必要流程阶段完成记录')
    assert runtime.is_healthy(),'多步骤流程完成但连接不健康'
