from datetime import UTC, datetime

from llama_index.core.schema import TextNode
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from pymongo import collection
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    CreateAlias,
    CreateAliasOperation,
    DeleteAlias,
    DeleteAliasOperation,
    Distance,
    PointStruct,
    VectorParams,
)

from src.ingestion.schemas import ChunkPayload


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

    async def create_collection(self) -> str:
        name = self.alias + "_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        await self.qdrant_client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(
                size=3072,
                distance=Distance.COSINE,
            ),
        )
        return name

    async def switch_alias(self, collection_name: str) -> None:
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

    async def ensure_collection(self) -> None:
        if not await self.qdrant_client.collection_exists(self.alias):
            collection_name = await self.create_collection()
            await self.switch_alias(collection_name)

    async def upsert_nodes(self, collection_name: str, nodes: list[TextNode]) -> int:
        if not nodes:
            return 0

        points: list[PointStruct] = []

        texts = [node.text for node in nodes]
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

    async def search(self, query: str, limit: int = 15) -> list[ChunkPayload]:
        query_vector = await self.embedding_model.aget_text_embedding(query)
        results = await self.qdrant_client.query_points(
            collection_name=self.alias,
            query=query_vector,
            limit=limit,
        )
        return [
            ChunkPayload.model_validate(point.payload)
            for point in results.points
            if point.payload is not None
        ]

    async def cleanup_old_collections(self) -> int:
        """Видаляє всі колекції, крім поточної (за аліасом)"""
        al = await self.qdrant_client.get_aliases()
        current = None
        for a in al.aliases:
            if a.alias_name == self.alias:
                current = a.collection_name
                break

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
