from aiogram import Router, types

from src.api_client import ApiClient

router = Router()


@router.message()
async def handle_chat(message: types.Message, api_client: ApiClient):
    text = message.text or ""
    response = await api_client.chat(text)
    await message.reply(text=response, parse_mode="markdown")
