from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SyncResponse(BaseModel):
    status: str
    found_pages: int | None = None
    chunks_indexed: int | None = None
    failed_count: int | None = None
    extracted: int | None = None  # документів наново видобуто з джерела
    removed: int | None = None  # зникли з джерела
    ocr_failed: int | None = None  # OCR не вдався, лишився текст читача (G2)
    masked: int | None = None  # замасковано карток і IBAN
    skipped_secret: int | None = None  # файлів із паролями не взято


class ChatRequest(BaseModel):
    telegram_id: int
    message: str


class ChatResponse(BaseModel):
    reply: str


class RateRequest(BaseModel):
    telegram_id: int
    message_id: int
    # Literal, а не int: оцінка приходить із callback_data, тобто від клієнта.
    value: Literal[1, -1]


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


class LoginCodeResponse(BaseModel):
    code: str
    expires_in: int  # секунд


class AutosyncRequest(BaseModel):
    telegram_id: int
    hours: int = Field(ge=0, le=24 * 30)  # 0 — вимкнути


class GroupImportRequest(BaseModel):
    telegram_id: int
    content: str  # CSV-експорт учасників Google-групи
    group: str | None = None  # адреса групи, якщо назва у файлі не збігається


class DriveToggleRequest(BaseModel):
    telegram_id: int
    drive_id: str
