"""Періодичний синк Notion і Drive та оновлення прав дисків.

Інтервал задає адмін з бота (`/autosync`), він живе в таблиці `settings`, а не в
змінних оточення: міняється без передеплою. Час останнього запуску теж у базі:
інакше кожен деплой із `main` запускав би синк одразу після старту.
"""

import asyncio
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.db import async_session_maker
from src.ingestion import pipeline
from src.models.setting import Setting
from src.repository import access

logger = logging.getLogger(__name__)

KEY = "autosync"
# Як часто перевіряти, чи настав час. Синк інкрементальний, тож запізнення на
# кілька хвилин нічого не змінює.
CHECK_SECONDS = 300
SUMMARY = ("found", "changed", "removed", "failed", "ocr_failed")


async def get_state(db: AsyncSession) -> dict:
    row = await db.get(Setting, KEY)
    return dict(row.value) if row else {"hours": 0}


async def _save(db: AsyncSession, state: dict) -> None:
    await db.merge(Setting(key=KEY, value=state))
    await db.commit()


async def set_hours(db: AsyncSession, hours: int) -> dict:
    """0 вимикає автосинк."""
    state = await get_state(db)
    state["hours"] = hours
    await _save(db, state)
    return state


def due(state: dict, now: datetime) -> bool:
    hours = state.get("hours") or 0
    if hours <= 0:
        return False
    last = state.get("last_started")
    return last is None or now - datetime.fromisoformat(last) >= timedelta(hours=hours)


async def run(app) -> dict:
    """Notion → Drive → права дисків. Збій кроку не зупиняє наступні."""
    result: dict = {}
    s = app.state
    async with async_session_maker() as db:
        steps = {
            "notion": lambda: pipeline.extract_notion(s.notion, db),
            "gdrive": lambda: pipeline.extract_gdrive(s.gdrive, s.gemini, db),
        }
        for source, extract in steps.items():
            try:
                extracted = asdict(await extract())
                indexed = await pipeline.index(s.qdrant, db, source)
                result[source] = {k: extracted[k] for k in SUMMARY}
                result[source]["indexed"] = indexed
            except Exception as exc:
                logger.exception("autosync: %s не вдався", source)
                await db.rollback()
                result[source] = {"error": repr(exc)[:300]}
        try:
            await access.refresh_drives(db, s.gdrive)
            result["drives"] = {"ok": True}
        except Exception as exc:
            logger.exception("autosync: права дисків не оновились")
            await db.rollback()
            result["drives"] = {"error": repr(exc)[:300]}
    return result


async def tick(app, now: datetime) -> bool:
    """Запускає синк, якщо настав час. True — синк відбувся."""
    async with async_session_maker() as db:
        state = await get_state(db)
        if not due(state, now) or pipeline.SYNC_LOCK.locked():
            return False
        # Час старту зберігається до синку: якщо процес упаде посеред нього,
        # після перезапуску синк не почнеться знову одразу.
        state["last_started"] = now.isoformat()
        await _save(db, state)

    async with pipeline.SYNC_LOCK:
        result = await run(app)

    async with async_session_maker() as db:
        state = await get_state(db)
        state["last_finished"] = datetime.now(UTC).isoformat()
        state["last_result"] = result
        await _save(db, state)
    logger.info("autosync: %s", result)
    return True


async def loop(app) -> None:
    while True:
        await asyncio.sleep(CHECK_SECONDS)
        try:
            await tick(app, datetime.now(UTC))
        except Exception:
            logger.exception("autosync: перевірка не вдалась")
