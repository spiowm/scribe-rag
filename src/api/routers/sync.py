import json

from fastapi import APIRouter, Depends

from src.api.dependencies import get_notion_connector
from src.api.schemas import SyncResponse
from src.connectors.notion import NotionConnector
from src.connectors.schemas import NotionPage
from src.ingestion.chunker import NotionChunker

router = APIRouter(
    prefix="/sync",
    tags=["Synchronization"],
)


@router.post("/notion", response_model=SyncResponse)
async def sync_notion_to_db(notion: NotionConnector = Depends(get_notion_connector)):
    # pages = await notion.fetch_all_pages()

    with open("regular_pages.json", "r", encoding="utf-8") as f:
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

    if not pages:
        return SyncResponse(
            status="error",
            found_pages=0,
            test_page_title=None,
            test_page_content=None,
        )

    first_page = pages[0]
    first_page_id = first_page.id
    first_page_id = "4246308affe648928fbac763feacd8d5"

    first_page.content = await notion.get_page_markdown(first_page_id)
    chunker = NotionChunker()
    nodes = chunker.chunk_page(first_page)

    return SyncResponse(
        status="success",
        found_pages=len(pages),
        test_page_title=first_page.title,
        test_page_content=first_page.content,
    )
