import io
import re
import struct
import subprocess
import threading

import docx
import olefile
import pypdfium2
from docx.table import Table as DocxTable
from google import genai
from google.genai import types
from pptx import Presentation

_PDFIUM_LOCK = threading.Lock()
_BASE64_IMG = re.compile(r"data:[^;]+;base64,[A-Za-z0-9+/=]+")


def pdf_text(blob: bytes) -> tuple[str, int]:
    with _PDFIUM_LOCK:
        doc = pypdfium2.PdfDocument(blob)
        try:
            pages = len(doc)
            parts = []
            for page in doc:
                textpage = page.get_textpage()
                try:
                    parts.append(textpage.get_text_bounded())
                finally:
                    textpage.close()
                    page.close()
            text = "\n".join(parts)
        finally:
            doc.close()
    return text, pages


def markdown_text(blob: bytes) -> tuple[str, int]:
    """Google Doc, експортований у text/markdown (або text/plain, якщо великий)."""
    # text/plain починається з BOM
    text = blob.decode("utf-8-sig", errors="replace")
    text = _BASE64_IMG.sub("", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    return text.strip(), 0


def pptx_text(blob: bytes) -> tuple[str, int]:
    """Текст і кількість слайдів."""
    prs = Presentation(io.BytesIO(blob))
    slides_text: list[str] = []

    for i, slide in enumerate(prs.slides, 1):
        parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                tf = getattr(shape, "text_frame", None)
                if tf and tf.text.strip():
                    parts.append(tf.text.strip())
            elif shape.has_table:
                table = getattr(shape, "table", None)
                if table:
                    for row in table.rows:
                        row_str = " | ".join(cell.text.strip() for cell in row.cells)
                        if row_str.strip():
                            parts.append(row_str)

        if slide.has_notes_slide:
            nts = getattr(slide.notes_slide.notes_text_frame, "text", None)
            if nts and nts.strip():
                parts.append(nts.strip())

        if parts:
            slides_text.append(f"[слайд {i}] " + "\n".join(parts))

    return "\n\n".join(slides_text), len(prs.slides)


def docx_text(blob: bytes) -> tuple[str, int]:
    """Абзаци й таблиці у тому порядку, як вони стоять у документі.

    `doc.paragraphs` і `doc.tables` окремо губили б, до якого абзацу таблиця.
    Сторінок у docx немає — нуль, тож OCR тут не застосовний.
    """
    doc = docx.Document(io.BytesIO(blob))
    parts: list[str] = []
    for block in doc.iter_inner_content():
        if isinstance(block, DocxTable):
            for row in block.rows:
                cells = [c.text.strip() for c in row.cells]
                # обʼєднана клітинка повторюється в кожній колонці, яку займає
                cells = [c for i, c in enumerate(cells) if i == 0 or c != cells[i - 1]]
                if any(cells):
                    parts.append(" | ".join(cells))
        elif text := block.text.strip():
            parts.append(text)
    return "\n".join(parts), 0


def doc_text(blob: bytes) -> tuple[str, int]:
    """Старий Word (.doc) через antiword: python-docx його не відкриває.

    `-w 0` — абзац одним рядком, без переносу за шириною. Рядки таблиць antiword
    вирівнює пробілами (`|Назва      |Значення    |`), тут вони стають `Назва | Значення`,
    як у `docx_text`. Не-Word, який Drive теж позначає application/msword (RTF,
    перейменований ODT — на 253 старих файлах дисків по одному), antiword не
    читає — виняток, файл іде в `failed`.
    """
    done = subprocess.run(
        ["antiword", "-m", "UTF-8.txt", "-w", "0", "-"],
        check=False,
        input=blob,
        capture_output=True,
        timeout=60,
    )
    if done.returncode:
        raise ValueError(f"antiword: {done.stderr.decode(errors='replace').strip()}")
    lines: list[str] = []
    for line in done.stdout.decode("utf-8", errors="replace").splitlines():
        line = line.replace("[pic]", "").strip()
        if line.startswith("|") and line.endswith("|"):
            line = " | ".join(
                c.strip() for c in line.strip("|").split("|") if c.strip()
            )
        if line:
            lines.append(line)
    return "\n".join(lines), 0


# Записи потоку `PowerPoint Document` ([MS-PPT] 2.3.1): тип і довжина вмісту.
_PPT_TEXT_CHARS = 0x0FA0  # TextCharsAtom, UTF-16LE
_PPT_TEXT_BYTES = 0x0FA8  # TextBytesAtom, байт = символ U+0000–U+00FF
# Шаблони з текстом-заглушкою («Click to edit Master text styles», «Образец
# текста»): шаблон слайдів, шаблон роздруківки й шаблон нотаток — останній
# той самий NotesContainer, що й нотатки слайда, але NotesAtom у нього
# посилається не на слайд: 0 за специфікацією, а у файлах на дисках — на
# ідентифікатор шаблону (від 0x80000000, як в обох .ppt вибірки).
_PPT_MASTERS = {0x03F8, 0x0FC9}
_PPT_NOTES, _PPT_NOTES_ATOM, _PPT_MASTER_ID = 0x03F0, 0x03F1, 0x80000000


def _ppt_master(data: bytes, i: int, kind: int) -> bool:
    if kind in _PPT_MASTERS:
        return True
    if kind != _PPT_NOTES or i + 12 > len(data):
        return False
    _, atom, _, slide = struct.unpack_from("<HHII", data, i)
    return atom == _PPT_NOTES_ATOM and (slide == 0 or slide >= _PPT_MASTER_ID)


def _ppt_records(data: bytes, start: int, end: int, parts: list[str]) -> bool:
    """Тексти записів у [start, end) → `parts`. False — записи не вкладаються в межі.

    Ознака контейнера (молодші 4 біти = 0xF) буває й у записів, усередині яких
    не записи, а дані (RoundTrip-атоми з OOXML у презентації EJF 2010): такий
    «контейнер» не вкладається у власні межі, і його тексти відкидаються цілими.
    """
    i = start
    while i + 8 <= end:
        ver, kind, size = struct.unpack_from("<HHI", data, i)
        body, i = i + 8, i + 8 + size
        if i > end:
            return False
        if ver & 0xF == 0xF:
            inner: list[str] = []
            if not _ppt_master(data, body, kind) and _ppt_records(data, body, i, inner):
                parts += inner
        elif kind == _PPT_TEXT_CHARS:
            parts.append(data[body:i].decode("utf-16-le", errors="replace"))
        elif kind == _PPT_TEXT_BYTES:
            parts.append(data[body:i].decode("latin-1"))
    return True


def ppt_text(blob: bytes) -> tuple[str, int]:
    """Стара PowerPoint (.ppt): текст слайдів і нотаток з потоку `PowerPoint Document`.

    Готового легкого читача немає: catppt (пакет catdoc) на презентації BTW 2013
    віддав лише «Office-téma». Формат простий — записи з заголовком 8 байт,
    контейнери містять записи, шаблони пропускаються цілими. Сторінок нуль:
    Gemini .ppt не приймає, OCR тут не застосовний.
    """
    with olefile.OleFileIO(io.BytesIO(blob)) as ole:
        data = ole.openstream("PowerPoint Document").read()
    parts: list[str] = []
    # хвіст потоку буває сміттям після останнього запису — зібране до нього лишається
    _ppt_records(data, 0, len(data), parts)
    # \r — розрив абзацу всередині текстового блоку, \x0b — розрив рядка;
    # `*` — поле номера слайда
    text = "\n".join(p.strip() for p in parts if p.strip() not in ("", "*"))
    return re.sub(r"[\r\x0b]+", "\n", text), 0


EXTRACTION_PROMPT = """
Витягни з цього документа увесь текст: заголовки, абзаци, списки, підписи, текст на схемах і в таблицях. 
Зберігай порядок і структуру, таблиці подавай рядками через |. 
Не переказуй, не коментуй, не додавай вступів — лише текст документа."""


async def gemini_text(
    client: genai.Client,
    model: str,
    blob: bytes,
    mime: str = "application/pdf",
) -> str:

    resp = await client.aio.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=blob, mime_type=mime), EXTRACTION_PROMPT],
        # Переписати текст зі сторінки роздумів не потребує, а платилось саме за
        # них: на 16 сторінках $0.00156/стор без обмеження проти $0.00070 з LOW,
        # той самий текст (схожість 0.96–1.00). MINIMAL Flash не приймає (400).
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
        ),
    )
    candidate = resp.candidates[0] if resp.candidates else None
    if candidate is None or not resp.text:
        raise ValueError(
            f"gemini не віддав текст: finish_reason={candidate.finish_reason if candidate else None}"
        )
    return resp.text
