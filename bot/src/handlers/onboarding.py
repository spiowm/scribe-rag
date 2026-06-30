import httpx
from aiogram import F, Router, types
from aiogram.types import ReplyKeyboardRemove

from src.api_client import ApiClient

router = Router()


@router.message(F.contact)
async def on_contact(message: types.Message, api_client: ApiClient):
    if message.from_user is None or message.contact is None:
        return
    c = message.contact
    if c.user_id != message.from_user.id:
        await message.answer("Поділись, будь ласка, *своїм* номеро через кнопку")
        return

    try:
        user = await api_client.link(
            telegram_id=message.from_user.id,
            telegram_username=message.from_user.username or "",
            phone=c.phone_number,
        )
    except httpx.HTTPError:
        await message.answer(
            "Не вдалось перевірити зараз. Спробуй пізніше.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if user is None:  # не член
        await message.answer(
            "На жаль, твого номера нема в інфобуці BEST Lviv",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await message.answer(
        f"Вітаю, {user.first_name}! Доступ відкрито. Можеш задавати питання.",
        reply_markup=ReplyKeyboardRemove(),
    )
