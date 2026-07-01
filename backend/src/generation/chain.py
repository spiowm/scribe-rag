import logging

from llama_index.core.llms import LLM, ChatMessage, MessageRole

from src.models.message import Message
from src.vectorstore.qdrant import QdrantRepository

logger = logging.getLogger(__name__)


class RagChain:
    def __init__(self, llm: LLM, qdrant: QdrantRepository):
        self.llm = llm
        self.qdrant = qdrant
        # Системний промпт, який пояснює моделі, як поводитись
        self.system_prompt = """
Ти — корисний асистент організації BEST.
Використовуй ТІЛЬКИ наданий контекст для відповіді на питання.
Обов'язково посилайся на джерело для підтвердження відповіді та отримання додатквої інформації.
"""

    async def generate_reply(self, message: str, history: list[Message]) -> str:
        payloads = await self.qdrant.search(query=message)

        if not payloads:
            return "Я не знайшов нічого релевантного у базі знань."

        context = "\n\n---\n\n".join(
            f"[Джерело: {p.title} | {p.url}]\n{p.text}" for p in payloads
        )

        messages = [ChatMessage(role=MessageRole.SYSTEM, content=self.system_prompt)]

        for m in history:
            role = MessageRole.USER if m.role == "user" else MessageRole.ASSISTANT
            messages.append(ChatMessage(role=role, content=m.content))

        messages.append(
            ChatMessage(
                role=MessageRole.USER,
                content=f"Контекст:\n{context}\n\nПитання: {message}",
            )
        )

        response = await self.llm.achat(messages=messages)
        return response.message.content or "Вибач, не вдалось сформувати відповідь"
