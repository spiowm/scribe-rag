# scribe-rag

RAG assistant for **BEST Lviv** — answers members' questions in Telegram over the org's
knowledge base (Notion + Google Drive → Qdrant) and member directory (MongoDB, read-only),
with conversation history in Postgres.

> **Continuation of [spiowm/n8n-infrastructure](https://github.com/spiowm/n8n-infrastructure).**
> That project prototyped the same assistant as a low-code n8n workflow. This one rebuilds it
> **from scratch in code** (Python: FastAPI + aiogram) for full control over the RAG pipeline,
> testability, and production deployment — no n8n.

Monorepo of **two independent [uv](https://docs.astral.sh/uv/) projects** (separate `.venv`
and `uv.lock` each), sharing **one `.env` at the repo root**:

- **`backend/`** — FastAPI + RAG core (LlamaIndex, Gemini, Qdrant) + SQLAlchemy/Alembic.
- **`bot/`** — aiogram Telegram bot, a thin HTTP client to the backend.

## Prerequisites

- [uv](https://docs.astral.sh/uv/)
- Docker or Podman with Compose — for Postgres, Qdrant, and the full stack.
- A Telegram bot token, a Gemini API key, a Notion token, a Google service-account JSON,
  a MongoDB URI.

## Setup

One env file for everything — Compose reads it for `${VAR}` substitution, and both services
read it when run on the host:

```bash
cp .env-example .env
```

Only six values are required; everything else has working defaults in `backend/src/config.py`.

## Run

### Local development

Storages in containers, apps on the host (hot reload, and `agy` uses your own keyring):

```bash
docker-compose up -d postgres qdrant

cd backend && uv run dev.py                              # http://localhost:8000
cd bot && uv run --env-file ../.env python -m src.main    # separate terminal
```

Postgres (5432) and Qdrant (6333/6334) are published on `127.0.0.1` only — Qdrant has no
auth of its own, so it must not be reachable from the network.

### Full stack

```bash
docker-compose up -d --build
```

The backend image runs `alembic upgrade head` on start. It must stay a **single uvicorn
worker**: the per-user request slot and the `agy` semaphore are in-process state.

## The LLM: agy, with the Gemini API as fallback

By default (`LLM_PROVIDER=agy`) answers come from the **Antigravity CLI** (`agy`) running as a
subprocess, which uses a Google AI Pro subscription instead of paid API calls. The model is
pinned in `AGY_MODEL`; the `agy` binary itself is pinned by version **and sha512** in
`backend/Dockerfile`.

If `agy` fails for any reason, the chain falls back to the Gemini API automatically. The
footer under each reply says which one answered:

```
agy | gemini-3.7-flash-medium | 17s | history 10
```

`api` there means the fallback ran — most often because `agy` is not logged in yet.

### Logging in inside the container

`agy` keeps its token in an OS keyring, so the container runs its own D-Bus + gnome-keyring
(see `backend/docker/entrypoint.sh`). Log in once; the token lives in the `agy_home` volume
and `agy` refreshes it on its own afterwards:

```bash
docker-compose stop backend
docker-compose run --rm --no-deps backend agy    # prints a URL, paste the code back
docker-compose start backend
```

Stopping the backend first matters: two keyring daemons on one volume is asking for trouble.
Over SSH `agy` uses a paste-the-code flow, so no browser is needed on the server.

## Indexing

The knowledge base is built by two endpoints (incremental — unchanged documents are carried
over):

```bash
curl -X POST localhost:8000/sync/notion
curl -X POST localhost:8000/sync/gdrive
```

Embeddings are billed through `GEMINI_API_KEY`, so prefer moving the `qdrant_data` volume
between machines over re-indexing:

```bash
podman volume export scribe-rag_qdrant_data -o qdrant.tar
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

**Lint the container layer:**

```bash
hadolint backend/Dockerfile bot/Dockerfile
shellcheck backend/docker/entrypoint.sh
```

## Deployment (Coolify)

Coolify clones the repo, so the `.env` file is not there — define the six variables in its
panel instead. Compose declares each of them as `${VAR}` under `environment:`, which is what
makes them show up in the UI and reach the containers; `env_file:` is deliberately not used,
since Coolify replaces it with its own generated file.

After the first deploy, verify the variables actually landed **in the container**, not just in
the YAML:

```bash
docker exec scribe-rag-backend-1 printenv | grep -cE "NOTION|MONGO|GEMINI"
```

Then transfer the `qdrant_data` volume and log `agy` in as above.

> Never mount a **named** volume with `:Z` — it relabels it for one container only and locks
> every other one out. Use `:z` or no flag. Details in `.notes/research/agy-in-container.md`.

---

See `.notes/CONTEXT.md` for architecture and decisions, `.notes/plans/` for the roadmap, and
`.notes/research/` for measured findings about `agy`, containers, and Telegram rendering.
