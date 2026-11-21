from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class Setting(Base):
    """Налаштування, які адмін змінює з бота без передеплою (`autosync`)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
