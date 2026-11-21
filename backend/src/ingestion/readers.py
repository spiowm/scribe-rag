"""Читачі файлів Drive: як дістати байти й текст для кожного типу.

Реєстр і є воротами інжестії: що в ньому немає, синк навіть не бачить —
запит до Drive просить лише ці типи. Додати тип = додати запис сюди.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from src.connectors.gdrive import GDriveConnector
from src.ingestion.extractors import (
    doc_text,
    docx_text,
    markdown_text,
    pdf_text,
    ppt_text,
    pptx_text,
)
from src.ingestion.sheets import sheet_text, xls_text

PDF = "application/pdf"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
GDOC = "application/vnd.google-apps.document"
GSLIDES = "application/vnd.google-apps.presentation"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
GSHEET = "application/vnd.google-apps.spreadsheet"
# Старі бінарні формати Office (до 2007).
DOC = "application/msword"
XLS = "application/vnd.ms-excel"
PPT = "application/vnd.ms-powerpoint"

MAX_BYTES = 100 * 1024 * 1024
# Менше символів на сторінку — значить текст у картинках, і його читає OCR.
# Було 400; на 4 дисках 3 335 із 6 278 сторінок під OCR виявились слайдами з
# уже витягнутим текстом (100–400), а пілот BTW узагалі без OCR дав 18/18 (F13).
OCR_DENSITY = 100


@dataclass(frozen=True)
class Reader:
    # Рідні файли Google не завантажуються, а експортуються в цей тип.
    export_as: str | None
    # (байти) -> (текст, сторінок). Нуль сторінок — OCR не застосовний.
    # Синхронний і часто важкий (pdfium, python-pptx), тому завжди в потоці.
    extract: Callable[[bytes], tuple[str, int]]
    # Тип на випадок, коли Drive відмовляє експортувати `export_as`: понад 10 МБ
    # він не віддає (`exportSizeLimitExceeded`). Markdown вбудовує картинки, тож
    # на EBEC так губились 17 звітів для компаній по 10–19 МБ, а їхній
    # text/plain — кілька КБ.
    export_fallback: str | None = None

    def blob_mime(self, mime: str) -> str:
        """Тип байтів, які дістануться читачу, — він же йде в OCR."""
        return self.export_as or mime

    async def fetch(self, gdrive: GDriveConnector, file_id: str) -> bytes:
        if not self.export_as:
            return await gdrive.download(file_id)
        try:
            return await gdrive.export(file_id, self.export_as)
        except httpx.HTTPStatusError as e:
            if not (self.export_fallback and _too_large(e.response)):
                raise
            return await gdrive.export(file_id, self.export_fallback)

    async def read(self, blob: bytes) -> tuple[str, int]:
        return await asyncio.to_thread(self.extract, blob)


READERS: dict[str, Reader] = {
    PDF: Reader(export_as=None, extract=pdf_text),
    PPTX: Reader(export_as=None, extract=pptx_text),
    GDOC: Reader(
        export_as="text/markdown", extract=markdown_text, export_fallback="text/plain"
    ),
    # Слайди Google — через PDF: так їх читає той самий pdfium, і OCR отримує PDF.
    GSLIDES: Reader(export_as=PDF, extract=pdf_text),
    DOCX: Reader(export_as=None, extract=docx_text),
    XLSX: Reader(export_as=None, extract=sheet_text),
    # xlsx, а не CSV: CSV-експорт віддає лише перший аркуш.
    GSHEET: Reader(export_as=XLSX, extract=sheet_text),
    DOC: Reader(export_as=None, extract=doc_text),
    XLS: Reader(export_as=None, extract=xls_text),
    PPT: Reader(export_as=None, extract=ppt_text),
}


def _too_large(response: httpx.Response) -> bool:
    return response.status_code == 403 and "exportSizeLimitExceeded" in response.text


def readable(f: dict) -> bool:
    # `~$…` — файл блокування, який Office лишає поруч із відкритим документом
    # (162 байти, тексту немає): на робочих дисках це 1 з 5 старих файлів.
    return (
        f.get("mimeType") in READERS
        and int(f.get("size", 0)) <= MAX_BYTES
        and not f.get("name", "").startswith("~$")
    )


# Більше Gemini вбудованими даними не приймає (100 МБ у base64, для PDF — 50 МБ),
# тож такий запит лише годинами вивантажувався б і падав. На BTW це подяки й
# постери по 59–95 МБ; великі змістовні файли (пропоузали, SAAR) мають текстовий
# шар і в OCR не йдуть узагалі.
OCR_MAX_BYTES = 50 * 1024 * 1024


def needs_ocr(text: str, pages: int, size: int = 0) -> bool:
    return bool(pages) and len(text) / pages < OCR_DENSITY and size <= OCR_MAX_BYTES
