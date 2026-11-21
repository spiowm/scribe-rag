import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import ClassVar

from src.ingestion import autosync, pipeline

NOW = datetime(2026, 11, 22, 3, 0, tzinfo=UTC)


def test_due_needs_interval_and_elapsed_time():
    assert not autosync.due({"hours": 0}, NOW)
    assert autosync.due({"hours": 24}, NOW)
    started = (NOW - timedelta(hours=23)).isoformat()
    assert not autosync.due({"hours": 24, "last_started": started}, NOW)
    started = (NOW - timedelta(hours=24)).isoformat()
    assert autosync.due({"hours": 24, "last_started": started}, NOW)


class FakeDb:
    """Сесія з одним рядком `settings`, спільним між сесіями."""

    store: ClassVar[dict] = {}

    async def get(self, model, key):
        value = self.store.get(key)
        return SimpleNamespace(value=value) if value is not None else None

    async def merge(self, row):
        self.store[row.key] = row.value

    async def commit(self):
        pass

    async def rollback(self):
        pass


@asynccontextmanager
async def fake_session():
    yield FakeDb()


def patch(monkeypatch, notion_fails=False):
    FakeDb.store = {}
    calls = []

    async def extract_notion(_notion, _db):
        calls.append("notion")
        if notion_fails:
            raise RuntimeError("notion down")
        return pipeline.Extracted(found=3, changed=1)

    async def extract_gdrive(_gdrive, _gemini, _db):
        calls.append("gdrive")
        return pipeline.Extracted(found=5, failed=2)

    async def index(_qdrant, _db, source):
        return 7

    async def refresh_drives(_db, _gdrive):
        calls.append("drives")

    monkeypatch.setattr(autosync, "async_session_maker", fake_session)
    monkeypatch.setattr(pipeline, "extract_notion", extract_notion)
    monkeypatch.setattr(pipeline, "extract_gdrive", extract_gdrive)
    monkeypatch.setattr(pipeline, "index", index)
    monkeypatch.setattr(autosync.access, "refresh_drives", refresh_drives)
    app = SimpleNamespace(
        state=SimpleNamespace(notion=None, gdrive=None, gemini=None, qdrant=None)
    )
    return app, calls


def test_tick_runs_every_step_and_records_result(monkeypatch):
    app, calls = patch(monkeypatch, notion_fails=True)
    FakeDb.store[autosync.KEY] = {"hours": 24}

    assert asyncio.run(autosync.tick(app, NOW))
    assert calls == ["notion", "gdrive", "drives"]
    state = FakeDb.store[autosync.KEY]
    assert state["last_started"] == NOW.isoformat()
    assert "notion down" in state["last_result"]["notion"]["error"]
    assert state["last_result"]["gdrive"] == {
        "found": 5,
        "changed": 0,
        "removed": 0,
        "failed": 2,
        "ocr_failed": 0,
        "indexed": 7,
    }

    # Інтервал ще не минув — другого синку немає.
    assert not asyncio.run(autosync.tick(app, NOW + timedelta(hours=1)))
    assert calls == ["notion", "gdrive", "drives"]


def test_tick_skips_when_disabled_or_sync_running(monkeypatch):
    app, calls = patch(monkeypatch)
    assert not asyncio.run(autosync.tick(app, NOW))

    FakeDb.store[autosync.KEY] = {"hours": 6}

    async def with_lock():
        async with pipeline.SYNC_LOCK:
            return await autosync.tick(app, NOW)

    assert not asyncio.run(with_lock())
    assert calls == []


def test_set_hours_keeps_last_run(monkeypatch):
    patch(monkeypatch)
    FakeDb.store[autosync.KEY] = {"hours": 24, "last_started": NOW.isoformat()}
    state = asyncio.run(autosync.set_hours(FakeDb(), 0))
    assert state == {"hours": 0, "last_started": NOW.isoformat()}
