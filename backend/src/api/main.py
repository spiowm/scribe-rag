import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from google import genai
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding

from src.api.routers import auth, chat, sync
from src.config import settings
from src.connectors.gdrive import GDriveConnector
from src.connectors.mongo import MongoConnector
from src.connectors.notion import NotionConnector
from src.db import engine
from src.generation.chain import RagChain
from src.vectorstore.qdrant import QdrantRepository

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
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

    app.state.mongo = MongoConnector(
        settings.MONGO_URI,
        settings.MONGO_DB,
        settings.MONGO_USERS_COLLECTION,
    )

    app.state.gdrive = GDriveConnector(
        settings.GOOGLE_DRIVE_CREDENTIALS,
        settings.GOOGLE_DRIVE_ROOTS,
    )

    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    app.state.rag_chain = RagChain(
        client=client,
        model=settings.GEMINI_LLM_MODEL,
        qdrant=app.state.qdrant,
        mongo=app.state.mongo,
    )

    yield

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

app.include_router(sync.router)
app.include_router(chat.router)
app.include_router(auth.router)


@app.get("/")
async def root():
    return {"message": "Scribe RAG API up!"}
