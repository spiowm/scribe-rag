import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db, get_memes, get_rag_chain, get_user
from src.api.schemas import ChatRequest, ChatResponse
from src.generation.chain import LAST_RUN, RagChain
from src.generation.tools import TOOLS
from src.models.user import User
from src.repository import chats
from src.vectorstore.memes import MemeRepository

router = APIRouter(
    prefix="/chat",
    tags=["Chat"],
)

# Один запит на юзера за раз. Без цього два повідомлення, надіслані поспіль,
# читають однакову історію й пишуть пари впереміш: заміряно, що відповідь на
# довге питання лягає в БД після відповіді на коротке, і наступного разу модель
# читає їх як відповіді не на ті питання.
BUSY: dict[int, float] = {}
# Страховка від зависання: якщо запит упав так, що finally не спрацював,
# юзер не мусить лишитись заблокованим назавжди.
BUSY_TTL = 300.0


def take_slot(user_id: int) -> None:
    """Займає єдиний слот юзера або відмовляє 429."""
    started = BUSY.get(user_id)
    if started is not None and time.monotonic() - started < BUSY_TTL:
        raise HTTPException(429, "busy")
    BUSY[user_id] = time.monotonic()


def free_slot(user_id: int) -> None:
    BUSY.pop(user_id, None)


def build_footer(run: dict, seconds: float, window: chats.Window) -> str:
    """Службовий рядок: хто відповів, якою моделлю, скільки часу, що в памʼяті."""
    provider = "agy" if run.get("provider") == "agy" else "api"
    cut = "…" if window.truncated else ""
    mem = (
        f"{cut}{len(window.messages)} messages · "
        f"{window.tokens / 1000:.1f}k/{chats.HISTORY_TOKEN_BUDGET // 1000}k"
    )
    line = f"{provider} | {run.get('model', '?')} | {seconds:.0f}s | {mem}"
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
    memes: MemeRepository = Depends(get_memes),
):
    """Те саме, що /chat/, але статуси йдуть одразу, а відповідь — у кінці."""
    take_slot(user.id)
    session = await chats.get_or_create_session(db, user.id)
    window = await chats.get_history_window(db, session.id)

    events: asyncio.Queue[str] = asyncio.Queue()
    started = time.monotonic()

    async def run() -> tuple[str, dict]:
        reply = await chain.generate_reply(
            request.message,
            window.messages,
            user.phone_number,
            on_event=events.put_nowait,
        )
        return reply, LAST_RUN.get() or {}

    async def stream():
        task = asyncio.create_task(run())
        meme = await memes.pick(request.message, user.id)
        if meme:
            yield json.dumps({"type": "meme", **meme}) + "\n"
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
                            + build_footer(info, seconds, window),
                        }
                    )
                    + "\n"
                )
                return
        except Exception as exc:  # noqa: BLE001
            task.cancel()
            yield json.dumps({"type": "error", "text": str(exc)}) + "\n"
        finally:
            free_slot(user.id)

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@router.post("/", response_model=ChatResponse)
async def process_message(
    request: ChatRequest,
    chain: RagChain = Depends(get_rag_chain),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_user),
):
    take_slot(user.id)
    try:
        session = await chats.get_or_create_session(db, user.id)
        window = await chats.get_history_window(db, session.id)

        started = time.monotonic()
        reply = await chain.generate_reply(
            request.message, window.messages, user.phone_number
        )
        seconds = time.monotonic() - started

        await chats.add_message(db, session.id, "user", request.message)
        await chats.add_message(db, session.id, "assistant", reply)

        run = LAST_RUN.get() or {}
        return ChatResponse(
            reply=build_tools_block(run) + reply + build_footer(run, seconds, window)
        )
    finally:
        free_slot(user.id)
