"""Deterministic, audited Web cases; preserve the FM and initial key-flow workbooks."""
import argparse
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import sys
from dataclasses import replace

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"sessionmanager_py")]
from meeting_test_runtime.web_full_cases import WebFullCase, validate_case, MODULES
from meeting_test_runtime.excel_loader import ExcelCaseLoader
from meeting_test_runtime.conf_audit import scan_conf_protocol
from meeting_test_runtime.web_full_steps import reviewed_steps, STEP_NAMES
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter

HEADERS=["用例ID","场景名称","模块","用例类型","终端组合","入会身份","会议模板","统一操作名","参数JSON",
         "执行级别","环境风险","用例状态","缺口原因","来源FM用例ID","复用分类","证据类型","是否执行","预期消息与断言"]
OP_SOURCES={
    "login":{"conf_login","conf_logout","front_login","front_login_guest","front_authenticate"},
    "chat":{"chat"},"self_av":{"mic_on","mic_off","camera_on","camera_off","set_audio_state","set_video_state"},
    "role":{"apply_host","release_host","set_user_presenter","set_user_manager","set_role"},
    "remote":{"request_remote_av","set_user_audio","set_user_video"},"screen":{"start_aux_stream","stop_aux_stream"},
    "whiteboard":set(),"whiteboard_view":{"set_window_state","set_layout","request_right","release_right"},
    "record":{"start_record","stop_record"},"mute":{"mute_all"},
    "lock":{"lock_room","unlock_room"},"kick":{"kick_user"},"close":{"close_room"},"info":set(),
}
FAMILIES={"guest_login":"login","rejoin":"login","guest_rejoin":"login","login_invalid":"login","guest_av":"self_av","av_cycle":"self_av",
          "approval":"self_av","av_cancel":"self_av","guest_role":"role","role_repeated":"role","manager_wrong_password":"role",
          "direct":"remote","mixed_control":"remote","remote_overlap":"remote","remote_target_left":"remote",
          "chat_roundtrip":"chat","chat_denied":"chat","guest_share":"screen","share_cycle":"screen","fm_screen":"screen",
          "whiteboard_cycle":"whiteboard","fm_whiteboard":"whiteboard_view","role_then_record":"record","info_membership":"info",
          "remote_denied":"remote","share_takeover":"screen","share_contention":"screen"}
SIGNALS={"login":{6501,6601,6517,6602,6505,6605,6631,6639,6508,6608},"self_av":{6509,6609},
         "role":{6509,6609,6511,6611},"chat":{6512,6612},"remote":{6509,6609,6542,6642,6543,6643},
         "screen":{6509,6609,6530,6630,6513,6613},"whiteboard":{6513,6613,6530,6630},"whiteboard_view":{6513,6613,6530,6630},"record":{6510,6610},
         "mute":{6514,6614,6509,6609},"lock":{6510,6610,6617,6604,6501,6601},"kick":{6506,6606,6605},"close":{6507,6607}}


def source_eligible(case, family):
    if case.operation not in OP_SOURCES.get(family,set()):return False
    if family=='chat':
        return case.params.get('dst_user_id') in (None,0,'0') and not ({'font','color'} & set(case.params))
    if family=='self_av':
        return case.params.get('state',0) in (0,1,2) and case.params.get('user_id') in (None,'${operator.room_user_id}')
    if family=='screen':return case.params.get('kind')=='vnc'
    if family=='role':return case.params.get('role','presenter') in ('presenter','admin','manager')
    if family=='whiteboard_view':
        return case.params.get('right_type')==1 if case.operation in ('request_right','release_right') else case.params.get('data_active')==1
    return True


def design_cases():
    source=ExcelCaseLoader(ROOT/"tests/e2e/data/fm_e2e_cases.xlsx").load_cases(include_disabled=True)
    result=[];seen=set()
    def add(scenario,name,combo="web->web",identities=None,template="BASE",params=None,priority="P1",status="IMPLEMENTED",reason="",risk="ISOLATED_STATEFUL",evidence="PROTOCOL",exception=False):
        kinds=combo.replace("+","->").split("->")
        identities=identities or tuple("account" for _ in kinds)
        params=params or {}
        signature=json.dumps([scenario,combo,identities,template,params],ensure_ascii=False,sort_keys=True)
        if signature in seen:raise ValueError("重复业务设计: "+signature)
        seen.add(signature)
        digest=hashlib.sha256(signature.encode()).hexdigest()[:9]
        family=FAMILIES.get(scenario,scenario)
        refs=tuple(c.case_id for c in source if source_eligible(c,family))
        case=WebFullCase("WEB-FULL-"+scenario.upper()+"-"+digest,name,scenario,combo,tuple(identities),template,params,
                         priority,status,reason,risk,refs,"BUSINESS_EXCEPTION" if exception else "NORMAL",evidence)
        case=replace(case,steps=tuple(reviewed_steps(case)))
        validate_case(case);result.append(case)
    modes=("discussion","training","control")
    cn={"discussion":"讨论","training":"培训","control":"会控","audio":"音频","video":"视频","screen":"屏幕","whiteboard":"白板",
        "presenter":"主持人","manager":"管理员","account":"账号用户","guest":"游客"}
    profiles={"fm->web":[("account","guest")],"web->fm":[("guest","account")],
              "web->web":[("guest","account"),("account","guest"),("guest","guest")]}
    for mode,combo in itertools.product(modes,profiles):
        for identity in profiles[combo]:
            add("guest_login",f"{cn[mode]}会议游客真实入会、成员通知和退出",combo,identity,mode,priority="P0",risk="DESTRUCTIVE_ISOLATED")
    for combo,kind in itertools.product(("web->fm","web->web"),("audio","video")):
        add("guest_av","讨论会议游客本人"+cn[kind]+"开关",combo,("guest","account"),"discussion",{"kind":kind},"P0")
    for mode,combo,kind in itertools.product(modes,("web->fm","web->web"),("screen","whiteboard")):
        add("guest_share",cn[mode]+"会议普通游客"+cn[kind]+"共享启停再开",combo,("guest","account"),mode,{"kind":kind},"P1")
    for mode,combo,label in itertools.product(modes,("web->fm","web->web"),("presenter","manager")):
        add("guest_role",cn[mode]+"会议游客申请并放弃"+cn[label],combo,("guest","account"),mode,{"role":label})
    for mode,identity,kind,label in itertools.product(("training","control"),("account","guest"),("audio","video"),("presenter","manager")):
        add("approval",cn[mode]+"会议"+cn[identity]+"申请"+cn[kind]+"由"+cn[label]+"批准",identities=(identity,"account"),template=mode,
            params={"kind":kind,"role":label,"decision":"approve"},priority="P0")
    for mode,kind in itertools.product(("training","control"),("audio","video")):
        add("av_cancel",cn[mode]+"会议游客取消"+cn[kind]+"申请",identities=("guest","account"),template=mode,params={"kind":kind},exception=True)
        add("approval",cn[mode]+"会议游客"+cn[kind]+"申请拒绝契约待确认",identities=("guest","account"),template=mode,
            params={"kind":kind,"role":"manager","decision":"reject"},status="SKIP_BLOCKED",reason="本人申请拒绝的正式状态及通知契约未确认，不能以超时当拒绝",exception=True)
    for combo,label,kind,path in itertools.product(("fm->web","web->fm","web->web"),("presenter","manager"),("audio","video"),("accept","reject","direct")):
        scenario="direct" if path=="direct" else "remote"
        params={"kind":kind,"role":label}
        if scenario=="remote":params["accept"]=path=="accept"
        add(scenario,cn[label]+"远程开启"+cn[kind]+{"accept":"目标接受","reject":"目标拒绝","direct":"无需确认"}[path],combo,
            template="DIRECT" if scenario=="direct" else "CONFIRM",params=params)
    for label,kind,path,identity in itertools.product(("presenter","manager"),("audio","video"),("accept","reject","direct"),("account","guest")):
        add("mixed_control","仅"+cn[label]+"依次控制FM和Web"+cn[identity]+cn[kind]+"（"+path+"）","web->fm+web",
            ("account","account",identity),"DIRECT" if path=="direct" else "CONFIRM",{"kind":kind,"role":label,"path":path})
    texts=[("中文","协议群聊中文"),("英文与空格","E2E public chat  abc"),("保留字符","<&>\"'"),
           ("换行","第一行\n第二行"),("制表","A\tB"),("普通长文","自动化长文本"*150)]
    for combo,(label,text) in itertools.product(("fm->web","web->fm","web->web"),texts):
        add("chat_roundtrip",label+"双向群聊及重复正文关联",combo,template="BASE",params={"text":text,"repeat":True},priority="P1",risk="SAFE_REUSABLE")
    for mode,combo,identity in itertools.product(modes,("fm->web","web->fm","web->web"),("guest",)):
        ids=("account","guest") if combo=="fm->web" else ("guest","account")
        add("chat_roundtrip",cn[mode]+"会议游客与对端双向群聊",combo,ids,mode,{"text":"游客公共消息<&>","repeat":False},"P0",risk="SAFE_REUSABLE")
    for guest in (False,True):
        add("chat_roundtrip","混合三端公共群聊全部送达"+("含游客" if guest else ""),"web->fm+web",("account","account","guest" if guest else "account"),
            "discussion",{"text":"三端广播<&>","repeat":True},"P1",risk="SAFE_REUSABLE")
    for combo,kind in itertools.product(("fm->web","web->fm","web->web"),("audio","video")):
        add("av_cycle","双端轮流"+cn[kind]+"连续开关与游标隔离",combo,template="BASE",params={"kind":kind},risk="SAFE_REUSABLE")
    for combo in ("fm->web","web->fm","web->web"):
        add("rejoin","对端退出后重新入会，核对新成员证据",combo,risk="DESTRUCTIVE_ISOLATED")
    for mode,combo in itertools.product(modes,('fm->web','web->fm','web->web')):
        alias='observer' if combo=='fm->web' else 'operator'
        identities=('account','guest') if alias=='observer' else ('guest','account')
        add('guest_rejoin',cn[mode]+'会议游客退出后匿名重新入会，校验新会话和成员',combo,identities,mode,
            {'guest_alias':alias},'P0',risk='DESTRUCTIVE_ISOLATED')
    for combo in ("web->fm","web->web"):
        add("whiteboard_cycle","白板关闭后再建，新旧资源隔离",combo,params={},priority="P2")
        add("share_cycle","桌面共享停止后重新开始",combo,params={"kind":"screen"},priority="P2")
        add("manager_wrong_password","错误管理员密码真实6611且无授权通知",combo,exception=True)
        add("role_repeated","已是管理员再次申请为SDK本地幂等",combo,params={"role":"manager"},evidence="SDK_LOCAL")
        add("info","本人及对端用户名昵称独立核对",combo,evidence="CACHE")
        add("info_membership","真实离会6605后SDK成员缓存删除",combo,risk="DESTRUCTIVE_ISOLATED",evidence="CACHE_AND_PROTOCOL")
        for label in ("presenter","manager"):
            add("role_then_record",cn[label]+"录制启停，放弃角色后录制被拒绝",combo,params={"role":label})
    for combo,kind,template in itertools.product(('web->fm','web->web'),('audio','video'),('DIRECT','CONFIRM')):
        add('remote_denied','普通成员不得远程开启他人'+cn[kind],combo,template=template,params={'kind':kind},exception=True)
    for identities in [('account','account'),('account','guest'),('guest','account')]:
        add('share_takeover','A屏幕共享结束后B接替再停止','web->web',identities,'discussion')
    add('share_takeover','Web屏幕共享结束后FM接替再停止','web->fm',template='discussion')
    add('share_contention','游客争抢被占用屏幕共享，A结束后游客接替','web->web',('account','guest'),'discussion',exception=True)
    for identity in ("account","guest"):
        add("fm_screen","FM发起屏幕共享，Web"+cn[identity]+"收到启停","fm->web",("account",identity),"discussion",priority="P0")
        add("fm_whiteboard","FM切换白板视图协议，Web"+cn[identity]+"接收并恢复","fm->web",("account",identity),"discussion")
        add("mute","FM静音后Web"+cn[identity]+"音频关闭","fm->web",("account",identity),"discussion")
        for scenario in ("lock","kick","close"):
            add(scenario,"FM"+{"lock":"锁会等待后解锁","kick":"请出目标","close":"关闭会议"}[scenario]+"，Web"+cn[identity]+"接收",
                "fm->web",("account",identity),"BASE",risk="DESTRUCTIVE_ISOLATED")
    for identity,failure in itertools.product(("account",),("account","password","room")):
        add("login_invalid","非法"+failure+"鉴权/入会真实拒绝",params={"failure":failure},exception=True,risk="DESTRUCTIVE_ISOLATED")
    # User-confirmed local rejection and latest-invitation ownership contracts.
    for kind in ("audio","video"):
        add("remote_target_left","目标离会后远程"+cn[kind]+"本地拒绝且无副作用",params={"kind":kind,"role":"manager"},
            exception=True,evidence="SDK_LOCAL")
        add("remote_overlap","重叠远程"+cn[kind]+"邀请只答复最新请求，接受拒绝及恢复", "web->web+web",template="CONFIRM",params={"kind":kind})
    add("chat_denied","无群聊权限SDK真实拒绝且没有6512发送",exception=True,evidence="SDK_LOCAL")
    return [replace(c,steps=tuple(reviewed_steps(c))) for c in result],source


def format_book(book):
    for sheet in book:
        sheet.freeze_panes="C2";sheet.auto_filter.ref=sheet.dimensions;sheet.sheet_view.showGridLines=False
        for cell in sheet[1]:
            cell.font=Font(name="Microsoft YaHei",size=10,bold=True,color="FFFFFF")
            cell.fill=PatternFill("solid",fgColor="23466A");cell.alignment=Alignment(wrap_text=True,vertical="center")
        sheet.row_dimensions[1].height=36
        for col in range(1,sheet.max_column+1):sheet.column_dimensions[get_column_letter(col)].width=26
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.font=Font(name="Microsoft YaHei",size=10);cell.alignment=Alignment(wrap_text=True,vertical="center")
            sheet.row_dimensions[row[0].row].height=58
    book["功能用例"].column_dimensions["B"].width=52
    book["功能用例"].column_dimensions["I"].width=58
    book["功能用例"].column_dimensions["M"].width=58
    book["功能用例"].column_dimensions["N"].width=75


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,default=ROOT/"tests/web_e2e/data/web_e2e_cases.xlsx")
    parser.add_argument('--refresh-generated',action='store_true',help='重新生成当前设计；只用于明确由此工具生成的工作簿')
    args=parser.parse_args()
    if args.output.exists() and not args.refresh_generated:raise FileExistsError("不覆盖已有人工维护Excel")
    cases,source=design_cases();book=Workbook();sheet=book.active;sheet.title="功能用例";sheet.append(HEADERS)
    expected={
        'fm_whiteboard':'取得布局权；FM真实6513 Mode=2/DataActive=1；Web新6613 ScreenID=0/LayoutMode=2、数据区ID=1白板Type=1且Data=0；切回视频Type=6/LayoutMode=3；释放布局权；连接健康。不创建资源或验证页面跟随。',
        'remote_target_left':'目标真实离会并从成员缓存移除；公开门面本地TARGET_NOT_IN_MEETING拒绝；无SDK远控调用或6509/6542；无音视频副作用、其他成员状态不变、操作端健康。',
        'remote_overlap':'三端CONFIRM模板；C先后收到A/B两条6642，只答复最新B。分别接受/拒绝，实际6543/6643来源、目标、设备及Result正确；A无本次6643；等待/拒绝不打开，接受后开启再关闭恢复，连接健康。',
        'lock':'实际6610.Lock=1/0；锁定时Web收到6617且不能完成入会；解锁后真实6601/6602及健康；SrcUserID可省略，如提供必须匹配操作端。',
    }
    for case in cases:
        sheet.append([case.case_id,case.name,MODULES[case.scenario],case.case_type,case.combination,",".join(case.identities),case.template,
            case.scenario,json.dumps(case.params,ensure_ascii=False),case.priority,case.risk,case.status,case.reason,",".join(case.source_ids),
            "改写复用" if case.source_ids else "Web新增",case.evidence,True,
            expected.get(case.scenario,'按步骤关联真实消息；'+('SDK本地拒绝/幂等，核对无实际成功副作用' if case.evidence=='SDK_LOCAL' else
                '缓存与独立实际消息比对' if case.evidence.startswith('CACHE') else
                '相关信令：'+','.join(map(str,sorted(SIGNALS.get(FAMILIES.get(case.scenario,case.scenario),set()))))+'；身份/用户/设备/资源/方向/序列/健康'))])
    profiles=book.create_sheet("终端配置");profiles.append(["配置ID","终端类型","账号引用","环境引用","入会身份","启用"])
    for alias,section in (("operator","operator"),("observer","observer"),("witness","invitee")):
        profiles.append([alias,"按用例组合",section,"test_env.ini","account或guest",True])
    domains=book.create_sheet("参数域");domains.append(["参数域ID","正常值JSON","边界契约","来源"])
    domains.append(["meeting_modes",json.dumps(["discussion","training","standard"]),"会控对应standard；实际权限必须验证","cesapi.html roomMode"])
    domains.append(["remote_paths",json.dumps(["accept","reject","direct"]),"等待/拒绝无成功6609；接受后开启","用户确认+ConfProtocol.xml"])
    flows=book.create_sheet("依赖流程");flows.append(["流程ID","步骤序号","步骤说明","对应用例ID","执行端","统一操作名","参数JSON"])
    for case in cases:
        for index,step in enumerate(case.steps,1):
            flows.append([case.case_id,index,STEP_NAMES[step['operation']],case.case_id,step['actor'],step['operation'],json.dumps(step['params'],ensure_ascii=False)])
    audit=book.create_sheet("FM复用审计");audit.append(["FM用例ID","操作","分类","Web用例ID","依据"])
    for old in source:
        related=[c.case_id for c in cases if old.case_id in c.source_ids]
        detail="保留业务目的，按Web身份/角色/动态设备改写" if related else "Web产品未确认主动能力，或FM专属接口/参数；不复制执行"
        if old.operation=="chat" and old.params.get("dst_user_id") not in (None,0,"0"):
            related=[];detail="Web不支持文字私聊"
        audit.append([old.case_id,old.operation,"改写复用" if related else "不适用",",".join(related),detail])
    signals=book.create_sheet("Conf信令检查");signals.append(["定义序号","cmdid","版本","协议说明","主要字段","Web适用性","Web用例ID","缺口原因"])
    for definition in scan_conf_protocol(ROOT/"ConfProtocol.xml"):
        related=[c for c in cases if definition.cmdid in SIGNALS.get(FAMILIES.get(c.scenario,c.scenario),set())]
        signals.append([definition.ordinal,definition.cmdid,definition.version,definition.description,",".join(definition.fields),
            "部分字段适用" if related else "不适用/未纳入已确认Web产品范围",",".join(c.case_id for c in related),
            "仅声明关联；真实字段覆盖以实际运行证据为准" if related else "不能用未知cmdid解析能力冒充业务闭环覆盖"])
    methods=book.create_sheet("业务方法检查");methods.append(["场景","模块","Web用例ID","证据类型","接口缺口"])
    for scenario in sorted({c.scenario for c in cases}):
        rows=[c for c in cases if c.scenario==scenario]
        methods.append([scenario,MODULES[scenario],",".join(c.case_id for c in rows),",".join(sorted({c.evidence for c in rows})),
                        ";".join(sorted({c.reason for c in rows if c.reason}))])
    format_book(book);book.save(args.output)
    print("WEB_CASE_DESIGN",len(cases),dict(Counter(c.status for c in cases)),dict(Counter(c.priority for c in cases)))
    print("FM_AUDIT",len(source),"CONF_DEFINITIONS",signals.max_row-1,"OUTPUT",args.output)


if __name__=="__main__":main()
