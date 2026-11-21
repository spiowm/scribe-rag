"""Позначки дисків у блоці «📚 Джерела» — кодом, після генерації.

Модель пише блок сама (`prompts.TELEGRAM_FORMAT`), але звідки документ, вона
не знає: у результатах пошуку мітки диска немає. Тож диск береться з
`documents` за посиланням, а не з відповіді моделі. За посиланням, а не з
результатів цього ходу: джерело буває з попередньої відповіді в розмові.
"""

import logging
import re
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.schemas import GENERAL
from src.repository import documents
from src.repository.access import DriveFacts

logger = logging.getLogger(__name__)

NOTION = "Notion"
GENERAL_DRIVE = "загальний диск"

# Останній згортний блок джерел; заголовок — як у промпті, з будь-якою оздобою.
BLOCK = re.compile(
    r"<details>\s*<summary>(?:(?!</summary>)[^\n])*Джерела(?:(?!</summary>)[^\n])*"
    r"</summary>(?P<body>.*?)</details>",
    re.DOTALL | re.IGNORECASE,
)
# Посилання markdown: назва буває з власними дужками (`[[ML][XXIV Board] …](url)`),
# тож шукається лише `](url`.
LINK = re.compile(r"\]\(\s*<?(https?://[^\s)>]+)")
NOTION_ID = re.compile(r"[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
GOOGLE_ID = re.compile(r"/d/([\w-]{10,})|[?&]id=([\w-]{10,})")


def url_key(url: str) -> str:
    """Що в посиланні означає документ, без хвостів на кшталт `?usp=drivesdk`.

    Notion — 32-значний id наприкінці шляху (зі слагом перед ним чи без),
    Google — id файлу; решта — посилання без `#…`.
    """
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if "notion" in host:
        ids = NOTION_ID.findall(parts.path.lower())
        if ids:
            return ids[-1].replace("-", "")
    if host.endswith("google.com"):
        m = GOOGLE_ID.search(parts.path + "?" + parts.query)
        if m:
            return m[1] or m[2]
    return url.split("#", 1)[0].rstrip("/")


def source_urls(reply: str) -> list[str]:
    """Посилання з блоку джерел; блоку немає — порожньо."""
    blocks = list(BLOCK.finditer(reply))
    return LINK.findall(blocks[-1]["body"]) if blocks else []


def searched(facts: DriveFacts) -> list[str]:
    """Де шукав бот: загальна база й увімкнені диски, з тими ж назвами, що в
    позначках."""
    return [NOTION, GENERAL_DRIVE, *(facts.connected[d] for d in facts.enabled)]


def label(source: str, scope: str, facts: DriveFacts) -> str | None:
    if source == "notion":
        return NOTION
    if scope == GENERAL:
        return GENERAL_DRIVE
    return facts.connected.get(scope)


def annotate(reply: str, labels: dict[str, str], where: list[str]) -> str:
    """Дописує ` · BTW` до рядків блоку джерел і рядок «Шукав у».

    `labels` — url_key → назва. Посилання без назви лишається як є. Кілька
    посилань в одному рядку — назви через кому, без повторів.
    """
    blocks = list(BLOCK.finditer(reply))
    if not blocks:
        return reply
    block = blocks[-1]
    lines = []
    for line in block["body"].split("\n"):
        found = [labels.get(url_key(u)) for u in LINK.findall(line)]
        tags = list(dict.fromkeys(t for t in found if t))
        lines.append(f"{line.rstrip()} · {', '.join(tags)}" if tags else line)
    body = "\n".join(lines).rstrip()
    # Порожній рядок перед підписом: інакше markdown приклеїть його до
    # останнього пункту списку.
    if where:
        body += f"\n\n<sub>Шукав у: {', '.join(where)}</sub>"
    start, end = block.span("body")
    return reply[:start] + body + "\n" + reply[end:]


async def tag_sources(db: AsyncSession, reply: str, facts: DriveFacts) -> str:
    """Відповідь із позначками дисків. Окраса: збій — відповідь як була."""
    urls = source_urls(reply)
    if not urls:
        return reply
    try:
        keys = {url_key(u) for u in urls}
        places = await documents.places(db, keys)
        labels = {}
        for url, source, scope in places:
            key = url_key(url)
            name = label(source, scope, facts)
            if key in keys and name:
                labels[key] = name
        return annotate(reply, labels, searched(facts))
    except Exception:
        logger.exception("позначки дисків у джерелах не додано")
        return reply
