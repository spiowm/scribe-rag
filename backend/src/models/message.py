from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String)  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    rating: Mapped[int | None] = mapped_column(nullable=True)
    # Як зібрано відповідь: кроки, виклики з аргументами й знайденими
    # source_id, токени, секунди, модель. Лише в повідомленнях бота; повних
    # результатів інструментів тут немає. Формат — `chain.run_record`;
    # `cut` — пам'ять уже не вміщала початок розмови (`routers.chat.build_record`).
    # deferred: історію для моделі читають по 200 повідомлень, і журнал їй не потрібен.
    # none_as_null: інакше None з Python лягає JSON-значенням `null`, а не NULL.
    run: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True, deferred=True
    )
