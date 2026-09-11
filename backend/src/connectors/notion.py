import asyncio
import logging
import re

from notion_client import AsyncClient
from notion_client.helpers import async_collect_paginated_api

from src.connectors.schemas import NotionPage

logger = logging.getLogger(__name__)

ROOT_TITLES = {"Home", "Без назви", ""}


class NotionConnector:
    def __init__(self, token: str, users_db_id: str):
        self.notion = AsyncClient(
            auth=token,
            log_level=logging.DEBUG,
        )
        self.users_db_id = users_db_id
        self._raw: dict[str, dict] = {}  # id -> сира сторінка з API
        self._block_parents: dict[str, dict] = {}  # id блока -> його parent

    @staticmethod
    def _page_title(page: dict) -> str:
        """Безпечно: сторінки з баз даних можуть не мати властивості title."""
        title_list = page.get("properties", {}).get("title", {}).get("title", [])
        return title_list[0]["plain_text"] if title_list else "Без назви"

    async def _parent_page_id(self, parent: dict) -> str | None:
        """Піднімається блоками, поки не дійде до сторінки.

        Сторінка в колонці дає child_page -> column -> column_list -> page,
        тобто 2-3 стрибки. Кеш робить спільних предків безкоштовними.
        """
        for _ in range(5):
            kind = parent.get("type")
            if kind == "page_id":
                return parent["page_id"]
            if kind != "block_id":
                return None

            block_id = parent["block_id"]
            if block_id not in self._block_parents:
                try:
                    block = await self.notion.blocks.retrieve(block_id)
                    self._block_parents[block_id] = block.get("parent") or {}
                except Exception as e:
                    logger.warning("не вдалось дістати блок %s: %s", block_id, e)
                    self._block_parents[block_id] = {}
            parent = self._block_parents[block_id]
            if not parent:
                return None
        return None

    async def resolve_paths(self, pages: list[NotionPage]) -> None:
        """Ставить path = назва батьківської сторінки.

        Викликати лише для тих, що йдуть на переіндексацію: на всі 323
        сторінки це ~720 запитів blocks.retrieve.
        """
        for page in pages:
            raw = self._raw.get(page.id) or {}
            parent_id = await self._parent_page_id(raw.get("parent") or {})
            parent = self._raw.get(parent_id or "")
            title = self._page_title(parent).strip() if parent else ""
            page.path = "" if title in ROOT_TITLES else title

    async def fetch_all_pages(self) -> list[NotionPage]:
        """Бере всі сторінки з Notion (без сторінок з баз данних)"""

        pages = await async_collect_paginated_api(
            self.notion.search,
            filter={
                "property": "object",
                "value": "page",
            },
        )

        # Батьком може бути будь-яка сторінка, у тому числі з бази даних,
        # тому тримаємо всі, а не regular_pages.
        self._raw = {p["id"]: p for p in pages}

        regular_pages = [p for p in pages if p["parent"]["type"] != "data_source_id"]

        results: list[NotionPage] = []
        for page in regular_pages:
            notion_page = NotionPage(
                id=page["id"],
                title=self._page_title(page),
                url=page.get("url", ""),
                last_edited=page.get("last_edited_time", ""),
            )
            results.append(notion_page)
        return results

    async def get_page_markdown(self, page_id: str) -> str:
        """Отримує markdown сторінку та очущує її"""
        response = await self.notion.pages.retrieve_markdown(page_id)
        md_content = response.get("markdown", "")

        # Видалення тимчасових S3 посилань на фотки
        md_content = re.sub(
            r"!\[.*?\]\(https://prod-files-secure\.s3\.[^)]+\)", "", md_content
        )

        # Видалення Notion-специфічних тегів
        md_content = re.sub(r"<empty-block\s*/?>", "", md_content)
        md_content = re.sub(r"</?columns>", "", md_content)
        md_content = re.sub(r"</?column>", "", md_content)
        md_content = re.sub(r"<table_of_contents\s*[^>]*\s*/?>", "", md_content)
        md_content = re.sub(r"<mention-user\s*[^>]*\s*/?>", "", md_content)
        md_content = re.sub(r"</?callout\s*[^>]*\s*/?>", "", md_content)
        md_content = re.sub(r"</?span\s*[^>]*\s*/?>", "", md_content)
        # Видалення Notion color/style атрибутів: {color="pink_bg"} тощо
        md_content = re.sub(r"{[^}]*}", "", md_content)
        # Заміна HTML тегів <br> на звичайний перенос рядка
        md_content = re.sub(r"<br\s*/?>", "\n", md_content)
        # Видалення database блоків: <database ...>...</database>
        md_content = re.sub(
            r"<database[^>]*>.*?</database>", "", md_content, flags=re.DOTALL
        )
        # Очищищення зайвих переносів рядків
        md_content = re.sub(r"\n\s*\n\s*\n+", "\n\n", md_content)
        return md_content.strip()

    async def fetch_pages_content(self, pages: list[NotionPage]) -> int:
        """Завантажує markdown контент для всіх сторінок паралельно. Повертає кількість невдач"""
        sem = asyncio.Semaphore(5)  # обмеження на кількість одночасних запитів

        async def load(page: NotionPage) -> bool:
            async with sem:
                try:
                    page.content = await self.get_page_markdown(page.id)
                    return True
                except Exception as e:
                    logger.error(f"Error fetching content for page {page.id}: {e}")
                    return False

        tasks = [load(page) for page in pages]
        results = await asyncio.gather(*tasks)
        failed_count = sum(1 for r in results if not r)
        return failed_count
