from typing import Optional

from pydantic import BaseModel


class NotionPage(BaseModel):
    id: str
    title: str
    url: str
    last_edited: str
    content: Optional[str] = None
