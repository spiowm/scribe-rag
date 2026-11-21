import asyncio
from typing import ClassVar

from src.config import Settings, settings
from src.ingestion import notion_db, pipeline
from src.ingestion.chunker import Chunker
from src.ingestion.schemas import GENERAL, ChunkMetadata

DB = {
    "id": "crm",
    "title": [{"plain_text": "База компаній"}],
    "url": "https://www.notion.so/fixture-database",
}
HACK, BEC = "ev-hack", "ev-bec"
TITLES = {HACK: "HACK 2026", BEC: "BEC 2026", "p-fixture-owner": "Відповідальний Приклад"}


def rich(text):
    return [{"plain_text": text}] if text else []


def company(
    rid, name, statuses=(), events=(), edited="2026-10-01T10:00:00.000Z", **extra
):
    props = {
        "Назва компанії": {"type": "title", "title": rich(name)},
        "Статус": {
            "type": "multi_select",
            "multi_select": [{"name": s} for s in statuses],
        },
        "Події": {"type": "relation", "relation": [{"id": e} for e in events]},
        "Відповідальний": {"type": "relation", "relation": [{"id": "p-fixture-owner"}]},
        "Номер": {"type": "phone_number", "phone_number": "+380000000001"},
        "Email": {"type": "email", "email": "fixture-1@example.test"},
        "Представник": {
            "type": "rich_text",
            "rich_text": rich("Контакт Приклад, 0000000002"),
        },
        "Last edited by": {
            "type": "last_edited_by",
            "last_edited_by": {"name": "Хтось", "person": {"email": "fixture-2@example.test"}},
        },
        **extra,
    }
    return {"id": rid, "last_edited_time": edited, "properties": props}


def test_row_resolves_relations_and_drops_contacts():
    row = company(
        "r1",
        "Компанія Приклад",
        statuses=["HACK10 Засіарено"],
        events=[HACK],
        Сайт={"type": "url", "url": "https://example.test/fixture-company"},
        Профіль={"type": "url", "url": "https://www.linkedin.com/in/fixture-contact"},
        Посилання={"type": "url", "url": "fixture-3@example.test / https://example.test/fixture-company"},
        Нотатка={"type": "rich_text", "rich_text": rich("Тг @fixture_contact")},
        Дата={"type": "date", "date": {"start": "2026-04-18", "end": "2026-04-19"}},
    )
    line = notion_db.render_row(row, TITLES)
    assert line == (
        "Компанія Приклад — Статус: HACK10 Засіарено · Події: HACK 2026 · "
        "Відповідальний: Відповідальний Приклад · Сайт: https://example.test/fixture-company · "
        "Дата: 2026-04-18..2026-04-19"
    )
    for leaked in ("380", "0000000002", "@", "Контакт Приклад", "fixture_contact", "Хтось", "linkedin"):
        assert leaked not in line


def test_contact_columns_dropped_by_name_and_type():
    names = notion_db.columns([company("r1", "X")])
    assert names == ["Назва компанії", "Статус", "Події", "Відповідальний"]


def test_empty_rows_skipped_untitled_kept():
    assert notion_db.render_row(company("r1", ""), {}) == ""
    assert notion_db.render_row(company("r2", "", statuses=["Ігнор"]), {}) == (
        "(без назви) — Статус: Ігнор"
    )


def test_partition_by_event_row_in_each_event():
    rows = [
        company("a", "A", events=[HACK, BEC]),
        company("b", "B", events=[HACK]),
        company("c", "C"),
        company("d", "D", events=["unshared-page"]),
    ]
    parts = notion_db.partition(rows, "Події", TITLES)
    got = {k: (label, [r["id"] for r in rs]) for k, (label, rs) in parts.items()}
    assert got == {
        HACK: ("HACK 2026", ["a", "b"]),
        BEC: ("BEC 2026", ["a"]),
        notion_db.NO_PART: ("без «Події»", ["c", "d"]),
    }


def test_documents_per_partition():
    rows = [
        company("a", "A", ["HACK10 Засіарено"], [HACK], "2026-10-05T00:00:00.000Z"),
        company("b", "B", ["HACK10 Ігнор"], [HACK, BEC], "2026-10-07T00:00:00.000Z"),
        company("c", "C", ["BEC`26 Відмова"], [BEC], "2026-09-01T00:00:00.000Z"),
    ]
    docs, masked = notion_db.database_documents(DB, rows, TITLES, "Події", "CR KB")
    by = {d["source_id"]: d for d in docs}
    hack = by[f"db:crm:{HACK}"]
    assert set(by) == {f"db:crm:{HACK}", f"db:crm:{BEC}"} and masked == 0
    assert hack["title"] == "База компаній — HACK 2026"
    assert hack["last_edited"] == "2026-10-07T00:00:00.000Z"
    assert (hack["source"], hack["scope"], hack["path"]) == ("notion", GENERAL, "CR KB")
    assert hack["text"].splitlines() == [
        (
            "## База «База компаній» · частина «HACK 2026» · рядків: 2 · "
            "колонки: Назва компанії, Статус, Відповідальний"
        ),
        # найсвіжіші першими; список подій у частині не повторюється
        "B — Статус: HACK10 Ігнор · Відповідальний: Відповідальний Приклад",
        "A — Статус: HACK10 Засіарено · Відповідальний: Відповідальний Приклад",
    ]
    assert hack["summary"] is None  # мала частина іде у вектор цілою


def test_summary_for_big_partition():
    rows = [
        company(
            f"r{i}",
            f"Компанія {i}",
            ["HACK10 Ігнор" if i % 3 else "HACK10 Засіарено", "ІЯК'18 Засіарено"],
            [HACK],
        )
        for i in range(300)
    ]
    (doc,), _ = notion_db.database_documents(DB, rows, TITLES, "Події")
    summary = doc["summary"]
    assert summary is not None and len(summary) < len(doc["text"]) / 10
    lines = summary.splitlines()
    assert lines[0] == "База даних Notion. Повний вміст — get_document."
    assert lines[1].startswith(
        "## База «База компаній» · частина «HACK 2026» · рядків: 300"
    )
    assert lines[2] == (
        "Часті значення «Статус»: ІЯК'18 Засіарено (300), HACK10 Ігнор (200), "
        "HACK10 Засіарено (100)"
    )
    # погоджені компанії поіменно, бо питають саме «хто засіарив»
    assert lines[3].startswith("HACK10 Засіарено (100): Компанія 0, Компанія 3, ")
    assert lines[3].endswith(" …") and lines[3].count(", ") == 19
    # погоджені іншої серії (ІЯК) в описі частини HACK лише розмивали б її
    assert not any(line.startswith("ІЯК") for line in lines)
    assert len(lines) == 6  # шапка, часті значення, погоджені, два рядки-приклади


def test_series_ignores_edition_and_lookalike_letters():
    assert notion_db.series("HACKʼ10 Засіарено") == notion_db.series("HACK 2026")
    assert notion_db.series("СTF'25 Засіарено") == notion_db.series("CTF 2025")
    assert notion_db.series("ІЯК'17 Заефарено") == notion_db.series("ІЯК17")
    assert notion_db.series("BEC`26") != notion_db.series("BCI`24")


def test_unsplit_database_single_document():
    events = [
        {
            "id": HACK,
            "last_edited_time": "2026-02-24T12:50:00.000Z",
            "properties": {
                "Назва івенту": {"type": "title", "title": rich("HACK 2026")},
                "Учасники кортіми": {
                    "type": "relation",
                    "relation": [{"id": "p-fixture-owner"}],
                },
            },
        }
    ]
    db = {"id": "events", "title": rich("База подій"), "url": ""}
    (doc,), _ = notion_db.database_documents(db, events, TITLES)
    assert doc["source_id"] == "db:events" and doc["title"] == "База подій"
    assert doc["text"].endswith("HACK 2026 — Учасники кортіми: Відповідальний Приклад")


def test_setting_parses_ids_and_split_property():
    s = Settings(NOTION_DATABASE_IDS=" crm:Події , events ,")  # pyright: ignore[reportCallIssue]
    assert s.NOTION_DATABASES == [("crm", "Події"), ("events", None)]
    assert Settings(NOTION_DATABASE_IDS="").NOTION_DATABASES == []  # pyright: ignore[reportCallIssue]


class FakeNotion:
    raw_pages: ClassVar[dict] = {
        HACK: {"properties": {"Назва": {"type": "title", "title": rich("HACK 2026")}}},
        "p-fixture-owner": {
            "properties": {"Ім'я": {"type": "title", "title": rich("Відповідальний Приклад")}}
        },
    }

    async def fetch_database(self, database_id):
        return DB, [company("a", "A", ["HACK10 Засіарено"], [HACK])], "CR KB"


def test_notion_tables_uses_setting(monkeypatch):
    monkeypatch.setattr(settings, "NOTION_DATABASE_IDS", "crm:Події")
    docs, _ = asyncio.run(pipeline.notion_tables(FakeNotion()))  # pyright: ignore[reportArgumentType]
    assert [d["title"] for d in docs] == ["База компаній — HACK 2026"]
    assert "Відповідальний Приклад" in docs[0]["text"]
    monkeypatch.setattr(settings, "NOTION_DATABASE_IDS", "")
    assert asyncio.run(pipeline.notion_tables(FakeNotion())) == ([], 0)  # pyright: ignore[reportArgumentType]


def test_whole_database_split_by_rows():
    head = "## База «База подій» · рядків: 20 · колонки: Назва івенту, Учасники кортіми"
    rows = [
        f"ПОДІЯ {i} — Учасники кортіми: "
        + ", ".join(f"Людина {i}-{j}" for j in range(12))
        for i in range(20)
    ]
    meta = ChunkMetadata(
        source="notion",
        source_id="db:events",
        title="База подій",
        url="",
        last_edited="",
        scope=GENERAL,
    )
    nodes = Chunker().chunk_rows(head + "\n" + "\n".join(rows), meta, budget=800)
    assert len(nodes) > 1
    got = []
    for node in nodes:
        first, *lines = node.text.splitlines()
        assert first == head and lines  # шапка в кожному фрагменті
        got += lines
    assert got == rows  # кожен рядок цілий і лише в одному фрагменті
