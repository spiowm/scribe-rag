import asyncio
import logging

from aiogram import Bot, Dispatcher

from src.api_client import ApiClient
from src.config import settings
from src.handlers import chat

logging.basicConfig(level=logging.INFO)

bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher()

dp.include_router(chat.router)


async def on_startup(dispatcher: Dispatcher) -> None:
    dispatcher["api_client"] = ApiClient(base_url=settings.BACKEND_URL)


async def on_shutdown(dispatcher: Dispatcher) -> None:
    await dispatcher["api_client"].close()


dp.startup.register(on_startup)
dp.shutdown.register(on_shutdown)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
