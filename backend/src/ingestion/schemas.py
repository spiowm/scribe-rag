from pydantic import BaseModel


class ChunkMetadata(BaseModel):
    source: str
    source_id: str
    title: str
    url: str
    last_edited: str
    path: str = ""


class ChunkPayload(ChunkMetadata):
    text: str


class SearchHit(BaseModel):
    score: float
    chunk: ChunkPayload
