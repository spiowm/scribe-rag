import logging

from google import genai
from google.genai import types

from src.generation.prompts import GLOSSARY, SYSTEM_PROMPT
from src.models.message import Message
from src.vectorstore.qdrant import QdrantRepository

logger = logging.getLogger(__name__)


SEARCH_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="search_knowledge_base",
            description=(
                "Шукає у базі знань BEST Lviv — сторінки Notion про процеси, івенти, "
                "ролі, людей, історію та традиції осередку.\n"
                "Викликай не лише тоді, коли потрібні факти, а й коли готуєш ідеї, "
                "план, аналіз чи пораду: щоб спиратись на те, як усе влаштовано саме "
                "в BEST, а не на загальні уявлення. Для ідей шукай схожі івенти й "
                "наявні напрацювання, для аналізу — як цей процес описаний у нас."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description="Самодостатній пошуковий запит українською",
                    )
                },
                required=["query"],
            ),
        )
    ]
)


class RagChain:
    def __init__(self, client: genai.Client, model: str, qdrant: QdrantRepository):
        self.client = client
        self.model = model
        self.qdrant = qdrant
        self.system_prompt = SYSTEM_PROMPT + GLOSSARY

        self.config = types.GenerateContentConfig(
            tools=[SEARCH_TOOL],
            system_instruction=self.system_prompt,
            temperature=0.2,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

        # Той самий конфіг, але без інструментів — для останнього виклику,
        # коли цикл вичерпав ітерації й моделі треба відповісти зібраним.
        self.final_config = types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            temperature=0.2,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

    async def _run_search(self, query: str) -> dict:
        """Виконує пошук і готує результат для моделі."""
        try:
            hits = await self.qdrant.search(query)
        except Exception:
            logger.exception("tool search failed: %r", query)
            return {"error": "пошук тимчасово недоступний"}

        logger.info("tool search: %r -> %d фрагментів", query, len(hits))
        return {
            "results": [
                {
                    "title": h.chunk.title,
                    "url": h.chunk.url,
                    "text": h.chunk.text[:1500],
                }
                for h in hits
            ]
        }

    async def generate_reply(self, message: str, history: list[Message]) -> str:

        role_map = {"user": "user", "assistant": "model"}

        contents: list[types.ContentUnion] = [
            types.Content(
                role=role_map[m.role],
                parts=[types.Part(text=m.content)],
            )
            for m in history
        ]

        contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part(text=message),
                ],
            )
        )

        num_iterations = 4
        for _ in range(num_iterations):
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=self.config,
            )
            candidate = response.candidates[0].content if response.candidates else None
            if candidate is None:
                return "Вибач, не вдалось сформувати відповідь"
            calls = [
                p.function_call for p in (candidate.parts or []) if p.function_call
            ]

            if not calls:
                return response.text or "Я не зміг згенерувати відповідь"

            contents.append(candidate)
            for fc in calls:
                query = (fc.args or {}).get("query")
                if not query:
                    continue
                result = await self._run_search(query=query)
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name=fc.name,
                                    response=result,
                                )
                            )
                        ],
                    )
                )
        # Ітерації вичерпані. Замість шаблонної відмови — останній виклик
        # без інструментів: модель мусить відповісти тим, що вже назбирала.
        logger.warning("agent: вичерпано %d ітерацій, відповідаю зібраним", num_iterations)
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=contents,
            config=self.final_config,
        )
        return response.text or "Вибач, не вдалось сформувати відповідь"
