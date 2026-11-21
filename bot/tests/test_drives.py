"""/drives: що завжди, диски людини й диски без доступу rich-повідомленням."""

from src.handlers.drives import PREFIX, render
from src.rich import prepare


def drive(name: str, on: bool = True, via: str = "") -> dict:
    group = via or f"група drive-best-lviv-{name.lower()}"
    return {"id": f"0A{name}", "name": name, "on": on, "via": [group]}


FULL = ["статус Full / Alumni"]
VIEW = {
    "recognized": True,
    "locked": ["Notion", "загальний диск Members", "SIM/SIT і Proposals"],
    "drives": [
        drive("BTW"),
        drive("CTF"),
        drive("EBEC", via="статус Full"),
        drive("EJF", on=False),
        drive("LEoE"),
    ],
    "no_access": [
        {"name": "Trainshops", "how": FULL},
        {"name": "Boards", "how": ["група drive-best-lviv-board"]},
    ],
}


def table(text: str, title: str) -> list[list[str]]:
    """Рядки таблиці під заголовком `title`, без шапки й розділювача."""
    lines = text.split(f"{title}**\n\n", 1)[1].split("\n\n", 1)[0].split("\n")
    return [[c.strip() for c in line.strip("|").split(" | ")] for line in lines[2:]]


def test_sections_and_buttons():
    text, kb = render(VIEW)
    assert "Notion · загальний диск Members · SIM/SIT і Proposals" in text
    assert table(text, "Твої диски") == [
        ["BTW", "✅ шукаю", "група btw"],
        ["CTF", "✅ шукаю", "група ctf"],
        ["EBEC", "✅ шукаю", "статус Full"],
        ["EJF", "⏸ вимкнув ти", "група ejf"],
        ["LEoE", "✅ шукаю", "група leoe"],
    ]
    assert table(text, "Немає доступу") == [
        ["Trainshops", "статус Full / Alumni"],
        ["Boards", "група board"],
    ]
    assert kb is not None
    buttons = [[b.text for b in row] for row in kb.inline_keyboard]
    assert buttons == [["✅ BTW", "✅ CTF", "✅ EBEC"], ["⏸ EJF", "✅ LEoE"]]
    assert kb.inline_keyboard[1][0].callback_data == PREFIX + "0AEJF"
    assert "довіднику" not in text
    assert prepare(text) == text  # тегів немає, тож rich.prepare нічого не міняє


def test_without_own_drives_no_section_and_no_buttons():
    view = {**VIEW, "drives": []}
    text, kb = render(view)
    assert "Твої диски" not in text and "Вимкни зайве" not in text
    assert "Немає доступу" in text and kb is None


def test_nothing_closed_hides_no_access():
    text, kb = render({**VIEW, "no_access": []})
    assert "Немає доступу" not in text and kb is not None


def test_unrecognized_sees_only_always_and_a_note():
    view = {**VIEW, "recognized": False, "drives": []}
    text, kb = render(view)
    assert "довіднику" in text and kb is None


def test_drive_without_grants_and_pipe_in_name():
    view = {
        **VIEW,
        "drives": [],
        "no_access": [{"name": "A|B", "how": []}],
    }
    assert table(render(view)[0], "Немає доступу") == [["A\\|B", "—"]]
