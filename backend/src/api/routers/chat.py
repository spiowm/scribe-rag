import asyncio
import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db, get_rag_chain, get_user
from src.api.schemas import ChatRequest, ChatResponse
from src.generation.chain import LAST_RUN, RagChain
from src.generation.tools import TOOLS
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


def build_tools_block(run: dict) -> str:
    """Згорнуте «Думання» перед відповіддю: що саме бот робив."""
    calls = run.get("calls") or []
    if not calls:
        return ""
    items = "<br>".join(
        f"↳ {TOOLS[name].icon if name in TOOLS else '⚙'} {text}" for name, text in calls
    )
    return (
        f"<details><summary><sub>_Думання_</sub></summary>\n"
        f"<sub>_{items}_</sub>\n</details>\n\n"
    )


@router.post("/session/new", status_code=204)
async def new_session(
    user: User = Depends(get_user),
    db: AsyncSession = Depends(get_db),
):
    await chats.create_session(db, user.id)


@router.post("/stream")
async def process_message_stream(
    request: ChatRequest,
    chain: RagChain = Depends(get_rag_chain),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_user),
):
    """Те саме, що /chat/, але статуси йдуть одразу, а відповідь — у кінці."""
    session = await chats.get_or_create_session(db, user.id)
    history = await chats.get_recent_messages(db, session.id, limit=10)

    events: asyncio.Queue[str] = asyncio.Queue()
    started = time.monotonic()

    async def run() -> tuple[str, dict]:
        reply = await chain.generate_reply(
            request.message, history, user.phone_number, on_event=events.put_nowait
        )
        return reply, LAST_RUN.get() or {}

    async def stream():
        task = asyncio.create_task(run())
        try:
            while True:
                getter = asyncio.create_task(events.get())
                done, _ = await asyncio.wait(
                    {getter, task}, return_when=asyncio.FIRST_COMPLETED
                )
                if getter in done:
                    yield json.dumps({"type": "status", "text": getter.result()}) + "\n"
                    continue
                getter.cancel()
                # задача завершилась: віддаємо все, що ще лежить у черзі, і відповідь
                while not events.empty():
                    yield (
                        json.dumps({"type": "status", "text": events.get_nowait()})
                        + "\n"
                    )
                reply, info = task.result()
                seconds = time.monotonic() - started
                await chats.add_message(db, session.id, "user", request.message)
                await chats.add_message(db, session.id, "assistant", reply)
                yield (
                    json.dumps(
                        {
                            "type": "reply",
                            "text": build_tools_block(info)
                            + reply
                            + build_footer(info, seconds, len(history)),
                        }
                    )
                    + "\n"
                )
                return
        except Exception as exc:  # noqa: BLE001
            task.cancel()
            yield json.dumps({"type": "error", "text": str(exc)}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


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

    run = LAST_RUN.get() or {}
    return ChatResponse(
        reply=build_tools_block(run) + reply + build_footer(run, seconds, len(history))
    )
