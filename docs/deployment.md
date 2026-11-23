# Deployment

The [README](../README.md#setup) lists the variables required by [Compose](../docker-compose.yaml). The backend, Telegram bot and public MCP endpoint run as separate services. The backend owns database migrations and uses one Uvicorn worker because its per-user request slots and the agy concurrency limit live in process memory.

## Source configuration

Set the Drive source IDs and any group mappings through Compose or in the environment of the corresponding host processes. Notion sync uses the pages visible to the integration token and any databases configured for table ingestion. Give the Google service account access to the selected Drive folders.

| Setting | Services | Value |
| --- | --- | --- |
| `GOOGLE_DRIVE_GENERAL_ID` | Backend | Shared drive indexed as general |
| `GOOGLE_DRIVE_GENERAL_FOREIGN` | Backend | Comma-separated IDs of other drives whose files can be indexed as general through shortcuts on the general drive |
| `GOOGLE_DRIVE_IDS` | Backend, MCP | Comma-separated IDs for additional work drives |
| `GOOGLE_DRIVE_GENERAL_FOREIGN_LABEL` | Backend | Optional display label for the additional general sources |
| `GOOGLE_DRIVE_FULL_MEMBERS_GROUP` | Backend, MCP | Optional Drive group granted by Full or Alumni status |
| `GOOGLE_DRIVE_ADMIN_GROUP` | Backend, MCP | Optional infrastructure group omitted from access suggestions |
| `GOOGLE_DRIVE_PAUSED_IDS` | Backend, MCP | Comma-separated work drive IDs to suspend |
| `NOTION_DATABASE_IDS` | Backend | Comma-separated Notion databases to index as tables, optionally using `id:relation-property` to group rows |
| `MONGO_DB`, `MONGO_USERS_COLLECTION` | Backend, MCP | Database and collection for member lookups |
| `R2_BUCKET` | Backend | Bucket used to serve meme images |
| `PUBLIC_URL` | MCP | Public MCP base URL. The service gets it from `MCP_PUBLIC_URL` in the checked-in Compose file. |
| `TELEGRAM_BOT_USERNAME` | MCP | Username of the Telegram bot that issues MCP login codes |
| `MCP_URL` | Bot | Public URL including `/mcp` |

Use a MongoDB account with read-only access to the member directory. Keep PostgreSQL, Qdrant and the internal backend on loopback or a trusted private network. The backend also checks `X-Internal-Token`. Generate a shared value for the backend and bot with `openssl rand -hex 32`. Expose only the OAuth-protected MCP service to external clients.

Set `GOOGLE_DRIVE_GENERAL_ID` before syncing Drive. Indexed drive access depends on the configured group mappings and the permissions returned by Drive. Review those settings before syncing work drives. Keep the full intended source list during sync, since removing a source from the configuration can remove its indexed documents.

To pause a work drive, keep its ID in `GOOGLE_DRIVE_IDS` and add it to `GOOGLE_DRIVE_PAUSED_IDS` for both the backend and MCP. The services hide that drive from lookup and settings while sync preserves its existing extracted text and vectors. Remove the ID from the pause list and run an incremental sync to bring the drive up to date. The common sources use a shared scope and require separate configuration.

## Coolify

1. Create a Docker Compose application pointed at `docker-compose.yaml`. Add the required values from the [environment template](../.env-example) as runtime variables in Coolify. Set `MCP_PUBLIC_URL` to the HTTPS origin assigned to the `mcp` service, without a trailing slash. Route that domain to port 8000 on the MCP container. [Coolify environment variables](https://coolify.io/docs/applications/configuration/environment-variables)
2. Keep persistent storage for PostgreSQL, Qdrant and the agy home directory. If restoring an existing deployment, restore PostgreSQL alongside Qdrant and verify that the configured alias points to the restored collection.
3. Check the backend startup logs for successful migrations. Verify the expected environment variable names without printing secret values. Check `/health` on the MCP origin and complete a Telegram sign-in before connecting your MCP client.

The checked-in Compose file passes through the settings it lists under `environment`. Add other overrides under the service that consumes them, or confirm Coolify has injected them into that service. Coolify also adds an `env_file` with all application variables to every service. Values under `environment` take precedence, so the Compose file sets the secrets that the public `mcp` service and the `bot` service do not use to the placeholder `unused`. An empty string would not work: Coolify replaces an empty value with the application variable of the same name. Keep that list in sync when adding a secret. Use `AGY_MODEL` to select the chat model. Set `AGY_FALLBACK_TO_API=true` only to opt in to Gemini API fallback. The separate `LLM_PROVIDER=gemini` setting selects Gemini API answers directly. [Backend settings](../backend/src/config.py)

Review the application's deployment policy before pushing changes to its watched branch. [Coolify automatic deployments](https://coolify.io/docs/applications/deployments/automatic-deployments)

### Scheduled sync

The backend syncs Notion and Drive and then refreshes drive permissions on its own schedule. The bot owner sets the interval with `/autosync <hours>` and turns it off with `/autosync off`; `/autosync` alone shows the interval and the result of the last run. Auto-sync is off until the owner sets an interval. The interval and the time of the last run are stored in Postgres, so a redeploy does not start an extra sync. Sync is incremental: it extracts changed files and embeds only changed documents. [Auto-sync](../backend/src/ingestion/autosync.py)

## Sign in to agy

The [backend entrypoint](../backend/docker/entrypoint.sh) starts D-Bus and gnome-keyring. The keyring lives in the agy home volume. Stop the backend before starting the interactive container so a single keyring daemon can access the volume during sign-in.

For a local Compose deployment, run the following commands from the repository root.

```bash
docker compose stop backend
docker compose run --rm -e TERM=xterm-256color --no-deps backend agy
docker compose start backend
```

Open the URL printed by agy and enter the authorization code into the terminal. Start the backend again after the login command exits.

On the Coolify host, find the exact backend container with `docker ps -a`. Replace the example name below with that container name. The commands use its deployed image and the volume mounted at `/home/app`.

```bash
BE='replace-with-backend-container-name'
IMG=$(docker inspect --format '{{.Config.Image}}' "$BE")
VOL=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/home/app"}}{{.Name}}{{end}}{{end}}' "$BE")
```

Once both values identify the expected running image and named volume, sign in interactively.

```bash
docker stop "$BE"
docker run --rm -it -e TERM=xterm-256color -v "$VOL:/home/app" "$IMG" agy
docker start "$BE"
```

The entrypoint generates a keyring password and stores it in the volume when `KEYRING_PW` is empty. If the deployment sets an explicit password, pass the same `KEYRING_PW` to the one-off container through a protected env file. On SELinux hosts, use shared labeling (`:z`) if relabeling is needed.

On startup the backend also writes `.gemini/antigravity-cli/settings.json` in the agy home. It adds deny rules for shell commands, file access and URL tools and sets `telemetryEnabled` to false, keeping any other keys. agy 1.2.10 has no rule for its built-in web search, so `search_web` stays available. The setting is not a substitute for the data-usage settings of the Antigravity account. [Settings writer](../backend/src/generation/agy_process.py)

If agy answers fail, check the backend logs for authentication errors and repeat sign-in if needed. The home volume preserves the keyring across container replacements. The reply footer shows the answering provider when a response succeeds.

## Host development

Install [uv](https://docs.astral.sh/uv/) and run the storage services from the root. Use separate terminals for the backend and bot commands.

```bash
docker compose up -d --wait postgres qdrant
```

```bash
cd backend
uv run --env-file ../.env alembic upgrade head
uv run dev.py
```

```bash
cd bot
uv run --env-file ../.env python -m src.main
```

For host-side agy, install the CLI and sign in with the same home directory that the backend uses for the subprocess. See `AGY_HOME` in [backend settings](../backend/src/config.py) and subprocess environment setup in [agy process](../backend/src/generation/agy_process.py).
