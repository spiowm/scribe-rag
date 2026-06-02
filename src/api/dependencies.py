from fastapi import Request

from src.connectors.notion import NotionConnector
from src.vectorstore.qdrant import QdrantRepository


def get_notion_connector(request: Request) -> NotionConnector:
    return request.app.state.notion


def get_qdrant_repository(request: Request) -> QdrantRepository:
    return request.app.state.qdrant
