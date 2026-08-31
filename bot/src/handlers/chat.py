import httpx
from aiogram import Bot, Router, types
from aiogram.types import InputRichMessage
from aiogram.utils.chat_action import ChatActionSender

from src.api_client import ApiClient

router = Router()


@router.message()
async def handle_chat(message: types.Message, api_client: ApiClient, bot: Bot):
    async with ChatActionSender.typing(chat_id=message.chat.id, bot=bot):
        if message.from_user is None or message.text is None:
            return
        try:
            await bot.send_rich_message_draft(
                chat_id=message.chat.id,
                draft_id=message.message_id,
                rich_message=InputRichMessage(
                    html="<tg-thinking>Шукаю відповідь у базі знань...</tg-thinking>"
                ),
            )

            reply = await api_client.chat(
                message=message.text, telegram_id=message.from_user.id
            )
        except httpx.HTTPError:
            await message.reply(
                "Вибач, зараз не можу відповісти. Спробуй трохи пізніше."
            )
            return

    if reply is None:
        await message.reply("Спершу підтверди членство — натисни /start")
        return
    await message.reply_rich(rich_message=InputRichMessage(markdown=reply))
