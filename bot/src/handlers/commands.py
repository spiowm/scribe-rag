from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

router = Router()


kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Поділитись своїм номером", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    name = message.from_user.first_name if message.from_user else "друже"
    await message.reply(
        text=f"Привіт, {name}! Я асистент BEST Lviv.\n"
        "Просто напиши питання — і я знайду відповідь у нашій базі знань.\n"
        "Щоб користуватись ботом, підтверди, що ти член BEST Lviv, надіславши номер телефону",
        reply_markup=kb,
    )
