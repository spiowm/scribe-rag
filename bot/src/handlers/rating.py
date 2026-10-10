import logging
from contextlib import suppress

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.api_client import ApiClient

logger = logging.getLogger(__name__)

router = Router()

MARKS = {1: "👍", -1: "👎"}


def keyboard(message_id: int, chosen: int | None = None) -> InlineKeyboardMarkup:
    """Кнопки оцінки під відповіддю.

    Після натискання обидві лишаються живими, а вибрану позначаємо галочкою:
    так видно, що оцінка зарахована, і можна передумати.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{mark} ✓" if value == chosen else mark,
                    callback_data=f"rate:{message_id}:{value}",
                )
                for value, mark in MARKS.items()
            ]
        ]
    )


@router.callback_query(F.data.startswith("rate:"))
async def handle_rating(query: types.CallbackQuery, api_client: ApiClient) -> None:
    """Зберігає оцінку й перемальовує кнопки.

    Телеграм чекає відповіді на колбек, інакше в клієнті висить годинник, —
    тому `query.answer()` є на кожному шляху, навіть на невдалому.
    """
    try:
        _, raw_id, raw_value = (query.data or "").split(":")
        message_id, value = int(raw_id), int(raw_value)
    except ValueError:
        await query.answer()
        return

    try:
        stored = await api_client.rate(query.from_user.id, message_id, value)
    except Exception:
        logger.warning("оцінка не збереглась", exc_info=True)
        await query.answer("Не вийшло зберегти, спробуй ще")
        return

    if not stored:
        # Бекенд не знайшов такої відповіді в сесіях цього юзера.
        await query.answer("Цю відповідь оцінити не вийде")
        return

    await query.answer("Дякую" if value > 0 else "Дякую, врахую")
    if query.message is None:
        return
    # Та сама оцінка вдруге дає «message is not modified» — це не помилка.
    with suppress(TelegramBadRequest):
        await query.message.edit_reply_markup(reply_markup=keyboard(message_id, value))
