from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode

from src.connectors.notion import NotionPage
from src.ingestion.schemas import ChunkMetadata


class NotionChunker:
    def __init__(self):
        self.parser = SentenceSplitter(chunk_size=800, chunk_overlap=100)

    def chunk_page(self, page: NotionPage) -> list[TextNode]:
        if not page.content or not page.content.strip():
            return []

        metadata = ChunkMetadata(
            source="notion",
            source_id=page.id,
            title=page.title,
            url=page.url,
            last_edited=page.last_edited,
        )

        doc = Document(
            text=page.content,
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
