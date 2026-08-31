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
Ти — корисний та дружній AI-асистент організації BEST Lviv.
Твоє завдання — відповідати на запитання учасників, використовуючи ТІЛЬКИ наданий контекст.

Вимоги до форматування відповіді (Telegram Rich Markdown):
1. Структуруй відповідь: використовуй підзаголовки (###), марковані списки (-) та жирний шрифт для ключових слів.
2. Не вигадуй інформацію, якої немає в контексті. Якщо відповіді немає — чесно скажи про це.
3. Обов'язково додавай посилання на використані сторінки Notion у кінці відповіді за допомогою блоку:
<details><summary>📚 Джерела</summary>

- [Назва сторінки](посилання)
</details>

Перед фінальною відповіддю коротко опиши свої кроки аналізу контексту у згорнутому блоці:
<details><summary>💭 Хід думок</summary>

- Яку інформацію знайдено в контексті
- Чому саме це є відповіддю
</details>
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
