"""Старі .doc/.xls/.ppt.

Файли в `fixtures/` синтетичні: docx/xlsx/pptx із python-бібліотек, переведені
`soffice --convert-to` у старі формати. Перевіряється збереження абзаців,
рядків таблиць, дат, слайдів і нотаток доповідача.
"""

import shutil
from pathlib import Path

import pytest

from src.ingestion.extractors import doc_text, ppt_text
from src.ingestion.readers import DOC, readable
from src.ingestion.sheets import table_summary, xls_text

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.skipif(not shutil.which("antiword"), reason="немає antiword")
def test_doc_paragraphs_and_table_rows():
    text, pages = doc_text((FIXTURES / "legacy.doc").read_bytes())
    assert pages == 0
    lines = text.splitlines()
    assert lines[0] == "Інженерний ярмарок кар’єри 2010: розклад середи"
    # рядок таблиці без вирівнювальних пробілів antiword
    assert "209а | Презентація компанії" in lines
    assert "Відповідальна — Оля, ґанок і їжак на місці." in lines


@pytest.mark.skipif(not shutil.which("antiword"), reason="немає antiword")
def test_doc_not_word_raises():
    with pytest.raises(ValueError, match="antiword"):
        doc_text(b"\x00" * 162)


def test_xls_same_reader_as_xlsx():
    text, pages = xls_text((FIXTURES / "legacy.xls").read_bytes())
    assert pages == 0
    assert (
        "## Аркуш «Закупівля» · рядків: 2 · колонки: Товар, Кількість, Коли, Початок"
        in text
    )
    # дата без часу — датою, сам час (розклад) — без 1899-12-31
    assert "стікерпак — Кількість: 35 · Коли: 2010-03-14 · Початок: 10:15:00" in text


def test_xls_title_without_extension():
    text = xls_text((FIXTURES / "legacy.xls").read_bytes())[0] * 20
    summary = table_summary(text, "Закупівля.xls")
    assert summary and summary.startswith("Таблиця «Закупівля». Повний вміст")


def test_ppt_slides_and_notes_without_masters():
    text, pages = ppt_text((FIXTURES / "legacy.ppt").read_bytes())
    assert pages == 0
    assert text.splitlines() == [
        "BEST Lviv – це…",
        "Перший в Україні осередок BEST (з 2002 р.)",
        "Мотивація членів",
        "Good team atmosphere",
        "Нотатка доповідача: розповісти про ІЯК",
    ]


def test_office_lock_files_skipped():
    assert readable({"mimeType": DOC, "name": "Звіт.doc", "size": "30000"})
    assert not readable({"mimeType": DOC, "name": "~$Звіт.doc", "size": "162"})
