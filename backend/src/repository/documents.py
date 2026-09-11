from sqlalchemy import delete, func
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


async def get_document(db: AsyncSession, source_id: str) -> Document | None:
    return await db.get(Document, source_id)
