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
