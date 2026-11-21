"""Підготовка відповіді моделі до rich-повідомлення Телеграма.

Markdown сам по собі зламатись не може: будь-який рядок розбирається. Телеграм
відкидає повідомлення через HTML усередині нього — невідомий тег, `<tr>` без
таблиці, незакритий тег, `<details>` посеред рядка чи приклеєний до пункту
списку (`RICH_MESSAGE_CONTENT_REQUIRED`). Тому тут чиститься лише HTML, а
решта тексту йде як є. Приклади допустимої розмітки та виправлення
пошкодженого HTML перевіряються в `tests/test_rich.py`.
"""

import html
import re

# Теги, які лишаються розміткою: строкові з документації rich-повідомлень і
# згортний блок, яким користуються бекенд і модель. Решта показується текстом.
INLINE = {
    "a",
    "b",
    "strong",
    "i",
    "em",
    "u",
    "ins",
    "s",
    "strike",
    "del",
    "code",
    "mark",
    "sub",
    "sup",
    "tg-spoiler",
}
BLOCK = {"details", "summary"}
VOID = {"br"}
ALLOWED = INLINE | BLOCK | VOID

# Після імені — пробіл, `/` чи `>`: так `<https://…>` і `<a@b.com>` лишаються
# автопосиланнями, а не тегами.
TAG = re.compile(r"<(/?)([a-zA-Z][\w-]*)(?=[\s/>])[^<>]*>")
# Код не чіпається: `<details>` у зворотних лапках — це текст, а не тег.
# Огороджений блок — до закривальної огорожі або до кінця; строковий код —
# у межах одного абзацу.
CODE = re.compile(
    r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,}).*?(?:^[ \t]{0,3}(?P=fence)[ \t]*$|\Z)"
    r"|(?P<ticks>`+)(?:(?!\n[ \t]*\n).)+?(?<!`)(?P=ticks)(?!`)",
    re.MULTILINE | re.DOTALL,
)


def _outside_code(text: str):
    """Пари (шматок, чи це код) у порядку тексту."""
    pos = 0
    for m in CODE.finditer(text):
        yield text[pos : m.start()], False
        yield m.group(0), True
        pos = m.end()
    yield text[pos:], False


def _broken_tags(text: str) -> set[int]:
    """Позиції тегів, які треба показати текстом.

    Зламаний тег — невідомий, без пари або не на своєму місці: `<summary>`
    поза `<details>`, `<details>` усередині строкового тега.
    """
    broken: set[int] = set()
    # Пара: [імʼя, позиція відкриття, позиція закриття, індекс батька].
    pairs: list[list] = []
    stack: list[int] = []
    pos = 0
    for part, is_code in _outside_code(text):
        if not is_code:
            for m in TAG.finditer(part):
                closing, name = m.group(1), m.group(2).lower()
                at = pos + m.start()
                if name not in ALLOWED:
                    broken.add(at)
                elif name in VOID:
                    continue
                elif not closing:
                    pairs.append([name, at, None, stack[-1] if stack else None])
                    stack.append(len(pairs) - 1)
                elif any(pairs[i][0] == name for i in stack):
                    # Усе, що відкрилось після пари й не закрилось, — зайве.
                    while pairs[stack[-1]][0] != name:
                        broken.add(pairs[stack.pop()][1])
                    pairs[stack.pop()][2] = at
                else:
                    broken.add(at)
        pos += len(part)
    broken.update(pairs[i][1] for i in stack)

    def misplaced(pair: list) -> bool:
        name, start, _, parent = pair
        if start in broken:
            return True
        if name == "summary":
            return (
                parent is None
                or pairs[parent][0] != "details"
                or (pairs[parent][1] in broken)
            )
        while name in BLOCK and parent is not None:
            if pairs[parent][0] in INLINE:
                return True
            parent = pairs[parent][3]
        return False

    for pair in pairs:
        if misplaced(pair):
            broken.update(at for at in pair[1:3] if at is not None)
    return broken


def prepare(reply: str) -> str:
    """Markdown для `InputRichMessage(markdown=…)`, який Телеграм прийме.

    Зламані теги екрануються (`&lt;`), тож людина бачить їх як текст. Перед
    `<details>` ставиться порожній рядок, а `</details>` переноситься на
    власний рядок: інакше блок опиняється всередині абзацу чи пункту.
    """
    broken = _broken_tags(reply)
    out = ""
    pos = 0
    for part, is_code in _outside_code(reply):
        if is_code:
            out += part
            pos += len(part)
            continue
        last = 0
        for m in TAG.finditer(part):
            out += part[last : m.start()]
            last = m.end()
            tag, name = m.group(0), m.group(2).lower()
            if pos + m.start() in broken:
                out += "&lt;" + tag[1:]
                continue
            if name == "details":
                head = out[: out.rfind("\n") + 1]
                line = out[len(head) :]
                if m.group(1):
                    if line.strip():
                        out += "\n"
                elif line.strip():
                    out = out.rstrip(" \t") + "\n\n"
                elif head.strip() and not re.search(r"\n[ \t]*\n$", head):
                    out = head + "\n" + line
            out += tag
        out += part[last:]
        pos += len(part)
    return out


def plain(reply: str) -> str:
    """Запасний `InputRichMessage(html=…)`: текст без тегів, усе екрановано.

    Іде, коли Телеграм таки відкинув markdown. Розмітка markdown лишається
    видимою, але повідомлення доходить завжди, а `<details>` і `<sub>` не
    сиплються в текст сміттям. Невідомі теги й код не чіпаються: там `<…>` —
    частина змісту, як-от процитований `<tr>` із документа.
    """
    parts = []
    for part, is_code in _outside_code(reply):
        if not is_code:
            part = TAG.sub(_unwrap, part)
        parts.append(part)
    return html.escape("".join(parts))


def _unwrap(m: re.Match) -> str:
    name = m.group(2).lower()
    if name not in ALLOWED:
        return m.group(0)
    return "\n" if name in VOID else ""


def table(head: list[str], rows: list[list[str]]) -> list[str]:
    """Рядки markdown-таблиці. `|` у клітинці розірвав би рядок на зайві
    колонки, тому екранується."""
    return [
        "| " + " | ".join(head) + " |",
        "|" + "---|" * len(head),
        *("| " + " | ".join(c.replace("|", "\\|") for c in row) + " |" for row in rows),
    ]
