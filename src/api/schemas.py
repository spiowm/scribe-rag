from pydantic import BaseModel


class SyncResponse(BaseModel):
    status: str
    found_pages: int
    test_page_title: str | None = None
    test_page_content: str | None = None
