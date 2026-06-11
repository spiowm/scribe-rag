from aiogram import Router, types
from aiogram.filters import Command

router = Router()


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    name = message.from_user.first_name if message.from_user else "друже"
    await message.reply(
        f"Привіт, {name}! Я асистент BEST Lviv.\n"
        "Просто напиши питання — і я знайду відповідь у нашій базі знань."
    )
