from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.connectors.mongo import MongoConnector
from src.connectors.notion import NotionConnector
from src.db import async_session_maker
from src.generation.chain import RagChain
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
