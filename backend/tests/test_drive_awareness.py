"""Бот знає про диски: блок фактів у промпті й позначки дисків біля джерел."""

import asyncio
from types import SimpleNamespace

from src.generation import sources
from src.generation.prompts import drive_context
from src.repository import access
from src.repository.access import DriveFacts

BTW, CTF, EBEC, COURSES = "0ANG", "0ADe", "0AKW", "0AK8"
ADMIN = "scribe-fixture-admin@googlegroups.com"
FULL = "scribe-fixture-full-members@googlegroups.com"


def facts(**kw) -> DriveFacts:
    base = {
        "recognized": True,
        "connected": {BTW: "BTW", COURSES: "Courses", CTF: "CTF", EBEC: "EBEC"},
        "reasons": {
            BTW: ["група scribe-fixture-btw"],
            CTF: ["група scribe-fixture-ctf"],
            EBEC: ["статус Full"],
        },
        "off": {CTF},
    }
    return DriveFacts(**{**base, **kw})


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def tuples(self):
        return self.rows


def test_drive_facts_names_reasons_and_opt_outs(monkeypatch):
    monkeypatch.setattr(access, "STATUS_GROUPS", {FULL: ("Full", "Alumni")})
    monkeypatch.setattr(access, "ADMIN_GROUPS", {ADMIN})

    async def access_via(db, member):
        return {
            BTW: ["scribe-fixture-btw@googlegroups.com"],
            CTF: ["scribe-fixture-ctf@googlegroups.com"],
            EBEC: ["scribe-fixture-full-members@googlegroups.com"],
            "BOARDS": ["scribe-fixture-board@googlegroups.com"],  # не індексований
        }

    async def opted_out(db, user_id):
        return {CTF, "GONE"}  # вимкнення диска, до якого вже нема доступу

    class Db:
        async def execute(self, _stmt):
            return Rows(
                [
                    (EBEC, "BEST Lviv - EBEC"),
                    (BTW, "BEST Lviv - BTW"),
                    (CTF, "BEST Lviv - CTF"),
                    (COURSES, "BEST Lviv - Courses"),
                ]
            )

    monkeypatch.setattr(access, "access_via", access_via)
    monkeypatch.setattr(access, "opted_out", opted_out)
    member = {"status": "Full", "email": "fixturemember1@example.test"}
    got = asyncio.run(access.drive_facts(Db(), 1, member, [BTW, CTF, EBEC, COURSES]))  # pyright: ignore[reportArgumentType]
    assert list(got.connected.values()) == ["BTW", "Courses", "CTF", "EBEC"]
    assert got.reasons == {
        BTW: ["група scribe-fixture-btw"],
        CTF: ["група scribe-fixture-ctf"],
        EBEC: ["статус Full"],
    }
    assert got.off == {CTF}
    assert got.enabled == [BTW, EBEC]


def test_drive_context_lists_enabled_off_and_closed():
    text = drive_context(facts())
    assert "підключені до бота: BTW, Courses, CTF, EBEC." in text
    assert (
        "Увімкнені, шукаєш і тут: BTW (група scribe-fixture-btw), "
        "EBEC (статус Full)." in text
    )
    assert "вимкнені в /drives — тут не шукаєш: CTF (група scribe-fixture-ctf)" in text
    assert "Без доступу: Courses." in text
    assert "Notion і загальний диск" in text


def test_drive_context_says_bot_admin():
    admin = facts(reasons={BTW: ["адмін бота"], CTF: ["адмін бота"]}, off=set())
    assert "Увімкнені, шукаєш і тут: BTW (адмін бота), CTF (адмін бота)." in (
        drive_context(admin)
    )


def test_drive_view_for_drives_command(monkeypatch):
    monkeypatch.setattr(access, "STATUS_GROUPS", {FULL: ("Full", "Alumni")})
    monkeypatch.setattr(access, "ADMIN_GROUPS", {ADMIN})

    from src.config import settings

    async def drive_facts(db, user_id, member, indexed):
        return facts()

    class Db:
        async def execute(self, _stmt):
            return Rows(
                [
                    (COURSES, "BEST Lviv - Courses", [ADMIN, FULL]),
                    ("MEMBERS", "BEST Lviv - Members", [ADMIN, FULL]),
                ]
            )

    monkeypatch.setattr(access, "drive_facts", drive_facts)
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_GENERAL_ID", "MEMBERS")
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_GENERAL_FOREIGN", "SIM")
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_GENERAL_FOREIGN_LABEL", "Зовнішні матеріали")
    got = asyncio.run(access.drive_view(Db(), 1, {}, []))  # pyright: ignore[reportArgumentType]
    assert got == {
        "recognized": True,
        "locked": ["Notion", "загальний диск Members", "Зовнішні матеріали"],
        "drives": [
            {
                "id": BTW,
                "name": "BTW",
                "on": True,
                "via": ["група scribe-fixture-btw"],
            },
            {
                "id": CTF,
                "name": "CTF",
                "on": False,
                "via": ["група scribe-fixture-ctf"],
            },
            {"id": EBEC, "name": "EBEC", "on": True, "via": ["статус Full"]},
        ],
        "no_access": [{"name": "Courses", "how": ["статус Full / Alumni"]}],
    }


def test_grants_without_status_group_names_drive_groups(monkeypatch):
    monkeypatch.setattr(access, "STATUS_GROUPS", {FULL: ("Full", "Alumni")})
    monkeypatch.setattr(access, "ADMIN_GROUPS", {ADMIN})

    board = "scribe-fixture-board@googlegroups.com"
    assert access._grants([ADMIN, board, "outside@example.test"]) == [
        "група scribe-fixture-board"
    ]
    assert access._grants([ADMIN]) == []


def test_drive_context_unrecognized_and_without_access():
    stranger = drive_context(facts(recognized=False, reasons={}, off=set()))
    assert "не впізнано" in stranger and "Увімкнені" not in stranger
    nobody = drive_context(facts(reasons={}, off=set()))
    assert "Доступу до жодного з них" in nobody and "Без доступу" not in nobody
    empty = drive_context(facts(connected={}, reasons={}, off=set()))
    assert "не підключено" in empty


def test_system_instruction_carries_drive_block():
    from src.generation.chain import RagChain

    chain = RagChain.__new__(RagChain)
    chain.system_prompt = "PROMPT"
    text = chain._system_instruction(None, drive_context(facts()))
    assert text.index("## З ким ти говориш") < text.index("## Де ти шукаєш")
    assert text.index("## Де ти шукаєш") < text.index("## Поточна дата")
    assert "## Де ти шукаєш" not in chain._system_instruction(None)


def test_url_key_ignores_tails_and_slugs():
    key = sources.url_key
    notion = "11111111111141118111000000000001"
    assert key(f"https://app.notion.com/p/Fixture-Page-{notion}") == notion
    assert key(f"https://www.notion.so/{notion}?pvs=25#abc") == notion
    assert (
        key("https://app.notion.com/p/11111111-1111-4111-8111-000000000001") == notion
    )
    gid = "synthetic-drive-file-01"
    assert key(f"https://docs.google.com/document/d/{gid}/edit?usp=drivesdk") == gid
    assert key(f"https://drive.google.com/open?id={gid}") == gid
    assert key("https://example.test/page/#x") == "https://example.test/page"


REPLY = """Відповідь.

<details><summary>📚 Джерела</summary>

- [[DOC][Example] Протокол.pdf](https://drive.google.com/file/d/synthetic-drive-file-02/view?usp=drivesdk)
- [Сторінка прикладу](https://app.notion.com/p/Fixture-Page-11111111111141118111000000000001)
- [Логістика](https://docs.google.com/document/d/synthetic-drive-file-03/edit?usp=drivesdk) і [план](https://docs.google.com/document/d/synthetic-drive-file-04/edit)
- [Статут](https://docs.google.com/document/d/synthetic-drive-file-05/edit)
- [Невідомий](https://drive.google.com/file/d/1NOTINDEXEDxxxxxxxxx/view)
</details>"""

PLACES = [
    (
        "https://drive.google.com/file/d/synthetic-drive-file-02/view?usp=drivesdk",
        "gdrive",
        EBEC,
    ),
    ("https://app.notion.com/p/11111111111141118111000000000001", "notion", "general"),
    (
        "https://docs.google.com/document/d/synthetic-drive-file-03/edit?usp=drivesdk",
        "gdrive",
        BTW,
    ),
    (
        "https://docs.google.com/document/d/synthetic-drive-file-04/edit?usp=drivesdk",
        "gdrive",
        CTF,
    ),
    (
        "https://docs.google.com/document/d/synthetic-drive-file-05/edit?usp=drivesdk",
        "gdrive",
        "general",
    ),
    # збіг частиною (contains), але інший документ — не годиться
    ("https://drive.google.com/file/d/1NOTINDEXEDxxxxxxxxxEXTRA/view", "gdrive", BTW),
]


def tag(monkeypatch, reply: str, places=PLACES) -> tuple[str, list]:
    asked = []

    async def fake_places(db, keys):
        asked.append(keys)
        return places

    monkeypatch.setattr(sources.documents, "places", fake_places)
    return asyncio.run(sources.tag_sources(None, reply, facts())), asked  # pyright: ignore[reportArgumentType]


def test_sources_get_drive_labels_and_searched_line(monkeypatch):
    out, asked = tag(monkeypatch, REPLY)
    lines = out.splitlines()
    assert lines[4].endswith("view?usp=drivesdk) · EBEC")
    assert lines[5].endswith(") · Notion")
    assert lines[6].endswith("/edit) · BTW, CTF")  # два посилання в рядку
    assert lines[7].endswith(") · загальний диск")
    assert lines[8].endswith("/view)")  # без збігу — як було
    # підпис окремим абзацом усередині блоку, `</details>` на своєму рядку
    assert lines[9:] == [
        "",
        "<sub>Шукав у: Notion, загальний диск, BTW, EBEC</sub>",
        "</details>",
    ]
    assert out.startswith("Відповідь.\n\n<details><summary>📚 Джерела</summary>\n\n- [")
    assert len(asked[0]) == 6


def test_reply_without_sources_is_untouched(monkeypatch):
    plain = "Привіт! Чим допомогти?"
    assert tag(monkeypatch, plain) == (plain, [])
    # згорнутий блок, але не джерела, і блок без посилань
    other = "Текст\n\n<details><summary>Деталі</summary>\n\n- [x](https://a.b/c)\n</details>"
    assert tag(monkeypatch, other) == (other, [])
    empty = "Текст\n\n<details><summary>📚 Джерела</summary>\n\n- Notion\n</details>"
    assert tag(monkeypatch, empty) == (empty, [])


def test_tagging_failure_keeps_reply(monkeypatch):
    async def broken(db, keys):
        raise RuntimeError("db down")

    monkeypatch.setattr(sources.documents, "places", broken)
    assert asyncio.run(sources.tag_sources(None, REPLY, facts())) == REPLY  # pyright: ignore[reportArgumentType]


def test_mcp_who_am_i_includes_drives(monkeypatch):
    from src.public import mcp_server

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, _model, _id):
            return SimpleNamespace(id=7, phone_number="+380")

    async def caller(db, mongo, user):
        return {"first_name": "Олексій", "status": "Baby"}, ["general"]

    async def drive_facts(db, user_id, member, indexed):
        assert user_id == 7 and member["status"] == "Baby"
        return facts()

    monkeypatch.setattr(mcp_server, "async_session_maker", Session)
    monkeypatch.setattr(access, "caller", caller)
    monkeypatch.setattr(access, "drive_facts", drive_facts)
    ctx = SimpleNamespace(
        request=SimpleNamespace(
            user=SimpleNamespace(access_token=SimpleNamespace(subject="7")),
            app=SimpleNamespace(state=SimpleNamespace(mongo=None)),
        )
    )
    text = asyncio.run(mcp_server.who_am_i(ctx))
    assert "**Олексій**" in text and "Увімкнені, шукаєш і тут: BTW" in text
