from datetime import datetime

from sqlalchemy import Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class Document(Base):
    """Повний видобутий текст документа — те саме, що пішло в чанкер."""

    __tablename__ = "documents"

    source_id: Mapped[str] = mapped_column(primary_key=True)
    source: Mapped[str]
    title: Mapped[str]
    path: Mapped[str] = mapped_column(default="")
    url: Mapped[str] = mapped_column(default="")
    last_edited: Mapped[str]
    text: Mapped[str] = mapped_column(Text)
    # Те саме, що `scope` у фрагментах: get_document не має віддати цілим
    # документ, якого цей користувач не знайшов би пошуком.
    scope: Mapped[str] = mapped_column(server_default="general")
    # Що ембедити замість `text`, коли сам текст у вектор не годиться (бази
    # Notion: опис із сирих рядків). None — нарізати `text`, як звичайно; опис
    # аркушів Drive рахує `pipeline.index` з самого тексту.
    summary: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
