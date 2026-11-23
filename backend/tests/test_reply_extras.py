"""Службові частини відповіді в Телеграмі: «Думання» і підказка про /new."""

import asyncio

from src.api.routers import chat
from src.repository import chats


def window(truncated: bool) -> chats.Window:
    return chats.Window(messages=[], tokens=0, truncated=truncated)


def hint(monkeypatch, truncated: bool, seen: bool) -> tuple[str, list]:
    asked = []

    async def fake_cut(db, session_id):
        asked.append(session_id)
        return seen

    monkeypatch.setattr(chats, "last_reply_cut", fake_cut)
    return asyncio.run(chat.build_context_hint(None, 7, window(truncated))), asked  # pyright: ignore[reportArgumentType]


def test_tools_block_shows_count():
    run = {"calls": [("search", "шукав А"), ("search", "шукав Б"), ("x", "щось")]}
    assert "<summary><sub>_Думання (3)_</sub></summary>" in chat.build_tools_block(run)
    assert chat.build_tools_block({}) == ""


def test_hint_only_on_first_cut_reply(monkeypatch):
    text, asked = hint(monkeypatch, truncated=True, seen=False)
    assert "/new" in text and asked == [7]
    # попередня відповідь уже з `cut`: підказку бачили
    assert hint(monkeypatch, truncated=True, seen=True)[0] == ""
    # розмова вміщається: БД навіть не питається
    assert hint(monkeypatch, truncated=False, seen=False) == ("", [])


def test_record_marks_cut():
    assert chat.build_record({}, 1.0, window(True))["cut"] is True
    assert "cut" not in chat.build_record({}, 1.0, window(False))
