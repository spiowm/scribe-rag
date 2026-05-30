from fastapi import FastAPI

from src.api.routers import sync

app = FastAPI(
    title="Scribe RAG API",
    description="API for RAG",
    version="1.0.0",
)

app.include_router(sync.router)


@app.get("/")
async def root():
    return {"message": "Scrive RAG API up!"}
