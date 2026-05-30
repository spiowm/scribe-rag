from src.config import settings
from src.connectors.notion import NotionConnector


def get_notion_connector() -> NotionConnector:
    return NotionConnector(settings.NOTION_TOKEN, settings.NOTION_USERS_DB_ID)
