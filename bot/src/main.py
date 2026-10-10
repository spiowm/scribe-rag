import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)

from src.api_client import ApiClient
from src.config import settings
from src.handlers import admin, chat, commands, onboarding, rating

logging.basicConfig(level=logging.INFO)

bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher()

dp.include_router(commands.router)
dp.include_router(onboarding.router)
dp.include_router(rating.router)
dp.include_router(admin.router)
# chat.router останній: у ньому @router.message() ловить усе, що не розібрали вище.
dp.include_router(chat.router)


async def on_startup(dispatcher: Dispatcher, bot: Bot) -> None:
    dispatcher["api_client"] = ApiClient(base_url=settings.BACKEND_URL)
    # Меню команд живе на боці Телеграма, тому ставиться при старті.
    menu = [
        BotCommand(command="start", description="Старт"),
        BotCommand(command="new", description="Нова розмова"),
    ]
    await bot.set_my_commands(menu, scope=BotCommandScopeAllPrivateChats())
    if settings.ADMIN_TELEGRAM_ID:
        # Окремою областю, а не в загальному меню: /admin не має світитись усім.
        await bot.set_my_commands(
            [*menu, BotCommand(command="admin", description="Зведення")],
            scope=BotCommandScopeChat(chat_id=settings.ADMIN_TELEGRAM_ID),
        )


async def on_shutdown(dispatcher: Dispatcher) -> None:
    await dispatcher["api_client"].close()


dp.startup.register(on_startup)
dp.shutdown.register(on_shutdown)


async def main():
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
