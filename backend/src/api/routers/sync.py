import logging

from fastapi import APIRouter, Depends, HTTPException
from google import genai
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_db,
    get_gdrive_connector,
    get_gemini_client,
    get_notion_connector,
    get_qdrant_repository,
)
from src.api.schemas import SyncResponse
from src.connectors.gdrive import GDriveConnector
from src.connectors.notion import NotionConnector
from src.ingestion import pipeline
from src.vectorstore.qdrant import QdrantRepository

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/sync",
    tags=["Synchronization"],
)


def _response(extracted: pipeline.Extracted, indexed: int) -> SyncResponse:
    return SyncResponse(
        status="success",
        found_pages=extracted.found,
        failed_count=extracted.failed,
        chunks_indexed=indexed,
        extracted=extracted.changed,
        removed=extracted.removed,
        ocr_failed=extracted.ocr_failed,
        masked=extracted.masked,
        skipped_secret=extracted.skipped_secret,
    )


def _take_lock() -> None:
    # Другий синк не чекає в черзі, а відмовляє: HTTP-клієнт інакше висів би
    # хвилинами без жодного сигналу, що він другий.
    if pipeline.SYNC_LOCK.locked():
        raise HTTPException(409, "sync_running")


@router.post("/notion", response_model=SyncResponse)
async def sync_notion_to_db(
    full: bool = False,
    reextract: bool = False,
    notion: NotionConnector = Depends(get_notion_connector),
    qdrant_repository: QdrantRepository = Depends(get_qdrant_repository),
    db: AsyncSession = Depends(get_db),
):
    """`full` — переіндексувати все з `documents` (нова нарізка чи схема);
    `reextract` — наново завантажити всі сторінки з Notion."""
    _take_lock()
    async with pipeline.SYNC_LOCK:
        try:
            extracted = await pipeline.extract_notion(notion, db, everything=reextract)
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc
        indexed = await pipeline.index(qdrant_repository, db, "notion", full=full)
    return _response(extracted, indexed)


@router.post("/gdrive", response_model=SyncResponse)
async def sync_gdrive_to_db(
    full: bool = False,
    reextract: bool = False,
    gdrive: GDriveConnector = Depends(get_gdrive_connector),
    qdrant_repository: QdrantRepository = Depends(get_qdrant_repository),
    gemini: genai.Client = Depends(get_gemini_client),
    db: AsyncSession = Depends(get_db),
):
    """`full` — переіндексувати все з `documents` без повторного OCR;
    `reextract` — наново завантажити й розпізнати всі файли (платний OCR)."""
    _take_lock()
    async with pipeline.SYNC_LOCK:
        extracted = await pipeline.extract_gdrive(
            gdrive, gemini, db, everything=reextract
        )
        indexed = await pipeline.index(qdrant_repository, db, "gdrive", full=full)
    return _response(extracted, indexed)
