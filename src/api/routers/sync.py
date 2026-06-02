import json

from fastapi import APIRouter, Depends
from llama_index.core.schema import TextNode

from src.api.dependencies import get_notion_connector, get_qdrant_repository
from src.api.schemas import SyncResponse
from src.connectors.notion import NotionConnector
from src.connectors.schemas import NotionPage
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
    # pages = await notion.fetch_all_pages()

    # тимчасовий мок на 10 сторінок =============
    with open("regular_pages_10.json", "r", encoding="utf-8") as f:
        cached_data = json.load(f)

    pages = [
        NotionPage(
            id=p["id"],
            title=p["properties"]["title"]["title"][0]["plain_text"]
            if p["properties"]["title"]["title"]
            else "Без назви",
            url=p.get("url", ""),
            last_edited=p.get("last_edited_time", ""),
        )
        for p in cached_data
    ]
    # тимчасовий мок на 10 сторінок =============

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
