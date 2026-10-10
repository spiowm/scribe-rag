from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_admin, get_db
from src.models.user import User
from src.repository import stats

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.post("/stats")
async def read_stats(
    admin: User = Depends(get_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Зведення для власника. Малює його бот — тут лише цифри."""
    return await stats.overview(db)
