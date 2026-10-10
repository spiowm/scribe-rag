import html
import logging

from aiogram import Bot, F, Router, types
from aiogram.filters import Command

from src.api_client import ApiClient
from src.config import settings

logger = logging.getLogger(__name__)

router = Router()

MARKS = {1: "👍", -1: "👎"}
# Скільки символів питання й відповіді лізе в рядок зведення.
QUESTION_HEAD, ANSWER_HEAD = 70, 90


async def alert(bot: Bot, message: types.Message, reason: str) -> None:
    """Каже власнику, що комусь не відповіли.

    AGY_FALLBACK_TO_API вимкнений, тож збій це не дорожча відповідь, а
    відсутня: юзер бачить «спробуй пізніше». Без цього про таке дізнаєшся
    лише зі скарги, та й то не завжди.
    """
    if settings.ADMIN_TELEGRAM_ID is None:
        return
    user = message.from_user
    who = (
        f"@{user.username}"
        if user and user.username
        else f"id {user.id if user else '?'}"
    )
    text = (
        "⚠️ <b>Бот не відповів</b>\n\n"
        f"Кому: {html.escape(who)}\n"
        f"Питання: {html.escape((message.text or '')[:200])}\n"
        f"Причина: {html.escape(reason[:300])}"
    )
    try:
        await bot.send_message(settings.ADMIN_TELEGRAM_ID, text, parse_mode="HTML")
    except Exception:
        # Сповіщення про збій не має ставати ще одним збоєм.
        logger.warning("сповіщення власнику не дійшло", exc_info=True)


def render(data: dict) -> str:
    """Малює зведення. Бекенд віддає лише цифри, вигляд — тут."""
    median = data.get("median_secs")
    lines = [
        f"📊 <b>Зведення за {data['days']} дн.</b>",
        "",
        f"Людей: {data['users']} · питали {data['users_active']}",
        (
            f"Питань: {data['questions_window']}"
            f" (сьогодні {data['questions_today']}, усього {data['questions']})"
        ),
        f"Медіана відповіді: {median:.0f} с" if median else "Медіана відповіді: —",
        f"Оцінки: 👍 {data['likes']} · 👎 {data['dislikes']}",
    ]
    if data["rated"]:
        lines += ["", "<b>Останні оцінки</b>"]
    for row in data["rated"]:
        mark = MARKS.get(row["rating"], "•")
        question = html.escape((row["question"] or "—")[:QUESTION_HEAD])
        body = row["answer"].replace("\n", " ")
        cut = "…" if len(body) > ANSWER_HEAD else ""
        answer = html.escape(body[:ANSWER_HEAD])
        lines.append(f"{mark} <b>{question}</b>\n<i>{answer}{cut}</i>")
    return "\n".join(lines)


@router.message(Command("admin"), F.from_user.id == settings.ADMIN_TELEGRAM_ID)
async def cmd_admin(message: types.Message, api_client: ApiClient):
    assert message.from_user
    data = await api_client.stats(message.from_user.id)
    if data is None:
        await message.reply("Бекенд не визнав мене адміном — звір ADMIN_TELEGRAM_ID")
        return
    await message.reply(render(data), parse_mode="HTML")
