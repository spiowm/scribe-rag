"""Бази даних Notion — табличними документами, як таблиці Drive (F6).

Рядок бази — запис `Назва — Колонка: значення · …`; relation розв'язується в
назви сторінок з того самого обходу `search`, people — в імена. У вектор іде
опис (`summary`), ціла база — у `documents` і віддається `get_document`.
Велику базу можна розбити за relation-властивістю (CRM — за подією): тоді
питання «партнери HACK10» знаходить опис саме цієї частини.

Колонки контактів (телефони й пошти представників компаній — треті особи)
не беруться зовсім: текст іде й у зовнішні AI через MCP.
"""

import re
from collections import Counter

from src.ingestion import privacy
from src.ingestion.schemas import GENERAL
from src.text import LOOKALIKE

# База, коротша за це, іде у вектор цілою, як малі таблиці: «База подій»
# (20 подій з кортімами, ~7 тис. символів) — це 2–3 фрагменти, і питання «хто
# був у кортімі ІЯК» знаходить рядок одразу, без get_document.
WHOLE = 10_000

# Службові й контактні типи властивостей: у тексті бази їм не місце.
SKIP_TYPES = {
    "phone_number",
    "email",
    "created_by",
    "last_edited_by",
    "created_time",
    "last_edited_time",
    "files",
    "button",
    "rollup",
}
# Текстові колонки з контактами (у CRM «Представник» — ім'я людини з компанії).
CONTACT = re.compile(
    r"представник|контакт|телефон|пошта|e-?mail|phone|contact", re.IGNORECASE
)
# Контакт людини у значенні будь-якої колонки: у CRM у «Посилання» замість
# сайту компанії часто пошта, нік у Telegram чи LinkedIn представника
# (72 значення з «@» на 08.11). Таке значення не береться цілком.
PERSONAL = re.compile(r"@|linkedin\.com/in\b", re.IGNORECASE)
NO_PART = "none"  # ключ частини для рядків без значення властивості розбиття
TOP_VALUES = 6  # скільки найчастіших значень select-колонки показати в описі
# Статуси CRM, що означають «компанія погодилась» (`HACKʼ10 Засіарено`,
# `BTW13 Заефарено`). Їх в описі видно разом із компаніями: у топі частих
# значень статус поточної події може не опинитись (у HACK 2026 перші три —
# брошури HACK8–10), а питають саме «хто засіарив HACK10».
AGREED = re.compile(r"засіар|заефар", re.IGNORECASE)
AGREED_NAMES = 20  # скільки компаній показати на один такий статус


def series(name: str) -> str:
    """Серія події за літерами на початку: `HACKʼ10 Засіарено` і `HACK 2026` —
    обидва HACK. Номер випуску з назви частини не вивести (`HACK 2026` —
    це `HACKʼ10`), тож частина бачить погоджених усієї своєї серії."""
    # явний набір: `ʼ` (U+02BC) для Unicode теж літера, а `HACKʼ10` — це HACK
    letters = re.match(r"[A-Za-zА-ЩЬЮЯҐЄІЇа-щьюяґєії]+", name.strip())
    return letters.group(0).upper().translate(LOOKALIKE) if letters else ""


def plain(rich: list | None) -> str:
    return "".join(r.get("plain_text", "") for r in rich or []).strip()


def page_title(page: dict) -> str:
    """Назва сторінки чи рядка: у рядках бази title-властивість зветься як завгодно."""
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return plain(prop["title"])
    return plain(page.get("title"))


def kept(name: str, prop: dict) -> bool:
    return prop.get("type") not in SKIP_TYPES and not CONTACT.search(name)


def _date(d: dict | None) -> str:
    if not d or not d.get("start"):
        return ""

    def short(x: str) -> str:
        return x[:16].replace("T", " ").removesuffix(" 00:00")

    return short(d["start"]) + (f"..{short(d['end'])}" if d.get("end") else "")


def _number(n) -> str:
    if n is None:
        return ""
    return str(int(n)) if isinstance(n, float) and n.is_integer() else str(n)


def value(prop: dict, titles: dict[str, str]) -> str:
    """Значення властивості текстом; порожнє — нема що показати."""
    kind = prop.get("type")
    v = prop.get(kind) if kind else None
    if kind in ("title", "rich_text"):
        return plain(v)
    if kind in ("select", "status"):
        return (v or {}).get("name", "")
    if kind == "multi_select":
        return ", ".join(o["name"] for o in v or [])
    if kind == "people":
        return ", ".join(p["name"] for p in v or [] if p.get("name"))
    if kind == "relation":
        # недоступні інтеграції сторінки (немає в `search`) просто пропускаються
        return ", ".join(titles[r["id"]] for r in v or [] if titles.get(r["id"]))
    if kind == "date":
        return _date(v)
    if kind == "number":
        return _number(v)
    if kind == "checkbox":
        return "так" if v else ""
    if kind == "url":
        return v or ""
    if kind == "unique_id" and v and v.get("number") is not None:
        return f"{v.get('prefix') or ''}-{v['number']}".lstrip("-")
    if kind == "formula" and v:
        f = v.get(v.get("type"))
        if v.get("type") == "date":
            return _date(f)
        if v.get("type") == "boolean":
            return "так" if f else ""
        return _number(f) if isinstance(f, (int, float)) else str(f or "")
    return ""


def columns(rows: list[dict], drop: str | None = None) -> list[str]:
    """Колонки, що йдуть у текст: назва першою, далі в порядку схеми."""
    seen: dict[str, str] = {}
    for row in rows:
        for name, prop in row["properties"].items():
            if name not in seen and name != drop and kept(name, prop):
                seen[name] = prop["type"]
    return sorted(seen, key=lambda n: seen[n] != "title")


def render_row(row: dict, titles: dict[str, str], drop: str | None = None) -> str:
    """`Назва — Колонка: значення · …`; "" — у рядку нічого показати.

    `drop` — властивість розбиття: у частині «HACK8» список усіх подій
    компанії лише роздував би текст (у CRM — на третину).
    """
    label, parts = "", []
    for name, prop in row["properties"].items():
        if name == drop or not kept(name, prop):
            continue
        text = value(prop, titles).replace("\n", " ")
        if prop["type"] == "title":
            label = text
        elif text and not PERSONAL.search(text):
            parts.append(f"{name}: {text}")
    if not parts and not label:
        return ""
    return f"{label or '(без назви)'} — " + " · ".join(parts)


def partition(
    rows: list[dict], prop: str | None, titles: dict[str, str]
) -> dict[str, tuple[str, list[dict]]]:
    """{ключ: (назва частини, рядки)}. Рядок із кількома подіями — у кожній.

    Ключ — id сторінки, на яку веде relation, тож перейменування події не
    міняє `source_id` частини. Без властивості розбиття — одна частина "".
    """
    if not prop:
        return {"": ("", rows)}
    parts: dict[str, tuple[str, list[dict]]] = {}
    for row in rows:
        refs = [
            r["id"]
            for r in (row["properties"].get(prop) or {}).get("relation") or []
            if titles.get(r["id"])
        ]
        for key in refs or [NO_PART]:
            label = titles.get(key, f"без «{prop}»")
            parts.setdefault(key, (label, []))[1].append(row)
    return parts


def summary(
    head: str, rows: list[dict], lines: list[str], label: str = "", width: int = 200
) -> str:
    """Опис для вектора: шапка, найчастіші значення select-колонок, два рядки.

    Найчастіші значення потрібні CRM: частина «HACK 2026» у статусах має
    «HACK10 Засіарено», і саме за цим словом її й шукають.
    """
    counts: dict[str, Counter] = {}
    agreed: dict[str, list[str]] = {}
    for row in rows:
        for name, prop in row["properties"].items():
            kind = prop.get("type")
            if kind not in ("select", "status", "multi_select") or not kept(name, prop):
                continue
            picked = prop.get(kind)
            options = picked if kind == "multi_select" else [picked] if picked else []
            counts.setdefault(name, Counter()).update(o["name"] for o in options or [])
            for o in options or []:
                if AGREED.search(o["name"]) and series(o["name"]) == series(label):
                    agreed.setdefault(o["name"], []).append(page_title(row))
    out = ["База даних Notion. Повний вміст — get_document.", head]
    for name, c in counts.items():
        if c:
            top = ", ".join(f"{v} ({n})" for v, n in c.most_common(TOP_VALUES))
            out.append(f"Часті значення «{name}»: {top}")
    for status, names in sorted(agreed.items(), key=lambda kv: -len(kv[1])):
        shown = ", ".join(n for n in names[:AGREED_NAMES] if n)
        more = " …" if len(names) > AGREED_NAMES else ""
        out.append(f"{status} ({len(names)}): {shown}{more}")
    out += [line[:width] for line in lines[:2]]
    return "\n".join(out)


def database_documents(
    database: dict,
    rows: list[dict],
    titles: dict[str, str],
    split_by: str | None = None,
    path: str = "",
) -> tuple[list[dict], int]:
    """Рядки `documents` для однієї бази і скільки замаскованих збігів.

    `last_edited` — найпізніша правка рядка частини: за нею крок 2 синку
    бачить, що опис треба переембедити.
    """
    name = page_title(database) or "База без назви"
    masked = 0
    docs: list[dict] = []
    for key, (label, part_rows) in partition(rows, split_by, titles).items():
        # найсвіжіші першими: так приклади в описі — про поточні події
        part_rows = sorted(part_rows, key=lambda r: r["last_edited_time"], reverse=True)
        lines = []
        for row in part_rows:
            line, hits = privacy.mask(render_row(row, titles, split_by))
            masked += hits
            if line:
                lines.append(line)
        if not lines:
            continue
        head = (
            f"## База «{name}»"
            + (f" · частина «{label}»" if label else "")
            + f" · рядків: {len(lines)} · колонки: {', '.join(columns(part_rows, split_by))}"
        )
        text = head + "\n" + "\n".join(lines)
        docs.append(
            {
                "source_id": f"db:{database['id']}" + (f":{key}" if key else ""),
                "source": "notion",
                "title": f"{name} — {label}" if label else name,
                "path": path,
                "url": database.get("url", ""),
                "last_edited": max(r["last_edited_time"] for r in part_rows),
                "text": text,
                "scope": GENERAL,
                "summary": summary(head, part_rows, lines, label)
                if len(text) > WHOLE
                else None,
            }
        )
    return docs, masked
