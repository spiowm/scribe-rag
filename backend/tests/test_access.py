import pytest

from src.repository.access import parse_export, resolve_group

EXPORT = """Members for group scribe-fixture-btw
Email address,Nickname,Group status,Email status,Email preference,Posting permissions,Join year,Join month,Join day,Join hour,Join minute,Join second,Time zone
Fixture._member1@gmail.com,fixture-manager,manager,,email,allowed,2000,1,1,0,0,0,UTC
fixture_member2@gmail.com,a,member,,email,allowed,2000,1,1,0,0,0,UTC
fixture_member3@gmail.com,b,owner,,email,allowed,2000,1,1,0,0,0,UTC
fixture_member4@gmail.com,c,invited,,email,not allowed,2000,1,1,0,0,0,UTC
fixture_member5@gmail.com,d,banned,,email,not allowed,2000,1,1,0,0,0,UTC
"""


def test_parse_export_takes_group_and_active_members():
    export = parse_export(EXPORT)
    assert export.group == "scribe-fixture-btw"
    assert export.members == {
        "fixture_member1@gmail.com": "manager",
        "fixture_member2@gmail.com": "member",
        "fixture_member3@gmail.com": "owner",
    }
    # запрошений і невідомий статус доступу не дають
    assert export.skipped == {"invited": 1, "banned": 1}


def test_gmail_dots_and_plus_folded_on_both_sides():
    import asyncio

    from src.repository import access

    key = access.email_key
    assert key(" Fixture._member6+tag@GMAIL.COM") == key("fixture_member6@gmail.com")
    assert key("fixture.member7@example.test") == "fixture.member7@example.test"  # інші домени як є

    class Db:
        def __init__(self):
            self.asked = []

        async def scalars(self, stmt):
            self.asked += stmt.compile().params.values()
            return []

    db = Db()
    asyncio.run(access.allowed_drives(db, {"email": "Fixture._member6@gmail.com"}))  # pyright: ignore[reportArgumentType]
    # група — за ключем, власна пошта в правах диска — як є
    assert db.asked == ["fixture_member6@gmail.com", ["fixture._member6@gmail.com"]]


def test_parse_export_without_title_line_and_with_bom():
    export = parse_export("﻿" + EXPORT.split("\n", 1)[1])
    assert export.group == ""
    assert len(export.members) == 3


def test_parse_export_rejects_other_csv():
    with pytest.raises(ValueError):
        parse_export("name,phone\nx,1\n")


GROUPS = {
    "scribe-fixture-btw@googlegroups.com",
    "scribe-fixture-mw@googlegroups.com",
    "scribe-fixture-full-members@googlegroups.com",
}


@pytest.mark.parametrize(
    "name, expected",
    [
        ("scribe-fixture-btw", "scribe-fixture-btw@googlegroups.com"),
        ("scribe-fixture-BTW ", "scribe-fixture-btw@googlegroups.com"),
        ("scribe-fixture-full-members@googlegroups.com", "scribe-fixture-full-members@googlegroups.com"),
        # назва групи в Google не завжди збігається з адресою — тоді нічого
        ("scribe-fixture-dat", None),
        ("fixturemember8@example.test", None),
        ("", None),
    ],
)
def test_resolve_group(name, expected):
    assert resolve_group(name, GROUPS) == expected


def test_resolve_group_ambiguous_local_part():
    assert resolve_group("x", {"x@one.test", "x@two.test"}) is None


def test_status_stands_in_for_full_members_group(monkeypatch):
    from src.repository import access

    full = "scribe-fixture-full-members@googlegroups.com"
    monkeypatch.setattr(access, "STATUS_GROUPS", {full: ("Full", "Alumni")})
    assert access.status_groups({"status": "Full"}) == {full}
    assert access.status_groups({"status": "Alumni"}) == {full}
    assert access.status_groups({"status": "Baby"}) == set()
    assert access.status_groups({}) == set()


def test_drive_rate_limit_403_is_retried_but_denial_is_not():
    import httpx

    from src.connectors.gdrive import _retryable

    def resp(code, reason=None):
        body = {"error": {"errors": [{"reason": reason}]}} if reason else {}
        return httpx.Response(code, json=body)

    assert _retryable(resp(403, "userRateLimitExceeded"))
    assert _retryable(resp(403, "rateLimitExceeded"))
    assert not _retryable(resp(403, "insufficientFilePermissions"))
    assert not _retryable(resp(404))
    assert _retryable(resp(503))


def test_member_email_that_is_a_group_address_opens_nothing():
    import asyncio

    from src.repository import access

    class Db:
        async def scalars(self, _stmt):
            return []  # у жодному експорті цієї пошти немає

    member = {"status": "Baby", "email": "scribe-fixture-board@googlegroups.com"}
    assert asyncio.run(access.allowed_drives(Db(), member)) == set()  # pyright: ignore[reportArgumentType]


def test_empty_drive_list_is_a_502_not_a_wipe():
    import asyncio

    from fastapi import HTTPException

    from src.api.routers import admin

    class Drive:
        async def list_drives(self):
            return []

    with pytest.raises(HTTPException) as err:
        asyncio.run(admin.refresh_drives(None, None, Drive()))  # pyright: ignore[reportArgumentType]
    assert (err.value.status_code, err.value.detail) == (502, "drive_api_empty")


def test_bot_admin_sees_every_drive(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from src.repository import access

    class Mongo:
        async def find_member_by_phone(self, _phone):
            return {"status": "Baby", "email": "fixture_member2@gmail.com"}

    class Db:
        async def scalars(self, stmt):
            # адміну — усі id з `drives`, без груп і експортів
            assert "group_members" not in str(stmt)
            return ["BTW", "BOARDS"]

    async def opted_out(_db, _user_id):
        return {"BOARDS"}  # вимкнення працює, як у всіх

    monkeypatch.setattr(access.settings, "ADMIN_TELEGRAM_ID", 42)
    monkeypatch.setattr(access, "opted_out", opted_out)

    def call(telegram_id):
        user = SimpleNamespace(id=1, telegram_id=telegram_id, phone_number="+380")
        return asyncio.run(access.caller(Db(), Mongo(), user))  # pyright: ignore[reportArgumentType]

    member, scopes = call(42)
    assert member and member[access.BOT_ADMIN]
    assert scopes == [access.GENERAL, "BTW"]
    via = asyncio.run(access.access_via(Db(), member))  # pyright: ignore[reportArgumentType]
    assert via == {"BTW": [access.BOT_ADMIN], "BOARDS": [access.BOT_ADMIN]}
    assert access._reason(access.BOT_ADMIN, member) == "адмін бота"


def test_not_admin_goes_through_groups(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from src.repository import access

    class Mongo:
        async def find_member_by_phone(self, _phone):
            return {"status": "Baby"}

    async def search_scopes(_db, _user_id, member):
        return [access.GENERAL]

    monkeypatch.setattr(access.settings, "ADMIN_TELEGRAM_ID", 42)
    monkeypatch.setattr(access, "search_scopes", search_scopes)
    user = SimpleNamespace(id=1, telegram_id=7, phone_number="+380")
    member, _ = asyncio.run(access.caller(None, Mongo(), user))  # pyright: ignore[reportArgumentType]
    assert member == {"status": "Baby"}
