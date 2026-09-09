import re

from pymongo import AsyncMongoClient


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

    async def close(self) -> None:
        await self._client.close()
