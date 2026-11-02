"""Streaming XLSX output with explicit overflow, text cells, and integrity metadata."""
import hashlib
import json
import os
import re
from pathlib import Path
from openpyxl import Workbook, load_workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

MAX_ROWS = 1048576
MAX_TEXT = 30000


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def write(path, tables, max_rows=MAX_ROWS):
    book = Workbook(write_only=True)
    contents, overflow = [], []
    digest = hashlib.sha256()
    path=Path(path)
    working=path.parent
    for parent in path.parents:
        if parent.name in ('service','Результаты проверки'):
            working=parent.parent
            break
    def text_row(ws, row, headers=None):
        cells = []
        row=list(row)
        source_hash=''
        node=''
        if headers and 'SHA-256' in headers:
            source_hash=str(row[headers.index('SHA-256')] or '')
        if headers and 'Узел' in headers:
            node=str(row[headers.index('Узел')] or '')
        archive_link=os.path.relpath(working/'archive'/'xml'/f'{source_hash}.xml',path.parent) if re.fullmatch('[0-9a-f]{64}',source_hash) else None
        for value in row:
            text = '' if value is None else str(value)
            # XLSX stores UTF-16; use a conservative bound for supplementary characters.
            if len(text.encode('utf-16-le')) > 60000:
                key = hashlib.sha256(text.encode()).hexdigest()
                overflow.append((key, text))
                text = f'Полное значение: лист ДлинныеЗначения; SHA-256 {key}'
                if archive_link:text += f'; первоисточник {archive_link}; SHA-256 {source_hash}; узел {node}'
            cell = WriteOnlyCell(ws, value=text)
            cell.data_type = 's'
            if archive_link and (str(value)==source_hash or len(str(value).encode('utf-16-le'))>60000):
                cell.hyperlink=archive_link
            cell.alignment = Alignment(wrap_text=True, vertical='top')
            cells.append(cell)
        ws.append(cells)
    def sheet(title, headers):
        ws = book.create_sheet(title)
        ws.freeze_panes = 'A2'
        for i in range(1, len(headers)+1):
            ws.column_dimensions[get_column_letter(i)].width = 25
        text_row(ws, headers)
        return ws
    for logical, (headers, rows) in tables.items():
        part, count, total = 1, 1, 0
        ws = sheet(logical[:31], headers)
        for row in rows:
            if count >= max_rows:
                ws.auto_filter.ref = f'A1:{get_column_letter(len(headers))}{count}'
                contents.append([logical, ws.title, str(count-1)])
                part += 1
                ws = sheet(logical[:26] + '_' + str(part), headers)
                count = 1
            row = list(row)
            digest.update(json_text([logical, row]).encode())
            text_row(ws, row, headers)
            count += 1
            total += 1
        ws.auto_filter.ref = f'A1:{get_column_letter(len(headers))}{count}'
        contents.append([logical, ws.title, str(count-1)])
    if overflow:
        ws = sheet('ДлинныеЗначения', ['SHA-256 полного текста', 'Часть', 'Текст'])
        count, part = 1, 1
        for key, text in overflow:
            for offset in range(0,len(text),10000):
                if count >= max_rows:
                    part += 1
                    ws = sheet(f'ДлинныеЗначения_{part}', ['SHA-256 полного текста', 'Часть', 'Текст'])
                    count = 1
                text_row(ws,[key,str(offset//10000),text[offset:offset+10000]])
                count += 1
    ws = sheet('Оглавление', ['Логический лист', 'Часть', 'Количество строк'])
    for row in contents:
        text_row(ws,row)
    book.save(path)
    return digest.hexdigest()


def rows(book, logical):
    names = [logical] + sorted((n for n in book.sheetnames if n.startswith(logical[:26]+'_') and n[len(logical[:26])+1:].isdigit()),key=lambda n:int(n.rsplit('_',1)[1]))
    if logical not in book.sheetnames:
        raise ValueError(f'Отсутствует обязательный лист {logical}')
    for name in names:
        for row in book[name].iter_rows(min_row=2,values_only=True):
            if any(x is not None for x in row):
                yield list(row)
