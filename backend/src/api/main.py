import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from google import genai
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding

from src.api.dependencies import internal_only
from src.api.routers import admin, auth, chat, drives, sync
from src.config import settings
from src.connectors.gdrive import GDriveConnector
from src.connectors.mongo import MongoConnector
from src.connectors.notion import NotionConnector
from src.db import engine
from src.generation import agy_cleanup, models_check
from src.generation.chain import RagChain
from src.vectorstore.memes import MemeRepository
from src.vectorstore.qdrant import QdrantRepository

logging.basicConfig(level=logging.INFO)

# Без них не працюють конектори backend. У Settings вони необовʼязкові, бо
# публічному MCP-сервісу не потрібні, тож перевірка тут, перед створенням
# конекторів.
BACKEND_SECRETS = (
    "NOTION_TOKEN",
    "GOOGLE_DRIVE_CREDENTIALS_JSON",
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = [name for name in BACKEND_SECRETS if not getattr(settings, name)]
    if missing:
        raise RuntimeError(f"Backend не стартує: не задано {', '.join(missing)}")

    app.state.notion = NotionConnector(
        settings.NOTION_TOKEN,
        settings.NOTION_USERS_DB_ID,
    )

    embedding_model = GoogleGenAIEmbedding(
        model_name=settings.GEMINI_EMBEDDING_MODEL,
        api_key=settings.GEMINI_API_KEY,
        num_workers=2,
        embed_batch_size=10,
        retries=6,
        retry_min_seconds=2,
        retry_max_seconds=60,
    )

    app.state.qdrant = QdrantRepository(
        url=settings.QDRANT_URL,
        collection_name=settings.QDRANT_COLLECTION_NAME,
        embedding_model=embedding_model,
    )
    await app.state.qdrant.ensure_collection()

    app.state.memes = MemeRepository(
        client=app.state.qdrant.qdrant_client,
        collection=settings.QDRANT_MEMES_COLLECTION,
        embedding_model=embedding_model,
    )
    await app.state.memes.warm()

    app.state.mongo = MongoConnector(
        settings.MONGO_URI,
        settings.MONGO_DB,
        settings.MONGO_USERS_COLLECTION,
    )

    app.state.gdrive = GDriveConnector(settings.GOOGLE_DRIVE_CREDENTIALS)

    app.state.gemini = genai.Client(api_key=settings.GEMINI_API_KEY)

    app.state.rag_chain = RagChain(
        client=app.state.gemini,
        model=settings.GEMINI_LLM_MODEL,
        qdrant=app.state.qdrant,
        mongo=app.state.mongo,
    )

    if not settings.INTERNAL_API_TOKEN:
        logging.getLogger(__name__).warning(
            "INTERNAL_API_TOKEN не задано: внутрішній API відкритий будь-кому з доступом до порту"
        )

    # Не чекає на старті: `agy models` іде кілька секунд, а застосунок
    # потрібен і без нього (прив'язка, адмінка, синк).
    models_task = asyncio.create_task(
        models_check.check_loop(app.state.gemini, hours=6)
    )

    cleanup_task = None
    if settings.LLM_PROVIDER == "agy":
        cleanup_task = asyncio.create_task(
            agy_cleanup.cleanup_loop(
                settings.AGY_HOME,
                settings.AGY_STATE_TTL_DAYS,
                settings.AGY_CLEANUP_HOURS,
            )
        )

    yield

    models_task.cancel()
    if cleanup_task:
        cleanup_task.cancel()
    await engine.dispose()
    await app.state.qdrant.qdrant_client.close()
    await app.state.mongo.close()
    await app.state.gdrive.close()


app = FastAPI(
    title="Scribe RAG API",
    description="API for RAG",
    version="1.0.0",
    lifespan=lifespan,
)

# Усе, крім кореня (перевірка, що сервіс живий), — лише з секретом бота.
internal = [Depends(internal_only)]
app.include_router(sync.router, dependencies=internal)
app.include_router(chat.router, dependencies=internal)
app.include_router(auth.router, dependencies=internal)
app.include_router(admin.router, dependencies=internal)
app.include_router(drives.router, dependencies=internal)


@app.get("/")
async def root():
    return {"message": "Scribe RAG API up!"}
