import asyncio
from types import SimpleNamespace

from src.vectorstore.qdrant import QdrantRepository


class FakeClient:
    def __init__(self, names: list[str], aliases: dict[str, str]):
        self.names = names
        self.aliases = aliases
        self.deleted: list[str] = []

    async def get_aliases(self):
        return SimpleNamespace(
            aliases=[
                SimpleNamespace(alias_name=a, collection_name=c)
                for a, c in self.aliases.items()
            ]
        )

    async def get_collections(self):
        return SimpleNamespace(
            collections=[SimpleNamespace(name=n) for n in self.names]
        )

    async def delete_collection(self, collection_name):
        self.deleted.append(collection_name)


def test_cleanup_keeps_other_alias_with_same_prefix():
    """Прибирання `general` не чіпає `general_ctx_<час>` і `memes`."""
    client = FakeClient(
        [
            "general_20261110_155116_533",
            "general_20261101_120000",
            "general_20261109_101010_001",
            "general_ctx_20261111_090000_123",
            "memes",
        ],
        {
            "general": "general_20261110_155116_533",
            "general_ctx": "general_ctx_20261111_090000_123",
        },
    )
    repo = QdrantRepository.__new__(QdrantRepository)
    repo.alias, repo.qdrant_client = "general", client  # pyright: ignore[reportAttributeAccessIssue]
    assert asyncio.run(repo._cleanup_old_collections()) == 2
    assert client.deleted == ["general_20261101_120000", "general_20261109_101010_001"]

    client.deleted = []
    repo.alias = "general_ctx"
    assert asyncio.run(repo._cleanup_old_collections()) == 0
