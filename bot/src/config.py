import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    BOT_TOKEN: str
    BACKEND_URL: str
    # Той самий id, що й у бекенда: кому писати про збої і хто бачить /admin.
    ADMIN_TELEGRAM_ID: int | None = None

    @field_validator("ADMIN_TELEGRAM_ID", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        """Порожнє значення змінної — це «не задано», а не нуль.

        У панелі Coolify і в compose незаданий ключ приходить порожнім рядком,
        а pydantic на ньому падає — тобто бот не піднявся б узагалі.
        """
        return value or None

    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()  # type: ignore
