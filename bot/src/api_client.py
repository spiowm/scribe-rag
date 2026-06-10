import logging

import httpx

from src.schemas import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 40.0):
        # AsyncClient тримає пул з'єднань, створюється один раз
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def chat(self, message: str) -> str:
        request = ChatRequest(message=message)
        response = await self._client.post("/chat/", json=request.model_dump())
        response.raise_for_status()
        data = ChatResponse.model_validate(response.json())

        return data.reply

    async def close(self) -> None:
        await self._client.aclose()
