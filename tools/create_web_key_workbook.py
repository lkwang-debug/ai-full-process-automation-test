"""Create the independent initial key-flow Excel; never writes the FM workbook."""
from pathlib import Path
import json

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'tests/web_e2e/data/web_key_flows.json'
OUTPUT=ROOT/'tests/web_e2e/data/web_key_flows.xlsx'
HEADERS=['流程ID','流程名称','优先级','环境风险','清理要求','实现状态','步骤序号','执行端',
         '统一操作名','参数JSON','输出变量JSON','预期Response','预期Notify序列JSON','预期接收端',
         '不应接收端','预期错误码JSON','预期非零Result','失败是否终止']


def main():
    if OUTPUT.exists():
        raise FileExistsError('执行入口Excel已存在；不覆盖人工维护的数据。')
    book=Workbook();sheet=book.active;sheet.title='依赖流程';sheet.append(HEADERS)
    for item in json.loads(SOURCE.read_text(encoding='utf-8')):
        for combination in item['combinations']:
            for index,variant in enumerate(item.get('variants',[{}])):
                params={**item.get('params',{}),**variant}
                run_id=f"{item['id']}[{combination}]-{index}"
                specification={'base_id':item['id'],'scenario':item['scenario'],'combination':combination,'params':params}
                sheet.append([run_id,item['name'],item['priority'],'ISOLATED_STATEFUL',
                    '关闭所有终端、共享资源和独立会议', 'IMPLEMENTED',1,'operator',
                    'execute_web_key_scenario',json.dumps(specification,ensure_ascii=False),
                    '{}','{}','[]','','','[]',False,True])
    sheet.freeze_panes='C2';sheet.auto_filter.ref=sheet.dimensions;sheet.sheet_view.showGridLines=False
    for cell in sheet[1]:
        cell.font=Font(name='Microsoft YaHei',size=10,bold=True,color='FFFFFF')
        cell.fill=PatternFill('solid',fgColor='23466A')
        cell.alignment=Alignment(horizontal='center',vertical='center',wrap_text=True)
    sheet.row_dimensions[1].height=35
    widths=[46,42,9,23,36,18,10,14,29,80,20,20,25,18,18,20,16,16]
    for index,width in enumerate(widths,1): sheet.column_dimensions[get_column_letter(index)].width=width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.font=Font(name='Microsoft YaHei',size=10)
            cell.alignment=Alignment(vertical='center',wrap_text=True)
        row[9].fill=PatternFill('solid',fgColor='FFF1CF')
        sheet.row_dimensions[row[0].row].height=64
    book.save(OUTPUT)
    print(f'已生成独立关键流程Excel：{OUTPUT}，{sheet.max_row-1}条。')


if __name__=='__main__': main()
