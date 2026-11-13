"""Use the existing dependency-flow model with one reviewed full-chain orchestrator."""
from pathlib import Path

from .excel_loader import ExcelCaseLoader
from .models import CaseStatus


SCENARIOS=frozenset({'login','chat','self_av','mute','role','info','remote','direct','screen',
                     'whiteboard','record','lock','kick','close'})
COMBINATIONS=frozenset({'fm->web','web->fm','web->web'})


def validate_spec(spec):
    scenario=spec.get('scenario');params=spec.get('params')
    if scenario not in SCENARIOS or spec.get('combination') not in COMBINATIONS:
        raise ValueError('未确认的Web场景或终端组合')
    allowed={'chat':{'text'},'self_av':{'kind'},'role':{'role'},'remote':{'kind','accept'},
             'direct':{'kind'},'record':{'role'}}.get(scenario,set())
    if not isinstance(params,dict) or set(params)!=allowed:
        raise ValueError(f'{scenario}: 缺少必需参数或存在未支持参数')
    if 'kind' in params and params['kind'] not in {'audio','video'}:
        raise ValueError('媒体类型必须为audio/video')
    if 'accept' in params and not isinstance(params['accept'],bool):
        raise ValueError('accept必须为JSON布尔值')
    if 'role' in params and params['role'] not in ({'presenter','manager','attendee'} if scenario=='record' else {'presenter','manager'}):
        raise ValueError('未确认的Web角色')
    if 'text' in params and not isinstance(params['text'],str):
        raise ValueError('聊天正文必须为字符串')


def load_web_key_flows(path):
    cases=[]
    for flow in ExcelCaseLoader(Path(path)).load_dependency_flows():
        if flow.status is not CaseStatus.IMPLEMENTED:
            continue
        if len(flow.steps)!=1 or flow.steps[0].operation!='execute_web_key_scenario':
            raise ValueError(f'{flow.flow_id}: 首阶段只允许评审后的完整关键链编排入口')
        spec=flow.steps[0].params
        validate_spec(spec)
        cases.append({'id':spec['base_id'],'run_id':flow.flow_id,'name':flow.name,'priority':flow.priority,
                      'scenario':spec['scenario'],'combination':spec['combination'],'params':spec['params']})
    return cases
