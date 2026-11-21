"""/access: права дисків і експорти груп таблицями rich-повідомлення."""

from datetime import datetime, timedelta

from src.handlers.access import render
from src.rich import prepare

ADMIN = "drive-best-lviv-fixture-admin@googlegroups.com"
FULL = "fixture-full-members@googlegroups.com"
BTW = "drive-best-lviv-fixture-btw@googlegroups.com"
EJF = "drive-best-lviv-fixture-ejf@googlegroups.com"
FAIR = "demo-fixture-jobfair@googlegroups.com"


def ago(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat()


def drive(name: str, *principals: str) -> dict:
    return {"id": name, "name": f"BEST Lviv - {name}", "principals": list(principals)}


DATA = {
    "drives": [
        drive("BTW", "fixture@example.test", ADMIN, BTW, FULL),
        drive("EJF", "fixture@example.test", ADMIN, FAIR, EJF, FULL),
        drive("RM", ADMIN, FULL),
    ],
    "refreshed_at": "2026-11-08T10:15:01.081553",
    "groups": {
        BTW: {"members": 40, "imported_at": ago(2)},
        EJF: {"members": 37, "imported_at": ago(90)},
    },
    "status_groups": {FULL: ["Alumni", "Full"]},
}


def table(text: str, title: str) -> list[list[str]]:
    """Рядки таблиці під заголовком `title`, без шапки й розділювача."""
    lines = text.split(f"**{title}**\n\n", 1)[1].split("\n\n", 1)[0].split("\n")
    return [[c.strip() for c in line.strip("|").split(" | ")] for line in lines[2:]]


def test_groups_table():
    rows = table(render(DATA), "Групи з прав дисків")
    assert rows == [
        ["`drive-best-lviv-fixture-admin`", "—", "⚠️ немає", "3"],
        ["`fixture-full-members`", "—", "🪪 статус Alumni, Full", "3"],
        ["`demo-fixture-jobfair`", "—", "⚠️ немає", "1"],
        ["`drive-best-lviv-fixture-btw`", "40", "✅ 2 дн.", "1"],
        ["`drive-best-lviv-fixture-ejf`", "37", "⏳ 90 дн.", "1"],
    ]


def test_drives_table_skips_shared_groups():
    rows = table(render(DATA), "Диски")
    assert rows == [
        ["BTW", "✅ fixture-btw", "1"],
        ["EJF", "⚠️ demo-fixture-jobfair, ⏳ fixture-ejf", "1"],
        ["RM", "лише спільні", "—"],
    ]


def test_every_row_has_header_width():
    text = render(DATA)
    for title in ("Групи з прав дисків", "Диски"):
        head = text.split(f"**{title}**\n\n", 1)[1].split("\n", 1)[0]
        width = head.count("|")
        assert all(len(r) == width - 1 for r in table(text, title))


def test_pipe_in_drive_name_stays_in_cell():
    data = {**DATA, "drives": [drive("A|B", ADMIN)]}
    assert "| A\\|B |" in render(data)


def test_export_today():
    data = {**DATA, "groups": {BTW: {"members": 1, "imported_at": ago(0)}}}
    assert ["`drive-best-lviv-fixture-btw`", "1", "✅ сьогодні", "1"] in table(
        render(data), "Групи з прав дисків"
    )


def test_markup_survives_prepare():
    text = render(DATA)
    assert prepare(text) == text


def test_no_drives():
    assert "Оновити права з Drive" in render({**DATA, "drives": []})
