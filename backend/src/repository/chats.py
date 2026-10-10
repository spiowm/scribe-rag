from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.chat_session import ChatSession
from src.models.message import Message
from typing import NamedTuple

CHARS_PER_TOKEN = 2.6
HISTORY_TOKEN_BUDGET = 8000


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
        .order_by(ChatSession.id.desc())
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


class Window(NamedTuple):
    messages: list[Message]
    tokens: int
    truncated: bool


def estimate_tokens(text: str) -> int:
    return round(len(text) / CHARS_PER_TOKEN)


async def get_history_window(
    db: AsyncSession, session_id: int, budget: int = HISTORY_TOKEN_BUDGET
) -> Window:
    """Останні повідомлення, що вкладаються в бюджет токенів.

    Ріже лише по межах повідомлень. Найновіше лишає завжди, навіть якщо воно
    саме більше за бюджет: інакше модель отримає розмову зовсім без контексту.
    """
    # Порядок по id, а не по created_at: `func.now()` віддає час початку
    # транзакції, а не моменту запису. Питання лягає в базу вже після
    # генерації, але штамп у нього з моменту, коли запит прийшов, — у базі це
    # видно як рівно тривалість генерації між питанням і відповіддю. Отже
    # created_at тут не колонка порядку вставки, а id — завжди.
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.id.desc())
        .limit(200)  # 200 повідомлень важать більше за будь-який бюджет
    )
    kept: list[Message] = []
    used = 0
    truncated = False
    for message in result.scalars():
        cost = estimate_tokens(message.content)
        if kept and used + cost > budget:
            truncated = True
            break
        kept.append(message)
        used += cost

    # Вікно має починатися з питання: відповідь без свого питання читається
    # як сказана невідомо на що.
    while len(kept) > 1 and kept[-1].role == "assistant":
        used -= estimate_tokens(kept[-1].content)
        kept.pop()
        truncated = True

    return Window(list(reversed(kept)), used, truncated)


async def set_rating(
    db: AsyncSession, message_id: int, user_id: int, value: int
) -> bool:
    """Ставить оцінку відповіді. Вертає False, якщо такої відповіді в цього юзера немає.

    Перевірка власника тут, а не в роутері, і вона частина самого UPDATE:
    `message_id` приходить із callback_data, тобто від клієнта, і за ним можна
    підсунути будь-яке число. Один запит замість «спершу прочитай, тоді онови»
    ще й не має вікна між перевіркою й записом.
    """
    result = await db.execute(
        update(Message)
        .where(
            Message.id == message_id,
            Message.role == "assistant",
            Message.session_id.in_(
                select(ChatSession.id).where(ChatSession.user_id == user_id)
            ),
        )
        .values(rating=value)
    )
    await db.commit()
    return bool(result.rowcount)
