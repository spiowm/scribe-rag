import asyncio
import logging

import httpx
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)


class GDriveConnector:
    FILES_URL = "https://www.googleapis.com/drive/v3/files"
    SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
    FOLDER = "application/vnd.google-apps.folder"
    SHORTCUT = "application/vnd.google-apps.shortcut"

    def __init__(self, credentials: dict, root_folder_ids: list[str]):
        self._creds = Credentials.from_service_account_info(
            credentials, scopes=self.SCOPES
        )
        self.root_folder_ids = root_folder_ids
        self._client = httpx.AsyncClient(timeout=60)
        self._lock = asyncio.Lock()

    async def _auth_header(self) -> dict[str, str]:
        async with self._lock:
            if not self._creds.valid:
                await asyncio.to_thread(self._creds.refresh, GoogleRequest())
        return {"Authorization": f"Bearer {self._creds.token}"}

    async def _get_with_retry(
        self,
        url: str,
        params: dict,
    ) -> httpx.Response:

        for attempt in range(3):
            try:
                headers = await self._auth_header()
                response = await self._client.get(url, headers=headers, params=params)
                response.raise_for_status()
                return response
            except (httpx.HTTPStatusError, httpx.TransportError) as e:
                retryable = isinstance(
                    e, httpx.TransportError
                ) or e.response.status_code in (429, 500, 502, 503, 504)
                if not retryable or attempt == 2:
                    raise
                await asyncio.sleep(2**attempt)

        raise RuntimeError("Не вдалося виконати запит після кількох спроб")

    async def list_children(self, folder_id: str) -> list[dict]:
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id,name,mimeType,size,modifiedTime,webViewLink,shortcutDetails)",
            "pageSize": 1000,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        items: list[dict] = []
        while True:
            response = await self._get_with_retry(self.FILES_URL, params=params)
            data = response.json()
            items.extend(data.get("files", []))
            token = data.get("nextPageToken")
            if not token:
                return items
            params["pageToken"] = token

    async def walk(self) -> list[dict]:
        files: list[dict] = []
        seen: set[str] = set(self.root_folder_ids)
        level = list(self.root_folder_ids)

        while level:
            results = await asyncio.gather(*(self.list_children(fid) for fid in level))
            level = []
            for items in results:
                for item in items:
                    mine = item.get("mimeType")
                    item_id = item.get("id")

                    #  1. Якщо це папка — йдемо в неї глибше
                    if mine == self.FOLDER:
                        if item_id and item_id not in seen:
                            seen.add(item_id)
                            level.append(item_id)

                    # 2. Якщо це ярлик (shortcut)
                    elif mine == self.SHORTCUT:
                        details = item.get("shortcutDetails") or {}
                        target_id = details.get("targetId")
                        target_mime = details.get("targetMimeType")

                        if not target_id:
                            continue

                        if target_mime == self.FOLDER:
                            if target_id not in seen:
                                seen.add(target_id)
                                level.append(target_id)
                        else:
                            files.append(
                                {**item, "id": target_id, "mimeType": target_mime}
                            )

                    # 3. Звичайний файл — зберігаємо
                    else:
                        files.append(item)

        return files

    async def download(self, file_id: str) -> bytes:
        """Завантажує двійкові файли як є (PDF, PPTX тощо)."""
        response = await self._get_with_retry(
            f"{self.FILES_URL}/{file_id}",
            params={
                "alt": "media",
                "supportsAllDrives": "true",
            },
        )
        return response.content

    async def export(self, file_id: str, mime: str) -> bytes:
        """Експортує Google Docs / Slides / Sheets у вибраний mime-тип."""
        response = await self._get_with_retry(
            f"{self.FILES_URL}/{file_id}/export",
            params={"mimeType": mime},
        )
        return response.content

    async def close(self) -> None:
        await self._client.aclose()
