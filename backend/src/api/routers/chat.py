from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db, get_rag_chain, get_user
from src.api.schemas import ChatRequest, ChatResponse
from src.generation.chain import RagChain
from src.models.user import User
from src.repository import chats

router = APIRouter(
    prefix="/chat",
    tags=["Chat"],
)


@router.post("/session/new", status_code=204)
async def new_session(
    user: User = Depends(get_user),
    db: AsyncSession = Depends(get_db),
):
    await chats.create_session(db, user.id)


@router.post("/", response_model=ChatResponse)
async def process_message(
    request: ChatRequest,
    chain: RagChain = Depends(get_rag_chain),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_user),
):
    session = await chats.get_or_create_session(db, user.id)
    history = await chats.get_recent_messages(db, session.id, limit=20)

    reply = await chain.generate_reply(request.message, history)

    await chats.add_message(db, session.id, "user", request.message)
    await chats.add_message(db, session.id, "assistant", reply)

    return ChatResponse(
        reply=reply,
    )
