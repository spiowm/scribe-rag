from pydantic import BaseModel


class NotionPage(BaseModel):
    id: str
    title: str
    url: str
    last_edited: str
    content: str | None = None
