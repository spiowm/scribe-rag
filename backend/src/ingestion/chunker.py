from llama_index.core import Document
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.core.schema import TextNode

from src.connectors.notion import NotionPage
from src.ingestion.schemas import ChunkPayload


class NotionChunker:
    def __init__(self):
        self.parser = MarkdownNodeParser()

    def chunk_page(self, page: NotionPage) -> list[TextNode]:
        if not page.content or not page.content.strip():
            return []

        payload = ChunkPayload(
            text="",
            source="notion",
            source_id=page.id,
            title=page.title,
            url=page.url,
            last_edited=page.last_edited,
        )

        doc = Document(
            text=page.content,
            metadata=payload.model_dump(exclude={"text"}),
        )
        doc.excluded_embed_metadata_keys = [
            "text",
            "source_type",
            "data_id",
            "data_url",
            "data_last_edited",
        ]

        nodes = self.parser.get_nodes_from_documents([doc])

        return [node for node in nodes if isinstance(node, TextNode)]
