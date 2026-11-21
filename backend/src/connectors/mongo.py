import re
from datetime import datetime

from pymongo import AsyncMongoClient

from src.text import LOOKALIKE, any_apostrophe_regex, fold_apostrophes

STATES = ("Active", "Inactive")
STATUSES = ("Observer", "Baby", "Full", "Alumni")
POSITION_WORD = re.compile(r"\w+")


def _prefix_pattern(token: str) -> dict:
    """Регулярка «поле починається з token», апостроф у token — будь-який."""
    return {"$regex": "^" + any_apostrophe_regex(token), "$options": "i"}


# `telegram_username` у довіднику — без «@», регістр довільний; на вхід може
# прийти і `@nick`, і посилання `t.me/nick`.
TELEGRAM_NICK = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t|telegram)\.me/+@?([A-Za-z0-9_]{5,32})/?"
    r"|@?([A-Za-z0-9_]{5,32})",
    re.IGNORECASE,
)

# Віце-президентів записують і скорочено (`VP4HR` у `board`, «VP4PR of XIX
# Board»), і повністю («Vice president for Human Resources of …»). Обидві форми
# зводяться до скорочення, і в запиті, і в рядку посади.
VP_AREAS = {
    "HR": "human resources",
    "IT": "informational technology|it",
    "PR": "public relations",
    "CR": "corporate relations",
    "DS": "design",
    "IR": "international relations",
}
VP_PATTERNS = {
    code: rf"\b(?:vice[ -]?president|vp)\s*(?:4|for)\s*(?:{full}|{code})\b"
    for code, full in VP_AREAS.items()
}


# Римські номери бордів часом набрані з кириличною «І» чи «Х» (`XXIІ`, `XXIІІ`),
# і латинське `XXII` в запиті їх не знаходило. Схожі літери зводяться до латиниці,
# лише коли слово після цього — римське число й латиниця в ньому вже є.
ROMAN = re.compile(r"[ivxlcdm]+")


def roman_latin(word: str) -> str:
    """`xxiі` → `xxii`; решта слів без змін. Слово — уже в нижньому регістрі."""
    fixed = word.translate(LOOKALIKE)
    if fixed != word and ROMAN.fullmatch(fixed) and re.search("[a-z]", word):
        return fixed
    return word


def telegram_nick(text: str) -> str | None:
    """Нік Telegram із запиту або None, якщо запит на нік не схожий."""
    m = TELEGRAM_NICK.fullmatch(text.strip())
    return (m.group(1) or m.group(2)) if m else None


def vp_short(text: str) -> str:
    """«Vice president for IT», «VP for IT» → «VP4IT»."""
    for code, pattern in VP_PATTERNS.items():
        text = re.sub(pattern, f"VP4{code}", text, flags=re.IGNORECASE)
    return text


class MongoConnector:
    def __init__(self, uri: str, db_name: str, collection_name: str):
        self._client = AsyncMongoClient(uri)
        self._members = self._client[db_name][collection_name]

    async def ping(self) -> int:
        return await self._members.count_documents({})

    async def find_member_by_phone(self, phone: str) -> dict | None:
        return await self._members.find_one({"phone_number": phone})

    async def search_members(self, name: str, limit: int = 5) -> list[dict]:
        """Шукає членів за фрагментом імені або за ніком Telegram.
        Повертає до limit найкращих збігів"""

        if nick := telegram_nick(name):
            # імена в довіднику майже всі кирилицею, тож латинське слово — це нік
            exact = {"$regex": f"^{re.escape(nick)}$", "$options": "i"}
            found = await self._members.find(
                {"telegram_username": exact}, {"_id": 0}
            ).to_list(limit)
            if found:
                return found

        def _match_score(doc: dict, tokens: list[str]) -> int:
            """Наскільки документ відповідає токенам запиту."""
            last = fold_apostrophes(doc.get("last_name") or "").lower()
            first = fold_apostrophes(doc.get("first_name") or "").lower()

            score = 0
            for token in tokens:
                if last == token:
                    score += 2
                elif last.startswith(token):
                    score += 1
                if first == token or first.startswith(token):
                    score += 1
            return score

        name = fold_apostrophes(name).lower()
        tokens = [t for t in re.split(r"[\s,]+", name) if len(t) >= 3]
        if not tokens:
            return []

        conditions = []

        for token in tokens:
            pattern = _prefix_pattern(token)
            conditions += [{"last_name": pattern}, {"first_name": pattern}]

        docs = [d async for d in self._members.find({"$or": conditions}, {"_id": 0})]
        docs.sort(key=lambda d: _match_score(d, tokens), reverse=True)
        return docs[:limit]

    async def find_by_position(self, position: str, limit: int = 40) -> dict:
        """Шукає людей за посадою й вертає ті рядки посад, що збіглися.

        Посади пишуть руками, тому в базі чотири види апострофа, а рік
        зліплений з назвою («MO MW Autumn’26»). Тому порівнюємо не фразу,
        а слова: всі слова запиту мають стояти в одному рядку посади.

        `board` шукаємо теж — там `VP4HR`, а в посадах те саме
        записано як «Vice president for Human Resources»: обидві форми
        зводить до скорочення `vp_short`.
        """

        def words(text: str) -> list[str]:
            folded = fold_apostrophes(text)
            return [roman_latin(w.lower()) for w in POSITION_WORD.findall(folded)]

        def matched(positions: list[str] | None, wanted: list[str]) -> list[str]:
            hits = []
            for title in positions or []:
                have = words(title) + words(vp_short(title))
                # Короткі слова («MO», «PR») — лише повним збігом, інакше «mo»
                # витягне «Motivation». Довгі — префіксом, щоб «respons»
                # знаходило «Responsible».
                if all(
                    w in have if len(w) <= 3 else any(h.startswith(w) for h in have)
                    for w in wanted
                ):
                    hits.append(title)
            return hits

        wanted = words(vp_short(position))
        if not wanted:
            return {"count": 0, "people": []}

        # Монго звузить вибірку за найдовшим словом (усі збіги його містять),
        # а точний відбір по всіх словах зробимо вже на кількох документах.
        # Скорочення `vp4it` в рядку може стояти повною назвою — тоді шаблон.
        top = max(wanted, key=len)
        pattern = VP_PATTERNS.get(top[3:].upper()) if top.startswith("vp4") else None
        if not pattern and ROMAN.fullmatch(top):
            # у базі той самий номер може стояти з кириличною «І» чи «Х»
            pattern = "".join({"i": "[iі]", "x": "[xх]"}.get(c, c) for c in top)
        longest = {"$regex": pattern or re.escape(top), "$options": "i"}

        people = []
        async for doc in self._members.find(
            {
                "$or": [
                    {"current_positions": longest},
                    {"past_positions": longest},
                    {"board": longest},
                ]
            },
            {
                "_id": 0,
                "first_name": 1,
                "last_name": 1,
                "status": 1,
                "state": 1,
                "family": 1,
                "board": 1,
                "current_positions": 1,
                "past_positions": 1,
            },
        ):
            current = matched(doc.get("current_positions"), wanted)
            past = matched(doc.get("past_positions"), wanted)
            board = matched([doc["board"]] if doc.get("board") else None, wanted)
            if not current and not past and not board:
                continue

            person = {
                key: doc.get(key)
                for key in ("first_name", "last_name", "status", "state", "family")
            }
            if board:
                person["board_role"] = doc["board"]
            if current:
                person["matched_current"] = current
            if past:
                person["matched_past"] = past
            people.append(person)

        def rank(person: dict) -> tuple:
            """Порядок важливіший, ніж здається: модель читає перших.

            Слово «president» є і в «Vice president for IT», тому спершу йде
            канонічна роль з `board`, далі поточні посади, і всередині — ті,
            чия назва з запитаного слова починається.
            """
            role = person.get("board_role")
            titles = person.get("matched_current") or person.get("matched_past") or []
            return (
                not (role and words(role) == wanted),
                not (person.get("matched_current") or role),
                not any(words(vp_short(t))[:1] == wanted[:1] for t in titles),
            )

        people.sort(key=rank)
        return {"count": len(people), "people": people[:limit]}

    async def query_members(
        self,
        status: str | None = None,
        state: str | None = None,
        family: str | None = None,
        mentor: str | None = None,
        board: bool | None = None,
        joined_from: str | None = None,
        joined_to: str | None = None,
        group_by: str | None = None,
        limit: int = 40,
    ) -> dict:
        """Рахує й перелічує членів за фільтрами.

        Завжди вертає count, а імена — лише коли їх мало: 400+ бейбіків
        у відповідь моделі не потрібні, вони лише зʼїдять контекст.
        """
        query: dict = {}
        if status:
            query["status"] = status
        if state:
            query["state"] = state
        if family:
            query["family"] = family
        mentor_resolved: str | None = None
        if mentor:
            # Поле завжди канонічне «Імʼя Прізвище», а на вхід може прийти
            # прізвище, зворотний порядок або прізвисько з мінетсів.
            folded = fold_apostrophes(mentor).lower()
            tokens = {t for t in re.split(r"[\s,]+", folded) if len(t) >= 3}
            rows = [
                (
                    f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
                    fold_apostrophes(c.get("last_name") or "").lower(),
                    fold_apostrophes(c.get("first_name") or "").lower(),
                )
                for c in await self.search_members(mentor, limit=5)
            ]
            # Точне прізвище важливіше за префікс: інакше «Лев» тягне «Левчишин».
            exact = [r for r in rows if r[1] in tokens]
            rows = exact or [r for r in rows if any(r[1].startswith(t) for t in tokens)]
            if len(rows) > 1:
                # «Микола Лев» проти «Софія Лев» — доуточнюємо іменем
                by_first = [r for r in rows if any(r[2].startswith(t) for t in tokens)]
                if by_first:
                    rows = by_first

            # distinct віддасть лише ті імена, що справді стоять у mentor_name
            actual = sorted(
                await self._members.distinct(
                    "mentor_name", {"mentor_name": {"$in": [r[0] for r in rows]}}
                )
            )
            if not actual:
                return {"count": 0, "mentor_not_found": mentor}
            if len(actual) > 1:
                # одне прізвище буває в кількох менторів, вгадувати не можна
                return {"ambiguous_mentor": actual}
            mentor_resolved = actual[0]
            query["mentor_name"] = mentor_resolved
        if board is True:
            query["board"] = {"$nin": ["", None]}
        elif board is False:
            query["board"] = {"$in": ["", None]}
        if joined_from or joined_to:
            span: dict = {}
            if joined_from:
                span["$gte"] = datetime.fromisoformat(joined_from)
            if joined_to:
                span["$lte"] = datetime.fromisoformat(joined_to)
            query["member_since"] = span

        count = await self._members.count_documents(query)
        result: dict = {"count": count}
        if mentor_resolved:
            result["mentor_resolved"] = mentor_resolved

        if group_by:
            # «хвиля набору» — це точна дата вступу: 739 людей на 87 дат
            field = {"wave": "member_since", "mentor": "mentor_name"}.get(
                group_by, group_by
            )
            groups = [
                g
                async for g in await self._members.aggregate(
                    [
                        {"$match": query},
                        {"$group": {"_id": f"${field}", "n": {"$sum": 1}}},
                        {"$sort": {"n": -1}},
                        {"$limit": 25},
                    ]
                )
            ]

            result["groups"] = [
                {
                    "value": g["_id"].date().isoformat()
                    if isinstance(g["_id"], datetime)
                    else g["_id"],
                    "count": g["n"],
                }
                for g in groups
                if g["_id"] not in (None, "")
            ]

        if count <= limit:
            result["people"] = [
                d
                async for d in self._members.find(
                    query,
                    {
                        "_id": 0,
                        "first_name": 1,
                        "last_name": 1,
                        "status": 1,
                        "state": 1,
                        "board": 1,
                        "family": 1,
                        "member_since": 1,
                        "birth_date": 1,
                        "mentor_name": 1,
                        "current_positions": 1,
                    },
                )
            ]
        return result

    async def emails(self) -> set[str]:
        """Усі пошти довідника, нормалізовані так само, як у доступах."""
        docs = await self._members.find({}, {"email": 1}).to_list(None)
        return {
            d["email"].strip().lower()
            for d in docs
            if isinstance(d.get("email"), str) and d["email"].strip()
        }

    async def close(self) -> None:
        await self._client.close()
