"""Render compact structural previews of all seven data tabs, without editing Excel."""
from pathlib import Path
import argparse
import textwrap
from openpyxl import load_workbook
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'reports/web_full_workbook_preview_20261013.png')
    parser.add_argument('--scenarios',nargs='*',help='仅预览指定场景的功能用例，不修改工作簿')
    args=parser.parse_args()
    path=ROOT/'tests/web_e2e/data/web_e2e_cases.xlsx'
    book=load_workbook(path,read_only=True,data_only=True)
    image=Image.new('RGB',(1600,2500),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',17)
    title=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',22)
    choices={'功能用例':['场景名称','模块','入会身份','会议模板','执行级别','用例状态'],
        '终端配置':['配置ID','终端类型','账号引用','环境引用','入会身份'],
        '参数域':['参数域ID','正常值JSON','边界契约','来源'],
        '依赖流程':['步骤说明','步骤序号','执行端','统一操作名','参数JSON'],
        'FM复用审计':['操作','分类','依据'],
        'Conf信令检查':['定义序号','cmdid','协议说明','Web适用性','缺口原因'],
        '业务方法检查':['场景','模块','证据类型','接口缺口']}
    y=20
    for sheet in book:
        if args.scenarios and sheet.title!='功能用例':continue
        rows=sheet.iter_rows(values_only=True);headers=list(next(rows));names=choices[sheet.title]
        if args.scenarios:rows=[row for row in rows if row[headers.index('统一操作名')] in args.scenarios]
        limit=len(rows) if args.scenarios else 3
        label=f'选定场景{limit}行' if args.scenarios else f'{sheet.max_row-1}行；仅预览前三行'
        draw.text((22,y),f'{sheet.title}（{label}，长值省略）',font=title,fill='#203B60');y+=38
        width=1550//len(names)
        for col,name in enumerate(names):
            draw.rectangle((20+col*width,y,20+(col+1)*width,y+36),fill='#23466A')
            draw.text((27+col*width,y+6),name,font=font,fill='white')
        y+=36
        for index,row in enumerate(rows):
            if index>=limit:break
            for col,name in enumerate(names):
                text=str(row[headers.index(name)] or '')
                lines=textwrap.wrap(text,width=max(8,(width-18)//17))[:3]
                if len(text)>sum(len(x) for x in lines) and lines:lines[-1]=lines[-1][:-1]+'…'
                draw.rectangle((20+col*width,y,20+(col+1)*width,y+76),fill='#F1F5FA' if index%2==0 else 'white',outline='#CDD6E2')
                draw.multiline_text((27+col*width,y+7),'\n'.join(lines),font=font,fill='#172033',spacing=3)
            y+=76
        y+=28
    destination=args.output
    image.crop((0,0,1600,min(y+10,2500))).save(destination)
    book.close();print(destination)


if __name__=='__main__':main()
