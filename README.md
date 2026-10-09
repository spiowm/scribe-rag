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

`agy` keeps its token in an OS keyring, so the container runs its own D-Bus +
gnome-keyring (see `backend/docker/entrypoint.sh`). The login is interactive, but needs no
browser on the server: `agy` prints a URL and waits for the authorization code, with a
60-second window — if it times out, just run the command again.

The backend must be **stopped** first. Two keyring daemons on one volume fight over the
same file, and `docker exec` into the running container does not work either: an exec
session has no `DBUS_SESSION_BUS_ADDRESS`, and pointing it at the socket by hand answers
`The connection is closed`.

**Locally:**

```bash
docker-compose stop backend
docker-compose run --rm --no-deps backend agy
docker-compose start backend
```

**On a Coolify server** the compose project belongs to Coolify and `/artifacts/<uuid>` is
removed after the build, so use `docker` directly. Note `-a`, which also lists stopped
containers:

```bash
BE=$(sudo docker ps -a --format '{{.Names}}' | grep '^backend-')
IMG=$(sudo docker inspect "$BE" --format '{{.Config.Image}}')
VOL=$(sudo docker inspect "$BE" --format '{{range .Mounts}}{{.Name}} {{end}}' \
      | tr ' ' '\n' | grep agy-home)

sudo docker stop "$BE"
sudo docker run --rm -it -e TERM=xterm-256color -v "$VOL:/home/app" "$IMG" agy
sudo docker start "$BE"
```

`-it` is required — without a TTY `agy` dies with `error opening TTY`. `TERM` matters too:
a terminal type the server does not know (e.g. `xterm-ghostty`) leaves the TUI unable to
draw itself.

Do not set `KEYRING_PW` unless you have a reason to. Left empty, the entrypoint generates
one on first start and keeps it in the volume, so the one-off login container picks up the
same password. If you do set it in the panel, the login container needs the same value
passed with `-e KEYRING_PW=...`, or it cannot unlock the keyring the backend created.

### When a re-login is needed

**Not on a schedule.** The stored token holds an `access_token` that expires in about an
hour and a `refresh_token` with no recorded lifetime; `agy` refreshes the first one by
itself and writes it back to the keyring. A Google refresh token for a published app has
no fixed expiry — it dies when revoked, when the account password changes, or after months
of disuse, which a running bot never reaches.

**Redeploys do not break it.** The image is rebuilt and the containers are recreated, but
the named volume holding the keyring stays. The login survives restarts and server reboots
too.

So re-login when it actually breaks, not preventively. The symptom is the footer under
each reply switching from `agy` to `api` — that is the Gemini API fallback doing its job.
Confirm in the log and repeat the login above:

```bash
sudo docker logs --tail 50 "$BE" 2>&1 | grep -i agy
```

What you lose until then is only money, not service: answers keep coming through the paid
API.

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
