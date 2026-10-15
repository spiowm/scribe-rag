from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base
from src.models.user import User  # noqa: F401  — на неї посилається user_id нижче


class McpCall(Base):
    """Один виклик інструмента через MCP.

    Питання людини MCP-клієнт серверу не передає — лише аргументи, які склала
    модель, тож `args` найближче до запиту. Повних результатів немає: це обсяг
    і зайві копії персональних даних.
    """

    __tablename__ = "mcp_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Без зовнішнього ключа: журнал лишається, навіть коли клієнта видалено.
    # Назва клієнта — в oauth_clients.info->>'client_name'.
    client_id: Mapped[str]
    tool: Mapped[str]
    args: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_ids: Mapped[list[str]] = mapped_column(JSONB)  # документи, що повернув пошук
    result_chars: Mapped[int]  # скільки тексту пішло в контекст клієнта
    error: Mapped[str | None]
    ms: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
