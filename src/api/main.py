from contextlib import asynccontextmanager

from fastapi import FastAPI
from llama_index.embeddings.gemini.base import GeminiEmbedding

from src.api.routers import sync
from src.config import settings
from src.connectors.notion import NotionConnector
from src.vectorstore.qdrant import QdrantRepository


@asynccontextmanager
async def lifespan(app: FastAPI):
    embedding_model = GeminiEmbedding(
        model_name=settings.GEMINI_EMBEDDING_MODEL,
        api_key=settings.GEMINI_API_KEY,
    )

    app.state.qdrant = QdrantRepository(
        url=settings.QDRANT_URL,
        collection_name=settings.QDRANT_COLLECTION_NAME,
        embedding_model=embedding_model,
    )
    await app.state.qdrant.ensure_collection()

    app.state.notion = NotionConnector(
        settings.NOTION_TOKEN,
        settings.NOTION_USERS_DB_ID,
    )

    yield

    await app.state.qdrant.qdrant.close()


app = FastAPI(
    title="Scribe RAG API",
    description="API for RAG",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(sync.router)


@app.get("/")
async def root():
    return {"message": "Scribe RAG API up!"}
