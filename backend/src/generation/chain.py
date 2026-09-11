import asyncio
import logging
from datetime import UTC, datetime

from google import genai
from google.genai import types

from src.connectors.mongo import MongoConnector
from src.generation import tools
from src.generation.prompts import GLOSSARY, ORG_PRIMER, SYSTEM_PROMPT
from src.models.message import Message
from src.vectorstore.qdrant import QdrantRepository

logger = logging.getLogger(__name__)


class RagChain:
    def __init__(
        self,
        client: genai.Client,
        model: str,
        qdrant: QdrantRepository,
        mongo: MongoConnector,
    ):
        self.client = client
        self.model = model
        self.qdrant = qdrant
        self.mongo = mongo
        self.system_prompt = SYSTEM_PROMPT + ORG_PRIMER + GLOSSARY

        self.config = types.GenerateContentConfig(
            tools=[tools.SEARCH_TOOL, tools.FIND_PERSON_TOOL],
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
            # Відсутності інструментів замало: модель повторює патерн із розмови
            # і все одно просить виклик, а тоді текстових частин немає взагалі.
            # NONE забороняє це явно й змушує відповісти словами.
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.NONE
                )
            ),
        )

    def _system_instruction(self) -> str:
        """Системний промпт із поточною датою.

        Дата підставляється на кожен запит, а не в __init__: процес живе
        тижнями, і зафіксована на старті дата з часом почала б брехати.
        """
        today = datetime.now(UTC).strftime("%d.%m.%Y")
        return (
            f"{self.system_prompt}\n\n"
            "## Поточна дата\n\n"
            f"Сьогодні {today}.\n\n"
            "Сторінки бази знань писалися в різний час, і час у них — на момент "
            "написання. Якщо в джерелі стоїть «відбудеться», «скоро» чи «наступного "
            "місяця», звір із сьогоднішньою датою: подія могла вже минути. "
            "У такому разі так і кажи — «за базою планувалося на <дата>, тобто це вже "
            "минуло; підсумків у базі немає» — замість того щоб переказувати "
            "джерело в майбутньому часі."
        )

    async def _dispatch(self, fc) -> dict:
        if fc.name == "search_knowledge_base":
            return await tools.run_search(self.qdrant, (fc.args or {}).get("query", ""))
        if fc.name == "find_person":
            return await tools.run_find_person(
                self.mongo, (fc.args or {}).get("name", "")
            )
        logger.warning("невідомий інструмент: %r", fc.name)
        return {"error": f"невідомий інструмент: {fc.name}"}

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
                config=self.config.model_copy(
                    update={"system_instruction": self._system_instruction()}
                ),
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
            results = await asyncio.gather(*(self._dispatch(fc) for fc in calls))
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                name=fc.name, response=r
                            )
                        )
                        for fc, r in zip(calls, results)
                    ],
                )
            )
        # Ітерації вичерпані. Замість шаблонної відмови — останній виклик
        # без інструментів: модель мусить відповісти тим, що вже назбирала.
        logger.warning(
            "agent: вичерпано %d ітерацій, відповідаю зібраним", num_iterations
        )
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=contents,
            config=self.final_config.model_copy(
                update={"system_instruction": self._system_instruction()}
            ),
        )
        return response.text or "Вибач, не вдалось сформувати відповідь"
