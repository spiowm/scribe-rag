from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from src.connectors.mongo import MongoConnector
from src.connectors.notion import NotionConnector
from src.db import async_session_maker
from src.generation.chain import RagChain
from src.models.user import User
from src.repository import users
from src.vectorstore.qdrant import QdrantRepository


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


def get_notion_connector(request: Request) -> NotionConnector:
    return request.app.state.notion


def get_qdrant_repository(request: Request) -> QdrantRepository:
    return request.app.state.qdrant


def get_rag_chain(request: Request) -> RagChain:
    return request.app.state.rag_chain


def get_mongo(request: Request) -> MongoConnector:
    return request.app.state.mongo


async def get_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    body = await request.json()
    telegram_id = body.get("telegram_id")
    if not telegram_id:
        raise HTTPException(422, "telegram_id is required")
    user = await users.get_user_by_telegram_id(db, telegram_id)
    if user is None:
        raise HTTPException(403, "not_linked")
    return user
