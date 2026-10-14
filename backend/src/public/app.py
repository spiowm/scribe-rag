import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.server.transport_security import TransportSecuritySettings

from src.config import settings
from src.connectors.mongo import MongoConnector
from src.db import engine
from src.public import login
from src.public.mcp_server import mcp
from src.public.oauth import provider
from src.vectorstore.qdrant import QdrantRepository

logging.basicConfig(level=logging.INFO)
PUBLIC_HOST = urlparse(settings.PUBLIC_URL).netloc
STATIC = Path(__file__).parent / "static"


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
    transport_security=TransportSecuritySettings(
        allowed_hosts=[PUBLIC_HOST, "127.0.0.1:*", "localhost:*"],
        allowed_origins=[
            settings.PUBLIC_URL,
            "http://127.0.0.1:*",
            "http://localhost:*",
        ],
    ),
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


# Логотип конектора. Gemini і claude.ai беруть іконку домену, а без неї
# підставляли 16-піксельний logo.png з best-lviv.org.ua, розмитий на збільшенні.
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC / "favicon.ico")


@app.get("/icon.png", include_in_schema=False)
async def icon():
    return FileResponse(STATIC / "icon.png")


@app.get("/", include_in_schema=False)
async def index():
    # Сервіси фавіконок читають іконку з <link> на головній сторінці.
    return HTMLResponse(
        '<!doctype html><html lang="uk"><head><meta charset="utf-8">'
        "<title>BEST Lviv — MCP</title>"
        '<link rel="icon" type="image/png" sizes="512x512" href="/icon.png">'
        '<link rel="apple-touch-icon" href="/icon.png">'
        "</head><body><p>MCP-сервер бази знань BEST Lviv. Адреса для "
        f"підключення: <code>{settings.PUBLIC_URL}/mcp</code></p></body></html>"
    )


app.include_router(login.router)

app.mount("/", mcp_app)
