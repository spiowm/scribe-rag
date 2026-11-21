import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_admin, get_db, get_gdrive_connector, get_mongo
from src.api.schemas import GroupImportRequest
from src.config import settings
from src.connectors.gdrive import GDriveConnector
from src.connectors.mongo import MongoConnector
from src.generation import models_check
from src.models.user import User
from src.repository import access, stats

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.post("/stats")
async def read_stats(
    admin: User = Depends(get_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Зведення для власника. Малює його бот — тут лише цифри."""
    return {**await stats.overview(db), "problems": dict(models_check.PROBLEMS)}


@router.post("/access")
async def read_access(
    admin: User = Depends(get_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Диски, групи в їхніх правах і свіжість експортів цих груп."""
    return await access.overview(db)


@router.post("/drives/refresh")
async def refresh_drives(
    admin: User = Depends(get_admin),
    db: AsyncSession = Depends(get_db),
    gdrive: GDriveConnector = Depends(get_gdrive_connector),
) -> dict:
    """Перечитує список дисків і їхні права з Drive API."""
    drives = await gdrive.list_drives()
    if not drives:
        # Порожній список — збій Drive API, а не «дисків немає»: зберегти його
        # означало б забрати в усіх доступ до дисків.
        raise HTTPException(502, "drive_api_empty")
    paused = settings.PAUSED_DRIVES
    drives = [d for d in drives if d["id"] not in paused]
    principals = await asyncio.gather(
        *(gdrive.drive_principals(d["id"]) for d in drives)
    )
    await access.save_drives(
        db,
        [
            {"id": d["id"], "name": d["name"], "principals": p}
            for d, p in zip(drives, principals)
        ],
    )
    return await access.overview(db)


@router.post("/groups/import")
async def import_group(
    body: GroupImportRequest,
    admin: User = Depends(get_admin),
    db: AsyncSession = Depends(get_db),
    mongo: MongoConnector = Depends(get_mongo),
) -> dict:
    """Замінює членство групи вмістом експорту CSV."""
    try:
        export = access.parse_export(body.content)
    except ValueError as exc:
        raise HTTPException(422, f"bad_export: {exc}") from exc

    known = await access.known_groups(db)
    if not known:
        raise HTTPException(409, "no_drives")
    # Підпис до файлу важливіший за назву всередині: назва групи в Google може
    # не збігатися з адресою, і тоді власник указує адресу явно.
    name = body.group or export.group
    group = access.resolve_group(name, known)
    if group is None:
        raise HTTPException(422, f"unknown_group: {name}")
    # Підпис — для файлу, чия назва групи не збігається з адресою. Якщо ж обидва
    # розпізнались і різні, це, найпевніше, не той файл: замінити не ту групу
    # означало б роздати й забрати доступи не тим людям.
    in_file = access.resolve_group(export.group, known)
    if body.group and in_file and in_file != group:
        raise HTTPException(422, f"group_mismatch: {in_file}")

    await access.import_group(db, group, export.members)
    directory = {access.email_key(e) for e in await mongo.emails()}
    overview = await access.overview(db)
    return {
        "group": group,
        "members": len(export.members),
        "skipped": export.skipped,
        "unmatched": sorted(e for e in export.members if e not in directory),
        "drives": [d["name"] for d in overview["drives"] if group in d["principals"]],
    }
