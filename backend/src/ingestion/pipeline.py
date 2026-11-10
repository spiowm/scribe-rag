"""Синк у два кроки: джерело → `documents` → Qdrant.

Крок 1 (`extract_*`) — усе дороге й залежне від джерела: завантаження,
читачі, OCR, маскування. Результат — дзеркало тексту в Постгресі, яке й так
потрібне `get_document`. Крок 2 (`index`) — лише нарізка й ембединг із цього
дзеркала. Звідси:

- повна переіндексація (`full`) — це крок 2 з нуля: OCR удруге не платиться;
- файл без тексту лежить у `documents` порожнім, тож не переобробляється
  щосинку, як було, коли стан брався лише з Qdrant;
- стан не залежить від тому Qdrant.
"""

import asyncio
import logging
import unicodedata
from dataclasses import dataclass, field

from google import genai
from llama_index.core.schema import TextNode
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.connectors.gdrive import GDriveConnector
from src.connectors.notion import NotionConnector
from src.ingestion import notion_db, privacy
from src.ingestion.chunker import Chunker
from src.ingestion.extractors import gemini_text
from src.ingestion.readers import READERS, needs_ocr, readable
from src.ingestion.schemas import GENERAL, ChunkMetadata
from src.ingestion.sheets import HEAD, table_summary
from src.repository import documents
from src.vectorstore.qdrant import QdrantRepository

logger = logging.getLogger(__name__)

# Колекція одна, а reindex копіює з поточної все, крім застарілого, і перемикає
# аліас. Два синки разом копіювали б з однієї старої, і зміни одного зникли б
# (G1). Воркер один, тож досить блокування в памʼяті процесу.
SYNC_LOCK = asyncio.Lock()

# Запит до Gemini без межі може не повернутись ніколи: на EBEC синк простояв
# так 12+ хв на одному PDF. Після межі файл лишається з текстом читача.
OCR_TIMEOUT = 300


@dataclass
class Extracted:
    found: int = 0
    changed: int = 0
    failed: int = 0
    removed: int = 0
    ocr_failed: int = 0
    masked: int = 0
    skipped_secret: int = 0
    rows: list[dict] = field(default_factory=list, repr=False)


def nfc(text: str) -> str:
    """Частина назв на дисках — у формі NFD: «й» = «и» + окремий знак (U+0306).
    Така назва не збігається з тим самим словом, набраним звичайно, ні в пошуку за
    точним словом, ні в порівнянні рядків. NFC зводить усе до однієї форми."""
    return unicodedata.normalize("NFC", text)


def _stale(known: dict[str, str], fresh: dict[str, str]) -> set[str]:
    return {sid for sid, edited in fresh.items() if known.get(sid) != edited}


async def notion_tables(notion: NotionConnector) -> tuple[list[dict], int]:
    """Документи баз із `NOTION_DATABASES` і скільки замасковано.

    Після `fetch_all_pages`: relation розв'язуються в назви з його сторінок.
    Помилка будь-якої бази валить синк, інакше її частини виглядали б
    зниклими й видалились би.
    """
    titles = {pid: notion_db.page_title(p) for pid, p in notion.raw_pages.items()}
    docs: list[dict] = []
    masked = 0
    for database_id, split_by in settings.NOTION_DATABASES:
        try:
            database, rows, path = await notion.fetch_database(database_id)
        except Exception as e:
            raise RuntimeError(f"Notion не віддав базу {database_id}: {e}") from e
        titles.update({r["id"]: notion_db.page_title(r) for r in rows})
        got, hits = notion_db.database_documents(database, rows, titles, split_by, path)
        docs += got
        masked += hits
    return docs, masked


async def extract_notion(
    notion: NotionConnector, db: AsyncSession, everything: bool = False
) -> Extracted:
    pages = await notion.fetch_all_pages()
    if not pages:
        raise RuntimeError("Notion не віддав жодної сторінки")
    tables, table_hits = await notion_tables(notion)
    known = {
        sid: m.last_edited for sid, m in (await documents.meta(db, "notion")).items()
    }
    fresh = {p.id: p.last_edited for p in pages}
    stale = set(fresh) if everything else _stale(known, fresh)

    to_fetch = [p for p in pages if p.id in stale]
    await notion.resolve_paths(to_fetch)
    failed = await notion.fetch_pages_content(to_fetch)

    out = Extracted(found=len(pages) + len(tables), failed=failed, masked=table_hits)
    for page in to_fetch:
        if page.content is None:
            continue  # не завантажилась — лишається попередня версія
        text, hits = privacy.mask(page.content)
        out.masked += hits
        out.rows.append(
            {
                "source_id": page.id,
                "source": "notion",
                "title": page.title,
                "path": page.path,
                "url": page.url,
                "last_edited": page.last_edited,
                "text": text,
                "scope": GENERAL,
                "summary": None,
            }
        )
    # Бази перезаписуються щосинку: їхні рядки й так прочитані всі, а видалений
    # рядок не зсуває найпізнішої правки частини. Опис у векторі оновиться
    # за `last_edited`, тобто з першою ж правкою іншого рядка.
    out.changed = len(out.rows) + sum(
        1 for t in tables if known.get(t["source_id"]) != t["last_edited"]
    )
    out.rows += tables
    await documents.upsert_documents(db, out.rows)
    alive = set(fresh) | {t["source_id"] for t in tables}
    out.removed = await documents.delete_documents(db, set(known) - alive)
    return out


async def gdrive_targets(gdrive: GDriveConnector) -> list[dict]:
    """Файли всіх джерел Drive з `scope`: загальний диск і диски з конфігу.

    Шлях файлів робочого диска починається з назви диска (`BTW/2023 …/MO`):
    без неї однакові теки різних дисків (`2024/HR`) було б не розрізнити (F11).
    Шлях загального — від кореня диска, без назви (`Documents/2. Minutes/…`).
    Помилка лістингу будь-якого диска валить увесь синк: інакше його файли
    виглядали б зниклими й видалились би з індексу.
    """
    mimes = set(READERS)
    general_id = settings.GOOGLE_DRIVE_GENERAL_ID.strip()
    if not general_id:
        raise RuntimeError("GOOGLE_DRIVE_GENERAL_ID is required for Drive sync")
    names = {d["id"]: d["name"] for d in await gdrive.list_drives()}
    paused = settings.PAUSED_DRIVES
    active = [drive for drive in settings.GOOGLE_DRIVES if drive not in paused]
    missing = [d for d in (general_id, *active) if d not in names]
    if missing:
        raise RuntimeError(f"сервісний акаунт не бачить дисків {missing}")

    def scope_for(f: dict, source: str) -> str | None:
        """Мітка — від диска, де файл фізично лежить, а не де стоїть ярлик на
        нього: інакше ярлик на файл `Boards` із загального диска відкривав би
        його всім. Диск, учасником якого акаунт не є (`SIM/SIT` — ярлик на
        загальному), прав не показує — тоді мітка джерела, як і було."""
        drive = f.get("driveId")
        if drive in paused:
            return None
        if drive == general_id:
            return GENERAL
        if drive and drive in names:
            return drive
        # З чужого диска загальним стає лише дозволене явно: інакше ярлик на
        # закриту теку, покладений будь-де на загальному диску, відкрив би її всім.
        if source == GENERAL and drive not in settings.GENERAL_FOREIGN_DRIVES:
            return None
        return source

    general, *drives = await asyncio.gather(
        *(
            gdrive.list_drive(d, mimes, paused_drives=paused)
            for d in (general_id, *active)
        ),
    )
    by_id: dict[str, dict] = {}
    for drive_id, files in zip(active, drives):
        short = names[drive_id].removeprefix("BEST Lviv - ")
        for f in files:
            scope = scope_for(f, drive_id)
            if scope is None:
                continue
            path = f"{short}/{f['path']}" if f["path"] else short
            by_id[f["id"]] = {**f, "path": path, "scope": scope}
    # загальне перекриває: файл, видимий усім, не має ховатись за диском
    for f in general:
        scope = scope_for(f, GENERAL)
        if scope is None:
            if f.get("driveId") not in paused:
                logger.warning("пропущено %s: ярлик на недозволений чужий диск", f["name"])
            continue
        if scope == GENERAL or f["id"] not in by_id:
            by_id[f["id"]] = {**f, "scope": scope}
    return [f for f in by_id.values() if readable(f)]


async def extract_gdrive(
    gdrive: GDriveConnector,
    gemini: genai.Client,
    db: AsyncSession,
    everything: bool = False,
) -> Extracted:
    targets = await gdrive_targets(gdrive)
    out = Extracted(found=len(targets))
    secret = {f["id"] for f in targets if privacy.is_secret_file(f["name"])}
    out.skipped_secret = len(secret)
    targets = [f for f in targets if f["id"] not in secret]

    meta = await documents.meta(db, "gdrive")
    known = {sid: m.last_edited for sid, m in meta.items()}
    fresh = {f["id"]: f["modifiedTime"] for f in targets}
    stale = set(fresh) if everything else _stale(known, fresh)
    to_process = [f for f in targets if f["id"] in stale]

    sem = asyncio.Semaphore(5)  # завантаження з Drive
    # Окремий ліміт на розпізнавання: це інший ресурс і інший порядок
    # тривалості. Якби OCR займав слоти завантаження, качання стало б
    # у чергу за 30-секундними запитами до Gemini.
    ocr_sem = asyncio.Semaphore(3)

    async def process(f: dict) -> dict:
        reader = READERS[f["mimeType"]]
        async with sem:
            blob = await reader.fetch(gdrive, f["id"])
        text, pages = await reader.read(blob)
        if needs_ocr(text, pages, len(blob)):
            async with ocr_sem:
                try:
                    text = await asyncio.wait_for(
                        gemini_text(
                            gemini,
                            settings.GEMINI_LLM_MODEL,
                            blob,
                            reader.blob_mime(f["mimeType"]),
                        ),
                        OCR_TIMEOUT,
                    )
                except Exception as e:  # noqa: BLE001 — лишається текст читача
                    out.ocr_failed += 1
                    logger.warning("gemini не дав текст для %s: %s", f["name"], e)
        text, hits = privacy.mask(nfc(text))
        out.masked += hits
        return {
            "summary": None,
            "source_id": f["id"],
            "source": "gdrive",
            "title": nfc(f["name"]),
            "path": nfc(f.get("path", "")),
            "url": f.get("webViewLink", ""),
            "last_edited": f["modifiedTime"],
            "text": text,
            "scope": f["scope"],
        }

    async def guarded(f: dict) -> tuple[dict, dict | BaseException]:
        try:
            return f, await process(f)
        except Exception as exc:  # noqa: BLE001 — один файл не валить синк
            return f, exc

    # Кожен файл записується, щойно готовий, а не в кінці: перше завантаження
    # диска йде годинами, і обрив інакше губив би все скачане й оплачений OCR.
    # Повторний запуск такі файли вже не чіпає — last_edited збігається.
    # Малі файли першими: у них більшість тексту, а важкі — здебільшого дизайн.
    to_process.sort(key=lambda f: int(f.get("size", 0)))
    batch: list[dict] = []
    for next_done in asyncio.as_completed([guarded(f) for f in to_process]):
        f, res = await next_done
        if isinstance(res, BaseException):
            out.failed += 1
            logger.warning("не вдалось обробити %s: %s", f["name"], res)
            continue
        out.rows.append(res)
        batch.append(res)
        if len(batch) >= 20:
            await documents.upsert_documents(db, batch)
            batch = []
    await documents.upsert_documents(db, batch)

    # Файл міг переїхати між загальним і диском: мітка переписується для всіх,
    # а не лише для незмінених. Інакше файл, що переїхав на закритий диск і
    # водночас не перечитався (збій завантаження), лишався б видимим усім.
    # Переміщення між теками `modifiedTime` не міняє, а від шляху залежать
    # вектор і нарізка мінетсів (`2. Minutes`) — тож шлях так само.
    done = {row["source_id"] for row in out.rows}
    place = {f["id"]: (f["scope"], nfc(f.get("path", ""))) for f in targets}
    moved = {
        sid: p
        for sid, p in place.items()
        if sid not in done and sid in meta and (meta[sid].scope, meta[sid].path) != p
    }
    if moved:
        await documents.set_places(db, moved)

    out.changed = len(out.rows)
    paused = settings.PAUSED_DRIVES
    removed = {
        sid for sid, row in meta.items() if sid not in fresh and row.scope not in paused
    }
    out.removed = await documents.delete_documents(db, removed)
    return out


# Файл Drive з коротшим текстом — логотип, бейдж, наліпка чи постер: OCR дає
# «RED», «HATOR», «Every Matrix», «Local Event on Education». На 8 дисках
# (10.11) таких 323 із 6 818, і нижче 40 символів усе — графіка; перший змістовний
# документ — список задач на 37 символів, далі вже нотатки й посилання.
# Таблиці поріг не зачіпає: заголовок `## Аркуш «…» · рядків: … · колонки: …`
# з першим рядком уже довший, найкоротша на дисках — 67 символів. Сторінки
# Notion пишуть люди, і коротка там буває змістовною
# («3rd board» — 27 символів про президента), тож поріг лише для Drive.
MIN_DRIVE_CHARS = 40


def indexable(source: str, chars: int) -> bool:
    """Чи йде документ у Qdrant. Інший лишається в `documents`: його віддає
    `get_document`, і синк не перечитує його щоразу, але фрагментів у нього
    немає, і індекс їх не чекає."""
    return chars >= (MIN_DRIVE_CHARS if source == "gdrive" else 1)


async def index(
    qdrant: QdrantRepository, db: AsyncSession, source: str, full: bool = False
) -> int:
    """Доводить індекс до `documents`: змінене й нове нарізати, зникле прибрати.

    Звіряється (last_edited, scope, path): зміна мітки доступу — теж привід
    переписати точки, інакше файл, що переїхав на закритий диск, лишився б
    видимим усім; шлях іде у вектор і керує нарізкою.
    """
    paused = settings.PAUSED_DRIVES if source == "gdrive" else set()
    wanted = {
        sid: (m.last_edited, m.scope, m.path)
        for sid, m in (await documents.meta(db, source)).items()
        if m.scope not in paused and indexable(source, m.chars)
    }
    indexed = await qdrant.index_state(source)
    stale = {sid for sid, meta in wanted.items() if full or indexed.get(sid) != meta}
    # Паузу визначає збережений scope точок: старий активний scope не
    # залишається видимим, якщо документ уже перейшов у призупинений.
    stale.update(
        sid
        for sid, (_, scope, _) in indexed.items()
        if scope not in paused and sid not in wanted
    )
    if not stale:
        return 0

    chunker = Chunker()
    nodes: list[TextNode] = []
    for doc in await documents.load(db, stale & set(wanted)):
        metadata = ChunkMetadata(
            source=doc.source,
            source_id=doc.source_id,
            title=doc.title,
            url=doc.url,
            last_edited=doc.last_edited,
            path=doc.path,
            scope=doc.scope,
        )
        if doc.summary is None and doc.source_id.startswith("db:"):
            # база Notion, що йде у вектор цілою: запис не має різатись навпіл
            nodes.extend(chunker.chunk_rows(doc.text, metadata))
            continue
        # Опис аркушів — чиста функція від тексту, назви й шляху, тож рахується
        # тут: зміна його формату доходить до індексу через `full`, без
        # повторного завантаження. Бази Notion беруть опис із сирих рядків —
        # його рахує крок 1.
        if doc.text.startswith(HEAD):
            summary = table_summary(doc.text, doc.title, doc.path)
            nodes.extend(chunker.chunk_table(summary or doc.text, metadata))
            continue
        nodes.extend(chunker.chunk(doc.summary or doc.text, metadata))
    return await qdrant.reindex(fresh_nodes=nodes, stale_ids=stale)
