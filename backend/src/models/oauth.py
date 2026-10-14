from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base
from src.models.user import User  # noqa: F401  — на неї посилаються user_id нижче


class OAuthClient(Base):
    """Клієнт, що зареєструвався сам через DCR: Gemini, claude.ai тощо.

    Поля RFC 7591 лежать як є: SDK лише просить їх назад за client_id,
    пошуку по них немає.
    """

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(primary_key=True)
    info: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class OAuthToken(Base):
    """Одне підключення: людина в одному клієнті. Від токенів — лише хеші.

    Оновлення замінює обидва хеші в цьому ж рядку, тож старі токени
    перестають діяти самі; відкликання — це видалення рядка.
    """

    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    client_id: Mapped[str] = mapped_column(
        ForeignKey("oauth_clients.client_id", ondelete="CASCADE")
    )
    access_hash: Mapped[str] = mapped_column(unique=True)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_hash: Mapped[str] = mapped_column(unique=True)
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resource: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class LoginCode(Base):
    """Одноразовий код із бота для сторінки входу."""

    __tablename__ = "login_codes"

    code: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
