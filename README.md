<p align="center">
  <img src="docs/assets/scribe-avatar.png" width="96" height="96" alt="Scribe robot avatar">
</p>

<h1 align="center">Scribe</h1>

<p align="center">A Telegram assistant that answers questions from BEST Lviv members and links to its sources in Notion and Google Drive. Also available via MCP.</p>

<p align="center">
  <a href="https://github.com/spiowm/scribe-rag/actions/workflows/ci.yml"><img src="https://github.com/spiowm/scribe-rag/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white" alt="Python 3.12+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license"></a>
</p>

<p align="center"><a href="#demo">Demo</a> · <a href="#architecture">Architecture</a> · <a href="#evaluation">Evaluation</a> · <a href="#setup">Setup</a></p>

## Demo

Scribe answers questions about BEST Lviv in Ukrainian and links to the documents it used. The same knowledge base is available in Telegram and, through MCP, in assistants such as Claude.

<table>
  <tr>
    <th>Telegram</th>
    <th>Claude via MCP</th>
  </tr>
  <tr>
    <td valign="top"><img src="docs/assets/demo-telegram.webp" height="500" alt="Telegram exchange in Ukrainian: the bot searches the knowledge base, answers what BEST Lviv is and lists three source links"></td>
    <td valign="top"><img src="docs/assets/demo-mcp.webp" height="500" alt="Claude calls the BEST Lviv knowledge-base search tool and answers how BEST Training Week works, citing Notion pages"></td>
  </tr>
</table>

## Architecture

```mermaid
flowchart LR
    TG([Telegram member]) --> BOT[Telegram bot] --> API[Backend<br/>FastAPI]
    API <-->|tool calls| LLM[Answer model<br/>agy or Gemini API]
    MC([Claude or another<br/>MCP client]) -->|OAuth| MCP[MCP service]

    API --> T{{Shared tools<br/>search · read · directory<br/>limited to member's drives}}
    MCP --> T

    T --> MG[(MongoDB<br/>member directory)]
    T --> PG[(PostgreSQL<br/>document text)]
    T --> QD[(Qdrant<br/>chunk vectors)]

    SRC[Notion<br/>Google Drive] -->|sync: extract, OCR| PG
    PG -->|chunk, embed<br/>with Gemini| QD
```

The Telegram bot streams answers from the backend, which runs the answer model and executes its tool calls. MCP clients bring their own model and call the same tools through the OAuth-protected MCP service.

During sync, the backend extracts content from Notion and Google Drive, stores the text in PostgreSQL, splits it into chunks, embeds them with Gemini and indexes them in Qdrant. PostgreSQL also keeps Telegram chat history, linked user accounts and MCP OAuth data. MongoDB holds the read-only member directory.

Search in Telegram and MCP uses the drives available to the member. The Telegram command `/drives` shows their drive access, and `/help` lists the bot's commands. The bot answers only in private chats and ignores groups and channels, so replies and MCP sign-in codes stay with the member who asked. Disable group joining for the bot in BotFather with `/setjoingroups`.

- **Rare names.** A single query embedding drives semantic search and extra queries filtered to individual rare names within the member’s allowed sources. Each filter gives matching chunks their own candidate pool before the backend deduplicates and ranks them. [Search implementation](backend/src/vectorstore/qdrant.py#L86-L144)
- **Index updates.** A sync copies unchanged points into a new collection and embeds changed chunks. The live search alias switches after the replacement collection is populated and passes a size check. [Reindexing](backend/src/vectorstore/qdrant.py#L260-L280) · [Alias switch](backend/src/vectorstore/qdrant.py#L323-L333)

The stack uses FastAPI, aiogram, LlamaIndex, Qdrant, PostgreSQL and MongoDB. The assistant began as an n8n workflow before its retrieval and indexing moved into Python. [Original n8n project](https://github.com/spiowm/n8n-infrastructure)

### Agent tools

The answer model in Telegram and MCP clients call the same read-only tools. [Tool declarations](backend/src/generation/tools.py)

| Tool | What it does |
| --- | --- |
| `search_knowledge_base` | Semantic search over Notion and Drive, plus exact matching of rare names |
| `get_document` | Reads a whole document in parts when search fragments are not enough |
| `find_person` | Member profile by name, surname or Telegram username |
| `find_by_position` | Who holds or held a position, current and past |
| `query_members` | Counts and groups members by status, family, mentor or intake date |
| `who_am_i` | MCP only: the asking member's card and the drives they can search |

## Evaluation

Search is checked separately from answers. Each regression question has a known correct document, and a script runs the same search the bot uses and records the rank of that document among the returned documents. The bot puts all 25 returned fragments into the model context, so the main metric is whether the document appears in them at all (recall@25). MRR averages 1/rank, so 1.0 means the document always ranks first.

| Check (vector search, 21.11.2026) | Result |
| --- | --- |
| 57 regression questions, general knowledge base | recall@25 54/57, hit@5 53/57, MRR 0.856 |
| Same questions for a member with 18 drives (35k chunks) | recall@25 53/57, hit@5 53/57, MRR 0.837 |
| 183 known documents across 7 work drives | recall@25 167/183 with all drives, 172/183 with only their own drive |

The answer model was chosen on 8 cases with fixed search results, graded by hand: `gemini-4-hydrogen-low` answered 7 of 8 without remarks with a median of 13.6 s per answer. That is about as fast as Flash Low (5 of 8, 13.0 s) and more careful with weak evidence.

The sets are small and were written by the author, and some questions reuse words from the documents, so absolute ranks are somewhat optimistic. The numbers are most useful for comparing changes against each other.

The [regression script](backend/eval/retrieval.py) runs from `backend/` against an indexed Qdrant and embeds every question with the Gemini API. 52 of the 57 questions are in [`cases.json`](backend/eval/cases.json). The other five point to members' personal application letters, so they are kept out of the repository and passed as a second `--cases` file.

```bash
uv run --env-file ../.env python eval/retrieval.py --show-misses
```

## Setup

Clone the repository with Docker and Compose installed, then copy the environment template.

```bash
cp .env-example .env
```

Fill in the following values in `.env`. The template provides a local `MCP_PUBLIC_URL` and a host-development `BACKEND_URL`.

| Service | Required values |
| --- | --- |
| Telegram | `BOT_TOKEN` |
| Internal API | `INTERNAL_API_TOKEN` (shared by the backend and bot) |
| PostgreSQL | `POSTGRES_PASSWORD` |
| Notion | `NOTION_TOKEN` |
| Google Drive | `GOOGLE_DRIVE_CREDENTIALS_JSON` (base64-encoded service-account JSON) |
| Gemini | `GEMINI_API_KEY` |
| Member directory | `MONGO_URI` |
| Cloudflare R2 | `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` |

If you are using your own knowledge base or directory, supply its IDs and names as described in [Deployment](docs/deployment.md#source-configuration).

```bash
docker compose up -d --build
```

Compose applies database migrations through the backend service. The internal API is at `http://localhost:8000` and the local MCP endpoint is at `http://localhost:8077/mcp`. For a custom bot, set its MCP URL and username as described in [Deployment](docs/deployment.md#source-configuration). Members can then use `/mcp_login` in Telegram to connect their account.

The default Telegram answer provider is Antigravity CLI (`agy`). Sign in using the [container keyring procedure](docs/deployment.md#sign-in-to-agy). Gemini API fallback is disabled by default. Document embeddings, query embeddings and Drive OCR use the Gemini API independently of the answer provider.

After configuring the sources, export the same `INTERNAL_API_TOKEN` used by Compose in your shell and run the sync endpoints. Sync can consume Gemini API quota.

```bash
curl --fail-with-body -X POST -H "X-Internal-Token: $INTERNAL_API_TOKEN" http://localhost:8000/sync/notion
curl --fail-with-body -X POST -H "X-Internal-Token: $INTERNAL_API_TOKEN" http://localhost:8000/sync/gdrive
```

For host development with `uv`, Coolify configuration and service troubleshooting, see [Deployment](docs/deployment.md).
