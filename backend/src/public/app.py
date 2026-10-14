import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)

from src.config import settings
from src.connectors.mongo import MongoConnector
from src.db import engine
from src.public import login
from src.public.mcp_server import mcp
from src.public.oauth import provider
from src.vectorstore.qdrant import QdrantRepository

logging.basicConfig(level=logging.INFO)

mcp_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    auth=AuthSettings.model_validate(
        {
            "issuer_url": settings.PUBLIC_URL,
            "resource_server_url": f"{settings.PUBLIC_URL}/mcp",
            "validate_token_resource": True,
            "client_registration_options": ClientRegistrationOptions(enabled=True),
            "revocation_options": RevocationOptions(enabled=True),
        }
    ),
    auth_server_provider=provider,
    token_verifier=ProviderTokenVerifier(provider),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    embedding_model = GoogleGenAIEmbedding(
        model_name=settings.GEMINI_EMBEDDING_MODEL,
        api_key=settings.GEMINI_API_KEY,
    )
    app.state.qdrant = QdrantRepository(
        url=settings.QDRANT_URL,
        collection_name=settings.QDRANT_COLLECTION_NAME,
        embedding_model=embedding_model,
    )
    app.state.mongo = MongoConnector(
        settings.MONGO_URI,
        settings.MONGO_DB,
        settings.MONGO_USERS_COLLECTION,
    )

    mcp_app.state = app.state
    async with mcp.session_manager.run():
        yield

    await engine.dispose()
    await app.state.qdrant.qdrant_client.close()
    await app.state.mongo.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health():
    return {"ok": True}


app.include_router(login.router)

app.mount("/", mcp_app)
