from pydantic import BaseModel, ConfigDict


class SyncResponse(BaseModel):
    status: str
    found_pages: int | None = None
    chunks_indexed: int | None = None
    failed_count: int | None = None


class ChatRequest(BaseModel):
    telegram_id: int
    message: str


class ChatResponse(BaseModel):
    reply: str


class LinkRequest(BaseModel):
    telegram_id: int
    telegram_username: str | None = None
    phone: str


class UserResponse(BaseModel):
    telegram_id: int
    telegram_username: str
    first_name: str
    last_name: str
    model_config = ConfigDict(from_attributes=True)
