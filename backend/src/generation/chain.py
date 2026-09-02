import logging

from llama_index.core.llms import LLM, ChatMessage, MessageRole

from src.generation.prompts import CONDENSE_PROMPT, GLOSSARY, SYSTEM_PROMPT
from src.models.message import Message
from src.vectorstore.qdrant import QdrantRepository

logger = logging.getLogger(__name__)


class RagChain:
    def __init__(self, llm: LLM, condense_llm: LLM, qdrant: QdrantRepository):
        self.llm = llm
        self.condense_llm = condense_llm
        self.qdrant = qdrant
        self.system_prompt = SYSTEM_PROMPT + GLOSSARY

    async def _condense_question(self, message: str, history: list[Message]) -> str:
        """Метод для перефразування питання у коротку форму, враховуючи історію чатів."""

        if not history:
            return message

        history_str = "\n".join(
            f"{m.role}: {m.content[:300] if m.role == 'assistant' else m.content}"
            for m in history[-4:]
        )

        response = await self.condense_llm.achat(
            messages=[
                ChatMessage(
                    role=MessageRole.USER,
                    content=CONDENSE_PROMPT.format(
                        history=history_str, question=message
                    ),
                )
            ]
        )

        rewritten = response.message.content or message
        logger.info("condense: %r -> %r", message, rewritten)
        return rewritten

    async def generate_reply(self, message: str, history: list[Message]) -> str:
        query = await self._condense_question(message, history)
        payloads = await self.qdrant.search(query=query)

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
