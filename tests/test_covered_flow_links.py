from openpyxl import load_workbook

from meeting_test_runtime.excel_loader import ExcelCaseLoader
from meeting_test_runtime.models import CaseStatus


EXCEL = 'tests/e2e/data/fm_e2e_cases.xlsx'


def test_existing_invitation_and_waiting_cases_back_reviewed_flows():
    loader = ExcelCaseLoader(EXCEL)
    cases = loader.load_cases(include_disabled=True)
    flows = {flow.flow_id: flow for flow in loader.load_dependency_flows()}
    links = {
        'FLOW-HTTP-INVITE': {case.case_id for case in cases
                             if case.scene_type == 'PAAS_INVITED_JOIN'},
        'FLOW-WAITING-ROOM': {case.case_id for case in cases
                              if case.scene_type == 'WAITING_REVIEW'},
        'FLOW-AUX-CONTENTION': {case.case_id for case in cases
                                if case.scene_type == 'AUX_FM_CONTENTION'},
    }
    assert len(links['FLOW-HTTP-INVITE']) == 2
    assert len(links['FLOW-WAITING-ROOM']) == 2
    assert len(links['FLOW-AUX-CONTENTION']) == 1
    assert {case.params['decision'] for case in cases
            if case.scene_type == 'WAITING_REVIEW'} == {'reject', 'mixed'}

    book = load_workbook(EXCEL, read_only=True, data_only=True)
    try:
        sheet = book['依赖流程']
        headers = [cell.value for cell in sheet[1]]
        rows = [dict(zip(headers, row)) for row in sheet.iter_rows(min_row=2, values_only=True)]
    finally:
        book.close()
    for flow_id, case_ids in links.items():
        assert flows[flow_id].status is CaseStatus.COVERED_BY_CASES
        reviewed = [row for row in rows if row['流程ID'] == flow_id]
        assert reviewed
        assert all(all(case_id in row['步骤说明'] for case_id in case_ids)
                   for row in reviewed)
    for flow_id in ('FLOW-H323-AUX-AUTO', 'FLOW-H323-AUX-APPROVAL'):
        assert flows[flow_id].status is CaseStatus.OUT_OF_SCOPE
    aux = next(case for case in cases if case.scene_type == 'AUX_FM_CONTENTION')
    assert aux.status is CaseStatus.IMPLEMENTED and aux.enabled
    assert not aux.blocker_id and aux.isolated
