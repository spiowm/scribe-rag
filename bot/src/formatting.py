from dataclasses import dataclass

from aiogram.types import MessageEntity
from telegramify_markdown import telegramify
from telegramify_markdown.content import Text


@dataclass
class Part:
    "Один готовий до відправки шматок: чистий текст + entities (без parse_mode)"

    text: str
    entities: list[MessageEntity]


async def render(md: str) -> list[Part]:
    "Сирий markdown від бекенду -> список частин для tg"
    parts: list[Part] = []
    for item in await telegramify(md, max_message_length=4000):
        if isinstance(item, Text):
            parts.append(
                Part(
                    text=item.text,
                    entities=[MessageEntity(**e.to_dict()) for e in item.entities],
                )
            )

    return parts
