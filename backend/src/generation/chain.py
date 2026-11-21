import asyncio
import contextvars
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from google import genai
from google.genai import types

from src.config import settings
from src.connectors.mongo import MongoConnector
from src.generation import agy_protocol, models_check, tools
from src.generation.agy_process import AgyError, AgyProcess
from src.generation.prompts import BOT_PROMPT, user_context
from src.models.message import Message
from src.vectorstore.qdrant import QdrantRepository

logger = logging.getLogger(__name__)

KYIV = ZoneInfo("Europe/Kyiv")
NUM_ITERATIONS = 8  # складний шлях буває в 4 кроки — береться із запасом удвічі

# Чим відповіли на цей запит — для інформаційного рядка під повідомленням.
# Контекстна змінна, а не поле класу: RagChain один на застосунок, і два
# одночасні запити перемішали б дані.
LAST_RUN: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "last_run", default=None
)


def current_run() -> dict:
    """Запис про поточний запит. Поза запитом створюємо власний, щоб не писати
    у спільний словник за замовчуванням."""
    run = LAST_RUN.get()
    if run is None:
        run = {}
        LAST_RUN.set(run)
    return run


def _add_tokens(run: dict, result: dict) -> None:
    """Складає токени ходу. `delta` — ціна саме цього кроку: лічильник usage
    в agy накопичувальний на розмову, тож різницю рахує AgyProcess."""
    delta = result.get("delta") or {}
    tokens = run.setdefault("tokens", {"input": 0, "cache": 0, "output": 0})
    tokens["input"] += delta.get("input_tokens", 0)
    tokens["cache"] += delta.get("cache_read_tokens", 0)
    tokens["output"] += delta.get("output_tokens", 0)


def _note_result(entry: dict, result: dict) -> None:
    """Що з результату лишається в журналі: розмір, знайдені документи, помилка.

    Сам текст не зберігається — це обсяг і зайві копії персональних даних.
    """
    entry["chars"] = len(json.dumps(result, ensure_ascii=False, default=str))
    if "results" in result:
        # кілька фрагментів одного документа — один source_id
        entry["source_ids"] = list(
            dict.fromkeys(r["source_id"] for r in result["results"])
        )
    if "error" in result:
        entry["error"] = result["error"]


def run_record(run: dict, seconds: float) -> dict:
    """Що зберегти біля відповіді, щоб потім розібрати 👎 без повторного прогону."""
    record = {
        "provider": run.get("provider"),
        "model": run.get("model"),
        "fallback": run.get("fallback", False),
        "steps": run.get("steps", 0),
        "seconds": round(seconds, 1),
        "calls": run.get("journal") or [],
    }
    for key in ("tokens", "errors"):  # токени рахує лише agy, помилки — рідкість
        if run.get(key):
            record[key] = run[key]
    return record


# Один процес agy тримає ~240 МБ, тож п'ять одночасних — це вже 1.4 ГБ.
# Семафор не пришвидшує відповіді, а не дає піку з'їсти машину: зайві повідомлення чекають своєї черги.
AGY_SEMAPHORE = asyncio.Semaphore(settings.AGY_MAX_PROCS)


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
        self.system_prompt = BOT_PROMPT

        self.config = types.GenerateContentConfig(
            tools=[*tools.DECLARATIONS],
            system_instruction=self.system_prompt,
            thinking_config=types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.LOW
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

        # Той самий конфіг, але без інструментів — для останнього виклику,
        # коли цикл вичерпав ітерації й моделі треба відповісти зібраним.
        self.final_config = types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            thinking_config=types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.LOW
            ),
            # Відсутності інструментів замало: модель повторює патерн із розмови
            # і все одно просить виклик, а тоді текстових частин немає взагалі.
            # NONE забороняє це явно й змушує відповісти словами.
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.NONE
                )
            ),
        )

        # Для agy: інструменти текстом і схема відповіді — з тих самих декларацій.
        self.provider = settings.LLM_PROVIDER
        if self.provider == "agy":
            self.agy_tools_doc = agy_protocol.render_tools_doc(tools.DECLARATIONS)
            schema_file = Path(settings.AGY_HOME) / "scribe-schema.json"
            # На чистій машині AGY_HOME ще не існує, і бекенд падав на старті.
            schema_file.parent.mkdir(parents=True, exist_ok=True)
            schema_file.write_text(
                json.dumps(
                    agy_protocol.build_schema(tools.DECLARATIONS), ensure_ascii=False
                ),
                encoding="utf-8",
            )
            self.agy_schema_path = str(schema_file)

    def _system_instruction(self, member: dict | None = None, drives: str = "") -> str:
        """Системний промпт із поточною датою, профілем співрозмовника і його
        дисками (`prompts.drive_context`)."""
        # За Києвом, не за UTC: 19 з 249 питань (7.6%) приходили 00:00–03:00 і
        # отримували вчорашнє «сьогодні».
        today = datetime.now(KYIV).strftime("%d.%m.%Y")
        return (
            f"{self.system_prompt}\n\n"
            f"{user_context(member)}\n\n"
            + (f"{drives}\n\n" if drives else "")
            + "## Поточна дата\n\n"
            f"Сьогодні {today}.\n\n"
            "Сторінки бази знань писалися в різний час, і час у них — на момент "
            "написання. Якщо в джерелі стоїть «відбудеться», «скоро» чи «наступного "
            "місяця», звір із сьогоднішньою датою: подія могла вже минути. "
            "У такому разі так і кажи — «за базою планувалося на <дата>, тобто це вже "
            "минуло; підсумків у базі немає» — замість того щоб переказувати "
            "джерело в майбутньому часі."
        )

    async def _dispatch(self, fc, scopes: list[str]) -> dict:
        args = dict(fc.args or {})
        run = current_run()
        run.setdefault("tools", []).append(fc.name)
        # Запис для журналу додається одразу, а не після виконання: виклики
        # одного кроку йдуть паралельно, і порядок у журналі має бути порядком моделі.
        entry: dict = {"step": run.get("steps", 0), "tool": fc.name, "args": args}
        # Спроби agy пишуть в один журнал, а кроки кожної — від нуля
        if "attempt" in run:
            entry["attempt"] = run["attempt"]
        run.setdefault("journal", []).append(entry)

        spec = tools.TOOLS.get(fc.name)
        if spec is None:
            logger.warning("невідомий інструмент: %r", fc.name)
            entry["error"] = "невідомий інструмент"
            return {"error": f"невідомий інструмент: {fc.name}"}

        # Той самий виклик удруге (M10): у стенді так було в 41 з 376 відповідей,
        # 131 зайвий виклик, деякі петлі по 3–8 разів. Результат уже є в
        # розмові вище, тож замість ще 25 фрагментів — нагадування.
        key = (fc.name, json.dumps(args, sort_keys=True, ensure_ascii=False))
        seen = run.setdefault("seen_calls", {})
        if key in seen:
            run["repeats"] = run.get("repeats", 0) + 1
            entry["repeat_of"] = seen[key]
            logger.info("повтор виклику %s з кроку %d", fc.name, seen[key])
            return {
                "note": (
                    f"Цей самий виклик уже виконано на кроці {seen[key]}, його "
                    "результат вище в розмові. Не повторюй його: зміни запит "
                    "або відповідай з того, що вже зібрано."
                )
            }
        seen[key] = run.get("steps", 0)

        value = (
            spec.describe(args)
            if spec.describe
            else (args.get(spec.arg) if spec.arg else None)
        )
        text = f"{spec.label}: {value}" if value else spec.label
        run.setdefault("calls", []).append((fc.name, text))
        if emit := run.get("on_event"):
            emit(f"{text}…")

        started = time.monotonic()
        try:
            result = await spec.run(self, args, tools.Limits(), scopes)
        except Exception as exc:
            entry["error"] = repr(exc)
            raise
        finally:
            entry["ms"] = round((time.monotonic() - started) * 1000)
        _note_result(entry, result)
        return result

    async def _reply_via_agy(
        self,
        message: str,
        history: list[Message],
        member: dict | None,
        scopes: list[str],
        drives: str = "",
    ) -> str:
        """Той самий цикл з інструментами, але крок моделі — через процес agy."""
        if reason := models_check.agy_missing():
            # інакше agy мовчить до тайм-ауту, і причини не видно нікому
            raise AgyError(reason)
        # Нова спроба — новий процес і нова розмова: того, що «вище», у ній немає.
        current_run().pop("seen_calls", None)
        proc = AgyProcess(
            bin_path=settings.AGY_BIN,
            home=settings.AGY_HOME,
            workdir=settings.AGY_WORKDIR,
            model=settings.AGY_MODEL,
            schema_path=self.agy_schema_path,
            turn_timeout=settings.AGY_TURN_TIMEOUT,
        )
        started = time.monotonic()
        run = current_run()
        async with AGY_SEMAPHORE:
            try:
                await proc.start()
                text = agy_protocol.first_message(
                    self._system_instruction(member, drives),
                    self.agy_tools_doc,
                    [(m.role, m.content) for m in history],
                    message,
                )
                for step_no in range(1, NUM_ITERATIONS + 1):
                    current_run()["steps"] = step_no
                    result = await proc.send(text)
                    _add_tokens(run, result)
                    step = agy_protocol.parse_step(result)
                    if step.empty:
                        raise AgyError("модель повернула порожній хід")
                    if not step.calls and not step.errors:
                        return step.answer
                    results = await asyncio.gather(
                        *(
                            self._dispatch(
                                types.FunctionCall(name=name, args=args), scopes
                            )
                            for name, args in step.calls
                        )
                    )
                    pairs = [(name, r) for (name, _), r in zip(step.calls, results)]
                    text = agy_protocol.tool_results_message(
                        pairs + step.errors, final=step_no == NUM_ITERATIONS
                    )
                logger.warning(
                    "agy: вичерпано %d ітерацій, відповідаю зібраним", NUM_ITERATIONS
                )
                result = await proc.send(text)
                _add_tokens(run, result)
                final = agy_protocol.parse_step(result)
                if final.empty:
                    raise AgyError("модель повернула порожню фінальну відповідь")
                return final.answer
            finally:
                await proc.close()
                tokens = run.get("tokens") or {}
                logger.info(
                    "agy %s: %.1f с, кроків %d, інструменти [%s], "
                    "токени вхід %d кеш %d вихід %d",
                    settings.AGY_MODEL,
                    time.monotonic() - started,
                    run.get("steps", 0),
                    ", ".join(run.get("tools") or []),
                    tokens.get("input", 0),
                    tokens.get("cache", 0),
                    tokens.get("output", 0),
                )

    async def generate_reply(
        self,
        message: str,
        history: list[Message],
        member: dict | None,
        scopes: list[str],
        on_event: Callable[[str], None] | None = None,
        drives: str = "",
    ) -> str:
        """`drives` — блок про диски співрозмовника, `prompts.drive_context`."""
        run = {
            "provider": "gemini",
            "model": self.model,
            "steps": 0,
            "tools": [],
            "fallback": False,
            "on_event": on_event,
        }
        LAST_RUN.set(run)
        if self.provider == "agy":
            run.update(provider="agy", model=settings.AGY_MODEL)
            for attempt in range(1, settings.AGY_ATTEMPTS + 1):
                run["attempt"] = attempt
                try:
                    return await self._reply_via_agy(
                        message, history, member, scopes, drives
                    )
                except AgyError as exc:
                    run.setdefault("errors", []).append(
                        f"agy, спроба {attempt}: {str(exc)[:300]}"
                    )
                    logger.warning(
                        "agy не впорався (спроба %d з %d): %s",
                        attempt,
                        settings.AGY_ATTEMPTS,
                        exc,
                    )
                    if (
                        attempt == settings.AGY_ATTEMPTS
                        and not settings.AGY_FALLBACK_TO_API
                    ):
                        raise
            run.update(
                provider="gemini",
                model=self.model,
                fallback=True,
                steps=0,
                tools=[],
                calls=[],
                journal=[],
                seen_calls={},
            )
            run.pop("attempt")

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

        for step in range(1, NUM_ITERATIONS + 1):
            current_run()["steps"] = step
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=self.config.model_copy(
                    update={
                        "system_instruction": self._system_instruction(member, drives)
                    }
                ),
            )
            candidate = response.candidates[0].content if response.candidates else None
            if candidate is None:
                return "Вибач, не вдалось сформувати відповідь"
            calls = [
                p.function_call for p in (candidate.parts or []) if p.function_call
            ]

            if not calls:
                logger.info("agent: %d ходів", step)
                return response.text or "Я не зміг згенерувати відповідь"

            contents.append(candidate)
            for fc in calls:
                query = (fc.args or {}).get("query")
                if not query:
                    continue
            results = await asyncio.gather(
                *(self._dispatch(fc, scopes) for fc in calls)
            )
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
            "agent: вичерпано %d ітерацій, відповідаю зібраним", NUM_ITERATIONS
        )
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=contents,
            config=self.final_config.model_copy(
                update={"system_instruction": self._system_instruction(member, drives)}
            ),
        )

        return response.text or "Вибач, не вдалось сформувати відповідь"
