from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from src.api_client import ApiClient

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


@router.message(Command("new"))
async def cmd_new(message: types.Message, api_client: ApiClient):
    assert message.from_user
    await api_client.new_session(message.from_user.id)
    await message.reply("Нова розмова розпочата")
