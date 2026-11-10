import io
from datetime import datetime

from openpyxl import Workbook

from src.ingestion.sheets import sheet_text, table_summary


def book(rows, extra_empty=0) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Форма"
    for r in rows:
        ws.append(r)
    for i in range(extra_empty):
        ws.cell(row=len(rows) + 1 + i, column=1).number_format = "0.00"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_records_keep_numbers_and_dates():
    text, pages = sheet_text(
        book(
            [
                ["Товар", "Кількість", "Ціна", "Коли"],
                ["стікерпак", 35, 21.25, datetime(2025, 7, 14)],
                ["худі", 10, 450.0, datetime(2025, 7, 15, 12, 30)],
            ]
        )
    )
    assert pages == 0
    assert (
        "## Аркуш «Форма» · рядків: 2 · колонки: Товар, Кількість, Ціна, Коли" in text
    )
    assert "стікерпак — Кількість: 35 · Ціна: 21.25 · Коли: 2025-07-14" in text
    assert "худі — Кількість: 10 · Ціна: 450 · Коли: 2025-07-15 12:30" in text


def test_empty_formatted_rows_not_counted():
    text, _ = sheet_text(book([["Ім'я", "Роль"], ["Оля", "HR"]], extra_empty=500))
    assert "рядків: 1 ·" in text and "показано" not in text


def test_summary_only_for_big_tables():
    small, _ = sheet_text(book([["Ім'я", "Роль"], ["Оля", "HR"]]))
    assert table_summary(small) is None
    rows = [["Ім'я", "Відповідь"]] + [
        [f"людина {i}", "довга відповідь " * 5] for i in range(100)
    ]
    big, _ = sheet_text(book(rows))
    summary = table_summary(big)
    assert summary is not None and len(summary) < len(big) / 5
    assert summary.startswith("Таблиця. Повний вміст — get_document.")
    assert (
        "рядків: 100" in summary
        and "людина 0" in summary
        and "людина 50" not in summary
    )
    # назва й шлях — у тексті опису, а не лише в метаданих
    named = table_summary(big, "Бюджет.xlsx", "Courses/[2021] Summer/Бюджети")
    assert named is not None and named.startswith(
        "Таблиця «Бюджет» · Courses/[2021] Summer/Бюджети. Повний вміст"
    )


def colored_book(fill_cells: dict[str, str], values: dict, merge=()):
    from openpyxl.styles import PatternFill

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "План"
    for ref, v in values.items():
        ws[ref] = v
    for ref, color in fill_cells.items():
        ws[ref].fill = PatternFill("solid", fgColor=color)
    for rng in merge:
        ws.merge_cells(rng)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_gantt_bar_becomes_dates():
    orange, head = "FFFF9900", "FFB6D7A8"
    values = {"A1": "Задачі", "A3": "Контент план", "A4": "Брошура", "A5": "Фото"}
    fills = {}
    for i, col in enumerate("BCDEF"):
        values[f"{col}1"] = ["пн", "вт", "ср", "чт", "пт"][i]
        values[f"{col}2"] = f"{13 + i}.05"
        fills[f"{col}1"] = fills[f"{col}2"] = head
    fills |= {"B3": orange, "C3": orange, "D3": orange, "F4": orange}
    text, _ = sheet_text(colored_book(fills, values))
    colored = text.split("Позначено кольором клітинок:\n")[1].splitlines()
    assert colored == ["Контент план — 13.05 – 15.05", "Брошура — 17.05"]


def test_calendar_day_takes_legend_by_color():
    green, red = "FF6AA84F", "FFFF0000"
    values = {"A1": "Грудень'26", "A2": "ПН", "B2": "ВТ", "C2": "СР"}
    values |= {"A3": 7, "B3": 8, "C3": 9, "A4": 14, "B4": 15, "C4": 16}
    values |= {"A6": "Вибори MO BCI", "A7": "Різдвяна вечірка"}
    fills = {"C3": green, "A6": green, "B4": red, "C4": red, "A7": red}
    text, _ = sheet_text(colored_book(fills, values, merge=["A1:C1"]))
    assert "Вибори MO BCI — Грудень'26 9" in text
    assert "Різдвяна вечірка — Грудень'26 15–16" in text


def test_colors_without_dates_add_nothing():
    yellow = "FFFFFF00"
    values = {"A1": "Товар", "B1": "Ціна", "C1": "Разом"}
    values |= {"A2": "худі", "B2": 450, "A3": "Загальна вартість", "C3": 763.5}
    fills = {"A3": yellow, "B3": yellow, "C3": yellow, "B2": yellow}
    text, _ = sheet_text(colored_book(fills, values))
    assert "кольором" not in text
