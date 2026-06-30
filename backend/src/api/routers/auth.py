import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db, get_mongo
from src.api.schemas import LinkRequest, UserResponse
from src.connectors.mongo import MongoConnector
from src.models.user import User
from src.repository import users

router = APIRouter(
    prefix="/auth",
    tags=["Auth"],
)


@router.post("/link", response_model=UserResponse)
async def link(
    body: LinkRequest,
    mongo: MongoConnector = Depends(get_mongo),
    db: AsyncSession = Depends(get_db),
):
    existing_user = await users.get_user_by_telegram_id(db, body.telegram_id)
    if existing_user:
        return existing_user

    normalized_phone = "0" + re.sub(r"\D", "", body.phone)[-9:]
    member = await mongo.find_member_by_phone(normalized_phone)
    if member is None:
        raise HTTPException(403, "not_a_member")

    user = User(
        telegram_id=body.telegram_id,
        telegram_username=body.telegram_username,
        first_name=member.get("first_name"),
        last_name=member.get("last_name"),
        phone_number=normalized_phone,
    )

    user = await users.create_user(db, user)

    return user
