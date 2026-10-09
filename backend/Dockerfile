FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# dbus + gnome-keyring — Secret Service, з якого agy бере токен підписки;
# libsecret-tools дає secret-tool для пересадки; util-linux — setpriv.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
        dbus dbus-x11 gnome-keyring libsecret-tools util-linux \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 app

# BuildKit підставляє TARGETARCH сам: amd64 або arm64.
ARG TARGETARCH
ARG AGY_VERSION=1.2.10
ARG AGY_URL_AMD64=https://storage.googleapis.com/antigravity-public/antigravity-cli/1.2.10-4751581200121856/linux-x64/cli_linux_x64.tar.gz
ARG AGY_SHA512_AMD64=e19aef599aaf47a9225e72f2cd420d5f97d6dd974e698685950557ca23a8bda63effe666773178c82912b98e9c197da29829b92e771eb6f66c82236f17864fee
ARG AGY_URL_ARM64=https://storage.googleapis.com/antigravity-public/antigravity-cli/1.2.10-4751581200121856/linux-arm/cli_linux_arm64.tar.gz
ARG AGY_SHA512_ARM64=105fee8989456e4cccd444bd34a7294c3315fc8e3bd3d94022e90053532be02a84375546da78758d6dcf2f4955c7d333ccbd4cd6f8b86c18e47667493d35179f
RUN set -eu; \
    echo "agy $AGY_VERSION для TARGETARCH=${TARGETARCH:-невідомо}"; \
    case "${TARGETARCH:-amd64}" in \
        amd64) url="$AGY_URL_AMD64"; sum="$AGY_SHA512_AMD64" ;; \
        arm64) url="$AGY_URL_ARM64"; sum="$AGY_SHA512_ARM64" ;; \
        *) echo "немає збірки agy для $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 3 -o /tmp/agy.tar.gz "$url"; \
    echo "$sum  /tmp/agy.tar.gz" | sha512sum -c -; \
    tar xzf /tmp/agy.tar.gz -C /tmp; \
    install -m 755 /tmp/antigravity /usr/local/bin/agy; \
    rm /tmp/agy.tar.gz; \
    agy --version | grep -qx "$AGY_VERSION" \
        || { echo "agy не запустився або версія інша:" >&2; agy --version >&2 || true; exit 1; }

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project

COPY . .

ENV HOME=/home/app \
    PATH=/app/.venv/bin:$PATH \
    AGY_BIN=/usr/local/bin/agy \
    AGY_HOME=/home/app/.agy-bot \
    AGY_WORKDIR=/home/app/.agy-bot-ws \
    AGY_CLI_DISABLE_AUTO_UPDATE=1 \
    LANG=C.UTF-8 \
    TZ=Europe/Kyiv

EXPOSE 8000

# Стартуємо root-ом: ентрипойнт прибирає /.dockerenv і сам переходить на app.
ENTRYPOINT ["/app/docker/entrypoint.sh"]
# uvicorn в один процес: BUSY і AGY_SEMAPHORE живуть у памʼяті процесу.
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 1"]
