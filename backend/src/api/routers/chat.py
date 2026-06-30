from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_rag_chain, get_user
from src.api.schemas import ChatRequest, ChatResponse
from src.generation.chain import RagChain
from src.models.user import User

router = APIRouter(
    prefix="/chat",
    tags=["Chat"],
)


@router.post("/", response_model=ChatResponse)
async def process_message(
    request: ChatRequest,
    chain: RagChain = Depends(get_rag_chain),
    user: User = Depends(get_user),
):
    reply = await chain.generate_reply(request.message)

    return ChatResponse(
        reply=reply,
    )
