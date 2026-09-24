FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# dbus + gnome-keyring — Secret Service, з якого agy бере токен підписки;
# libsecret-tools дає secret-tool для пересадки; util-linux — setpriv.
RUN apt-get update && apt-get install -y --no-install-recommends \
        dbus dbus-x11 gnome-keyring libsecret-tools util-linux \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 app

# Версію закріплено навмисно: офіційний інсталятор завжди тягне найсвіжішу,
# а поведінка між версіями мінялась (на 1.2.3 стрімінгу не було зовсім).
# Оновлення має бути свідомим кроком: узяти url і sha512 з
#   https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/linux_amd64.json
# і прогнати перевірку в контейнері.
#
# Кладемо в /usr/local/bin, а не в ~/.local/bin: домівка app — це том,
# і він перекрив би бінарник.
ARG AGY_VERSION=1.2.10
ARG AGY_URL=https://storage.googleapis.com/antigravity-public/antigravity-cli/1.2.10-4751581200121856/linux-x64/cli_linux_x64.tar.gz
ARG AGY_SHA512=e19aef599aaf47a9225e72f2cd420d5f97d6dd974e698685950557ca23a8bda63effe666773178c82912b98e9c197da29829b92e771eb6f66c82236f17864fee
RUN curl -fsSL -o /tmp/agy.tar.gz "$AGY_URL" \
    && echo "$AGY_SHA512  /tmp/agy.tar.gz" | sha512sum -c - \
    && tar xzf /tmp/agy.tar.gz -C /tmp \
    && mv /tmp/antigravity /usr/local/bin/agy \
    && chmod +x /usr/local/bin/agy \
    && rm /tmp/agy.tar.gz \
    && /usr/local/bin/agy --version 2>&1 | grep -qx "$AGY_VERSION"

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
