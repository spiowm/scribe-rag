from pydantic import BaseModel


class ChatRequest(BaseModel):
    telegram_id: int
    message: str


class ChatResponse(BaseModel):
    reply: str


class LinkRequest(BaseModel):
    telegram_id: int
    telegram_username: str
    phone: str


class UserResponse(BaseModel):
    telegram_id: int
    telegram_username: str
    first_name: str
    last_name: str
