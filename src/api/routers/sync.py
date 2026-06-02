from fastapi import APIRouter, Depends
from llama_index.core.schema import TextNode

from src.api.dependencies import get_notion_connector, get_qdrant_repository
from src.api.schemas import SyncResponse
from src.connectors.notion import NotionConnector
from src.ingestion.chunker import NotionChunker
from src.vectorstore.qdrant import QdrantRepository

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
        return SyncResponse(
            status="error",
            found_pages=0,
        )

    chunker = NotionChunker()

    all_nodes: list[TextNode] = []
    for page in pages:
        page.content = await notion.get_page_markdown(page.id)
        nodes = chunker.chunk_page(page)
        all_nodes.extend(nodes)

    await qdrant_repository.upsert_nodes(all_nodes)

    return SyncResponse(
        status="success",
        found_pages=len(pages),
        test_page_title=pages[0].title,
        test_page_content=pages[0].content,
        chunks_indexed=len(all_nodes),
    )
