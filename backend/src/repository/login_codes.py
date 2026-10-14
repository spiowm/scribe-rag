import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.oauth import LoginCode

TTL = timedelta(minutes=5)

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
LENGTH = 8


async def create(db: AsyncSession, user_id: int) -> str:
    """Новий код замість попередніх; заодно прибирає прострочені коди всіх людей."""
    await db.execute(
        delete(LoginCode).where(
            or_(LoginCode.user_id == user_id, LoginCode.expires_at < datetime.now(UTC))
        )
    )
    code = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    db.add(LoginCode(code=code, user_id=user_id, expires_at=datetime.now(UTC) + TTL))
    await db.commit()
    return code


async def take(db: AsyncSession, code: str) -> int | None:
    """Гасить код і вертає user_id. Одним DELETE, тож двічі код не спрацює."""
    user_id = await db.scalar(
        delete(LoginCode)
        .where(
            LoginCode.code == code.strip().upper(),
            LoginCode.expires_at > datetime.now(UTC),
        )
        .returning(LoginCode.user_id)
    )
    await db.commit()
    return user_id
