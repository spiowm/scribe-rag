import logging

import httpx
from aiogram import Bot, Router, types
from aiogram.types import InputRichMessage
from aiogram.utils.chat_action import ChatActionSender

from src.api_client import ApiClient

logger = logging.getLogger(__name__)

router = Router()


@router.message()
async def handle_chat(message: types.Message, api_client: ApiClient, bot: Bot):
    async with ChatActionSender.typing(chat_id=message.chat.id, bot=bot):
        if message.from_user is None or message.text is None:
            return

        async def draft(text: str) -> None:
            await bot.send_rich_message_draft(
                chat_id=message.chat.id,
                draft_id=message.message_id,
                rich_message=InputRichMessage(
                    html=f"<tg-thinking>{text}</tg-thinking>"
                ),
            )

        reply = None
        try:
            await draft("Думаю...")
            async for event in api_client.chat_stream(
                message=message.text, telegram_id=message.from_user.id
            ):
                if event["type"] == "status":
                    await draft(event["text"])
                elif event["type"] == "reply":
                    reply = event["text"]
                elif event["type"] == "error":
                    await message.reply(
                        "Вибач, зараз не можу відповісти. Спробуй трохи пізніше."
                    )
                    return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                await message.reply("Спершу підтверди членство — натисни /start")
                return
            await message.reply(
                "Вибач, зараз не можу відповісти. Спробуй трохи пізніше."
            )
            return

    if not reply:
        await message.reply("Спершу підтверди членство — натисни /start")
        return
    logger.info("reply: %d символів, починається з %r", len(reply), reply[:80])
    await message.reply_rich(rich_message=InputRichMessage(markdown=reply))
