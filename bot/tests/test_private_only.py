"""Оновлення з груп і каналів не доходять до жодного обробника, навіть вкладеного."""

import asyncio
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from src.private_only import PrivateOnly

USER = User(id=1, is_bot=False, first_name="Fixture")
BOT_USER = User(id=42, is_bot=True, first_name="Bot", username="fixture_bot")


def chat(kind: str) -> Chat:
    return Chat(id=1 if kind == "private" else -100, type=kind)


def message(kind: str, text: str, sender: User | None = USER) -> Message:
    return Message(
        message_id=1, date=datetime.now(), chat=chat(kind), from_user=sender, text=text
    )


def build() -> tuple[Dispatcher, list[str]]:
    """Диспетчер як у main.py: middleware на dp.update, обробники у вкладеному роутері."""
    calls: list[str] = []
    parent, child = Router(), Router()

    @child.message(Command("mcp_login"))
    async def login(_: Message) -> None:
        calls.append("mcp_login")

    @child.callback_query(F.data == "rate:up")
    async def rate(_: CallbackQuery) -> None:
        calls.append("rate")

    @child.message()
    async def anything(_: Message) -> None:
        calls.append("message")

    parent.include_router(child)
    dp = Dispatcher()
    dp.update.outer_middleware(PrivateOnly())
    dp.include_router(parent)
    return dp, calls


def feed(**update) -> list[str]:
    dp, calls = build()
    bot = Bot(token="42:FIXTURE")
    asyncio.run(dp.feed_update(bot, Update(update_id=1, **update)))
    return calls


def test_private_chat_is_handled():
    assert feed(message=message("private", "/mcp_login")) == ["mcp_login"]
    assert feed(message=message("private", "привіт")) == ["message"]


def test_group_commands_and_replies_are_ignored():
    for kind in ("group", "supergroup"):
        assert feed(message=message(kind, "/mcp_login@fixture_bot")) == []
        assert feed(message=message(kind, "/mcp_login")) == []
        assert feed(message=message(kind, "привіт")) == []
        assert feed(edited_message=message(kind, "привіт")) == []


def test_channel_posts_are_ignored():
    assert feed(channel_post=message("channel", "/mcp_login", sender=None)) == []


def test_buttons_follow_the_chat_of_their_message():
    def press(kind: str) -> list[str]:
        query = CallbackQuery(
            id="1",
            from_user=USER,
            chat_instance="fixture",
            data="rate:up",
            message=message(kind, "відповідь", sender=BOT_USER),
        )
        return feed(callback_query=query)

    assert press("private") == ["rate"]
    assert press("group") == []
    assert press("supergroup") == []


def test_buttons_without_chat_are_ignored():
    query = CallbackQuery(
        id="1",
        from_user=USER,
        chat_instance="fixture",
        data="rate:up",
        inline_message_id="fixture",
    )
    assert feed(callback_query=query) == []
