# scribe-rag

RAG assistant for **BEST Lviv** — answers members' questions in Telegram over the org's
knowledge base (Notion → Qdrant), with conversation memory in Postgres.

> **Continuation of [spiowm/n8n-infrastructure](https://github.com/spiowm/n8n-infrastructure).**
> That project prototyped the same assistant as a low-code n8n workflow. This one rebuilds it
> **from scratch in code** (Python: FastAPI + aiogram) for full control over the RAG pipeline,
> testability, and production deployment — no n8n.

Monorepo of **two independent [uv](https://docs.astral.sh/uv/) projects** (separate
`.venv`, `uv.lock`, `.env` each):

- **`backend/`** — FastAPI + RAG core (LlamaIndex, Gemini, Qdrant) + SQLAlchemy/Alembic.
- **`bot/`** — aiogram Telegram bot, a thin HTTP client to the backend.

## Prerequisites

- [uv](https://docs.astral.sh/uv/)
- Docker (Postgres + Qdrant) — run them before the backend.
- A Telegram bot token, a Gemini API key, a Notion token.

## Setup

Each service has its own env file. Copy and fill it:

```bash
cp backend/.env-example backend/.env   # GEMINI / NOTION / QDRANT / POSTGRES
cp bot/.env-example bot/.env            # BOT_TOKEN / BACKEND_URL
```

## Run

Run each service **from its own folder** (the package is `src`, imports resolve from cwd):

```bash
# backend (terminal 1) — needs Postgres + Qdrant up
cd backend && uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload   # http://localhost:8000


# bot (terminal 2) — needs the backend up
cd bot && uv run python -m src.main
```

## Develop

**Add a dependency** (from the relevant service folder):

```bash
cd backend && uv add <package>
```

**Database migrations** (Alembic, backend only):

```bash
cd backend
uv run alembic upgrade head                              # apply migrations
uv run alembic revision --autogenerate -m "<message>"    # create one after model changes
uv run alembic current                                   # show current revision
```

> After adding a new model, import it in `migrations/env.py` so autogenerate sees it,
> then review the generated file before `upgrade head`.

See `.notes/CONTEXT.md` for architecture, decisions, and the development roadmap.
