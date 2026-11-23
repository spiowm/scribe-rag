import asyncio
import json
from pathlib import Path

import pytest

from src.generation.agy_process import DENY, AgyError, AgyProcess, write_settings

# Те, що agy 1.2.10 друкує без логіну (заміряно в контейнері 08.11).
NOT_SIGNED_IN = {
    "event": "result",
    "result": {
        "conversation_id": "",
        "status": "ERROR",
        "response": "",
        "error": "authentication failed or timed out",
    },
}


def fake_agy(tmp_path, *lines: str) -> str:
    script = tmp_path / "agy"
    body = "\n".join(f"echo '{line}'" for line in lines)
    script.write_text(f"#!/bin/sh\necho 'Authentication required.' >&2\n{body}\n")
    script.chmod(0o755)
    return str(script)


def start(tmp_path, bin_path: str) -> str:
    proc = AgyProcess(
        bin_path=bin_path,
        home=str(tmp_path),
        workdir=str(tmp_path),
        model="m",
        schema_path=str(tmp_path / "schema.json"),
        turn_timeout=5,
    )
    return asyncio.run(proc.start())


def test_result_instead_of_init_keeps_reason(tmp_path):
    bin_path = fake_agy(tmp_path, json.dumps(NOT_SIGNED_IN))
    with pytest.raises(AgyError, match="authentication failed or timed out"):
        start(tmp_path, bin_path)


def test_init_gives_conversation(tmp_path):
    bin_path = fake_agy(
        tmp_path, json.dumps({"event": "init", "conversation_id": "c1"})
    )
    assert start(tmp_path, bin_path) == "c1"


CALLS = {
    "action": "call_tools",
    "tool_calls": [{"name": "find_person", "args_json": "{}"}],
}


def test_stale_structured_output_gives_text_answer():
    from src.generation.agy_process import stale_answer

    result = {"structured_output": CALLS, "response": "Готова відповідь текстом."}
    assert stale_answer(result, CALLS) == "Готова відповідь текстом."


def test_fresh_or_repeated_on_purpose_is_not_stale():
    from src.generation.agy_process import stale_answer

    same = json.dumps(CALLS)
    # новий вивід, хоч текст і не JSON
    assert stale_answer({"structured_output": CALLS, "response": "текст"}, None) is None
    # модель справді повторила ті самі виклики: текст — той самий JSON
    assert stale_answer({"structured_output": CALLS, "response": same}, CALLS) is None
    fenced = f"```json\n{same}\n```"
    assert stale_answer({"structured_output": CALLS, "response": fenced}, CALLS) is None


def settings_file(home: Path) -> Path:
    return home / ".gemini" / "antigravity-cli" / "settings.json"


def test_settings_written_on_clean_home(tmp_path):
    path = write_settings(tmp_path)
    assert path == settings_file(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"permissions": {"deny": list(DENY)}, "telemetryEnabled": False}


def test_settings_keep_other_keys_and_rules(tmp_path):
    path = settings_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "model": "x",
                "telemetryEnabled": True,
                "permissions": {"allow": ["mcp(a)"], "deny": ["mcp(b)", "command(*)"]},
            }
        ),
        encoding="utf-8",
    )
    write_settings(tmp_path)
    write_settings(tmp_path)  # повторний старт нічого не дублює
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["model"] == "x"
    assert data["telemetryEnabled"] is False
    assert data["permissions"]["allow"] == ["mcp(a)"]
    deny = data["permissions"]["deny"]
    assert deny[:2] == ["mcp(b)", "command(*)"]
    assert sorted(deny) == sorted({"mcp(b)", *DENY})


def test_broken_settings_replaced(tmp_path):
    path = settings_file(tmp_path)
    path.parent.mkdir(parents=True)
    for broken in ("{not json", "[1, 2]", '{"permissions": "all"}'):
        path.write_text(broken, encoding="utf-8")
        write_settings(tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["permissions"]["deny"] == list(DENY)
