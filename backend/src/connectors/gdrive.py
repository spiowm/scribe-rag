import asyncio
import logging
from collections.abc import Collection

import httpx
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)


# Ліміт частоти Drive віддає не лише як 429, а й як 403 з такою причиною —
# її треба повторювати, а справжню відмову в доступі (теж 403) — ні.
RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}


def _retryable(response: httpx.Response) -> bool:
    if response.status_code in (429, 500, 502, 503, 504):
        return True
    if response.status_code != 403:
        return False
    try:
        errors = response.json().get("error", {}).get("errors", [])
    except ValueError:
        return False
    return any(e.get("reason") in RATE_LIMIT_REASONS for e in errors)


class GDriveConnector:
    FILES_URL = "https://www.googleapis.com/drive/v3/files"
    DRIVES_URL = "https://www.googleapis.com/drive/v3/drives"
    SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
    FOLDER = "application/vnd.google-apps.folder"
    SHORTCUT = "application/vnd.google-apps.shortcut"

    def __init__(self, credentials: dict):
        self._creds = Credentials.from_service_account_info(
            credentials, scopes=self.SCOPES
        )
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

        attempts = 5
        for attempt in range(attempts):
            try:
                headers = await self._auth_header()
                response = await self._client.get(url, headers=headers, params=params)
                response.raise_for_status()
                return response
            except (httpx.HTTPStatusError, httpx.TransportError) as e:
                retryable = isinstance(e, httpx.TransportError) or _retryable(
                    e.response
                )
                if not retryable or attempt == attempts - 1:
                    raise
                await asyncio.sleep(2**attempt)

        raise RuntimeError("Не вдалося виконати запит після кількох спроб")

    async def _list(self, params: dict) -> list[dict]:
        """Усі сторінки одного запиту files.list."""
        params = {
            "pageSize": 1000,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            **params,
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

    async def list_drives(self) -> list[dict]:
        """Спільні диски, учасником яких є сервісний акаунт: [{id, name}]."""
        drives: list[dict] = []
        params: dict = {"pageSize": 100, "fields": "nextPageToken,drives(id,name)"}
        while True:
            response = await self._get_with_retry(self.DRIVES_URL, params=params)
            data = response.json()
            drives.extend(data.get("drives", []))
            token = data.get("nextPageToken")
            if not token:
                return drives
            params["pageToken"] = token

    async def drive_principals(self, drive_id: str) -> list[str]:
        """Пошти груп і людей, яким видано доступ до диска (у нижньому регістрі).

        Будь-яка роль дає читання, тож роль не повертається. `anyone` і
        `domain` пошти не мають — таких прав на дисках зараз немає.
        """
        emails: set[str] = set()
        params: dict = {
            "supportsAllDrives": "true",
            "pageSize": 100,
            "fields": "nextPageToken,permissions(type,emailAddress)",
        }
        while True:
            response = await self._get_with_retry(
                f"{self.FILES_URL}/{drive_id}/permissions", params=params
            )
            data = response.json()
            emails |= {
                p["emailAddress"].lower()
                for p in data.get("permissions", [])
                if p.get("emailAddress")
            }
            if not data.get("nextPageToken"):
                return sorted(emails)
            params["pageToken"] = data["nextPageToken"]

    async def _shortcut_target(
        self, target: str, fields: str, paused_drives: Collection[str]
    ) -> dict | None:
        try:
            response = await self._get_with_retry(
                f"{self.FILES_URL}/{target}",
                params={"supportsAllDrives": "true", "fields": fields},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (403, 404):
                return None
            raise
        meta = response.json()
        if meta.get("trashed") or meta.get("driveId") in paused_drives:
            return None
        return meta

    async def _resolve_shortcuts(
        self, items: list[tuple[dict, str]], paused_drives: Collection[str]
    ) -> list[dict]:
        """Ярлики на файли → метадані самої цілі, з назвою й шляхом ярлика.

        Від ярлика лишаються лише назва й місце: дата зміни, розмір і, головне,
        `driveId` — від цілі. Інакше файл з `Boards`, на який стоїть ярлик на
        відкритішому диску, отримав би мітку доступу того диска (знайшло рев'ю
        08.11: ярлики на бюджети `Boards` лежать на `Leisure events`), а зміни
        цілі синк не бачив би, бо дата ярлика не міняється.
        Недосяжна ціль (видалена, без доступу) пропускається.
        """
        sem = asyncio.Semaphore(10)

        async def one(item: dict, path: str) -> dict | None:
            target = (item.get("shortcutDetails") or {}).get("targetId")
            async with sem:
                meta = await self._shortcut_target(
                    target,
                    "id,mimeType,size,modifiedTime,webViewLink,driveId,trashed",
                    paused_drives,
                )
            if meta is None:
                return None
            return {**meta, "name": item.get("name", ""), "path": path}

        resolved = await asyncio.gather(*(one(item, path) for item, path in items))
        return [r for r in resolved if r]

    async def _shortcut_folders(
        self, folders: list[tuple[str, str]], paused_drives: Collection[str]
    ) -> list[tuple[str, str]]:
        if not paused_drives:
            return folders
        sem = asyncio.Semaphore(10)

        async def allowed(folder_id: str) -> bool:
            async with sem:
                return await self._shortcut_target(
                    folder_id, "driveId,trashed", paused_drives
                ) is not None

        keep = await asyncio.gather(*(allowed(folder_id) for folder_id, _ in folders))
        return [folder for folder, eligible in zip(folders, keep) if eligible]

    async def get_file(self, file_id: str) -> dict:
        response = await self._get_with_retry(
            f"{self.FILES_URL}/{file_id}",
            params={"supportsAllDrives": "true", "fields": "id,name,driveId"},
        )
        return response.json()

    async def list_drive(
        self, drive_id: str, mimes: set[str], *, paused_drives: Collection[str] = ()
    ) -> list[dict]:
        """Файли вказаних типів на диску — плоскими запитами, без обходу тек.

        Обхід вглиб коштує запит на кожну теку (на 20 дисках їх ~5 800), а тут
        три запити на диск: файли потрібних типів, теки (з них локально
        будується шлях від кореня диска) і ярлики.

        Ярлик на теку веде на інший диск, де акаунт може й не бути учасником
        (`corpora=drive` там дає 403), тому ці піддерева обходяться `walk()`.
        """
        if drive_id in paused_drives:
            return []
        base = {"corpora": "drive", "driveId": drive_id}
        fields = "nextPageToken, files(id,name,mimeType,size,modifiedTime,webViewLink,parents,driveId"
        types = " or ".join(f"mimeType = '{m}'" for m in sorted(mimes))
        files, folders, shortcuts = await asyncio.gather(
            self._list(
                {**base, "q": f"({types}) and trashed = false", "fields": fields + ")"}
            ),
            self._list(
                {
                    **base,
                    "q": f"mimeType = '{self.FOLDER}' and trashed = false",
                    "fields": "nextPageToken, files(id,name,parents)",
                }
            ),
            self._list(
                {
                    **base,
                    "q": f"mimeType = '{self.SHORTCUT}' and trashed = false",
                    "fields": fields + ",shortcutDetails)",
                }
            ),
        )
        tree = {f["id"]: (f["name"], (f.get("parents") or [None])[0]) for f in folders}

        def path_of(parent: str | None) -> str | None:
            """Шлях від кореня диска до теки `parent`; None — теки не видно."""
            names: list[str] = []
            seen: set[str] = set()
            while parent != drive_id:
                if parent is None or parent not in tree or parent in seen:
                    return None
                seen.add(parent)
                name, parent_next = tree[parent]
                names.append(name)
                parent = parent_next
            return "/".join(reversed(names))

        found: list[dict] = []
        for f in files:
            path = path_of((f.get("parents") or [None])[0])
            if path is not None:
                found.append({**f, "path": path})

        subtrees: list[tuple[str, str]] = []
        file_shortcuts: list[tuple[dict, str]] = []
        for s in shortcuts:
            path = path_of((s.get("parents") or [None])[0])
            details = s.get("shortcutDetails") or {}
            target, target_mime = details.get("targetId"), details.get("targetMimeType")
            if path is None or not target:
                continue
            if target_mime == self.FOLDER:
                subtrees.append((target, f"{path}/{s['name']}" if path else s["name"]))
            elif target_mime in mimes:
                file_shortcuts.append((s, path))
        found.extend(await self._resolve_shortcuts(file_shortcuts, paused_drives))

        if subtrees:
            found.extend(
                f
                for f in await self.walk(subtrees, paused_drives=paused_drives)
                if f.get("mimeType") in mimes
            )
        return found

    async def list_children(self, folder_id: str) -> list[dict]:
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id,name,mimeType,size,modifiedTime,webViewLink,shortcutDetails,driveId)",
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

    async def walk(
        self, roots: list[tuple[str, str]], *, paused_drives: Collection[str] = ()
    ) -> list[dict]:
        """Обхід тек вглиб: запит на кожну теку. `roots` — пари (тека, шлях).

        Лише для тек на дисках, де акаунт не учасник: решту дає `list_drive`.
        """
        files: list[dict] = []
        shortcut_files: list[tuple[dict, str]] = []
        # тягнемо за собою шлях папок: три SAAR.pdf розрізняються лише ним
        level = await self._shortcut_folders(roots, paused_drives)
        seen: set[str] = {fid for fid, _ in level}

        while level:
            results = await asyncio.gather(
                *(self.list_children(fid) for fid, _ in level)
            )
            next_level: list[tuple[str, str]] = []
            folder_shortcuts: list[tuple[str, str]] = []

            for (_, prefix), items in zip(level, results):
                for item in items:
                    if item.get("driveId") in paused_drives:
                        continue
                    mine = item.get("mimeType")
                    item_id = item.get("id")
                    name = item.get("name", "")
                    child_path = f"{prefix}/{name}" if prefix else name

                    #  1. Якщо це папка — йдемо в неї глибше
                    if mine == self.FOLDER:
                        if item_id and item_id not in seen:
                            seen.add(item_id)
                            next_level.append((item_id, child_path))

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
                                folder = (target_id, child_path)
                                next_level.append(folder)
                                folder_shortcuts.append(folder)
                        else:
                            shortcut_files.append((item, prefix))

                    # 3. Звичайний файл — зберігаємо
                    else:
                        files.append({**item, "path": prefix})

            if paused_drives and folder_shortcuts:
                allowed = await self._shortcut_folders(folder_shortcuts, paused_drives)
                skipped = set(folder_shortcuts) - set(allowed)
                next_level = [folder for folder in next_level if folder not in skipped]
            level = next_level

        return files + await self._resolve_shortcuts(shortcut_files, paused_drives)

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
