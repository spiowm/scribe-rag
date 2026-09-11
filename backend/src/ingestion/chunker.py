from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode

from src.ingestion.schemas import ChunkMetadata


class Chunker:
    def __init__(self):
        self.parser = SentenceSplitter(chunk_size=800, chunk_overlap=100)

    def chunk(self, text: str, metadata: ChunkMetadata) -> list[TextNode]:
        if not text or not text.strip():
            return []

        doc = Document(
            text=text,
            metadata=metadata.model_dump(),
        )
        doc.excluded_embed_metadata_keys = [
            "source",
            "source_id",
            "url",
            "last_edited",
        ]

        nodes = self.parser.get_nodes_from_documents([doc])

        return [node for node in nodes if isinstance(node, TextNode)]
