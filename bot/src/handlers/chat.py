import httpx
from aiogram import Bot, Router, types
from aiogram.utils.chat_action import ChatActionSender

from src.api_client import ApiClient
from src.formatting import render

router = Router()


@router.message()
async def handle_chat(message: types.Message, api_client: ApiClient, bot: Bot):
    async with ChatActionSender.typing(chat_id=message.chat.id, bot=bot):
        try:
            reply = await api_client.chat(message.text or "")
        except httpx.HTTPError:
            await message.reply(
                "Вибач, зараз не можу відповісти. Спробуй трохи пізніше."
            )
            return

    for i, part in enumerate(await render(reply)):
        if i == 0:
            await message.reply(text=part.text, entities=part.entities)
        else:
            await message.answer(text=part.text, entities=part.entities)
