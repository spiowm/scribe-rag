"""Бот працює лише в особистих чатах.

У групі відповідь бачать усі учасники: код входу MCP з /mcp_login@бот дав би
будь-кому з чату вхід від імені того, хто його попросив, а відповіді показали б
його диски й контакти довідника. Тому оновлення з груп, супергруп і каналів
відкидаються до всіх роутерів, разом із натисканнями кнопок під повідомленнями
в таких чатах.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.enums import ChatType
from aiogram.types import Chat, TelegramObject


class PrivateOnly(BaseMiddleware):
    """Outer-middleware для `dp.update`: пропускає лише оновлення з особистих чатів.

    Чат бере `UserContextMiddleware` диспетчера, який стоїть раніше в ланцюжку.
    Оновлення без чату (інлайн-запити, кнопки під інлайн-повідомленнями) теж
    відкидаються: бот їх не обробляє, а звідки вони, не видно.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat: Chat | None = data.get("event_chat")
        if chat is None or chat.type != ChatType.PRIVATE:
            return UNHANDLED
        return await handler(event, data)
