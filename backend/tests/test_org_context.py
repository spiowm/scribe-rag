import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest
from google.genai import errors, types
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from pydantic import SecretStr

from src.config import settings
from src.generation import chain as generation
from src.generation.agy_process import AgyError, AgyProviderError
from src.generation.prompts import BOT_PROMPT
from src.public.mcp_server import mcp

MARKER = "runtime-only-organization-marker"
CONTEXT = f"Довідка\n\n{MARKER} $literal\n"


@pytest.fixture
def runtime_context(monkeypatch):
    monkeypatch.setattr(settings, "ORG_CONTEXT", SecretStr(CONTEXT))


def make_chain(monkeypatch, provider, generate=None):
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider)
    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))
    )
    return generation.RagChain(client, "test", None, None)


def sdk_reply():
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(parts=[types.Part(text="Готово")]))
        ]
    )


def test_blank_context_uses_shared_prompt(monkeypatch):
    monkeypatch.setattr(settings, "ORG_CONTEXT", SecretStr(""))
    assert make_chain(monkeypatch, "gemini").system_prompt == BOT_PROMPT


def test_organization_context_reaches_agy(
    monkeypatch, tmp_path, caplog, runtime_context
):
    caplog.set_level(logging.INFO, logger=generation.__name__)
    monkeypatch.setattr(settings, "AGY_HOME", str(tmp_path))
    monkeypatch.setattr(generation.models_check, "agy_missing", lambda: None)
    proc = SimpleNamespace(
        start=AsyncMock(),
        send=AsyncMock(return_value={"response": json.dumps({"answer": "Готово"})}),
        close=AsyncMock(),
    )
    monkeypatch.setattr(generation, "AgyProcess", lambda **_: proc)

    generate = AsyncMock(return_value=sdk_reply())
    chain = make_chain(monkeypatch, "agy", generate)
    assert asyncio.run(chain.generate_reply("Запит", [], None, ["general"])) == "Готово"
    assert CONTEXT in proc.send.await_args.args[0]
    assert MARKER not in caplog.text
    assert MARKER not in (tmp_path / "scribe-schema.json").read_text()

    monkeypatch.setattr(settings, "AGY_ATTEMPTS", 1)
    monkeypatch.setattr(settings, "AGY_FALLBACK_TO_API", False)
    proc.send.side_effect = AgyProviderError(f"input echoed: {CONTEXT}")
    with pytest.raises(AgyError, match="невдала відповідь провайдера") as error:
        asyncio.run(chain.generate_reply("Запит", [], None, ["general"]))
    assert MARKER not in str(error.value)
    assert MARKER not in caplog.text

    async def reply_with_run():
        reply = await chain.generate_reply("Запит", [], None, ["general"])
        return reply, generation.current_run()

    monkeypatch.setattr(settings, "AGY_FALLBACK_TO_API", True)
    reply, run = asyncio.run(reply_with_run())
    assert reply == "Готово"
    assert CONTEXT in generate.await_args.kwargs["config"].system_instruction
    assert run["fallback"]
    assert MARKER not in json.dumps(generation.run_record(run, 0), ensure_ascii=False)


def test_organization_context_reaches_gemini_and_final_call(
    monkeypatch, runtime_context
):
    generate = AsyncMock(return_value=sdk_reply())
    chain = make_chain(monkeypatch, "gemini", generate)
    assert asyncio.run(chain.generate_reply("Запит", [], None, ["general"])) == "Готово"
    assert CONTEXT in generate.await_args.kwargs["config"].system_instruction

    monkeypatch.setattr(generation, "NUM_ITERATIONS", 0)
    assert asyncio.run(chain.generate_reply("Запит", [], None, ["general"])) == "Готово"
    final_config = generate.await_args.kwargs["config"]
    assert CONTEXT in final_config.system_instruction
    assert final_config.tool_config.function_calling_config.mode == "NONE"

    generate.side_effect = errors.APIError(400, {"error": {"message": CONTEXT}})
    with pytest.raises(RuntimeError, match="Gemini API: HTTP 400") as error:
        asyncio.run(chain.generate_reply("Запит", [], None, ["general"]))
    assert MARKER not in str(error.value)


def test_mcp_initialization_and_tools_skip_organization_context(runtime_context):
    async def verify():
        async with create_client_server_memory_streams() as (client, server):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(mcp.run, *server, mcp.create_initialization_options())
                async with ClientSession(*client) as session:
                    init = await session.initialize()
                    tools = await session.list_tools()
                    assert MARKER not in init.model_dump_json()
                    assert MARKER not in tools.model_dump_json()
                    assert {"search_knowledge_base", "who_am_i"} <= {
                        tool.name for tool in tools.tools
                    }
                tasks.cancel_scope.cancel()

    asyncio.run(verify())
