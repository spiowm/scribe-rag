import httpx
from aiogram import Bot, Router, types
from aiogram.utils.chat_action import ChatActionSender

from src.api_client import ApiClient
from src.formatting import render

router = Router()


@router.message()
async def handle_chat(message: types.Message, api_client: ApiClient, bot: Bot):
    async with ChatActionSender.typing(chat_id=message.chat.id, bot=bot):
        if message.from_user is None or message.text is None:
            return
        try:
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
    for i, part in enumerate(await render(reply)):
        if i == 0:
            await message.reply(text=part.text, entities=part.entities)
        else:
            await message.answer(text=part.text, entities=part.entities)
