from llama_index.core import Document
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.core.schema import TextNode

from src.connectors.notion import NotionPage


class NotionChunker:
    def __init__(self):
        self.parser = MarkdownNodeParser()

    def chunk_page(self, page: NotionPage) -> list[TextNode]:
        if not page.content or not page.content.strip():
            return []

        doc = Document(
            text=page.content,
            metadata={
                "page_id": page.id,
                "page_title": page.title,
                "page_url": page.url,
                "last_edited": page.last_edited,
                "source": "notion",
            },
        )
        doc.excluded_embed_metadata_keys = ["page_id", "page_url", "last_edited"]

        nodes = self.parser.get_nodes_from_documents([doc])

        return nodes
