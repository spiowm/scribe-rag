import json
import logging
import re

from notion_client import AsyncClient
from notion_client.helpers import async_collect_paginated_api

from src.connectors.schemas import NotionPage

logger = logging.getLogger(__name__)


class NotionConnector:
    def __init__(self, token: str, users_db_id: str):
        self.notion = AsyncClient(
            auth=token,
            log_level=logging.DEBUG,
        )
        self.users_db_id = users_db_id

    async def fetch_all_pages(self) -> list[NotionPage]:
        """Бере всі сторінки з Notion (без сторінок з баз данних)"""

        # тимчасовий мок на 10 сторінок =============
        with open("tests/regular_pages_10.json", "r", encoding="utf-8") as f:
            pages = json.load(f)

        # тимчасовий мок на 10 сторінок =============

        # pages = await async_collect_paginated_api(
        #     self.notion.search,
        #     filter={
        #         "property": "object",
        #         "value": "page",
        #     },
        # )

        # Відсікання сторінок, що в базах даних
        regular_pages = [p for p in pages if p["parent"]["type"] != "data_source_id"]

        results: list[NotionPage] = []
        for page in regular_pages:
            title_list = page["properties"]["title"]["title"]
            title = title_list[0]["plain_text"] if title_list else "Без назви"
            notion_page = NotionPage(
                id=page["id"],
                title=title,
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
