from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db, get_mongo
from src.api.schemas import ChatRequest
from src.connectors.mongo import MongoConnector

router = APIRouter(
    prefix="/example",
    tags=["Example"],
)


@router.get("/")
async def example1(
    mongo: MongoConnector = Depends(get_mongo),
    db: AsyncSession = Depends(get_db),
):
    ans = await mongo.ping()

    return ans
