import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from google.genai import types

from src.connectors.mongo import MongoConnector
from src.db import async_session_maker
from src.repository import documents
from src.text import drop_apostrophes
from src.vectorstore.qdrant import QdrantRepository

logger = logging.getLogger(__name__)


SEARCH_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="search_knowledge_base",
            description=(
                "Шукає в базі знань організації. Два джерела в одному індексі:\n"
                "• **Notion** — сторінки про процеси, івенти, ролі, історію та традиції "
                "осередку, хендбуки по відділах, «як робити X» від попередників.\n"
                "• **Google Drive** — документи й архіви: статут, внутрішні правила, "
                "плани, звіти, протоколи зборів, матеріали подій і тренінгів.\n"
                "Тобто в базі є і «як у нас прийнято», і «що записано в документі», "
                "і «що саме вирішили на тих зборах».\n"
                "Про конкретну людину тут лежить те, чого немає в картці: що вона "
                "робила, говорила, за що її памʼятають. Саму картку — статус, родину, "
                "посади — бери з find_person, а на розгорнуте питання про людину клич "
                "обидва інструменти одним кроком.\n"
                "Викликай не лише тоді, коли потрібні факти, а й коли готуєш ідеї, "
                "план, аналіз чи пораду, щоб спиратись на досвід організації. "
                "Для ідей шукай схожі івенти й "
                "наявні напрацювання, для аналізу — як цей процес описаний у нас.\n"
                "У кожного фрагмента є `url`: на ці сторінки й посилайся у відповіді."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description="Самодостатній пошуковий запит українською",
                    ),
                    "terms": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING),
                        description=(
                            "Власні назви з питання, які мусять бути у фрагменті "
                            "дослівно: назви формувань і команд, івенти з номером, "
                            "хвилі набору, прізвища, прізвиська, назви документів. "
                            "Наприклад: Команда Орбіта, Ярмарок’24, весна’26.\n"
                            "Навіщо: пошук по змісту такі слова знаходить погано — "
                            "вони нічого не означають, а лише вказують на щось "
                            "конкретне. Якщо в питанні є така назва і ти не передаш "
                            "її тут, фрагмент про неї може не знайтись зовсім.\n"
                            "Передавай 1-2 назви в тій формі, у якій їх пишуть "
                            "у документах (називний відмінок, без відмінювання). "
                            "Звичайні слова сюди НЕ передавай — ні «традиція», "
                            "ні «виключення», ні «осередок», ні «фінанси»: "
                            "для них достатньо query."
                        ),
                    ),
                },
                required=["query"],
            ),
        )
    ]
)

QUERY_MEMBERS_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="query_members",
            description=(
                "Рахує й перелічує членів за довідником. Використовуй як джерело "
                "чисел про склад організації.\n"
                "**Обовʼязково** для: «скільки в нас X», «хто має статус X», "
                "«скільки людей у родині X», «чиї діти», «хто з менторів має "
                "найбільше дітей», «хто з набору X».\n"
                "Про набори: щоб сказати щось про людей одної хвилі — хто "
                "там найстарший, хто ще активний, скільки їх — спершу візьми "
                "**весь** набір через `joined_from`/`joined_to`. У `people` "
                "приходять дати народження й вступу, а ще **поточні посади** — "
                "єдине в базі, з чого видно, хто чим зараз зайнятий.\n"
                "Про дітей особливо: у текстах списки «Діти:» є лише для частини "
                "менторів, і часто **неповні** — тому число з пошуку буває меншим "
                "за справжнє. Для «скільки в когось дітей» бери цю тулзу, не пошук.\n"
                "НЕ бери її для: складу чинного борду, історії бордів, кількості "
                "заявок на набір — це є в документах, і там повніше "
                "(мінетси кажуть не лише скільки взяли, а й скільки подалось).\n"
                "У відповіді завжди є `count`. `people` приходить лише коли людей "
                "мало — інакше список зайняв би весь контекст. Якщо повернувся "
                "`ambiguous_mentor` — імені мало, перепитай, кого саме мають на увазі."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "status": types.Schema(
                        type=types.Type.STRING,
                        enum=["Observer", "Baby", "Full", "Alumni"],
                        description="Рівень членства з довідника.",
                    ),
                    "state": types.Schema(
                        type=types.Type.STRING,
                        enum=["Active", "Inactive"],
                        description="Активність. Це окреме від статусу поле.",
                    ),
                    "family": types.Schema(
                        type=types.Type.STRING,
                        description="Назва групи менторства, дослівно з довідника.",
                    ),
                    "mentor": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "Імʼя, прізвище чи нік ментора. Якщо є кілька збігів, "
                            "попроси уточнити, кого саме мають на увазі."
                        ),
                    ),
                    "joined_from": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "Вступили не раніше цієї дати, ISO `2024-03-01`. "
                            "Разом із `joined_to` дає набір: люди одної хвилі "
                            "мають ОДНАКОВУ дату вступу, тож «весна 2024» — це "
                            "діапазон лютий-квітень 2024, «осінь24» — "
                            "вересень-листопад."
                        ),
                    ),
                    "joined_to": types.Schema(
                        type=types.Type.STRING,
                        description="Вступили не пізніше цієї дати, ISO.",
                    ),
                    "group_by": types.Schema(
                        type=types.Type.STRING,
                        enum=["status", "state", "family", "mentor"],
                        description=(
                            "Розбити на групи з підрахунком. Так відповідають "
                            "питання «хто з менторів найбільше», «як розподілені "
                            "по родинах», «скільки кого за статусами»."
                        ),
                    ),
                },
            ),
        )
    ]
)

GET_DOCUMENT_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="get_document",
            description=(
                "Читає документ бази знань ЦІЛКОМ, а не фрагментами.\n"
                "Пошук віддає найрелевантніші фрагменти з усієї бази, тож може "
                "пропустити частину пунктів у потрібному документі.\n"
                "Коли викликати:\n"
                "• потрібно ВСЕ з одного документа — усі голосування, весь склад, "
                "уся процедура, усі пункти статуту;\n"
                "• фрагменти схожі на уривки: обривається перелік, є голоси без "
                "імен, є пункт 3 без пунктів 1 і 2;\n"
                "• користувач каже, що відповідь неповна — не переформульовуй "
                "пошук по колу, бери документ.\n"
                "• фрагмент — опис таблиці («Таблиця. Повний вміст — "
                "get_document»): у пошуку лише шапка й два рядки, решта рядків — "
                "тільки в документі.\n"
                "Не викликай, якщо на питання вже відповіли фрагменти: "
                "документ коштує більше контексту.\n"
                "Спершу зроби пошук, візьми `source_id` з його результату — "
                "вгадувати або складати його не можна."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "source_id": types.Schema(
                        type=types.Type.STRING,
                        description="`source_id` з результату пошуку, дослівно",
                    ),
                    "part": types.Schema(
                        type=types.Type.INTEGER,
                        description=(
                            "Частина великого документа, з 1. У відповіді є "
                            "`parts_total`: якщо їх більше однієї, а потрібного "
                            "ще немає — бери наступну. Частини йдуть у порядку "
                            "документа, тож заголовок розділу стоїть перед своїм "
                            "вмістом: так видно, чия це звітність."
                        ),
                    ),
                },
                required=["source_id"],
            ),
        )
    ]
)

FIND_PERSON_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="find_person",
            description=(
                "Знаходить людину в довіднику членів за іменем, прізвищем "
                "або ніком у Telegram (`@nick`, `t.me/nick`). "
                "Повертає профіль: статус, активність, родину, дату вступу, ментора, "
                "контакти — і головне, історію посад та ролей на івентах.\n"
                "Це ЄДИНЕ джерело картки людини: статус, активність, родина, ангел, "
                "дата вступу, контакти, посади. У базі знань цих полів немає, "
                "і за імʼям вона шукає ненадійно, тож картку бери лише звідси.\n"
                "Але картка — ще не людина. Що людина робила й говорила, живе в базі "
                "знань: мінетси, промови, звітності, сторінки алюмні. Тому на "
                "«хто такий Х», «розкажи про Х», прожарку чи характеристику клич "
                "цей інструмент І пошук по базі одним кроком — вони йдуть паралельно. "
                "Виняток — коротке питання, на яке картка й відповідає («який у Х "
                "телефон», «Х ще фул?», «коли в неї день народження»).\n"
                "Передавай лише імʼя, прізвище або нік, без питальних слів.\n"
                "Якщо повернеться кілька людей — не вгадуй, але й не мовчи: "
                "перелічи їх користувачеві й попроси уточнити. Показати список "
                "кандидатів — це не вигадка, а допомога. Те саме, коли точного збігу "
                "немає, але є схожі: спробуй імовірну повну форму імені "
                "і покажи, кого знайшов."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "name": types.Schema(
                        type=types.Type.STRING,
                        description="Імʼя та/або прізвище людини або її нік у Telegram",
                    )
                },
                required=["name"],
            ),
        )
    ]
)


FIND_BY_POSITION_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="find_by_position",
            description=(
                "Знаходить людей за посадою, коли імені не знають. "
                "find_person так не працює — він шукає лише за іменем.\n"
                "Посади записані англійською. Передавай канонічну назву або "
                "скорочення з довідника. Наприклад, «відповідальний за PR» → "
                "`PR Responsible`.\n"
                "**Шукай без року.** Роки в довіднику записані різними способами, "
                "тож уточнюй період уже за результатом. Довгі назви керівних "
                "посад можуть мати канонічне скорочення.\n"
                "У відповіді: `matched_current` — посада зараз, `matched_past` — "
                "колишня, і в самому рядку стоїть івент та рік; `board_role` — "
                "роль у чинному борді. Спершу йдуть ті, хто на посаді зараз. "
                "`count` більший за довжину `people` означає, що список урізано. "
                "Якщо прийшов `hint` — це вказівка, як перепитати, а не факт "
                "про осередок.\n"
                "Порожній результат означає лише, що в довіднику такого рядка "
                "немає: посади туди вносять руками й часто із запізненням. "
                "Це НЕ підстава сказати, що посада вакантна або що конкурс "
                "ще відкритий — таке можна лише з документа."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "position": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "Назва посади англійською, без року й без питальних "
                            "слів: `PR Responsible`, `Logistics Team`. "
                            "Знайдуться рядки, де стоять усі передані слова."
                        ),
                    )
                },
                required=["position"],
            ),
        )
    ]
)


# Notion віддає посилання з тексту сторінки відносними: `/p/<id>?pvs=25#<блок>`
# або `/<id>#<блок>`. Відкривається лише `app.notion.com/p/<id>`: так виглядають
# `documents.url` і посилання, що їх Notion сам пише абсолютними, а
# `app.notion.com/<id>` без `/p/` дає 404.
_NOTION_RELATIVE_LINK = re.compile(r"\]\(/(?:p/)?([0-9a-f]{32})([?#][^)\s]*)?\)")


def absolute_notion_links(text: str) -> str:
    """Робить відносні посилання Notion у markdown абсолютними."""
    return _NOTION_RELATIVE_LINK.sub(
        lambda m: f"](https://app.notion.com/p/{m[1]}{m[2] or ''})", text
    )


async def run_search(
    qdrant: QdrantRepository,
    query: str,
    terms: list[str] | None,
    limit: int,
    scopes: list[str],
) -> dict:
    """Виконує пошук і готує результат для моделі."""
    try:
        hits = await qdrant.search(query, scopes, terms=terms, limit=limit)
    except Exception:
        logger.exception("tool search failed: %r terms=%r", query, terms)
        return {"error": "пошук тимчасово недоступний"}

    logger.info(
        "tool search: %r terms=%r -> %d фрагментів", query, terms, len(hits)
    )
    results = []
    for h in hits:
        item = {
            "source_id": h.chunk.source_id,  # щоб можна було взяти документ цілком
            "title": h.chunk.title,
            "url": h.chunk.url,
            "text": absolute_notion_links(h.chunk.text),
        }
        # шлях є лише в файлів з Drive; у Notion його поки немає, і порожнє
        # поле в кожному з 25 фрагментів — це зайвий шум у контексті
        if h.chunk.path:
            item["path"] = h.chunk.path
        if h.chunk.section:
            item["section"] = h.chunk.section
        results.append(item)
    return {"results": results}


def _partial_match_note(name: str, people: list[dict]) -> str | None:
    """Попереджає, коли слова запиту збіглися в різних людей.

    `search_members` шукає за кожним словом окремо, тому на «Тарас Татарчинський»
    повертає Олексія Татарчинського і чотирьох тезок Тарасів — а людини з таким
    повним імʼям у довіднику немає. Модель приймала це за підтвердження, що Тарас
    Татарчинський існує, і добудовувала спорідненість («його брат Тарас»).
    """
    asked = [w.lower() for w in re.findall(r"\w{3,}", drop_apostrophes(name))]
    if len(asked) < 2:
        return None
    for person in people:
        full = f"{person.get('first_name', '')} {person.get('last_name', '')}"
        full = drop_apostrophes(full).lower()
        parts = full.split()
        if all(any(part.startswith(word) for part in parts) for word in asked):
            return None
    return (
        f"повного збігу за «{name}» немає — серед знайдених є тезки й однофамільці. "
        "Не виводь зі спільного прізвища спорідненості."
    )


async def run_find_person(mongo: MongoConnector, name: str) -> dict:
    """Шукає людину в інфобуці"""
    try:
        docs = await mongo.search_members(name)
    except Exception:
        logger.exception("tool find_person failed: %r", name)
        return {"error": "довідник членів тимчасово недоступний"}

    logger.info("tool find_person: %r -> %d осіб", name, len(docs))
    if not docs:
        return {"people": [], "matched": 0}

    # Технічні поля для таблиці інфобука, а не факти про людину: дати в них
    # масово переписують при зміні борду, і модель робила з них висновки
    # («завершила активність у квітні»).
    HIDDEN = ("status_last_changed", "state_last_changed")

    def serialize(d: dict) -> dict:
        return {
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in d.items()
            if k not in HIDDEN
        }

    # Повний профіль лише найкращому збігу. Решта — коротко, бо їх достатньо,
    # щоб перепитати «кого саме», а пʼять повних профілів це ~1250 токенів
    # на один виклик: заміряно 12 тисяч на одинадцять викликів підряд.
    SHORT = ("first_name", "last_name", "status", "state", "family", "member_since")
    people = [serialize(docs[0])] + [
        serialize({k: v for k, v in d.items() if k in SHORT}) for d in docs[1:]
    ]
    result = {"people": people, "matched": len(docs)}
    if note := _partial_match_note(name, people):
        result["note"] = note
    return result


CYRILLIC = re.compile(r"[а-яіїєґА-ЯІЇЄҐ]")


async def run_find_by_position(mongo: MongoConnector, position: str) -> dict:
    """Шукає людей за посадою в інфобуці"""
    try:
        result = await mongo.find_by_position(position)
    except Exception:
        logger.exception("tool find_by_position failed: %r", position)
        return {"error": "довідник членів тимчасово недоступний"}

    logger.info(
        "tool find_by_position: %r -> %d осіб", position, result.get("count", 0)
    )
    if not result.get("count"):
        # Порожньо буває з двох різних причин, і модель мусить їх розрізняти:
        # кириличний запит — це наша помилка виклику, а не факт про осередок.
        if CYRILLIC.search(position):
            result["hint"] = (
                "Посади в довіднику записані англійською. Повтори запит "
                "англійською назвою: «мо мв» → MO MW, «пі ар» → PR Responsible, "
                "«президент» → President."
            )
        else:
            result["hint"] = (
                "Такого рядка в довіднику немає. Спробуй коротше (без року й без "
                "назви івенту) або пошукай документи про вибори. Порожній "
                "результат НЕ означає, що посада вакантна."
            )
    return result


async def run_get_document(
    source_id: str, part: int, part_chars: int, scopes: list[str]
) -> dict:
    """Віддає повний текст документа — той самий, що пішов у чанкер."""
    try:
        async with async_session_maker() as db:
            doc = await documents.get_document(db, source_id, scopes)
    except Exception:
        logger.exception("tool get_document failed: %r", source_id)
        return {"error": "сховище документів тимчасово недоступне"}

    if doc is None:
        logger.warning("tool get_document: немає документа %r", source_id)
        return {
            "error": (
                f"документа з source_id={source_id} немає. "
                "Візьми source_id дослівно з результату пошуку."
            )
        }

    # заміна до нарізки на частини: інакше межа частини може розрізати посилання
    text = absolute_notion_links(doc.text)
    total = max(1, -(-len(text) // part_chars))
    part = max(1, min(part, total))
    start = (part - 1) * part_chars

    logger.info(
        "tool get_document: %r частина %d/%d (%d символів)",
        doc.title, part, total, len(text),
    )
    result = {
        "title": doc.title,
        "url": doc.url,
        "last_edited": doc.last_edited,
        "part": part,
        "parts_total": total,
        "text": text[start : start + part_chars],
    }
    if doc.path:
        result["path"] = doc.path
    return result


async def run_query_members(mongo: MongoConnector, **kwargs) -> dict:
    """Підрахунки по довіднику. Порожні аргументи не передаємо далі."""
    args = {k: v for k, v in kwargs.items() if v not in (None, "")}
    try:
        result = await mongo.query_members(**args)
    except Exception:
        logger.exception("tool query_members failed: %r", args)
        return {"error": "довідник членів тимчасово недоступний"}

    logger.info(
        "tool query_members: %r -> count=%s groups=%s",
        args, result.get("count"), len(result.get("groups") or []),
    )
    return {
        k: (
            [
                {
                    kk: (vv.isoformat() if isinstance(vv, datetime) else vv)
                    for kk, vv in person.items()
                }
                for person in v
            ]
            if k == "people"
            else v
        )
        for k, v in result.items()
    }


@dataclass(frozen=True)
class Limits:
    """Скільки інструменти віддають за раз — залежить від того, хто питає.

    Бот викидає результати інструментів після кожної відповіді, а MCP-клієнт
    тримає їх у контексті всю розмову, тож йому потрібні менші порції.
    """

    search: int = 25  # фрагментів на основний запит і на кожен термін окремо
    document_part: int = 60_000  # символів у частині документа, ~20 тис. токенів


@dataclass(frozen=True)
class ToolSpec:
    """Усе про один інструмент в одному місці.

    Раніше це знання було розкидане: декларація тут, маршрутизація й назва для
    людини в chain.py, емодзі в роутері. Додати інструмент означало чотири
    правки в трьох файлах — і одну з них ми справді забули, отримавши дубль
    декларації, на якому впав API.
    """

    declaration: types.Tool
    # (залежності, аргументи, ліміти, scopes) -> результат. Залежності — будь-що
    # з .qdrant і .mongo: у бота це RagChain, у MCP — app.state. scopes — що
    # користувачу можна бачити в базі знань; довідника членів не стосується.
    run: Callable[[Any, dict, Limits, list[str]], Awaitable[dict]]
    label: str  # як дія зветься для людини: «Пошук в базі»
    icon: str  # для згорнутого блока в повідомленні
    arg: str | None = None  # який аргумент показати поруч із назвою
    # Коли одного аргумента мало (у query_members їх вісім), опис збирається так.
    describe: Callable[[dict], str] | None = None


def _members_label(args: dict) -> str:
    """Людською: «фули», «активні, родина Кролики», «діти Барана», «по родинах»."""
    statuses = {"Full": "фули", "Baby": "бейбі", "Observer": "обзервери",
                "Alumni": "алюмні"}
    groups = {"status": "по статусах", "state": "по активності",
              "family": "по родинах", "mentor": "по менторах"}
    parts = []
    if args.get("status"):
        parts.append(statuses.get(args["status"], args["status"]))
    if args.get("state"):
        parts.append("активні" if args["state"] == "Active" else "неактивні")
    if args.get("family"):
        parts.append(f"родина {args['family']}")
    if args.get("mentor"):
        parts.append(f"діти {args['mentor']}")
    if args.get("joined_from") or args.get("joined_to"):
        parts.append("набір")
    if args.get("group_by"):
        parts.append(groups.get(args["group_by"], args["group_by"]))
    return ", ".join(parts)


def _by_name(*specs: ToolSpec) -> dict[str, ToolSpec]:
    """Ключ беремо з декларації, щоб реєстр не розійшовся з тим, що бачить модель."""
    registry: dict[str, ToolSpec] = {}
    for spec in specs:
        for decl in spec.declaration.function_declarations or []:
            if decl.name:
                registry[decl.name] = spec
    return registry


TOOLS = _by_name(
    ToolSpec(
        declaration=SEARCH_TOOL,
        run=lambda deps, args, limits, scopes: run_search(
            deps.qdrant,
            args.get("query", ""),
            args.get("terms"),
            limits.search,
            scopes,
        ),
        label="Пошук в базі",
        icon="🔎",
        arg="query",
    ),
    ToolSpec(
        declaration=FIND_PERSON_TOOL,
        run=lambda deps, args, _limits, _scopes: run_find_person(
            deps.mongo, args.get("name", "")
        ),
        label="Перегляд профілю",
        icon="👤",
        arg="name",
    ),
    ToolSpec(
        declaration=FIND_BY_POSITION_TOOL,
        run=lambda deps, args, _limits, _scopes: run_find_by_position(
            deps.mongo, args.get("position", "")
        ),
        label="Пошук за посадою",
        icon="🏷",
        arg="position",
    ),
    ToolSpec(
        declaration=GET_DOCUMENT_TOOL,
        # документ читається з Postgres, залежності тут не потрібні
        run=lambda _deps, args, limits, scopes: run_get_document(
            args.get("source_id", ""),
            int(args.get("part") or 1),
            limits.document_part,
            scopes,
        ),
        label="Читання документа",
        icon="📄",
    ),
    ToolSpec(
        declaration=QUERY_MEMBERS_TOOL,
        run=lambda deps, args, _limits, _scopes: run_query_members(
            deps.mongo, **args
        ),
        label="Перегляд інфобука",
        icon="📊",
        describe=_members_label,
    ),
)

# Те, що йде в модель: порядок збережено, бо dict пам'ятає порядок вставки.
DECLARATIONS = [spec.declaration for spec in TOOLS.values()]
