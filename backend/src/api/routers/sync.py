import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from llama_index.core.schema import TextNode

from src.api.dependencies import (
    get_gdrive_connector,
    get_notion_connector,
    get_qdrant_repository,
)
from src.api.schemas import SyncResponse
from src.connectors.gdrive import GDriveConnector
from src.connectors.notion import NotionConnector
from src.ingestion.chunker import Chunker
from src.ingestion.extractors import pdf_text
from src.ingestion.schemas import ChunkMetadata
from src.vectorstore.qdrant import QdrantRepository

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/sync",
    tags=["Synchronization"],
)


@router.post("/notion", response_model=SyncResponse)
async def sync_notion_to_db(
    notion: NotionConnector = Depends(get_notion_connector),
    qdrant_repository: QdrantRepository = Depends(get_qdrant_repository),
):
    pages = await notion.fetch_all_pages()

    if not pages:
        raise HTTPException(status_code=502, detail="No pages found in Notion database")

    state = await qdrant_repository.index_state()
    fresh = {p.id: p.last_edited for p in pages}

    stale_ids = {
        page_id
        for page_id, edited in fresh.items()
        if state.get(page_id) != edited  # нове або змінене
    } | (set(state) - set(fresh))  # зниклі

    to_process = [p for p in pages if p.id in stale_ids]
    failed_count = await notion.fetch_pages_content(to_process)

    failed_ids = {p.id for p in to_process if p.content is None}
    if failed_ids:
        logger.warning(
            "не завантажилось %d сторінок, лишаю попередні чанки", len(failed_ids)
        )
    stale_ids -= failed_ids

    chunker = Chunker()

    all_nodes: list[TextNode] = []

    for page in to_process:
        if page.content is None:
            continue
        metadata = ChunkMetadata(
            source="notion",
            source_id=page.id,
            title=page.title,
            url=page.url,
            last_edited=page.last_edited,
        )
        all_nodes.extend(chunker.chunk(text=page.content, metadata=metadata))

    indexed = await qdrant_repository.reindex(
        fresh_nodes=all_nodes, stale_ids=stale_ids
    )

    return SyncResponse(
        status="success",
        found_pages=len(pages),
        failed_count=failed_count,
        chunks_indexed=indexed,
    )


@router.post("/gdrive", response_model=SyncResponse)
async def sync_gdrive_to_db(
    gdrive: GDriveConnector = Depends(get_gdrive_connector),
    qdrant_repository: QdrantRepository = Depends(get_qdrant_repository),
):
    files = await gdrive.walk()

    targets = [
        f
        for f in files
        if f["mimeType"] == "application/pdf"
        and int(f.get("size", 0)) <= 100 * 1024 * 1024
    ]

    chunker = Chunker()
    all_nodes: list[TextNode] = []

    for f in targets:
        blob = await gdrive.download(f["id"])
        text, pages = await asyncio.to_thread(pdf_text, blob)
        metadata = ChunkMetadata(
            source="gdrive",
            source_id=f["id"],
            title=f["name"],
            url=f.get("webViewLink", ""),
            last_edited=f["modifiedTime"],
        )
        all_nodes.extend(chunker.chunk(text=text, metadata=metadata))

    if not all_nodes:
        raise HTTPException(status_code=502, detail="No chunks found in Gdrive")

    collection_name = await qdrant_repository.create_collection()

    await qdrant_repository.upsert_nodes(
        collection_name=collection_name, nodes=all_nodes
    )

    await qdrant_repository.switch_alias(collection_name)

    try:
        await qdrant_repository.cleanup_old_collections()
    except Exception:
        logger.exception("Failed to cleanup old collections")

    return SyncResponse(
        status="success",
        found_pages=len(targets),
        # failed_count=failed_count,
        chunks_indexed=len(all_nodes),
    )
