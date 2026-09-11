import logging
from datetime import UTC, datetime

from llama_index.core.schema import MetadataMode, TextNode
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    CreateAlias,
    CreateAliasOperation,
    DeleteAlias,
    DeleteAliasOperation,
    Distance,
    PointStruct,
    VectorParams,
    FieldCondition,
    Filter,
    MatchValue,
)

from src.ingestion.schemas import ChunkPayload, SearchHit

logger = logging.getLogger(__name__)


class QdrantRepository:
    def __init__(
        self,
        url: str,
        collection_name: str,
        embedding_model: GoogleGenAIEmbedding,
    ) -> None:
        self.alias = collection_name
        self.qdrant_client = AsyncQdrantClient(url=url)
        self.embedding_model = embedding_model

    async def search(self, query: str, limit: int = 15) -> list[SearchHit]:
        query_vector = await self.embedding_model.aget_text_embedding(query)
        results = await self.qdrant_client.query_points(
            collection_name=self.alias,
            query=query_vector,
            limit=limit,
        )
        return [
            SearchHit(
                score=point.score, chunk=ChunkPayload.model_validate(point.payload)
            )
            for point in results.points
            if point.payload is not None
        ]

    async def ensure_collection(self) -> None:
        if not await self.qdrant_client.collection_exists(self.alias):
            collection_name = await self._create_collection()
            await self._switch_alias(collection_name)

    async def index_state(self, source: str) -> dict[str, str]:
        """{source_id: last_edited} — що зараз у живій колекції для вказаного джерела."""
        current = await self._current_collection()
        if not current:
            return {}

        state: dict[str, str] = {}
        offset = None
        batch_size = 500

        scroll_filter = Filter(
            must=[
                FieldCondition(
                    key="source",
                    match=MatchValue(value=source),
                )
            ]
        )

        while True:
            records, next_offset = await self.qdrant_client.scroll(
                collection_name=current,
                offset=offset,
                limit=batch_size,
                scroll_filter=scroll_filter,
                with_payload=["source_id", "last_edited"],
                with_vectors=False,
            )

            for r in records:
                if r.payload:
                    source_id = r.payload.get("source_id")
                    last_edited = r.payload.get("last_edited")
                    if source_id and last_edited:
                        state[source_id] = last_edited

            if next_offset is None:
                break
            offset = next_offset

        return state

    async def reindex(self, fresh_nodes: list[TextNode], stale_ids: set[str]) -> int:
        current = await self._current_collection()
        new = await self._create_collection()
        copied = await self._copy_points(current, new, stale_ids) if current else 0
        added = await self._upsert_nodes(new, fresh_nodes)
        total = copied + added

        if current:
            before = (await self.qdrant_client.get_collection(current)).points_count
            if before and total < before * 0.5:
                await self.qdrant_client.delete_collection(new)
                raise RuntimeError(
                    f"нова колекція має {total} точок проти {before} — аліас не перемкнено"
                )

        await self._switch_alias(new)
        try:
            await self._cleanup_old_collections()
        except Exception:
            logger.exception("не вдалось прибрати старі колекції")
        return copied + added

    async def _create_collection(self) -> str:
        name = self.alias + "_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        await self.qdrant_client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(
                size=3072,
                distance=Distance.COSINE,
            ),
        )
        return name

    async def _current_collection(self) -> str | None:
        """Колекція, на яку зараз вказує аліас."""
        aliases = await self.qdrant_client.get_aliases()
        for a in aliases.aliases:
            if a.alias_name == self.alias:
                return a.collection_name
        return None

    async def _switch_alias(self, collection_name: str) -> None:
        await self.qdrant_client.update_collection_aliases(
            change_aliases_operations=[
                DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=self.alias)),
                CreateAliasOperation(
                    create_alias=CreateAlias(
                        collection_name=collection_name, alias_name=self.alias
                    )
                ),
            ]
        )

    async def _copy_points(self, src: str, dst: str, stale_ids: set[str]) -> int:
        """Копіює точки з однієї колекції в іншу, крім документів зі stale_ids."""
        total_copied = 0
        offset = None
        batch_size = 128  # такий самий розмір батчу, як і в _upsert_nodes

        while True:
            records, next_offset = await self.qdrant_client.scroll(
                collection_name=src,
                offset=offset,
                limit=batch_size,
                with_payload=True,
                with_vectors=True,
            )

            # Відкидаємо точки, чий документ застарів чи оновлюється
            valid_points = [
                PointStruct(id=r.id, vector=r.vector, payload=r.payload)
                for r in records
                if r.payload and r.payload.get("source_id") not in stale_ids
            ]

            if valid_points:
                await self.qdrant_client.upsert(
                    collection_name=dst,
                    points=valid_points,
                )
                total_copied += len(valid_points)

            if next_offset is None:
                break
            offset = next_offset

        return total_copied

    async def _upsert_nodes(self, collection_name: str, nodes: list[TextNode]) -> int:
        if not nodes:
            return 0

        points: list[PointStruct] = []

        texts = [node.get_content(metadata_mode=MetadataMode.EMBED) for node in nodes]
        vectors = await self.embedding_model.aget_text_embedding_batch(texts)

        for i, node in enumerate(nodes):
            payload = ChunkPayload(
                text=node.text,
                **node.metadata,
            )
            points.append(
                PointStruct(
                    id=node.node_id,
                    vector=vectors[i],
                    payload=payload.model_dump(),
                )
            )

        batch_size = 128
        for i in range(0, len(points), batch_size):
            await self.qdrant_client.upsert(
                collection_name=collection_name,
                points=points[i : i + batch_size],
            )

        return len(points)

    async def _cleanup_old_collections(self) -> int:
        """Видаляє всі колекції, крім поточної (за аліасом)"""
        current = await self._current_collection()

        if current is None:
            return 0
        cols = await self.qdrant_client.get_collections()
        collections_names = [
            col.name
            for col in cols.collections
            if col.name.startswith(f"{self.alias}_")
        ]

        deleted_count = 0
        for collection_name in collections_names:
            if collection_name != current:
                await self.qdrant_client.delete_collection(
                    collection_name=collection_name
                )
                deleted_count += 1

        return deleted_count
