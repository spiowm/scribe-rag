import asyncio

from llama_index.core.schema import TextNode
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import Distance, PointStruct, VectorParams

from src.ingestion.schemas import ChunkPayload


class QdrantRepository:
    def __init__(
        self,
        url: str,
        collection_name: str,
        embedding_model: GoogleGenAIEmbedding,
    ) -> None:
        self.collection_name = collection_name
        self.qdrant_client = AsyncQdrantClient(url=url)
        self.embedding_model = embedding_model

    async def ensure_collection(self) -> None:
        if not await self.qdrant_client.collection_exists(self.collection_name):
            await self.qdrant_client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=3072,
                    distance=Distance.COSINE,
                ),
            )

    async def upsert_nodes(self, nodes: list[TextNode]) -> int:
        if not nodes:
            return 0

        points: list[PointStruct] = []

        batch_size = 20

        for i in range(0, len(nodes), batch_size):
            batch_nodes = nodes[i : i + batch_size]
            texts = [node.text for node in batch_nodes]
            vectors = await self.embedding_model.aget_text_embedding_batch(texts)

            for j, node in enumerate(batch_nodes):
                payload = ChunkPayload(
                    text=node.text,
                    **node.metadata,
                )

                points.append(
                    PointStruct(
                        id=node.node_id,
                        vector=vectors[j],
                        payload=payload.model_dump(),
                    )
                )

            if i + batch_size < len(nodes):
                await asyncio.sleep(2)

        await self.qdrant_client.upsert(
            collection_name=self.collection_name,
            points=points,
        )

        return len(points)

    async def search(self, query: str, limit: int = 5) -> list[ChunkPayload]:
        query_vector = await self.embedding_model.aget_text_embedding(query)
        results = await self.qdrant_client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            limit=limit,
        )
        return [
            ChunkPayload.model_validate(point.payload)
            for point in results.points
            if point.payload is not None
        ]
