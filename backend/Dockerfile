FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --no-dev --frozen --no-install-project

COPY . .

EXPOSE 8000


CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8000"]
