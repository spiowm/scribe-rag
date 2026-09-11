import logging
from datetime import datetime

from google.genai import types

from src.connectors.mongo import MongoConnector
from src.db import async_session_maker
from src.repository import documents
from src.vectorstore.qdrant import QdrantRepository

logger = logging.getLogger(__name__)


SEARCH_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="search_knowledge_base",
            description=(
                "Шукає у базі знань BEST Lviv. Два джерела в одному індексі:\n"
                "• **Notion** — сторінки про процеси, івенти, ролі, історію та традиції "
                "осередку, хендбуки по відділах, «як робити X» від попередників.\n"
                "• **Google Drive** — офіційні документи й архіви: статут, внутрішні "
                "правила, Code of Conduct, КСПЗ, LTSP, річні плани (AAP) і звіти "
                "(AAR, SAAR), мінетси зборів і виборів, мотиваційні листи та екшн-плани "
                "конкретних людей, міжнародні пропоузали, матеріали тренінгів (BKT, SIT).\n"
                "Тобто в базі є і «як у нас прийнято», і «що записано в документі», "
                "і «що саме вирішили на тих зборах».\n"
                "Для питань про конкретну людину використовуй find_person, "
                "а не цей інструмент.\n"
                "Викликай не лише тоді, коли потрібні факти, а й коли готуєш ідеї, "
                "план, аналіз чи пораду: щоб спиратись на те, як усе влаштовано саме "
                "в BEST, а не на загальні уявлення. Для ідей шукай схожі івенти й "
                "наявні напрацювання, для аналізу — як цей процес описаний у нас."
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
                            "Наприклад: SoDeep, ІЯК17, осінь14, Мінчак, бірко, "
                            "BEST::HACKath0n.\n"
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

GET_DOCUMENT_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="get_document",
            description=(
                "Читає документ бази знань ЦІЛКОМ, а не фрагментами.\n"
                "Навіщо: пошук віддає 25 фрагментів з усієї бази, а один "
                "документ може мати 127 чанків. Тому на питання «дай усі X з "
                "цього документа» пошук фізично не може відповісти повно: "
                "у протоколі SAAR 17 голосувань, і один запит приносить 3 з них. "
                "Ти цього не бачиш — відповідь виглядає повною.\n"
                "Коли викликати:\n"
                "• потрібно ВСЕ з одного документа — усі голосування, весь склад, "
                "уся процедура, усі пункти статуту;\n"
                "• фрагменти схожі на уривки: обривається перелік, є голоси без "
                "імен, є пункт 3 без пунктів 1 і 2;\n"
                "• користувач каже, що відповідь неповна.\n"
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
                            "ще немає — бери наступну."
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
                "Знаходить людину в довіднику членів BEST Lviv за іменем або прізвищем. "
                "Повертає профіль: статус, активність, родину, дату вступу, ментора, "
                "контакти — і головне, історію посад та ролей на івентах.\n"
                "Це ЄДИНИЙ правильний спосіб відповідати на питання про конкретну людину: "
                "«хто такий Х», «які посади обіймав Х», «де Х був залучений». "
                "Пошук по базі знань імена знаходить погано, не використовуй його для цього.\n"
                "Передавай лише імʼя та/або прізвище, без питальних слів: «Мартинюк Дмитро», "
                "а не «хто такий Дмитро Мартинюк».\n"
                "Якщо повернеться кілька людей — не вгадуй, але й не мовчи: "
                "перелічи їх користувачеві й попроси уточнити. Показати список "
                "кандидатів — це не вигадка, а допомога. Те саме, коли точного збігу "
                "немає, але є схожі: спробуй імовірну повну форму імені "
                "(Лекс → Олексій, Діма → Дмитро) і покажи, кого знайшов."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "name": types.Schema(
                        type=types.Type.STRING,
                        description="Імʼя та/або прізвище людини",
                    )
                },
                required=["name"],
            ),
        )
    ]
)


async def run_search(
    qdrant: QdrantRepository, query: str, terms: list[str] | None = None
) -> dict:
    """Виконує пошук і готує результат для моделі."""
    try:
        hits = await qdrant.search(query, terms=terms)
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
            "text": h.chunk.text[:1500],
        }
        # шлях є лише в файлів з Drive; у Notion його поки немає, і порожнє
        # поле в кожному з 25 фрагментів — це зайвий шум у контексті
        if h.chunk.path:
            item["path"] = h.chunk.path
        results.append(item)
    return {"results": results}


async def run_find_person(mongo: MongoConnector, name: str) -> dict:
    """Шукає людину в інфобуці"""
    try:
        docs = await mongo.search_members(name)
    except Exception:
        logger.exception("tool find_person failed: %r", name)
        return {"error": "довідник членів тимчасово недоступний"}

    logger.info("tool find_person: %r -> %d осіб", name, len(docs))
    return {
        "people": [
            {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in d.items()}
            for d in docs
        ]
    }


PART_CHARS = 60_000  # ~20 тис. токенів на частину


async def run_get_document(source_id: str, part: int = 1) -> dict:
    """Віддає повний текст документа — той самий, що пішов у чанкер."""
    try:
        async with async_session_maker() as db:
            doc = await documents.get_document(db, source_id)
    except Exception:
        logger.exception("tool get_document failed: %r", source_id)
        return {"error": "сховище документів тимчасово недоступне"}

    if doc is None:
        return {
            "error": (
                f"документа з source_id={source_id} немає. "
                "Візьми source_id дослівно з результату пошуку."
            )
        }

    total = max(1, -(-len(doc.text) // PART_CHARS))
    part = max(1, min(part, total))
    start = (part - 1) * PART_CHARS

    logger.info(
        "tool get_document: %r частина %d/%d (%d символів)",
        doc.title, part, total, len(doc.text),
    )
    result = {
        "title": doc.title,
        "url": doc.url,
        "last_edited": doc.last_edited,
        "part": part,
        "parts_total": total,
        "text": doc.text[start : start + PART_CHARS],
    }
    if doc.path:
        result["path"] = doc.path
    return result
