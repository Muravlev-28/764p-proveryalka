"""Lossless text cells and explicit validation of client input."""
from pathlib import Path
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font


def read_clients(path, sheet, columns, header_row=3, extra_columns=None):
    book = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    try:
        ws = book[sheet]
        headers = [cell.value for cell in next(ws.iter_rows(min_row=header_row, max_row=header_row))]
        indexes = []
        for name in columns:
            if headers.count(name) != 1:
                raise ValueError(f'Заголовок отсутствует или неоднозначен: {name}')
            indexes.append(headers.index(name))
        records, skipped = [], 0
        for row_number, row in enumerate(ws.iter_rows(min_row=header_row + 1), header_row + 1):
            if all(cell.value is None for cell in row):
                skipped += 1
                continue
            values, issues = [], []
            for i in indexes:
                cell = row[i]
                if cell.data_type == 'f':
                    issues.append('Формула: требуется сохранённое текстовое значение')
                elif cell.data_type == 'e':
                    issues.append('Ошибка Excel')
                elif cell.value is not None:
                    values.append(str(cell.value))
            if not values:
                issues.append('Отсутствует имя или наименование')
            raw = {str(headers[i] or f'Колонка {i+1}'): (str(c.value) if c.value is not None else '') for i,c in enumerate(row) if i < len(headers)}
            aliases = {'inn': ('ИНН','ИНН клиента'), 'birth': ('Дата рождения','ДатаРождения'), 'id': ('IDКлиента','ID клиента'), 'type': ('Тип лица','ТипЛица')}
            extra = {key: next((raw[h] for h in names if raw.get(h)), '') for key,names in aliases.items()}
            for key,heading in (extra_columns or {}).items():
                if heading:
                    if headers.count(heading)!=1:raise ValueError(f'Неоднозначный дополнительный заголовок: {heading}')
                    extra[key]=raw.get(heading,'')
            records.append(dict(row=row_number, values=values, issues=issues, raw=raw, **extra))
        return records, skipped
    finally:
        book.close()


def write_book(path, sheets, max_rows=1048576):
    """Split sheets at row limit. Refuse oversized cells; never silently truncate."""
    book = Workbook()
    book.remove(book.active)
    contents = []
    for title, (headers, rows) in sheets.items():
        part = 0
        ws = None
        for row in rows:
            if ws is None or ws.max_row >= max_rows:
                part += 1
                name = title[:26] + (f'_{part}' if part > 1 else '')
                ws = book.create_sheet(name)
                ws.append(headers)
                ws.freeze_panes = 'A2'
                contents.append([title, name])
            for col, value in enumerate(row, 1):
                text = '' if value is None else str(value)
                if len(text) > 32767:
                    raise ValueError('Превышен лимит ячейки XLSX; требуется ссылка на архивный XML')
                cell = ws.cell(ws.max_row + 1 if col == 1 else target_row, col)
                target_row = cell.row
                cell.value, cell.data_type = text, 's'
                cell.alignment = Alignment(wrap_text=True, vertical='top')
        if ws is None:
            ws = book.create_sheet(title[:31])
            ws.append(headers)
            ws.freeze_panes = 'A2'
            contents.append([title, ws.title])
    toc = book.create_sheet('Оглавление', 0)
    toc.append(['Логический лист', 'Часть'])
    for row in contents:
        toc.append(row)
    for ws in book:
        ws.auto_filter.ref = ws.dimensions
        ws.freeze_panes = 'A2'
        for cell in ws[1]:
            cell.font = Font(bold=True)
            ws.column_dimensions[cell.column_letter].width = 28
    book.save(path)


def template(path):
    book = Workbook()
    ws = book.active
    ws.title = 'Клиенты'
    ws.append(['Синтетический шаблон; замените примеры своими данными'])
    ws.append([])
    ws.append(['IDКлиента', 'Наименование полное'])
    ws.append(['TEST-001', 'Примеров Тест Тестович'])
    ws.append(['TEST-002', 'ООО Синтетический пример'])
    book.save(path)
