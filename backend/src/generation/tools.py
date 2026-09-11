import logging
from datetime import datetime

from google.genai import types

from src.connectors.mongo import MongoConnector
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
    return {
        "results": [
            {
                "title": h.chunk.title,
                "url": h.chunk.url,
                "text": h.chunk.text[:1500],
            }
            for h in hits
        ]
    }


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
