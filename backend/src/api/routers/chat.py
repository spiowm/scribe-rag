import time
from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db, get_rag_chain, get_user
from src.api.schemas import ChatRequest, ChatResponse
from src.generation.chain import LAST_RUN, RagChain
from src.models.user import User
from src.repository import chats

router = APIRouter(
    prefix="/chat",
    tags=["Chat"],
)


def build_footer(run: dict, seconds: float, history: int) -> str:
    """Service line under the reply: who answered, on what model, how long."""
    provider = "agy" if run.get("provider") == "agy" else "api"
    line = f"{provider} | {run.get('model', '?')} | {seconds:.0f}s | history {history}"
    return f"\n\n<sub>_{line}_</sub>"


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
    history = await chats.get_recent_messages(db, session.id, limit=10)

    started = time.monotonic()
    reply = await chain.generate_reply(request.message, history, user.phone_number)
    seconds = time.monotonic() - started

    await chats.add_message(db, session.id, "user", request.message)
    await chats.add_message(db, session.id, "assistant", reply)

    return ChatResponse(
        reply=reply + build_footer(LAST_RUN.get() or {}, seconds, len(history))
    )
