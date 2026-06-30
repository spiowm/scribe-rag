from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db, get_mongo
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
    doc = await mongo.find_member_by_phone("+380938082105")
    return {"first_name": doc["first_name"]} if doc else {"found": False}
