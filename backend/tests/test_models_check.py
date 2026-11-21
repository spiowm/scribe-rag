import asyncio

from src.config import settings
from src.generation import models_check


class FakeModels:
    def __init__(self, known):
        self.known = known

    async def get(self, model):
        if model not in self.known:
            raise RuntimeError("404")


class FakeClient:
    def __init__(self, known):
        self.aio = type("Aio", (), {"models": FakeModels(known)})()


def run(monkeypatch, agy_list, known):
    async def fake_agy():
        return agy_list

    monkeypatch.setattr(models_check, "agy_models", fake_agy)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "agy")
    return asyncio.run(models_check.check(FakeClient(known)))  # pyright: ignore[reportArgumentType]


def test_all_present(monkeypatch):
    known = {settings.GEMINI_LLM_MODEL, settings.GEMINI_EMBEDDING_MODEL}
    assert run(monkeypatch, {settings.AGY_MODEL}, known) == {}
    assert models_check.agy_missing() is None


def test_missing_agy_model_fails_fast(monkeypatch):
    known = {settings.GEMINI_LLM_MODEL, settings.GEMINI_EMBEDDING_MODEL}
    problems = run(monkeypatch, {"gemini-3.8-flash-high"}, known)
    assert set(problems) == {"AGY_MODEL"}
    assert "gemini-3.8-flash-high" in (models_check.agy_missing() or "")


def test_missing_ocr_model_reported_without_blocking_chat(monkeypatch):
    problems = run(monkeypatch, {settings.AGY_MODEL}, {settings.GEMINI_EMBEDDING_MODEL})
    assert set(problems) == {"GEMINI_LLM_MODEL"}
    assert models_check.agy_missing() is None


# Вивід `agy models`: перший рядок — статус, далі «id<TAB>назва».
AGY_MODELS_OUTPUT = (
    "Fetching available models...\n"
    "gemini-3.8-flash-high\tGemini 3.8 Flash (high)\n"
    "gemini-3.8-flash-medium\tGemini 3.8 Flash (medium)\n"
    "gemini-3.8-flash-low\tGemini 3.8 Flash (low)\n"
    "gemini-3.1-pro-high\tGemini 3.1 Pro (high)\n"
    "claude-sonnet-4-6\tClaude Sonnet 4.6\n"
)


def run_bin(monkeypatch, tmp_path, output):
    """check() через справжній agy_models() з фейковим AGY_BIN."""
    (tmp_path / "out.txt").write_text(output)
    fake = tmp_path / "agy"
    fake.write_text(f"#!/bin/sh\ncat {tmp_path / 'out.txt'}\n")
    fake.chmod(0o755)
    monkeypatch.setattr(settings, "AGY_BIN", str(fake))
    monkeypatch.setattr(settings, "AGY_MODEL", "gemini-3.8-flash-low")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "agy")
    known = {settings.GEMINI_LLM_MODEL, settings.GEMINI_EMBEDDING_MODEL}
    return asyncio.run(models_check.check(FakeClient(known)))  # pyright: ignore[reportArgumentType]


def test_real_agy_output_parsed(monkeypatch, tmp_path):
    assert run_bin(monkeypatch, tmp_path, AGY_MODELS_OUTPUT) == {}
    assert models_check.agy_missing() is None


def test_real_agy_output_without_model_blocks(monkeypatch, tmp_path):
    output = AGY_MODELS_OUTPUT.replace("gemini-3.8-flash-low", "gemini-3.9-flash-low")
    assert set(run_bin(monkeypatch, tmp_path, output)) == {"AGY_MODEL"}
    assert "gemini-3.8-flash-low" in (models_check.agy_missing() or "")


def test_unparsable_agy_output_does_not_block(monkeypatch, tmp_path):
    for output in (
        AGY_MODELS_OUTPUT.replace("\t", "   "),
        '[{"id": "gemini-3.8-flash-low"}]\n',
        "",
    ):
        problems = run_bin(monkeypatch, tmp_path, output)
        assert set(problems) == {"AGY_MODEL"}, output
        assert models_check.agy_missing() is None, output
