from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config import settings

# Пул з'єднань до Postgres. Створюється 1 раз на весь проєкт
engine = create_async_engine(settings.POSTGRES_URL, echo=False)

async_session_maker = async_sessionmaker(engine, expire_on_commit=False)
