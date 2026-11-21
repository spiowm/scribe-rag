"""Чи існують моделі з конфігу (A2).

Google прибирає моделі без попередження, і збій тихий: agy з невідомою
моделлю просто мовчить, а бот відмовляє через ~4 хвилини двох спроб; OCR
мовчки падає на текст pdfium. Перевірка на старті й раз на кілька годин
кладе висновок сюди, а ланцюг на відомо відсутній моделі відмовляє одразу і
з причиною, яку бот перешле власнику.
"""

import asyncio
import logging
import os

from google import genai

from src.config import settings

logger = logging.getLogger(__name__)

# Останній висновок: {що: проблема}. Порожньо — усе на місці або ще не
# перевіряли; памʼять процесу, бо після рестарту перевірка біжить знову.
PROBLEMS: dict[str, str] = {}
# Причина відмовляти одразу: модель agy відомо відсутня. Окремо від PROBLEMS,
# щоб поведінкою керував висновок перевірки, а не формулювання повідомлення.
AGY_BLOCKED: str | None = None


async def agy_models() -> set[str]:
    proc = await asyncio.create_subprocess_exec(
        settings.AGY_BIN,
        "models",
        env={**os.environ, "HOME": settings.AGY_HOME},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except TimeoutError:
        proc.kill()
        raise
    if proc.returncode:
        raise RuntimeError(out.decode(errors="replace")[-300:])
    # рядки «id<TAB>назва»; перший рядок — «Fetching available models...»
    return {
        line.split("\t", 1)[0].strip()
        for line in out.decode(errors="replace").splitlines()
        if "\t" in line
    }


async def check(client: genai.Client) -> dict[str, str]:
    global AGY_BLOCKED
    problems: dict[str, str] = {}
    blocked = None
    if settings.LLM_PROVIDER == "agy":
        try:
            available = await agy_models()
            if not available:
                # формат виводу змінився — це не доказ, що моделі немає
                problems["AGY_MODEL"] = "agy models: не розібрав відповідь"
            elif settings.AGY_MODEL not in available:
                blocked = problems["AGY_MODEL"] = (
                    f"agy не знає {settings.AGY_MODEL}; є: "
                    + ", ".join(sorted(m for m in available if "flash" in m))
                )
        except Exception as exc:  # noqa: BLE001 — перевірка не валить застосунок
            problems["AGY_MODEL"] = f"agy models не відповів: {exc!r}"[:300]
    for key in ("GEMINI_LLM_MODEL", "GEMINI_EMBEDDING_MODEL"):
        name = getattr(settings, key)
        try:
            await client.aio.models.get(model=name)
        except Exception as exc:  # noqa: BLE001
            problems[key] = f"Gemini API не знає {name}: {exc!r}"[:300]
    PROBLEMS.clear()
    PROBLEMS.update(problems)
    AGY_BLOCKED = blocked
    for key, text in problems.items():
        logger.error("модель %s: %s", key, text)
    return problems


async def check_loop(client: genai.Client, hours: float) -> None:
    while True:
        await check(client)
        await asyncio.sleep(hours * 3600)


def agy_missing() -> str | None:
    """Причина відмовити одразу, якщо модель agy відомо відсутня."""
    return AGY_BLOCKED
