import json
from pathlib import Path

import pytest

from meeting_test_runtime.web_key_loader import load_web_key_flows,validate_spec

ROOT=Path(__file__).resolve().parents[1]


def test_excel_expands_to_exactly_the_same_reviewed_50_cases_and_order():
    data=ROOT/'tests/web_e2e/data'
    expected=[]
    for item in json.loads((data/'web_key_flows.json').read_text(encoding='utf-8')):
        for combination in item['combinations']:
            for index,variant in enumerate(item.get('variants',[{}])):
                expected.append({'id':item['id'],'run_id':f"{item['id']}[{combination}]-{index}",
                    'name':item['name'],'priority':item['priority'],'scenario':item['scenario'],
                    'combination':combination,'params':{**item.get('params',{}),**variant}})
    actual=load_web_key_flows(data/'web_key_flows.xlsx')
    assert actual==expected and len(actual)==50
    assert len({c['run_id'] for c in actual})==50


@pytest.mark.parametrize('params',[
    {'kind':'audio','accept':'false'},
    {'kind':'audio','accept':False,'raw_xml':'<cmd id="9999"/>'},
    {'kind':'unknown','accept':True},
])
def test_invalid_or_unregistered_excel_parameters_fail_before_server_calls(params):
    with pytest.raises(ValueError):
        validate_spec({'scenario':'remote','combination':'web->fm','params':params})
