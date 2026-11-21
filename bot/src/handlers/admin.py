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
# Скільки символів журналу викликів і одного аргумента в ньому.
CALLS_HEAD, ARG_HEAD = 160, 40
# Значок і головний аргумент інструмента. Аргумент — за назвою, а не першим:
# JSONB зберігає ключі за довжиною, і в get_document першим стає `part`.
TOOLS = {
    "search_knowledge_base": ("🔎", "query"),
    "find_person": ("👤", "name"),
    "find_by_position": ("🏷", "position"),
    "get_document": ("📄", "source_id"),
    "query_members": ("📊", None),
}


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


def render_run(run: dict | None) -> str:
    """Журнал прогону одним рядком: що кликала модель і скільки це коштувало.

    Повний журнал (аргументи, source_id) — у `messages.run`, тут лише щоб
    побачити, чи шукала модель узагалі й чим.
    """
    if not run:
        return ""
    calls = []
    for call in run.get("calls") or []:
        args = call.get("args") or {}
        icon, key = TOOLS.get(call.get("tool"), ("⚙", None))
        value = args.get(key) if key else " ".join(map(str, args.values()))
        mark = "↻" if "repeat_of" in call else ("⚠" if "error" in call else "")
        calls.append(f"{icon}{mark} {str(value or '')[:ARG_HEAD]}".strip())
    line = " · ".join(calls) or "без інструментів"
    if len(line) > CALLS_HEAD:
        line = line[:CALLS_HEAD] + "…"
    tail = f"{run.get('steps', 0)} кр., {run.get('seconds', 0):.0f} с"
    if run.get("fallback"):
        tail += ", fallback"
    return f"\n<code>{html.escape(line)}</code> — {tail}"


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
    for key, text in (data.get("problems") or {}).items():
        lines.insert(1, f"⚠️ <b>{html.escape(key)}</b>: {html.escape(text)}")
    if data["rated"]:
        lines += ["", "<b>Останні оцінки</b>"]
    for row in data["rated"]:
        mark = MARKS.get(row["rating"], "•")
        question = html.escape((row["question"] or "—")[:QUESTION_HEAD])
        body = row["answer"].replace("\n", " ")
        cut = "…" if len(body) > ANSWER_HEAD else ""
        answer = html.escape(body[:ANSWER_HEAD])
        lines.append(
            f"{mark} <b>{question}</b>\n<i>{answer}{cut}</i>"
            + render_run(row.get("run"))
        )
    return "\n".join(lines)


@router.message(Command("admin"), F.from_user.id == settings.ADMIN_TELEGRAM_ID)
async def cmd_admin(message: types.Message, api_client: ApiClient):
    assert message.from_user
    data = await api_client.stats(message.from_user.id)
    if data is None:
        await message.reply("Бекенд не визнав мене адміном — звір ADMIN_TELEGRAM_ID")
        return
    await message.reply(render(data), parse_mode="HTML")
