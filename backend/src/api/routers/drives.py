from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db, get_mongo, get_user
from src.api.schemas import DriveToggleRequest
from src.config import settings
from src.connectors.mongo import MongoConnector
from src.models.user import User
from src.repository import access

router = APIRouter(prefix="/drives", tags=["Drives"])


async def _view(db: AsyncSession, mongo: MongoConnector, user: User) -> dict:
    member, _ = await access.caller(db, mongo, user)
    return await access.drive_view(db, user.id, member, settings.GOOGLE_DRIVES)


@router.post("/")
async def list_drives(
    user: User = Depends(get_user),
    db: AsyncSession = Depends(get_db),
    mongo: MongoConnector = Depends(get_mongo),
) -> dict:
    """Де бот шукає для людини: завжди, її диски (ввімкнені чи ні) і без доступу."""
    return await _view(db, mongo, user)


@router.post("/toggle")
async def toggle(
    body: DriveToggleRequest,
    user: User = Depends(get_user),
    db: AsyncSession = Depends(get_db),
    mongo: MongoConnector = Depends(get_mongo),
) -> dict:
    # drive_id приходить із callback_data, тобто від клієнта: вмикати можна
    # лише те, що є серед доступних. Пошук однаково перетне з доступом, але
    # таблиця не має засмічуватись чужими дисками.
    view = await _view(db, mongo, user)
    if body.drive_id not in {d["id"] for d in view["drives"]}:
        raise HTTPException(403, "no_access")
    await access.toggle_drive(db, user.id, body.drive_id)
    return await _view(db, mongo, user)
