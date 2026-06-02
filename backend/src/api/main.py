import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from llama_index.embeddings.gemini.base import GeminiEmbedding
from llama_index.llms.gemini import Gemini

from src.api.routers import chat, sync
from src.config import settings
from src.connectors.notion import NotionConnector
from src.generation.chain import RagChain
from src.vectorstore.qdrant import QdrantRepository

logging.basicConfig(level=logging.DEBUG)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.notion = NotionConnector(
        settings.NOTION_TOKEN,
        settings.NOTION_USERS_DB_ID,
    )

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

    llm = Gemini(
        model_name=settings.GEMINI_LLM_MODEL,
        api_key=settings.GEMINI_API_KEY,
    )

    app.state.rag_chain = RagChain(
        llm=llm,
        qdrant=app.state.qdrant,
    )

    yield

    await app.state.qdrant.qdrant_client.close()


app = FastAPI(
    title="Scribe RAG API",
    description="API for RAG",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(sync.router)
app.include_router(chat.router)


@app.get("/")
async def root():
    return {"message": "Scribe RAG API up!"}
