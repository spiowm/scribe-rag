"""Адмінка доступів: права дисків і експорти Google-груп."""

import html
import logging
from datetime import datetime

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import InputRichMessage
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src import rich
from src.api_client import ApiClient
from src.config import settings

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(F.from_user.id == settings.ADMIN_TELEGRAM_ID)
router.callback_query.filter(F.from_user.id == settings.ADMIN_TELEGRAM_ID)

REFRESH = "access:refresh"
# Група в правах стількох дисків і більше — спільна (фули, адміни): вона є в
# таблиці груп, а в рядках дисків її не повторено.
SHARED_FROM = 3
# Експорт — знімок, і з часом він старіє. Після стількох днів — позначка.
STALE_DAYS = 60
EXPORT_LIMIT = 1024 * 1024
# Спільний початок адрес груп дисків: у таблиці дисків він не несе змісту.
GROUP_PREFIX = "drive-best-lviv-"


def _short(email: str) -> str:
    return email.removesuffix("@googlegroups.com")


def _status(group: str, groups: dict, by_status: dict) -> tuple[str, str]:
    """Позначка й стан експорту групи: (емодзі, опис)."""
    info = groups.get(group)
    if info is None and group in by_status:
        return "🪪", "статус " + ", ".join(by_status[group])
    if info is None:
        return "⚠️", "немає"
    age = (datetime.now() - datetime.fromisoformat(info["imported_at"])).days
    mark = "⏳" if age > STALE_DAYS else "✅"
    return mark, "сьогодні" if age < 1 else f"{age} дн."


def render(data: dict) -> str:
    """Markdown rich-повідомлення: таблиця груп і таблиця дисків."""
    drives, groups = data["drives"], data["groups"]
    by_status = data.get("status_groups") or {}
    if not drives:
        return "Дисків ще немає — натисни «Оновити права з Drive»."

    def is_group(email: str) -> bool:
        return email in groups or email.endswith("@googlegroups.com")

    usage: dict[str, int] = {}
    for d in drives:
        for p in filter(is_group, d["principals"]):
            usage[p] = usage.get(p, 0) + 1
    shared = {g for g, n in usage.items() if n >= SHARED_FROM}

    group_rows = []
    for g in sorted(usage, key=lambda g: (-usage[g], g)):
        mark, state = _status(g, groups, by_status)
        members = str(groups[g]["members"]) if g in groups else "—"
        group_rows.append([f"`{_short(g)}`", members, f"{mark} {state}", str(usage[g])])

    drive_rows = []
    for d in drives:
        own = sorted(p for p in d["principals"] if is_group(p) and p not in shared)
        names = [
            f"{_status(g, groups, by_status)[0]} "
            + _short(g).removeprefix(GROUP_PREFIX)
            for g in own
        ]
        people = sum(not is_group(p) for p in d["principals"])
        drive_rows.append(
            [
                d["name"].removeprefix("BEST Lviv - "),
                ", ".join(names) or "лише спільні",
                str(people) if people else "—",
            ]
        )

    refreshed = datetime.fromisoformat(data["refreshed_at"])
    lines = [
        "🔐 **Доступи до дисків**",
        "",
        f"_Права з Drive: {refreshed:%d.%m.%Y %H:%M}_",
        "",
        "**Групи з прав дисків**",
        "",
        *rich.table(["Група", "Людей", "Експорт", "Дисків"], group_rows),
        "",
        "**Диски**",
        "",
        *rich.table(["Диск", "Свої групи", "Напряму"], drive_rows),
        "",
        (
            f"Експорт — його вік, ⏳ — старший за {STALE_DAYS} днів. Групи з "
            f"{SHARED_FROM}+ дисків у рядках дисків не повторено, а "
            f"`{GROUP_PREFIX}` у назвах скорочено. «Напряму» — особисті пошти "
            "в правах диска."
        ),
        "",
        (
            "Щоб оновити групу, надішли сюди CSV-експорт учасників із Google "
            "Groups. Якщо назва групи у файлі не збігається з адресою — додай "
            "адресу підписом."
        ),
    ]
    return "\n".join(lines)


async def show(message: types.Message, text: str, edit: bool = False) -> None:
    """Rich-повідомлення з таблицями; якщо Телеграм markdown не прийняв —
    той самий текст без розмітки, щоб власник побачив хоч щось."""
    send = message.edit_text if edit else message.reply_rich
    try:
        await send(
            rich_message=InputRichMessage(markdown=rich.prepare(text)),
            reply_markup=keyboard(),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return  # «Оновити» двічі за хвилину: права ті самі
        logger.warning("/access: markdown не розібрався", exc_info=True)
        await send(
            rich_message=InputRichMessage(html=rich.plain(text)),
            reply_markup=keyboard(),
        )


def keyboard() -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Оновити права з Drive", callback_data=REFRESH)
    return kb.as_markup()


@router.message(Command("access"))
async def cmd_access(message: types.Message, api_client: ApiClient):
    assert message.from_user
    data = await api_client.admin_call("access", message.from_user.id)
    if "error" in data:
        await message.reply(f"Бекенд відмовив: {data['error']}")
        return
    await show(message, render(data))


@router.callback_query(F.data == REFRESH)
async def on_refresh(callback: types.CallbackQuery, api_client: ApiClient):
    await callback.answer("Читаю права дисків…")
    data = await api_client.admin_call("drives/refresh", callback.from_user.id)
    if not isinstance(callback.message, types.Message):
        return
    if "error" in data:
        # callback.answer уже був: другу відповідь на callback Телеграм не приймає
        await callback.message.answer(f"Не вийшло: {data['error']}")
        return
    await show(callback.message, render(data), edit=True)


def render_import(data: dict) -> str:
    skipped = ", ".join(f"{k}: {v}" for k, v in data["skipped"].items())
    drives = ", ".join(d.removeprefix("BEST Lviv - ") for d in data["drives"])
    lines = [
        f"✅ <b>{html.escape(_short(data['group']))}</b>: {data['members']} учасників",
        f"Диски: {html.escape(drives)}",
    ]
    if skipped:
        lines.append(f"Не враховано (немає доступу): {html.escape(skipped)}")
    if data["unmatched"]:
        lines += [
            "",
            (
                f"Немає в довіднику ({len(data['unmatched'])}) — доступу через "
                "бота не матимуть, доки пошта в інфобуці інша:"
            ),
            *(f"• <code>{html.escape(e)}</code>" for e in data["unmatched"]),
        ]
    return "\n".join(lines)


ERRORS = {
    "no_drives": "Спершу /access → «Оновити права з Drive»: без прав дисків "
    "невідомо, які групи щось відкривають.",
}


@router.message(F.document)
async def on_export(message: types.Message, bot: Bot, api_client: ApiClient):
    assert message.from_user and message.document
    doc = message.document
    if not (doc.file_name or "").lower().endswith(".csv"):
        await message.reply("Чекаю CSV-експорт учасників Google-групи.")
        return
    if (doc.file_size or 0) > EXPORT_LIMIT:
        await message.reply("Файл завеликий для експорту групи.")
        return
    file = await bot.download(doc)
    assert file
    content = file.read().decode("utf-8", errors="replace")
    data = await api_client.admin_call(
        "groups/import",
        message.from_user.id,
        content=content,
        group=(message.caption or "").strip() or None,
    )
    error = data.get("error")
    if error is None:
        await message.reply(render_import(data), parse_mode="HTML")
    elif error.startswith("unknown_group"):
        name = error.partition(":")[2].strip() or "без назви"
        await message.reply(
            f"Групи «{html.escape(name)}» немає в правах жодного диска. "
            "Надішли файл ще раз із підписом — адресою групи, як у /access.",
            parse_mode="HTML",
        )
    elif error.startswith("group_mismatch"):
        in_file = error.partition(":")[2].strip()
        await message.reply(
            f"У файлі — група «{html.escape(_short(in_file))}», а в підписі інша. "
            "Нічого не змінив: перевір, чи це той файл.",
            parse_mode="HTML",
        )
    else:
        await message.reply(ERRORS.get(error, f"Не вийшло: {html.escape(error)}"))
