import logging

from fastapi import APIRouter, Depends, HTTPException
from llama_index.core.schema import TextNode

from src.api.dependencies import get_notion_connector, get_qdrant_repository
from src.api.schemas import SyncResponse
from src.connectors.notion import NotionConnector
from src.ingestion.chunker import NotionChunker
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

    failed_count = await notion.fetch_pages_content(pages)

    if failed_count > 3:
        raise HTTPException(
            status_code=502, detail=f"Too many failed pages ({failed_count})"
        )

    chunker = NotionChunker()

    all_nodes: list[TextNode] = []
    for page in pages:
        nodes = chunker.chunk_page(page)
        all_nodes.extend(nodes)

    if not all_nodes:
        raise HTTPException(status_code=502, detail="No chunks found in Notion pages")

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
        found_pages=len(pages),
        failed_count=failed_count,
        chunks_indexed=len(all_nodes),
    )
