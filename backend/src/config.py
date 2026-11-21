import base64
import json
import os
from pathlib import Path

from pydantic import Field, SecretStr, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Телеграм-id власника: йому йдуть сповіщення про збої, і лише він має
    # доступ до /admin. Не задано — адмінки й сповіщень просто немає.
    ADMIN_TELEGRAM_ID: int | None = None
    # Спільний секрет бота й бекенда (M3). Внутрішній API вірить telegram_id
    # з тіла запиту, тож без секрету будь-хто з доступом до порту видав би себе
    # за іншого й читав би чужі диски. Не задано — перевірки немає (локально).
    INTERNAL_API_TOKEN: str = ""
    TELEGRAM_BOT_USERNAME: str = "best_lviv_assistant_bot"
    PUBLIC_URL: str = "http://localhost:8077"

    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "postgres"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION_NAME: str = "general"
    QDRANT_MEMES_COLLECTION: str = "memes"

    # Секрети R2, Notion і Drive потрібні лише конекторам backend, тому тут
    # необовʼязкові: публічний MCP-сервіс стартує без них. Backend перевіряє
    # їх на старті (`BACKEND_SECRETS` у `src/api/main.py`).
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET: str = "scribe-rag"

    NOTION_TOKEN: str = ""
    # Бази Notion, що індексуються табличними документами: id бази (з її
    # посилання) через кому, за потреби з relation-властивістю розбиття після
    # двокрапки: `817b…:Події` — частина на кожну подію. Порожньо — бази не
    # беруться, як і раніше.
    NOTION_DATABASE_IDS: str = ""

    GOOGLE_DRIVE_CREDENTIALS_JSON: str = ""
    # Загальний диск та дозволені джерела ярликів із зовнішніх дисків.
    GOOGLE_DRIVE_GENERAL_ID: str = ""
    GOOGLE_DRIVE_GENERAL_FOREIGN: str = ""
    GOOGLE_DRIVE_GENERAL_FOREIGN_LABEL: str = "інші загальні джерела"
    GOOGLE_DRIVE_FULL_MEMBERS_GROUP: str = ""
    GOOGLE_DRIVE_ADMIN_GROUP: str = ""
    # Диски, що індексуються цілком і вмикаються галочкою: id через кому.
    GOOGLE_DRIVE_IDS: str = ""
    GOOGLE_DRIVE_PAUSED_IDS: str = ""

    GEMINI_API_KEY: str = Field()
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-2"
    # Модель для OCR та відповідей через API, незалежна від AGY_MODEL.
    # Для OCR рівень роздумів LOW задається в ingestion/extractors.py.
    GEMINI_LLM_MODEL: str = "gemini-3.8-flash"
    ORG_CONTEXT: SecretStr = SecretStr("")

    # Модель через підписку agy замість платного API. "gemini" — через API.
    LLM_PROVIDER: str = "agy"
    AGY_BIN: str = "agy"
    AGY_HOME: str = str(Path.home() / ".agy-bot")
    AGY_WORKDIR: str = str(Path.home() / ".agy-bot-ws")
    AGY_MODEL: str = "gemini-3.8-flash-low"
    AGY_TURN_TIMEOUT: float = 120
    AGY_MAX_PROCS: int = 10
    AGY_STATE_TTL_DAYS: float = 3
    AGY_CLEANUP_HOURS: float = 6
    AGY_FALLBACK_TO_API: bool = False
    AGY_ATTEMPTS: int = 2

    MONGO_URI: str = Field()
    MONGO_DB: str = "infobook"
    MONGO_USERS_COLLECTION: str = "users"

    @field_validator("ADMIN_TELEGRAM_ID", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        """Порожнє значення змінної — це «не задано», а не нуль.

        У панелі Coolify і в compose незаданий ключ приходить порожнім рядком,
        а pydantic на ньому падає — тобто бот не піднявся б узагалі.
        """
        return value or None

    @field_validator("GOOGLE_DRIVE_FULL_MEMBERS_GROUP", "GOOGLE_DRIVE_ADMIN_GROUP")
    @classmethod
    def _normalize_group_email(cls, value: str) -> str:
        return value.strip().lower()

    @computed_field
    @property
    def POSTGRES_URL(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @computed_field
    @property
    def QDRANT_URL(self) -> str:
        return f"http://{self.QDRANT_HOST}:{self.QDRANT_PORT}"

    @property
    def GOOGLE_DRIVE_CREDENTIALS(self) -> dict:
        return json.loads(base64.b64decode(self.GOOGLE_DRIVE_CREDENTIALS_JSON))

    @computed_field
    @property
    def GENERAL_FOREIGN_DRIVES(self) -> set[str]:
        return {
            x.strip() for x in self.GOOGLE_DRIVE_GENERAL_FOREIGN.split(",") if x.strip()
        }

    @computed_field
    @property
    def GOOGLE_DRIVES(self) -> list[str]:
        return [x.strip() for x in self.GOOGLE_DRIVE_IDS.split(",") if x.strip()]

    @property
    def PAUSED_DRIVES(self) -> set[str]:
        return {
            drive.strip()
            for drive in self.GOOGLE_DRIVE_PAUSED_IDS.split(",")
            if drive.strip()
        }

    @model_validator(mode="after")
    def _pause_only_drive_scopes(self) -> "Settings":
        # Загальне має одну мітку для Notion і кількох джерел Drive.
        general = {self.GOOGLE_DRIVE_GENERAL_ID.strip(), *self.GENERAL_FOREIGN_DRIVES}
        if self.PAUSED_DRIVES & general:
            raise ValueError("GOOGLE_DRIVE_PAUSED_IDS accepts only work drives")
        return self

    @computed_field
    @property
    def NOTION_DATABASES(self) -> list[tuple[str, str | None]]:
        """[(id бази, властивість розбиття або None)]."""
        out = []
        for item in self.NOTION_DATABASE_IDS.split(","):
            db_id, _, split_by = item.strip().partition(":")
            if db_id:
                out.append((db_id.strip(), split_by.strip() or None))
        return out

    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()  # type: ignore
