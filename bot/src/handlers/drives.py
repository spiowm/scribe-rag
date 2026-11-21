"""Де шукати: що завжди, диски людини з перемикачами й диски без доступу."""

import logging

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import InputRichMessage
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src import rich
from src.api_client import ApiClient
from src.handlers.access import GROUP_PREFIX

logger = logging.getLogger(__name__)

router = Router()

# drive_id — 19 символів, разом із префіксом ~25 байт із 64 дозволених.
PREFIX = "drive:"


def _short(reason: str) -> str:
    """`група drive-best-lviv-btw` → `група btw`."""
    return reason.replace(GROUP_PREFIX, "")


def render(view: dict) -> tuple[str, types.InlineKeyboardMarkup | None]:
    """Markdown rich-повідомлення й кнопки дисків, які можна перемкнути."""
    # Між рядками — порожній: один перенос абзацу в rich-повідомленні не рве.
    lines = [
        "📚 **Де я шукаю для тебе**",
        "",
        "🔒 **Завжди, для всіх** (не вимикається)",
        "",
        " · ".join(view["locked"]),
    ]
    if not view["recognized"]:
        lines += ["", "_Тебе немає в довіднику, тож з дисків — лише ці._"]
    drives = view["drives"]
    if drives:
        rows = [
            [
                d["name"],
                "✅ шукаю" if d["on"] else "⏸ вимкнув ти",
                ", ".join(map(_short, d["via"])),
            ]
            for d in drives
        ]
        lines += [
            "",
            "**Твої диски**",
            "",
            *rich.table(["Диск", "Стан", "Доступ через"], rows),
        ]
    if view["no_access"]:
        rows = [
            [d["name"], ", ".join(map(_short, d["how"])) or "—"]
            for d in view["no_access"]
        ]
        lines += [
            "",
            "🚫 **Немає доступу**",
            "",
            *rich.table(["Диск", "Доступ дає"], rows),
        ]
    if not drives:
        return "\n".join(lines), None

    lines += ["", "Вимкни зайве — тоді відповіді точніші."]
    kb = InlineKeyboardBuilder()
    for d in drives:
        kb.button(
            text=f"{'✅' if d['on'] else '⏸'} {d['name']}",
            callback_data=PREFIX + d["id"],
        )
    kb.adjust(3)
    return "\n".join(lines), kb.as_markup()


async def show(message: types.Message, view: dict, edit: bool = False) -> None:
    """Як у /access: якщо Телеграм markdown не прийняв — той самий текст без
    розмітки."""
    text, kb = render(view)
    send = message.edit_text if edit else message.reply_rich
    try:
        await send(
            rich_message=InputRichMessage(markdown=rich.prepare(text)),
            reply_markup=kb,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        logger.warning("/drives: markdown не розібрався", exc_info=True)
        await send(
            rich_message=InputRichMessage(html=rich.plain(text)), reply_markup=kb
        )


@router.message(Command("drives"))
async def cmd_drives(message: types.Message, api_client: ApiClient):
    assert message.from_user
    view = await api_client.drives(message.from_user.id)
    if view is None:
        await message.reply("Спершу підтверди, що ти член BEST Lviv: /start")
        return
    await show(message, view)


@router.callback_query(F.data.startswith(PREFIX))
async def on_toggle(callback: types.CallbackQuery, api_client: ApiClient):
    assert callback.data
    view = await api_client.toggle_drive(
        callback.from_user.id, callback.data.removeprefix(PREFIX)
    )
    if view is None:
        await callback.answer("До цього диска в тебе немає доступу", show_alert=True)
        return
    await callback.answer()
    if isinstance(callback.message, types.Message):
        await show(callback.message, view, edit=True)
