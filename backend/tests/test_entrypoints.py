"""Доступ на вході: секрет внутрішнього API, мітки MCP, чужий диск у /drives."""

import asyncio
import re
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.config import settings
from src.ingestion.schemas import GENERAL


def test_every_internal_route_needs_the_token(monkeypatch):
    """Новий роутер без `internal_only` теж ловиться: обхід усіх маршрутів."""
    from src.api.main import app

    monkeypatch.setattr(settings, "INTERNAL_API_TOKEN", "secret")
    client = TestClient(app)  # без `with`: lifespan і зʼєднання не потрібні
    routes = [r for r in app.routes if isinstance(r, APIRoute) and r.path != "/"]
    assert len(routes) > 10
    for route in routes:
        path = re.sub(r"\{[^}]+\}", "x", route.path)
        for method in route.methods:
            response = client.request(method, path, json={"telegram_id": 1})
            assert response.status_code == 401, (method, route.path)
    assert client.get("/").status_code == 200


class FakeSession:
    def __init__(self, user):
        self.user = user

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, _model, _id):
        return self.user


def run_mcp_search(monkeypatch, user):
    from src.generation import tools
    from src.public import mcp_server
    from src.repository import access

    got = {}

    async def search(deps, args, limits, scopes):
        got["scopes"] = scopes
        return {"results": []}

    async def search_scopes(db, user_id, member):
        got["asked"] = (user_id, member)
        return [GENERAL, "DRIVE"]

    async def find_member_by_phone(phone):
        return {"phone": phone}

    async def save_call(*_args):
        pass

    spec = tools.TOOLS["search_knowledge_base"]
    monkeypatch.setitem(
        tools.TOOLS,
        "search_knowledge_base",
        tools.ToolSpec(**{**spec.__dict__, "run": search}),
    )
    monkeypatch.setattr(access, "search_scopes", search_scopes)
    monkeypatch.setattr(mcp_server, "async_session_maker", lambda: FakeSession(user))
    monkeypatch.setattr(mcp_server, "save_call", save_call)
    mongo = SimpleNamespace(find_member_by_phone=find_member_by_phone)
    ctx = SimpleNamespace(
        request=SimpleNamespace(
            user=SimpleNamespace(access_token=SimpleNamespace(subject="7")),
            app=SimpleNamespace(state=SimpleNamespace(mongo=mongo)),
        )
    )
    params = SimpleNamespace(name="search_knowledge_base", arguments={"query": "x"})
    asyncio.run(mcp_server.call_tool(ctx, params))
    return got


def test_mcp_search_gets_the_caller_scopes(monkeypatch):
    user = SimpleNamespace(id=7, telegram_id=1, phone_number="+380")
    got = run_mcp_search(monkeypatch, user)
    assert got["asked"] == (7, {"phone": "+380"})
    assert got["scopes"] == [GENERAL, "DRIVE"]


def test_mcp_without_user_searches_only_general(monkeypatch):
    got = run_mcp_search(monkeypatch, None)
    assert "asked" not in got
    assert got["scopes"] == [GENERAL]


def test_toggle_of_a_drive_without_access_is_403(monkeypatch):
    from src.api.routers import drives
    from src.repository import access

    toggled = []

    async def caller(db, mongo, user):
        return {"email": "a@b.c"}, [GENERAL, "MINE"]

    async def drive_view(db, user_id, member, indexed):
        return {"drives": [{"id": "MINE", "name": "Mine", "on": True, "via": []}]}

    async def toggle_drive(db, user_id, drive_id):
        toggled.append(drive_id)

    monkeypatch.setattr(access, "caller", caller)
    monkeypatch.setattr(access, "drive_view", drive_view)
    monkeypatch.setattr(access, "toggle_drive", toggle_drive)
    user = SimpleNamespace(id=1)

    def toggle(drive_id):
        body = SimpleNamespace(drive_id=drive_id)
        return asyncio.run(drives.toggle(body, user, None, None))  # pyright: ignore[reportArgumentType]

    with pytest.raises(HTTPException) as err:
        toggle("FOREIGN")
    assert err.value.status_code == 403 and toggled == []
    toggle("MINE")
    assert toggled == ["MINE"]
