import asyncio
import logging
import time

import httpx
from aiogram import Bot, Router, types
from aiogram.types import InputRichMessage
from aiogram.utils.chat_action import ChatActionSender

from src.api_client import ApiClient


logger = logging.getLogger(__name__)

router = Router()


@router.message()
async def handle_chat(message: types.Message, api_client: ApiClient, bot: Bot):
    async with ChatActionSender.typing(chat_id=message.chat.id, bot=bot):
        if message.from_user is None or message.text is None:
            return

        async def draft(text: str) -> None:
            """Чернетка «Думаю…» — окраса, і її збій не має валити відповідь.

            На довгій відповіді тикер стукає сюди щодві секунди, тобто на
            двохвилинній генерації це шістдесят викликів до Телеграма. Один
            колись відвалюється по таймауту, і раніше TelegramNetworkError
            вилітав із хендлера разом із готовою відповіддю.
            """
            try:
                await bot.send_rich_message_draft(
                    chat_id=message.chat.id,
                    draft_id=message.message_id,
                    rich_message=InputRichMessage(
                        html=f"<tg-thinking>{text}</tg-thinking>"
                    ),
                )
            except Exception:
                logger.debug("чернетка не оновилась", exc_info=True)

        reply = None
        state = "Думаю…"
        started = time.monotonic()

        async def ticker() -> None:
            """Цокає секунди в чернетці, поки ланцюг мовчить."""
            while True:
                await asyncio.sleep(2)
                await draft(f"{state} · {int(time.monotonic() - started)} с")

        tick = asyncio.create_task(ticker())
        try:
            # Чернетку не малюємо одразу: мем приходить на ~0.7 с, а тикер
            # спить дві секунди перед першим малюнком — так картинка
            # виявляється в чаті першою. Проміжок прикриває індикатор набору.
            async for event in api_client.chat_stream(
                message=message.text, telegram_id=message.from_user.id
            ):
                if event["type"] == "status":
                    state = event["text"]
                    await draft(f"{state} · {int(time.monotonic() - started)} с")
                elif event["type"] == "meme":
                    # Окремим повідомленням, а не в чернетці: чернетка вміє
                    # показувати лише файли, які Телеграм уже має, а новий
                    # presigned URL для неї — «новий файл» і відхиляється.
                    caption = "\n\n".join(
                        part
                        for part in (
                            event["caption"],
                            f'<a href="{event["link"]}">🔗 пост у каналі</a>',
                        )
                        if part
                    )
                    try:
                        await message.answer_photo(
                            photo=event["url"], caption=caption, parse_mode="HTML"
                        )
                    except Exception:
                        # Мем це окраса: якщо Телеграм не забрав картинку з R2,
                        # відповідь однаково мусить доїхати.
                        logger.warning("мем не надіслався", exc_info=True)
                elif event["type"] == "reply":
                    reply = event["text"]
                elif event["type"] == "error":
                    await message.reply(
                        "Вибач, зараз не можу відповісти. Спробуй трохи пізніше."
                    )
                    return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                # Ще пишемо попередню відповідь. Реакція замість тексту, щоб
                # десяток надісланих поспіль повідомлень не дав десяток реплаїв.
                await message.react([types.ReactionTypeEmoji(emoji="💅")])
                return
            elif exc.response.status_code == 403:
                await message.reply("Спершу підтверди членство — натисни /start")
                return
            await message.reply(
                "Вибач, зараз не можу відповісти. Спробуй трохи пізніше."
            )
            return
        finally:
            tick.cancel()

    if not reply:
        await message.reply("Спершу підтверди членство — натисни /start")
        return
    logger.info("reply: %d символів, починається з %r", len(reply), reply[:80])
    await message.reply_rich(rich_message=InputRichMessage(markdown=reply))
