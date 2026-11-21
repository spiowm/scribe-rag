import asyncio
import json

import pytest

from src.generation.agy_process import AgyError, AgyProcess

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
