"""Великий Google Doc: Drive не експортує понад 10 МБ markdown, тоді — text/plain."""

import asyncio

import httpx
import pytest

from src.ingestion.extractors import markdown_text
from src.ingestion.readers import GDOC, READERS


def refused(reason: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://drive/export")
    body = {"error": {"code": 403, "errors": [{"reason": reason}]}}
    response = httpx.Response(403, json=body, request=request)
    return httpx.HTTPStatusError("403", request=request, response=response)


class FakeDrive:
    def __init__(self, reason: str):
        self.reason, self.asked = reason, []

    async def export(self, file_id, mime):
        self.asked.append(mime)
        if mime == "text/markdown":
            raise refused(self.reason)
        return "﻿Звіт для BEST Shot\nДякуємо".encode()


def test_too_large_markdown_falls_back_to_plain_text():
    drive = FakeDrive("exportSizeLimitExceeded")
    blob = asyncio.run(READERS[GDOC].fetch(drive, "f"))  # pyright: ignore[reportArgumentType]
    assert drive.asked == ["text/markdown", "text/plain"]
    assert markdown_text(blob) == ("Звіт для BEST Shot\nДякуємо", 0)


def test_other_refusal_is_not_masked_by_fallback():
    drive = FakeDrive("insufficientFilePermissions")
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(READERS[GDOC].fetch(drive, "f"))  # pyright: ignore[reportArgumentType]
    assert drive.asked == ["text/markdown"]
