"""Журнал прогону біля відповіді бота (M1): що кликала модель і що знайшла."""

import asyncio
import json

import pytest
from google.genai import types

from src.generation import chain as chain_mod
from src.generation import tools
from src.ingestion.schemas import GENERAL


def fake(monkeypatch, name, run):
    spec = tools.TOOLS[name]
    monkeypatch.setitem(tools.TOOLS, name, tools.ToolSpec(**{**spec.__dict__, "run": run}))


def call(rag, tool, **args):
    return asyncio.run(
        rag._dispatch(types.FunctionCall(name=tool, args=args), [GENERAL])
    )


def test_calls_are_journaled_without_results(monkeypatch):
    async def search(deps, args, limits, scopes):
        return {
            "results": [
                {"source_id": "doc-a", "title": "A", "url": "", "text": "секрет " * 50},
                {"source_id": "doc-b", "title": "B", "url": "", "text": "x"},
                {"source_id": "doc-a", "title": "A", "url": "", "text": "y"},
            ]
        }

    async def document(deps, args, limits, scopes):
        return {"error": "документа з source_id=zzz немає."}

    fake(monkeypatch, "search_knowledge_base", search)
    fake(monkeypatch, "get_document", document)
    rag = object.__new__(chain_mod.RagChain)
    chain_mod.LAST_RUN.set(
        {"provider": "agy", "model": "m", "fallback": False, "steps": 1,
         "tokens": {"input": 9000, "cache": 4000, "output": 300}}
    )
    call(rag, "search_knowledge_base", query="BTW 2023", terms=["BTW"])
    chain_mod.current_run()["steps"] = 2
    call(rag, "search_knowledge_base", query="BTW 2023", terms=["BTW"])
    call(rag, "get_document", source_id="zzz")

    record = chain_mod.run_record(chain_mod.current_run(), 12.345)
    first, again, doc = record["calls"]
    assert first["step"] == 1 and first["args"] == {"query": "BTW 2023", "terms": ["BTW"]}
    assert first["source_ids"] == ["doc-a", "doc-b"]
    assert first["chars"] > 350 and isinstance(first["ms"], int)
    assert again == {"step": 2, "tool": "search_knowledge_base",
                     "args": first["args"], "repeat_of": 1}
    assert doc["error"].startswith("документа") and "source_ids" not in doc
    assert record["steps"] == 2 and record["seconds"] == 12.3
    assert record["tokens"]["input"] == 9000 and "errors" not in record
    # тексту фрагментів у журналі немає
    assert "секрет" not in json.dumps(record, ensure_ascii=False)


def test_failing_tool_is_journaled_and_still_raises(monkeypatch):
    async def boom(deps, args, limits, scopes):
        raise RuntimeError("qdrant лежить")

    fake(monkeypatch, "find_person", boom)
    rag = object.__new__(chain_mod.RagChain)
    chain_mod.LAST_RUN.set({"steps": 1})
    with pytest.raises(RuntimeError):
        call(rag, "find_person", name="Мінчак")
    (entry,) = chain_mod.current_run()["journal"]
    assert "qdrant лежить" in entry["error"] and "ms" in entry


def test_unknown_tool_is_journaled():
    rag = object.__new__(chain_mod.RagChain)
    chain_mod.LAST_RUN.set({"steps": 1})
    call(rag, "drop_table", name="x")
    record = chain_mod.run_record(chain_mod.current_run(), 1)
    assert record["calls"][0]["error"] == "невідомий інструмент"


def test_agy_attempt_is_journaled():
    """Кроки двох спроб agy — в одному журналі, і кожна рахує кроки від нуля."""
    rag = object.__new__(chain_mod.RagChain)
    chain_mod.LAST_RUN.set({"steps": 1, "attempt": 1})
    call(rag, "drop_table", name="x")
    chain_mod.current_run()["attempt"] = 2
    call(rag, "drop_table", name="x")
    first, second = chain_mod.current_run()["journal"]
    assert (first["step"], first["attempt"]) == (1, 1)
    assert (second["step"], second["attempt"]) == (1, 2)


def test_record_is_json_and_keeps_agy_failures():
    run = {"provider": "gemini", "model": "g", "fallback": True, "steps": 0,
           "on_event": print, "seen_calls": {("x", "{}"): 1},
           "errors": ["agy, спроба 1: тайм-аут"]}
    record = chain_mod.run_record(run, 3)
    json.dumps(record)  # службові поля run (колбек, ключі-кортежі) не потрапляють
    assert record["errors"] == ["agy, спроба 1: тайм-аут"] and record["calls"] == []
    assert "tokens" not in record
