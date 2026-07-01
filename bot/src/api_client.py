import logging

import httpx

from src.schemas import (
    ChatRequest,
    ChatResponse,
    LinkRequest,
    UserResponse,
)

logger = logging.getLogger(__name__)


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 40.0):
        # AsyncClient тримає пул з'єднань, створюється один раз
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def link(
        self, telegram_id: int, telegram_username: str, phone: str
    ) -> UserResponse | None:
        request = LinkRequest(
            telegram_id=telegram_id,
            telegram_username=telegram_username,
            phone=phone,
        )
        response = await self._client.post("/auth/link", json=request.model_dump())
        if response.status_code == 403:
            return None
        response.raise_for_status()

        return UserResponse.model_validate(response.json())

    async def new_session(self, telegram_id: int) -> None:
        response = await self._client.post(
            "/chat/session/new", json={"telegram_id": telegram_id}
        )
        response.raise_for_status()

    async def chat(self, message: str, telegram_id: int) -> str | None:
        request = ChatRequest(telegram_id=telegram_id, message=message)
        response = await self._client.post("/chat/", json=request.model_dump())
        if response.status_code == 403:
            return None
        response.raise_for_status()
        data = ChatResponse.model_validate(response.json())

        return data.reply

    async def close(self) -> None:
        await self._client.aclose()
