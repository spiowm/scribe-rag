import html

import httpx
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from src.api_client import ApiClient

router = Router()

# Перше, що бачить людина після доступу. Відібрані прогоном через бота:
# стабільно дають відповідь із бази і працюють навіть для свіжого обзервера.
STARTER_QUESTIONS = [
    "Просмаж мене по-бестівськи 🔥",
    "Хто мій ангел і чим він відомий?",
    "Найдивніші рішення, які ухвалював осередок",
    "Іспит на фула: відповіді сховай 🔒",
]

# По одній у ряд, бо вдвох довші тексти обрізаються. Тап надсилає текст
# кнопки звичайним повідомленням, тож далі його обробляє chat.py.
starter_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=q)] for q in STARTER_QUESTIONS],
    resize_keyboard=True,
    one_time_keyboard=True,
    input_field_placeholder="Або напиши своє питання",
)


# Що вміє бот: після /start, після підтвердження членства й на /help.
# HTML, бо в звичайному повідомленні rich-розмітка не потрібна.
HELP = (
    "🤖 <b>Я асистент BEST Lviv</b>\n"
    "Знаю про людей, івенти, процеси й документи осередку: шукаю в Notion "
    "і на дисках, до яких маєш доступ.\n"
    "\n"
    "💬 <b>Як питати</b>\n"
    "Розмову пам'ятаю, тож можна уточнювати.\n"
    "<i>Думання</i> — що я шукав, 📚 <i>Джерела</i> — звідки відповідь. "
    "Тапни, щоб розгорнути.\n"
    "👍 / 👎 під відповіддю допомагають мені ставати кращим.\n"
    "\n"
    "⌨️ <b>Команди</b>\n"
    "/new — нова розмова з чистого аркуша\n"
    "/drives — де я шукаю, вмикай і вимикай диски\n"
    "/mcp_login — база BEST Lviv у твоєму Claude, Gemini чи ChatGPT\n"
    "/help — ця підказка"
)


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.reply(HELP, parse_mode="HTML")


@router.message(F.contact)
async def on_contact(message: types.Message, api_client: ApiClient):
    if message.from_user is None or message.contact is None:
        return
    c = message.contact
    if c.user_id != message.from_user.id:
        await message.answer("Поділись, будь ласка, своїм номером через кнопку")
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
            "Не знайшов цей номер в інфобуці BEST Lviv.\n\n"
            "Якщо ти член осередку, найпевніше в інфобуці записаний інший номер. Напиши @spiowm — оновимо, "
            "і тоді натисни /start ще раз.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await message.answer(
        f"Вітаю, {html.escape(user.first_name)}! Доступ відкрито.\n\n"
        f"{HELP}\n\n"
        "Тицьни питання знизу, щоб спробувати, або пиши своє.",
        parse_mode="HTML",
        reply_markup=starter_kb,
    )
