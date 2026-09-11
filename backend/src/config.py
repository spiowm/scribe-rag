import os
from pathlib import Path
import base64
import json

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "postgres"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION_NAME: str = "general"

    NOTION_TOKEN: str = Field()
    NOTION_USERS_DB_ID: str = "3052e2ad-04c6-4950-8e1b-cb2f082d70f2"

    GOOGLE_DRIVE_CREDENTIALS_JSON: str = Field()
    GOOGLE_DRIVE_FOLDER_IDS: str = Field()

    GEMINI_API_KEY: str = Field()
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-2"
    GEMINI_LLM_MODEL: str = "gemini-3.7-flash"

    MONGO_URI: str = Field()
    MONGO_DB: str = "infobook"
    MONGO_USERS_COLLECTION: str = "users"

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
    def GOOGLE_DRIVE_ROOTS(self) -> list[str]:
        return [x.strip() for x in self.GOOGLE_DRIVE_FOLDER_IDS.split(",") if x.strip()]

    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()  # type: ignore
