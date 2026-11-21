from typing import NamedTuple

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.document import Document


async def upsert_documents(db: AsyncSession, rows: list[dict]) -> int:
    """Ключ source_id, тому повторний sync просто перезаписує текст"""
    if not rows:
        return 0

    batch_size = 200
    for i in range(0, len(rows), batch_size):
        stmt = insert(Document).values(rows[i : i + batch_size])
        stmt = stmt.on_conflict_do_update(
            index_elements=[Document.source_id],
            set_={
                "source": stmt.excluded.source,
                "title": stmt.excluded.title,
                "path": stmt.excluded.path,
                "url": stmt.excluded.url,
                "last_edited": stmt.excluded.last_edited,
                "text": stmt.excluded.text,
                "scope": stmt.excluded.scope,
                "summary": stmt.excluded.summary,
                "updated_at": func.now(),
            },
        )
        await db.execute(stmt)

    await db.commit()
    return len(rows)


async def delete_documents(db: AsyncSession, source_ids: set[str]) -> int:
    """Прибирає документи, що зникли з джерела"""
    if not source_ids:
        return 0
    await db.execute(delete(Document).where(Document.source_id.in_(source_ids)))
    await db.commit()
    return len(source_ids)


class Meta(NamedTuple):
    last_edited: str
    scope: str
    path: str
    # Довжина тексту без пробілів по краях. Порожній чи надто короткий документ
    # лежить у `documents`, щоб не переоброблятись щосинку, але фрагментів у
    # нього немає — індекс його не чекає (`pipeline.indexable`).
    chars: int


async def meta(db: AsyncSession, source: str) -> dict[str, Meta]:
    """Що вже видобуто з джерела — усе, з чим звіряються обидва кроки синку."""
    rows = await db.execute(
        select(
            Document.source_id,
            Document.last_edited,
            Document.scope,
            Document.path,
            func.length(func.trim(Document.text)),
        ).where(Document.source == source)
    )
    return {sid: Meta(e, sc, p, t) for sid, e, sc, p, t in rows.tuples()}


async def load(db: AsyncSession, source_ids: set[str]) -> list[Document]:
    if not source_ids:
        return []
    rows = await db.scalars(select(Document).where(Document.source_id.in_(source_ids)))
    return list(rows)


async def set_places(db: AsyncSession, places: dict[str, tuple[str, str]]) -> None:
    """Нові мітка доступу й шлях без зміни тексту — для файлів, що переїхали."""
    for source_id, (scope, path) in places.items():
        await db.execute(
            update(Document)
            .where(Document.source_id == source_id)
            .values(scope=scope, path=path)
        )
    await db.commit()


async def places(db: AsyncSession, parts: set[str]) -> list[tuple[str, str, str]]:
    """(url, source, scope) документів, чий url містить одну з частин.

    Частина — id документа з посилання (`sources.url_key`): у тексті відповіді
    посилання бувають з іншими хвостами, ніж у `documents.url`, тож точний збіг
    звіряє той, хто кличе.
    """
    if not parts:
        return []
    rows = await db.execute(
        select(Document.url, Document.source, Document.scope).where(
            or_(*(Document.url.contains(p, autoescape=True) for p in parts))
        )
    )
    return [(url, source, scope) for url, source, scope in rows.tuples()]


async def get_document(
    db: AsyncSession, source_id: str, scopes: list[str]
) -> Document | None:
    """None і тоді, коли документ є, але не в дозволеному: про існування
    закритого документа користувач дізнатись не має."""
    doc = await db.get(Document, source_id)
    return doc if doc is not None and doc.scope in scopes else None
