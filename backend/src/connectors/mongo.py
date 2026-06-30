from pymongo import AsyncMongoClient


class MongoConnector:
    def __init__(self, uri: str, db_name: str, collection_name: str):
        self._client = AsyncMongoClient(uri)
        self._members = self._client[db_name][collection_name]

    async def ping(self) -> int:
        return await self._members.count_documents({})

    async def find_member_by_phone(self, phone: str) -> dict | None:
        return await self._members.find_one({"phone_number": phone})

    async def close(self) -> None:
        await self._client.close()
