import html
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router, types
from aiogram.filters import Command, CommandObject

from src.api_client import ApiClient
from src.config import settings

logger = logging.getLogger(__name__)

router = Router()

KYIV = ZoneInfo("Europe/Kyiv")
AUTOSYNC_USAGE = "/autosync 24 — синк кожні 24 год, /autosync off — вимкнути."
SOURCES = {"notion": "Notion", "gdrive": "Drive"}

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


def _when(iso: str) -> str:
    return f"{datetime.fromisoformat(iso).astimezone(KYIV):%d.%m %H:%M}"


def render_autosync(state: dict) -> str:
    hours = state.get("hours") or 0
    last = state.get("last_started")
    if not hours:
        lines = ["🔄 Автосинк вимкнено."]
    elif last:
        next_run = datetime.fromisoformat(last) + timedelta(hours=hours)
        lines = [
            f"🔄 Автосинк кожні {hours} год, наступний ≈ {_when(next_run.isoformat())}."
        ]
    else:
        lines = [f"🔄 Автосинк кожні {hours} год, перший — за кілька хвилин."]

    result, finished = state.get("last_result"), state.get("last_finished")
    if last and (not finished or finished < last):
        lines.append(f"Синк від {_when(last)} ще триває.")
    if result and finished:
        lines.append(f"\nОстанній синк, завершено {_when(finished)}:")
        for key, name in SOURCES.items():
            r = result.get(key) or {}
            if "error" in r:
                lines.append(f"✗ {name}: {html.escape(r['error'])}")
                continue
            line = (
                f"✓ {name}: змінено {r.get('changed', 0)}, "
                f"прибрано {r.get('removed', 0)}, збоїв {r.get('failed', 0)}"
            )
            if r.get("ocr_failed"):
                line += f", OCR не вдався {r['ocr_failed']}"
            lines.append(line)
        drives = result.get("drives") or {}
        if "error" in drives:
            lines.append(f"✗ Права дисків: {html.escape(drives['error'])}")
        else:
            lines.append("✓ Права дисків оновлено")
    lines.append(f"\n<i>{AUTOSYNC_USAGE}</i>")
    return "\n".join(lines)


@router.message(Command("autosync"), F.from_user.id == settings.ADMIN_TELEGRAM_ID)
async def cmd_autosync(
    message: types.Message, command: CommandObject, api_client: ApiClient
):
    assert message.from_user
    arg = (command.args or "").strip().lower()
    if not arg:
        data = await api_client.admin_call("autosync", message.from_user.id)
    elif arg in ("off", "0") or (arg.isdigit() and 0 < int(arg) <= 24 * 30):
        hours = 0 if arg == "off" else int(arg)
        data = await api_client.admin_call(
            "autosync/set", message.from_user.id, hours=hours
        )
    else:
        await message.reply(f"Не зрозумів. {AUTOSYNC_USAGE}")
        return
    if "error" in data:
        await message.reply(f"Бекенд відмовив: {data['error']}")
        return
    await message.reply(render_autosync(data), parse_mode="HTML")
