import asyncio

import pytest
from fastapi import FastAPI

from src.api import main
from src.config import settings


async def start():
    async with main.lifespan(FastAPI()):
        pass


def test_backend_without_secrets_fails_before_connectors(monkeypatch):
    # решта секретів задана, щоб результат не залежав від оточення
    for name in main.BACKEND_SECRETS:
        monkeypatch.setattr(settings, name, "x")
    monkeypatch.setattr(settings, "NOTION_TOKEN", "")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "")
    # конектори не створюються: падіння раніше за будь-яке зʼєднання
    monkeypatch.setattr(main, "NotionConnector", None)

    with pytest.raises(RuntimeError) as err:
        asyncio.run(start())
    assert str(err.value) == (
        "Backend не стартує: не задано NOTION_TOKEN, R2_SECRET_ACCESS_KEY"
    )
