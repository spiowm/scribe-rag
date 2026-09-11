import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from google import genai
from llama_index.core.schema import TextNode
from sqlalchemy.ext.asyncio import AsyncSession
from src.api.dependencies import (
    get_gdrive_connector,
    get_gemini_client,
    get_notion_connector,
    get_qdrant_repository,
    get_db,
)
from src.repository import documents
from src.api.schemas import SyncResponse
from src.config import settings
from src.connectors.gdrive import GDriveConnector
from src.connectors.notion import NotionConnector
from src.ingestion.chunker import Chunker
from src.ingestion.extractors import gemini_text, markdown_text, pdf_text, pptx_text
from src.ingestion.schemas import ChunkMetadata
from src.vectorstore.qdrant import QdrantRepository

logger = logging.getLogger(__name__)

PDF = "application/pdf"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
GDOC = "application/vnd.google-apps.document"
GSLIDES = "application/vnd.google-apps.presentation"

router = APIRouter(
    prefix="/sync",
    tags=["Synchronization"],
)


@router.post("/notion", response_model=SyncResponse)
async def sync_notion_to_db(
    full: bool = False,
    notion: NotionConnector = Depends(get_notion_connector),
    qdrant_repository: QdrantRepository = Depends(get_qdrant_repository),
    db: AsyncSession = Depends(get_db),
):
    pages = await notion.fetch_all_pages()

    if not pages:
        raise HTTPException(status_code=502, detail="No pages found in Notion database")

    state = {} if full else await qdrant_repository.index_state("notion")
    fresh = {p.id: p.last_edited for p in pages}

    stale_ids = {
        page_id
        for page_id, edited in fresh.items()
        if state.get(page_id) != edited  # нове або змінене
    } | (set(state) - set(fresh))  # зниклі

    to_process = [p for p in pages if p.id in stale_ids]
    await notion.resolve_paths(to_process)
    failed_count = await notion.fetch_pages_content(to_process)

    failed_ids = {p.id for p in to_process if p.content is None}
    if failed_ids:
        logger.warning(
            "не завантажилось %d сторінок, лишаю попередні чанки", len(failed_ids)
        )
    stale_ids -= failed_ids

    chunker = Chunker()

    all_nodes: list[TextNode] = []

    rows: list[dict] = []

    for page in to_process:
        if page.content is None:
            continue
        metadata = ChunkMetadata(
            source="notion",
            source_id=page.id,
            title=page.title,
            url=page.url,
            last_edited=page.last_edited,
            path=page.path,
        )
        all_nodes.extend(chunker.chunk(text=page.content, metadata=metadata))
        rows.append(
            {
                "source_id": page.id,
                "source": "notion",
                "title": page.title,
                "path": page.path,
                "url": page.url,
                "last_edited": page.last_edited,
                "text": page.content,
            }
        )

    indexed = await qdrant_repository.reindex(
        fresh_nodes=all_nodes, stale_ids=stale_ids
    )

    await documents.upsert_documents(db, rows)
    await documents.delete_documents(db, set(state) - set(fresh))

    return SyncResponse(
        status="success",
        found_pages=len(pages),
        failed_count=failed_count,
        chunks_indexed=indexed,
    )


@router.post("/gdrive", response_model=SyncResponse)
async def sync_gdrive_to_db(
    full: bool = False,
    gdrive: GDriveConnector = Depends(get_gdrive_connector),
    qdrant_repository: QdrantRepository = Depends(get_qdrant_repository),
    gemini: genai.Client = Depends(get_gemini_client),
    db: AsyncSession = Depends(get_db),
):
    files = await gdrive.walk()

    ALLOWED = {PDF, PPTX, GDOC, GSLIDES}
    targets = [
        f
        for f in files
        if f["mimeType"] in ALLOWED and int(f.get("size", 0)) <= 100 * 1024 * 1024
    ]

    # full=True — не порівнюємо з індексом, переганяємо всі файли заново.
    # Потрібно, коли змінилась схема метаданих: інкрементальний шлях
    # переносить старі точки як є, і нове поле в них не зʼявиться.
    state = {} if full else await qdrant_repository.index_state("gdrive")
    fresh = {f["id"]: f["modifiedTime"] for f in targets}

    stale_ids = {fid for fid, edited in fresh.items() if state.get(fid) != edited} | (
        set(state) - set(fresh)
    )

    to_process = [f for f in targets if f["id"] in stale_ids]

    sem = asyncio.Semaphore(5)  # завантаження з Drive
    # Окремий ліміт на розпізнавання: це інший ресурс і інший порядок
    # тривалості. Якби OCR займав слоти завантаження, качання стало б
    # у чергу за 30-секундними запитами до Gemini.
    ocr_sem = asyncio.Semaphore(3)
    chunker = Chunker()
    chunker = Chunker()

    async def process(f: dict) -> tuple[list[TextNode], dict]:
        mime = f["mimeType"]
        async with sem:
            if mime == GDOC:
                blob = await gdrive.export(f["id"], "text/markdown")
                blob_mime = "text/markdown"
            elif mime == GSLIDES:
                blob = await gdrive.export(f["id"], "application/pdf")
                blob_mime = PDF
            else:
                blob = await gdrive.download(f["id"])
                blob_mime = mime

        if mime == GDOC:
            text, pages = markdown_text(blob)
        elif mime == PPTX:
            text, pages = await asyncio.to_thread(pptx_text, blob)
        else:
            text, pages = await asyncio.to_thread(pdf_text, blob)

        if pages and len(text) / pages < 400:
            async with ocr_sem:
                try:
                    text = await gemini_text(
                        gemini, settings.GEMINI_LLM_MODEL, blob, blob_mime
                    )
                except Exception as e:
                    logger.warning("gemini не дав текст для %s: %s", f["name"], e)

        metadata = ChunkMetadata(
            source="gdrive",
            source_id=f["id"],
            title=f["name"],
            url=f.get("webViewLink", ""),
            last_edited=f["modifiedTime"],
            path=f.get("path", ""),
        )
        nodes = chunker.chunk(text=text, metadata=metadata)
        if not nodes:
            logger.warning(
                "нуль чанків: %s (%d символів, %d стор)", f["name"], len(text), pages
            )
        row = {
            "source_id": f["id"],
            "source": "gdrive",
            "title": f["name"],
            "path": f.get("path", ""),
            "url": f.get("webViewLink", ""),
            "last_edited": f["modifiedTime"],
            "text": text,
        }
        return nodes, row

    results = await asyncio.gather(
        *(process(f) for f in to_process), return_exceptions=True
    )

    all_nodes: list[TextNode] = []
    rows: list[dict] = []
    failed = 0
    for f, res in zip(to_process, results):
        if isinstance(res, BaseException):
            failed += 1
            logger.warning("не вдалось обробити %s: %s", f["name"], res)
            stale_ids.discard(f["id"])
        else:
            nodes, row = res
            all_nodes.extend(nodes)
            rows.append(row)

    indexed = await qdrant_repository.reindex(
        fresh_nodes=all_nodes, stale_ids=stale_ids
    )
    await documents.upsert_documents(db, rows)
    await documents.delete_documents(db, set(state) - set(fresh))

    return SyncResponse(
        status="success",
        found_pages=len(targets),
        failed_count=failed,
        chunks_indexed=indexed,
    )
