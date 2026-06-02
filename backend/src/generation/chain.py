import logging

from llama_index.core.llms import LLM

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

    async def generate_reply(self, message: str) -> str:
        payloads = await self.qdrant.search(query=message)

        if not payloads:
            return "Я не знайшов нічого релевантного у базі знань."

        chunks = []
        for p in payloads:
            chunk = f"[Джерело: {p.title} | {p.url}]\n{p.text}"
            chunks.append(chunk)

        context = "\n\n---\n\n".join(chunks)
        prompt = (
            self.system_prompt
            + f"\n\nКонтекст:\n{context}"
            + f"\n\nПитання: {message}"
            + "\n\nВідповідь:"
        )
        logger.debug(f"Prompt: {prompt}")

        response = await self.llm.acomplete(prompt=prompt)

        return response.text
