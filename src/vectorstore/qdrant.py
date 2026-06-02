import asyncio

from llama_index.core.schema import TextNode
from llama_index.embeddings.gemini.base import GeminiEmbedding
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import Distance, PointStruct, VectorParams


class QdrantRepository:
    def __init__(
        self,
        url: str,
        collection_name: str,
        embedding_model: GeminiEmbedding,
    ) -> None:
        self.collection_name = collection_name
        self.qdrant = AsyncQdrantClient(url=url)
        self.embedding_model = embedding_model

    async def ensure_collection(self) -> None:
        if not await self.qdrant.collection_exists(self.collection_name):
            await self.qdrant.create_collection(
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
                points.append(
                    PointStruct(
                        id=node.node_id,
                        vector=vectors[j],
                        payload={
                            "text": node.text,
                            **node.metadata,
                        },
                    )
                )

            if i + batch_size < len(nodes):
                await asyncio.sleep(2)

        await self.qdrant.upsert(
            collection_name=self.collection_name,
            points=points,
        )

        return len(points)
