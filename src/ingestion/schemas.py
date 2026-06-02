from pydantic import BaseModel


class ChunkPayload(BaseModel):
    text: str
    source: str  # notion | drive
    source_id: str
    title: str
    url: str
    last_edited: str
