from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.chat_session import ChatSession
from src.models.message import Message


async def create_session(db: AsyncSession, user_id: int) -> ChatSession:
    session = ChatSession(user_id=user_id)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def get_active_session(db: AsyncSession, user_id: int) -> ChatSession | None:
    session = await db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.created_at.desc())
        .limit(1)
    )
    return session.scalar_one_or_none()


async def get_or_create_session(db: AsyncSession, user_id: int) -> ChatSession:
    session = await get_active_session(db, user_id)
    if session is None:
        session = await create_session(db, user_id)
    return session


async def add_message(
    db: AsyncSession,
    session_id: int,
    role: str,
    content: str,
) -> Message:
    message = Message(session_id=session_id, role=role, content=content)
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message


async def get_recent_messages(
    db: AsyncSession, session_id: int, limit: int = 20
) -> list[Message]:
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    messages = result.scalars().all()
    return list(reversed(messages))
