from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db, get_rag_chain
from src.api.schemas import ChatRequest, ChatResponse
from src.generation.chain import RagChain

router = APIRouter(
    prefix="/chat",
    tags=["Chat"],
)


@router.post("/", response_model=ChatResponse)
async def process_message(
    request: ChatRequest,
    chain: RagChain = Depends(get_rag_chain),
    db: AsyncSession = Depends(get_db),
):
    reply = await chain.generate_reply(request.message)

    return ChatResponse(
        reply=reply,
    )
