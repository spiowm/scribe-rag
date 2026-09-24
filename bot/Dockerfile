FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --no-dev --frozen --no-install-project

COPY . .

CMD ["uv", "run", "python", "-m", "src.main"]
