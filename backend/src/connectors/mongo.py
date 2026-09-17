import re
from datetime import datetime

from pymongo import AsyncMongoClient

STATES = ("Active", "Inactive")
STATUSES = ("Observer", "Baby", "Full", "Alumni")
POSITION_WORD = re.compile(r"\w+")


class MongoConnector:
    def __init__(self, uri: str, db_name: str, collection_name: str):
        self._client = AsyncMongoClient(uri)
        self._members = self._client[db_name][collection_name]

    async def ping(self) -> int:
        return await self._members.count_documents({})

    async def find_member_by_phone(self, phone: str) -> dict | None:
        return await self._members.find_one({"phone_number": phone})

    async def search_members(self, name: str, limit: int = 5) -> list[dict]:
        """Шукає членів за фрагментом імені. Повертає до limit найкращих збігів"""

        def _match_score(doc: dict, tokens: list[str]) -> int:
            """Наскільки документ відповідає токенам запиту."""
            last = (doc.get("last_name") or "").lower()
            first = (doc.get("first_name") or "").lower()

            score = 0
            for token in tokens:
                if last == token:
                    score += 2
                elif last.startswith(token):
                    score += 1
                if first == token or first.startswith(token):
                    score += 1
            return score

        tokens = [t for t in re.split(r"[\s,]+", name.lower()) if len(t) >= 3]
        if not tokens:
            return []

        conditions = []

        for token in tokens:
            pattern = {"$regex": f"^{re.escape(token)}", "$options": "i"}
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
        записано як «Vice president for Human Resources».
        """

        def words(text: str) -> list[str]:
            return [w.lower() for w in POSITION_WORD.findall(text)]

        def matched(positions: list[str] | None, wanted: list[str]) -> list[str]:
            hits = []
            for title in positions or []:
                have = words(title)
                # Короткі слова («MO», «PR») — лише повним збігом, інакше «mo»
                # витягне «Motivation». Довгі — префіксом, щоб «respons»
                # знаходило «Responsible».
                if all(
                    w in have if len(w) <= 3 else any(h.startswith(w) for h in have)
                    for w in wanted
                ):
                    hits.append(title)
            return hits

        wanted = words(position)
        if not wanted:
            return {"count": 0, "people": []}

        # Монго звузить вибірку за найдовшим словом (усі збіги його містять),
        # а точний відбір по всіх словах зробимо вже на кількох документах.
        longest = {"$regex": re.escape(max(wanted, key=len)), "$options": "i"}

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
                not any(words(t)[:1] == wanted[:1] for t in titles),
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
            tokens = {t for t in re.split(r"[\s,]+", mentor.lower()) if len(t) >= 3}
            rows = [
                (
                    f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
                    (c.get("last_name") or "").lower(),
                    (c.get("first_name") or "").lower(),
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
                # «Панчук» це і Павло, і Тетяна — вгадувати не можна
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
                    },
                )
            ]
        return result

    async def close(self) -> None:
        await self._client.close()
