"""Фрагмент закритого диска не дістається тим, кому диск не дозволено.

Потрібен живий Qdrant (`podman compose up -d qdrant`); без нього тест
пропускається. Ембединги — заглушка, тож API не викликається.
"""

import asyncio
import hashlib
import uuid

import httpx
import pytest
from llama_index.core.schema import TextNode
from qdrant_client.http.models import DeleteAlias, DeleteAliasOperation

from src.config import settings
from src.ingestion.schemas import GENERAL
from src.repository import documents
from src.vectorstore.qdrant import QdrantRepository

DRIVE = "BTW_DRIVE"
SECRET = "кодовеслово"


class FakeEmbedding:
    """Один і той самий вектор для однакового тексту, різні для різного."""

    @staticmethod
    def _vec(text: str) -> list[float]:
        seed = hashlib.sha256(text.encode()).digest()
        return [((seed[i % 32] + i) % 17) / 17 + 0.01 for i in range(3072)]

    async def aget_query_embedding(self, text: str) -> list[float]:
        return self._vec(text)

    async def aget_text_embedding_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


def node(text: str, scope: str) -> TextNode:
    return TextNode(
        id_=str(uuid.uuid4()),
        text=text,
        metadata={
            "source": "gdrive",
            "source_id": f"{scope}-{text[:10]}",
            "title": text[:20],
            "url": "",
            "last_edited": "2026-01-01",
            "path": "",
            "scope": scope,
        },
    )


def qdrant_up() -> bool:
    try:
        return httpx.get(settings.QDRANT_URL, timeout=1).is_success
    except httpx.HTTPError:
        return False


@pytest.fixture
def repo():
    if not qdrant_up():
        pytest.skip("немає Qdrant")
    alias = f"isolation_{uuid.uuid4().hex[:8]}"
    repo = QdrantRepository(settings.QDRANT_URL, alias, FakeEmbedding())  # pyright: ignore[reportArgumentType]

    async def setup():
        await repo.ensure_collection()
        await repo.reindex(
            fresh_nodes=[
                node(f"відкритий текст про BTW {SECRET}", GENERAL),
                node(f"закритий протокол BTW {SECRET} {SECRET}", DRIVE),
                node("ще один закритий файл без слова", DRIVE),
            ],
            stale_ids=set(),
        )

    asyncio.run(setup())
    yield repo

    async def teardown():
        current = await repo._current_collection()
        await repo.qdrant_client.update_collection_aliases(
            change_aliases_operations=[
                DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=repo.alias))
            ]
        )
        if current:
            await repo.qdrant_client.delete_collection(current)
        await repo.qdrant_client.close()

    asyncio.run(teardown())


def scopes_of(hits) -> set[str]:
    return {h.chunk.scope for h in hits}


def test_general_user_never_sees_closed_drive(repo):
    hits = asyncio.run(
        repo.search(f"закритий протокол BTW {SECRET} {SECRET}", [GENERAL])
    )
    assert hits and scopes_of(hits) == {GENERAL}


def test_rare_term_path_is_filtered_too(repo):
    hits = asyncio.run(repo.search("щось інше", [GENERAL], terms=[SECRET]))
    assert scopes_of(hits) == {GENERAL}


def test_rare_term_counted_only_in_allowed(repo):
    """У дозволеному слово рідкісне (1), у закритому — ні: рахунок не протікає."""
    allowed = repo._scope_filter([GENERAL])
    assert asyncio.run(repo._rare_terms([SECRET], 1, allowed)) == [SECRET]


def test_drive_member_sees_both(repo):
    hits = asyncio.run(repo.search("BTW", [GENERAL, DRIVE]))
    assert scopes_of(hits) == {GENERAL, DRIVE}


def test_empty_scopes_refused(repo):
    with pytest.raises(ValueError):
        asyncio.run(repo.search("BTW", []))


class FakeDb:
    def __init__(self, doc):
        self.doc = doc

    async def get(self, _model, _key):
        return self.doc


class Doc:
    scope = DRIVE


def test_get_document_hides_closed_document():
    assert asyncio.run(documents.get_document(FakeDb(Doc()), "x", [GENERAL])) is None  # type: ignore[arg-type]
    assert asyncio.run(documents.get_document(FakeDb(Doc()), "x", [GENERAL, DRIVE]))  # type: ignore[arg-type]
    assert asyncio.run(documents.get_document(FakeDb(None), "x", [DRIVE])) is None  # type: ignore[arg-type]


def test_full_index_preserves_paused_point_ids_in_new_collection(repo, monkeypatch):
    from src.ingestion import pipeline
    from src.models.document import Document

    old = asyncio.run(repo.index_state("gdrive"))
    paused_ids = {sid for sid, (_, scope, _) in old.items() if scope == DRIVE}
    assert paused_ids
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_PAUSED_IDS", DRIVE)

    async def meta(_db, _source):
        return {
            **{
                sid: documents.Meta("new", DRIVE, "", 50)
                for sid in paused_ids
            },
            "active-new": documents.Meta("new", GENERAL, "", 50),
        }

    async def load(_db, ids):
        assert ids == {"active-new"}
        return [
            Document(
                source_id="active-new",
                source="gdrive",
                title="Synthetic active file",
                url="",
                text="У документі достатньо вмісту для створення фрагмента.",
                scope=GENERAL,
                last_edited="new",
                path="",
            )
        ]

    async def paused_points(collection):
        points, offset = await repo.qdrant_client.scroll(
            collection, with_payload=True, with_vectors=True
        )
        assert offset is None
        return {
            point.id: (point.payload, point.vector)
            for point in points
            if point.payload["scope"] == DRIVE
        }

    async def verify():
        before = await repo._current_collection()
        old_points = await paused_points(before)
        assert old_points

        assert await pipeline.index(repo, None, "gdrive", full=True)
        after = await repo._current_collection()
        assert before != after
        new_points = await paused_points(after)
        assert old_points.keys() == new_points.keys()
        for key, (payload, vector) in old_points.items():
            assert new_points[key][0] == payload
            assert new_points[key][1] == pytest.approx(vector)

        # Searching the post-swap collection with active scopes still hides
        # the retained paused vectors.
        hits = await repo.search(SECRET, [GENERAL])
        assert hits and scopes_of(hits) == {GENERAL}

    monkeypatch.setattr(documents, "meta", meta)
    monkeypatch.setattr(documents, "load", load)
    asyncio.run(verify())
