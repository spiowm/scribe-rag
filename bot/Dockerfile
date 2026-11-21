FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --no-dev --frozen --no-install-project

COPY . .

# --no-dev: інакше `uv run` доставив би dev-групу (pytest) на старті контейнера.
CMD ["uv", "run", "--no-dev", "python", "-m", "src.main"]
