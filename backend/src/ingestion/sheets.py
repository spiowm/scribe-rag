"""Таблиці (xlsx, старі xls і Google Sheets, експортовані в xlsx) — текстом, без моделі.

Кожна непорожня клітинка — трійка (мітка рядка, мітка колонки, значення).
Мітка колонки — з шапки з протягуванням управо (місяць над сімома днями
підписує всі сім), мітка рядка — найлівіша клітинка. Далі групування:
«записи» (рядок = запис: відповіді форм, мерч, розбори) або «сітка» (гант:
колонка = період).

У вектор іде не таблиця, а опис (`summary`): 300 рядків відповідей форми інакше
займали б топ пошуку на будь-яке схоже питання. Ціла таблиця лежить у
`documents` і віддається через `get_document`.
"""

import io
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time
from typing import Literal

import xlrd
from openpyxl import load_workbook

from src.ingestion.sheet_colors import colored_lines

MAX_ROWS = 400  # аркуш довший за це — форма на сотні відповідей, хвіст позначається
DAYS = {
    "ПН",
    "ВТ",
    "СР",
    "ЧТ",
    "ПТ",
    "СБ",
    "НД",
    "MON",
    "TUE",
    "WED",
    "THU",
    "FRI",
    "SAT",
    "SUN",
}
HEAD = "## Аркуш"
# Менша таблиця йде у вектор цілою: опис її не скоротив би.
SMALL = 1500


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return (
            value.date().isoformat()
            if value.time() == time()
            else value.isoformat(" ", "minutes")
        )
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _number(text: str) -> bool:
    return text.replace(".", "", 1).replace(",", "", 1).isdigit()


def _header_row(rows: list[list[str]]) -> int | None:
    """Перший рядок, де щонайменше дві непорожні нечислові клітинки."""
    for i, r in enumerate(rows[:12]):
        if sum(1 for c in r if c and not _number(c)) >= 2:
            return i
    return None


def read_sheet(values: Iterable[Sequence]) -> dict | None:
    """`values` — рядки аркуша значеннями (`iter_rows(values_only=True)`)."""
    rows: list[list[str]] = []
    total = 0  # непорожніх: відформатовані порожні рядки xlsx теж віддає
    for r in values:
        cells = [_cell(c) for c in r]
        if not any(cells):
            if len(rows) < MAX_ROWS:
                rows.append(cells)
            continue
        total += 1
        if len(rows) < MAX_ROWS:
            rows.append(cells)
    while rows and not any(rows[-1]):
        rows.pop()
    if not rows:
        return None
    h = _header_row(rows)
    if h is None:
        return None
    width = max(len(r) for r in rows)
    labels, last = [], ""
    for i in range(width):
        cell = rows[h][i] if i < len(rows[h]) else ""
        last = cell or last
        labels.append(last)

    body = [r for r in rows[h + 1 :] if any(r)]
    total -= sum(1 for r in rows[: h + 1] if any(r))
    dense_left = sum(1 for r in body if r and r[0]) / max(len(body), 1)
    shape = "записи" if dense_left >= 0.6 else "сітка"

    lines: list[str] = []
    if shape == "записи":
        for r in body:
            label = r[0] if r and r[0] else ""
            parts = [
                f"{labels[i]}: {v}" if labels[i] else v
                for i, v in enumerate(r)
                if v and not (i == 0 and label)
            ]
            if label or parts:
                lines.append((f"{label} — " if label else "") + " · ".join(parts))
    else:
        # У сітці числа й дні тижня — це номери днів у шапці ганта, не зміст.
        buckets: dict[str, list[str]] = {}
        for r in body:
            for i, v in enumerate(r):
                if not v or v.upper() in DAYS or _number(v):
                    continue
                col = labels[i] if i < len(labels) and labels[i] else "(без мітки)"
                if v not in buckets.setdefault(col, []):
                    buckets[col].append(v)
        lines = [f"{col}: {', '.join(vals)}" for col, vals in buckets.items()]

    columns = list(dict.fromkeys(c[:60] for c in labels if c))
    return {
        "rows": max(total, 0),
        "shown": len(body),
        "columns": columns,
        "lines": lines,
    }


def _book_text(
    sheets: Iterable[tuple[str, Iterable[Sequence], list[str]]],
) -> str:
    """Аркуші: назва, рядки значеннями й рядки з кольорів (`sheet_colors`)."""
    blocks: list[str] = []
    for name, values, colored in sheets:
        got = read_sheet(values)
        if not got or not got["lines"]:
            continue
        cut = (
            f" (показано перші {got['shown']})"
            if got["rows"] > got["shown"] + 1
            else ""
        )
        head = (
            f"{HEAD} «{name}» · рядків: {got['rows']}{cut} · "
            f"колонки: {', '.join(got['columns'][:20])}"
        )
        blocks.append(head + "\n" + "\n".join(got["lines"] + colored))
    return "\n\n".join(blocks)


def sheet_text(blob: bytes) -> tuple[str, int]:
    """Усі аркуші книги текстом. Сторінок нуль — OCR тут не застосовний.

    Не `read_only`: у ньому немає обʼєднаних клітинок, а без них кольори
    календаря не прочитати.
    """
    wb = load_workbook(io.BytesIO(blob), data_only=True)
    try:
        text = _book_text(
            (ws.title, ws.iter_rows(values_only=True), colored_lines(ws))
            for ws in wb.worksheets
        )
    finally:
        wb.close()
    return text, 0


def _xls_value(cell: xlrd.sheet.Cell, datemode: Literal[0, 1]):
    """Клітинка xls як значення openpyxl: дата — datetime, порожня й помилка — None."""
    if cell.ctype == xlrd.XL_CELL_DATE:
        number = float(cell.value)
        try:
            moment = xlrd.xldate_as_datetime(number, datemode)
        except OverflowError:  # число з форматом дати, але поза календарем
            return number
        # Сам час без дати (розклад по 15 хвилин) — менше доби від нуля Excel.
        return moment.time() if number < 1 else moment
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
        return None
    return cell.value


def xls_text(blob: bytes) -> tuple[str, int]:
    """Старий Excel (.xls, BIFF) — тим самим читачем, що й xlsx.

    openpyxl його не відкриває, а xlrd читає сам, без конвертера, і кодову
    сторінку (cp1251 у файлах 2007–2010) бере з самого файла.
    """
    book = xlrd.open_workbook(file_contents=blob)
    datemode = 1 if book.datemode else 0  # відлік дат від 1904 (старі Mac) чи 1900
    text = _book_text(
        (
            sh.name,
            ([_xls_value(c, datemode) for c in sh.row(i)] for i in range(sh.nrows)),
            [],
        )
        for sh in book.sheets()
    )
    return text, 0


def table_summary(
    text: str, title: str = "", path: str = "", sample: int = 1, width: int = 200
) -> str | None:
    """Що йде у вектор замість таблиці: назва й шлях, шапки аркушів і рядок-приклад.

    Назва й шлях — у самому тексті, хоч вони є й у метаданих фрагмента: інакше
    вектор опису визначають переліки з рядків (позиції закупівель, імена), і
    «бюджет BEST Beat Course 2021» не знаходив `Бюджет.xlsx` з теки курсу.
    Один рядок-приклад показує структуру даних, не перевантажуючи опис
    випадковими значеннями з таблиці.

    None — таблиця мала й іде у вектор цілою.
    """
    if len(text) <= SMALL:
        return None
    name = title.removesuffix(".xlsx").removesuffix(".xls")
    head = "Таблиця" + (f" «{name}»" if title else "")
    out = [head + (f" · {path}" if path else "") + ". Повний вміст — get_document."]
    for block in text.split("\n\n" + HEAD):
        lines = block.removeprefix(HEAD).splitlines()
        if not lines:
            continue
        out.append(HEAD + lines[0])
        out += [line[:width] for line in lines[1 : 1 + sample]]
    return "\n".join(out)
