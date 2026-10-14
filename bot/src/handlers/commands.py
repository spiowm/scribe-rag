from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from src.api_client import ApiClient

router = Router()


kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Поділитись своїм номером", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)


# Посилання зі сторінки входу: t.me/<бот>?start=mcp_login. Стоїть вище за
# cmd_start, бо Command("start") теж зловив би /start mcp_login.
@router.message(CommandStart(deep_link=True, magic=F.args == "mcp_login"))
@router.message(Command("mcp_login"))
async def cmd_mcp_login(message: types.Message, api_client: ApiClient):
    assert message.from_user
    code = await api_client.login_code(message.from_user.id)
    if code is None:
        await message.reply(
            "Спершу підтверди, що ти член BEST Lviv: поділись номером телефону.",
            reply_markup=kb,
        )
        return
    await message.reply(
        f"Код для входу: <code>{code}</code>\n"
        "Діє 5 хвилин і спрацює один раз. Торкнись коду, щоб скопіювати.",
        parse_mode="HTML",
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
