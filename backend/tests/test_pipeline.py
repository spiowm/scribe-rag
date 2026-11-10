import asyncio

import pytest

from src.config import settings
from src.ingestion import pipeline
from src.ingestion.schemas import GENERAL

PDF = "application/pdf"
BTW = "BTW_DRIVE"


MEMBERS, BOARDS = "MEMBERS_DRIVE", "BOARDS_DRIVE"
DRIVES = (
    {"id": BTW, "name": "BEST Lviv - BTW"},
    {"id": MEMBERS, "name": "BEST Lviv - Members"},
    {"id": BOARDS, "name": "BEST Lviv - Boards"},
)


class FakeDrive:
    """`general` — лістинг загального диска (Members), `drive` — робочого."""

    def __init__(self, general, drive, drives=DRIVES):
        self.general, self.drive, self.drives = general, drive, list(drives)

    async def list_drives(self):
        return self.drives

    async def list_drive(self, drive_id, mimes, *, paused_drives=()):
        return self.general if drive_id == MEMBERS else self.drive


def targets(drive: FakeDrive):
    return asyncio.run(pipeline.gdrive_targets(drive))  # pyright: ignore[reportArgumentType]


def f(fid, path="", mime=PDF, drive=None):
    return {
        "id": fid,
        "name": fid,
        "mimeType": mime,
        "driveId": drive,
        "size": "10",
        "modifiedTime": "t",
        "path": path,
    }


@pytest.fixture(autouse=True)
def general_drive(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_GENERAL_ID", MEMBERS)
    # справжні диски з локального .env не мають потрапляти в тести
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_IDS", "")
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_PAUSED_IDS", "")
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_GENERAL_FOREIGN", "")


@pytest.fixture
def one_drive(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_IDS", BTW)


def test_drive_files_get_scope_and_drive_name_in_path(one_drive):
    out = targets(FakeDrive([], [f("a", "[2023] BTW - XII/PR"), f("b")]))
    by = {x["id"]: x for x in out}
    assert by["a"]["scope"] == BTW and by["a"]["path"] == "BTW/[2023] BTW - XII/PR"
    assert by["b"]["path"] == "BTW"


def test_general_wins_over_drive_for_the_same_file(one_drive):
    out = targets(FakeDrive([f("a", "Documents", drive=MEMBERS)], [f("a", "x")]))
    assert [(x["scope"], x["path"]) for x in out] == [(GENERAL, "Documents")]


def test_whole_general_drive_is_general_with_path_from_its_root():
    """Не лише `Documents`: таблиця планування в корені й `Logos` теж загальні,
    а шлях — від кореня диска, без його назви."""
    out = targets(
        FakeDrive(
            [
                f("plan", "", drive=MEMBERS),
                f("ap", "Documents/1. Action Plans", drive=MEMBERS),
                f("logo", "Logos/8. BTW", drive=MEMBERS),
            ],
            [],
        )
    )
    assert {x["id"]: (x["scope"], x["path"]) for x in out} == {
        "plan": (GENERAL, ""),
        "ap": (GENERAL, "Documents/1. Action Plans"),
        "logo": (GENERAL, "Logos/8. BTW"),
    }


def test_unseen_general_drive_fails_instead_of_looking_empty():
    with pytest.raises(RuntimeError):
        targets(FakeDrive([], [], drives=[DRIVES[0]]))


def test_unseen_drive_fails_instead_of_looking_empty(one_drive):
    with pytest.raises(RuntimeError):
        targets(FakeDrive([], [], drives=[]))


def test_unreadable_types_dropped(one_drive):
    out = targets(FakeDrive([], [f("a", mime="image/jpeg")]))
    assert out == []


def test_same_tool_call_twice_is_answered_from_memory(monkeypatch):
    from google.genai import types

    from src.generation import chain as chain_mod
    from src.generation import tools

    calls = []

    async def fake_run(deps, args, limits, scopes):
        calls.append(args)
        return {"results": []}

    spec = tools.TOOLS["search_knowledge_base"]
    monkeypatch.setitem(
        tools.TOOLS,
        "search_knowledge_base",
        tools.ToolSpec(**{**spec.__dict__, "run": fake_run}),
    )
    rag = object.__new__(chain_mod.RagChain)
    chain_mod.LAST_RUN.set({"steps": 1})
    fc = types.FunctionCall(
        name="search_knowledge_base", args={"query": "BTW", "terms": ["a"]}
    )
    first = asyncio.run(rag._dispatch(fc, [GENERAL]))
    chain_mod.current_run()["steps"] = 2
    again = asyncio.run(rag._dispatch(fc, [GENERAL]))
    other = asyncio.run(
        rag._dispatch(
            types.FunctionCall(name="search_knowledge_base", args={"query": "EBEC"}),
            [GENERAL],
        )
    )
    assert first == {"results": []} and other == {"results": []}
    assert "кроці 1" in again["note"]
    assert len(calls) == 2


def test_shortcut_to_closed_drive_keeps_the_target_drive_scope(one_drive):
    """Ярлик на файл Boards, що лежить на BTW, не відкриває його тим, хто бачить BTW
    (рев'ю 08.11: бюджети Boards ярликами на Leisure events)."""
    out = targets(FakeDrive([], [f("budget", "LG", drive=BOARDS), f("own", drive=BTW)]))
    assert {x["id"]: x["scope"] for x in out} == {"budget": BOARDS, "own": BTW}


def test_file_on_the_general_drive_is_general_wherever_linked(one_drive):
    out = targets(FakeDrive([f("a", drive=MEMBERS)], [f("b", drive=MEMBERS)]))
    assert {x["id"]: x["scope"] for x in out} == {"a": GENERAL, "b": GENERAL}


def test_general_shortcut_into_boards_is_not_general(one_drive):
    out = targets(FakeDrive([f("x", drive=BOARDS)], []))
    assert [x["scope"] for x in out] == [BOARDS]


def test_foreign_drive_keeps_source_scope(one_drive, monkeypatch):
    """SIM/SIT: диск, де акаунт не учасник, прав не показує — мітка джерела."""
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_GENERAL_FOREIGN", "FOREIGN")
    out = targets(FakeDrive([f("sim", drive="FOREIGN")], [f("x", drive="FOREIGN")]))
    assert {x["id"]: x["scope"] for x in out} == {"sim": GENERAL, "x": BTW}


def test_unlisted_foreign_drive_on_general_is_skipped(one_drive, monkeypatch):
    """Ярлик на чужу закриту теку будь-де на загальному диску не стає загальним."""
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_GENERAL_FOREIGN", "FOREIGN")
    out = targets(
        FakeDrive([f("ok", drive="FOREIGN"), f("no", drive="OTHER"), f("mine")], [])
    )
    assert [x["id"] for x in out] == ["ok"]


def test_retry_starts_with_empty_call_memory(monkeypatch):
    """Друга спроба agy — нова розмова: «результат вище» там неправда."""
    from src.generation import chain as chain_mod

    chain_mod.LAST_RUN.set({"steps": 3, "seen_calls": {("x", "{}"): 1}})

    class Stop(Exception):
        pass

    def boom(**_kw):
        raise Stop

    monkeypatch.setattr(chain_mod, "AgyProcess", boom)
    rag = object.__new__(chain_mod.RagChain)
    rag.agy_schema_path = "schema.json"
    with pytest.raises(Stop):
        asyncio.run(rag._reply_via_agy("q", [], None, [GENERAL]))
    assert "seen_calls" not in chain_mod.current_run()


def test_nfd_names_become_nfc():
    import unicodedata

    nfd = unicodedata.normalize("NFD", "Сценарій")
    assert nfd != "Сценарій"
    assert pipeline.nfc(nfd) == "Сценарій"


def test_sheet_summary_built_at_index_from_text(monkeypatch):
    """Опис аркушів рахується в index() з тексту, назви й шляху: зміна формату
    доходить через `full` без повторного видобутку (рев'ю 09.11, 1.3)."""
    from src.models.document import Document

    text = "## Аркуш «Бюджет» · рядків: 80 · колонки: Стаття, Сума\n" + "\n".join(
        f"Позиція {i} — Сума: {i * 10}" for i in range(80)
    )
    doc = Document(
        source_id="sheet",
        source="gdrive",
        title="Бюджет.xlsx",
        path="Courses/BBC 2021",
        url="",
        last_edited="t",
        text=text,
        scope=GENERAL,
        summary="Таблиця. Повний вміст — get_document.",  # старий формат
    )

    async def meta(db, source):
        return {
            "sheet": pipeline.documents.Meta(
                "t", GENERAL, "Courses/BBC 2021", len(text)
            )
        }

    async def load(db, ids):
        return [doc]

    class FakeQdrant:
        async def index_state(self, source):
            return {"sheet": ("t", GENERAL, "Courses/BBC 2021")}

        async def reindex(self, fresh_nodes, stale_ids):
            self.nodes = fresh_nodes
            return len(fresh_nodes)

    monkeypatch.setattr(pipeline.documents, "meta", meta)
    monkeypatch.setattr(pipeline.documents, "load", load)
    qdrant = FakeQdrant()
    asyncio.run(pipeline.index(qdrant, None, "gdrive", full=True))  # pyright: ignore[reportArgumentType]
    embedded = "\n".join(n.text for n in qdrant.nodes)
    assert "Таблиця «Бюджет» · Courses/BBC 2021" in embedded
    assert "Позиція 79" not in embedded


def test_file_moved_inside_drive_gets_new_path_without_rereading(
    one_drive, monkeypatch
):
    """Переміщення між теками `modifiedTime` не міняє (рев'ю 09.11, 1.7)."""
    saved = {}

    async def meta(db, source):
        return {"a": pipeline.documents.Meta("t", BTW, "BTW/Old", 1000)}

    async def set_places(db, got):
        saved.update(got)

    async def upsert(db, rows):
        assert not rows  # нічого не перечитується

    async def delete(db, ids):
        return len(ids)

    monkeypatch.setattr(pipeline.documents, "meta", meta)
    monkeypatch.setattr(pipeline.documents, "set_places", set_places)
    monkeypatch.setattr(pipeline.documents, "upsert_documents", upsert)
    monkeypatch.setattr(pipeline.documents, "delete_documents", delete)
    drive = FakeDrive([], [f("a", "2. Minutes", drive=BTW)])
    out = asyncio.run(pipeline.extract_gdrive(drive, None, None))  # pyright: ignore[reportArgumentType]
    assert out.changed == 0
    assert saved == {"a": (BTW, "BTW/2. Minutes")}


def test_moved_failed_and_secret_files_through_both_steps(monkeypatch):
    """Сценарій `review-2026-11-09/t_moved.py` на `documents` у памʼяті: файл
    переїхав на диск D; переїхав і змінився, але не завантажився; став
    «секретним». Обидва перші мають опинитись під D, третій — зникнути."""
    from src.models.document import Document

    old = "старий текст " * 20
    rows = {
        sid: {
            "source_id": sid,
            "source": "gdrive",
            "title": sid,
            "path": "",
            "url": "",
            "last_edited": "t1",
            "text": old,
            "scope": GENERAL,
            "summary": None,
        }
        for sid in ("F1", "F2", "F3")
    }

    async def meta(db, source):
        return {
            s: pipeline.documents.Meta(
                r["last_edited"], r["scope"], r["path"], len(r["text"])
            )
            for s, r in rows.items()
        }

    async def upsert(db, new):
        rows.update({r["source_id"]: r for r in new})

    async def set_places(db, places):
        for sid, (scope, path) in places.items():
            rows[sid].update(scope=scope, path=path)

    async def delete(db, ids):
        for sid in ids:
            rows.pop(sid)
        return len(ids)

    async def load(db, ids):
        return [Document(**rows[s]) for s in ids]

    async def targets(_gdrive):
        return [
            {
                "id": "F1",
                "name": "F1",
                "mimeType": "m",
                "modifiedTime": "t1",
                "scope": "D",
            },
            {
                "id": "F2",
                "name": "F2",
                "mimeType": "m",
                "modifiedTime": "t2",
                "scope": "D",
            },
            {
                "id": "F3",
                "name": "F3 password",
                "mimeType": "m",
                "modifiedTime": "t1",
                "scope": GENERAL,
            },
        ]

    class Reader:
        async def fetch(self, _gdrive, fid):
            if fid == "F2":
                raise RuntimeError("download failed")
            return b"x"

    class FakeQdrant:
        async def index_state(self, source):
            return {s: ("t1", GENERAL, "") for s in ("F1", "F2", "F3")}

        async def reindex(self, fresh_nodes, stale_ids):
            self.scopes = {
                n.metadata["source_id"]: n.metadata["scope"] for n in fresh_nodes
            }
            self.stale = stale_ids
            return len(fresh_nodes)

    for name, fn in [
        ("meta", meta),
        ("upsert_documents", upsert),
        ("set_places", set_places),
        ("delete_documents", delete),
        ("load", load),
    ]:
        monkeypatch.setattr(pipeline.documents, name, fn)
    monkeypatch.setattr(pipeline, "gdrive_targets", targets)
    monkeypatch.setitem(pipeline.READERS, "m", Reader())

    out = asyncio.run(pipeline.extract_gdrive(None, None, None))  # pyright: ignore[reportArgumentType]
    assert (out.changed, out.failed, out.removed, out.skipped_secret) == (0, 1, 1, 1)
    assert {s: (r["scope"], r["text"]) for s, r in rows.items()} == {
        "F1": ("D", old),
        "F2": ("D", old),  # не завантажився — мітка однаково нова
    }
    qdrant = FakeQdrant()
    asyncio.run(pipeline.index(qdrant, None, "gdrive"))  # pyright: ignore[reportArgumentType]
    assert qdrant.scopes == {"F1": "D", "F2": "D"}
    assert qdrant.stale == {"F1", "F2", "F3"}  # точки F3 прибираються


def test_general_drive_listing_keeps_target_drive_of_shortcuts(monkeypatch):
    """Справжній `list_drive` на загальному диску: ярлик на файл `Boards` лишає
    мітку Boards, а не стає загальним; тека-ярлик на чужий диск (`SIM/SIT`)
    обходиться й лишається загальною — її прав акаунт не бачить."""
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_GENERAL_FOREIGN", "FOREIGN")
    from src.connectors.gdrive import GDriveConnector

    drive = object.__new__(GDriveConnector)
    folders = [{"id": "docs", "name": "Documents", "parents": [MEMBERS]}]
    files = [
        {**f("plan", drive=MEMBERS), "parents": [MEMBERS]},
        {**f("ap", drive=MEMBERS), "parents": ["docs"]},
    ]
    shortcuts = [
        {
            "id": "s1",
            "name": "Бюджет",
            "parents": ["docs"],
            "shortcutDetails": {"targetId": "budget", "targetMimeType": PDF},
        },
        {
            "id": "s2",
            "name": "SIM/SIT",
            "parents": [MEMBERS],
            "shortcutDetails": {
                "targetId": "sim",
                "targetMimeType": GDriveConnector.FOLDER,
            },
        },
    ]

    async def fake_list(params):
        assert params["driveId"] == MEMBERS
        if GDriveConnector.FOLDER in params["q"]:
            return folders
        if GDriveConnector.SHORTCUT in params["q"]:
            return shortcuts
        return files

    class Response:
        def json(self):
            return {**f("budget", drive=BOARDS), "trashed": False}

    async def fake_get(url, params):
        assert url.endswith("/budget")
        return Response()

    async def fake_children(folder_id):
        assert folder_id == "sim"
        return [f("lesson", drive="FOREIGN")]

    async def drives():
        return list(DRIVES)

    drive._list = fake_list  # type: ignore[method-assign]
    drive._get_with_retry = fake_get  # type: ignore[method-assign]
    drive.list_children = fake_children  # type: ignore[method-assign]
    drive.list_drives = drives  # type: ignore[method-assign]
    out = asyncio.run(pipeline.gdrive_targets(drive))
    assert {x["id"]: (x["scope"], x["path"]) for x in out} == {
        "plan": (GENERAL, ""),
        "ap": (GENERAL, "Documents"),
        "budget": (BOARDS, "Documents"),
        "lesson": (GENERAL, "SIM/SIT"),
    }


def test_infobook_on_any_drive_leaves_documents_and_index(monkeypatch):
    """Інфобук, що вже лежить у `documents` і Qdrant, прибирається наступним
    синком: крок 1 видаляє рядок, крок 2 — точки."""
    rows = {"ib", "ok"}

    async def targets(_gdrive):
        return [
            {"id": "ib", "name": "Інфобук BEC`2026", "mimeType": "m", "modifiedTime": "t", "scope": BTW},
            {"id": "ok", "name": "Хендбук", "mimeType": "m", "modifiedTime": "t", "scope": BTW},
        ]  # fmt: skip

    async def meta(db, source):
        return {s: pipeline.documents.Meta("t", BTW, "", 1000) for s in rows}

    async def delete(db, ids):
        rows.difference_update(ids)
        return len(ids)

    async def nothing(*_args):
        return []

    class FakeQdrant:
        async def index_state(self, source):
            return {s: ("t", BTW, "") for s in ("ib", "ok")}

        async def reindex(self, fresh_nodes, stale_ids):
            self.stale = stale_ids
            return 0

    monkeypatch.setattr(pipeline, "gdrive_targets", targets)
    monkeypatch.setattr(pipeline.documents, "meta", meta)
    monkeypatch.setattr(pipeline.documents, "delete_documents", delete)
    monkeypatch.setattr(pipeline.documents, "upsert_documents", nothing)
    monkeypatch.setattr(pipeline.documents, "set_places", nothing)
    monkeypatch.setattr(pipeline.documents, "load", nothing)

    out = asyncio.run(pipeline.extract_gdrive(None, None, None))  # pyright: ignore[reportArgumentType]
    assert (out.skipped_secret, out.removed, rows) == (1, 1, {"ok"})
    qdrant = FakeQdrant()
    asyncio.run(pipeline.index(qdrant, None, "gdrive"))  # pyright: ignore[reportArgumentType]
    assert qdrant.stale == {"ib"}


def test_logo_text_is_not_indexed_but_tables_and_notion_are(monkeypatch):
    """Логотип після OCR («RED») не дає фрагментів і прибирається з індексу,
    якщо вже там був; довгий документ, маленька таблиця й коротка сторінка чи
    база Notion індексуються як раніше."""
    from src.ingestion.sheets import _book_text
    from src.models.document import Document

    texts = {
        "gdrive": {
            "logo": "RED",
            "doc": "Регламент закупівель: хто погоджує, як звітувати. " * 5,
            "sheet": _book_text([("A", [["Стаття", "Сума"], ["їжа", "700"]], [])]),
        },
        "notion": {
            "board": "### President\nАртем Беренда",
            "db:base": "Компанія — Статус: погоджено",
        },
    }

    def run(source):
        docs = {
            sid: Document(
                source_id=sid,
                source=source,
                title=sid,
                path="",
                url="",
                last_edited="t",
                text=text,
                scope=GENERAL,
                summary=None,
            )
            for sid, text in texts[source].items()
        }

        async def meta(db, src):
            return {
                sid: pipeline.documents.Meta("t", GENERAL, "", len(d.text.strip()))
                for sid, d in docs.items()
            }

        async def load(db, ids):
            return [docs[s] for s in ids]

        class FakeQdrant:
            async def index_state(self, src):
                return {"logo": ("t", GENERAL, "")} if src == "gdrive" else {}

            async def reindex(self, fresh_nodes, stale_ids):
                self.got = {n.metadata["source_id"] for n in fresh_nodes}
                self.stale = stale_ids
                return len(fresh_nodes)

        monkeypatch.setattr(pipeline.documents, "meta", meta)
        monkeypatch.setattr(pipeline.documents, "load", load)
        qdrant = FakeQdrant()
        asyncio.run(pipeline.index(qdrant, None, source))  # pyright: ignore[reportArgumentType]
        return qdrant

    drive = run("gdrive")
    assert drive.got == {"doc", "sheet"}
    assert "logo" in drive.stale  # уже проіндексований логотип прибирається
    assert run("notion").got == {"board", "db:base"}
