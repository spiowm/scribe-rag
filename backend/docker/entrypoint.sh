#!/bin/sh
set -e

if [ "$(id -u)" = "0" ]; then
    # agy вважає контейнером усе, де є /.dockerenv, і тоді йде у файлове
    # сховище токена, яке не читається (antigravity-cli#479).
    if [ -f /.dockerenv ]; then
        rm -f /.dockerenv
        echo "[entrypoint] /.dockerenv прибрано"
    fi

    chown app:app /home/app 2>/dev/null || true

    exec setpriv --reuid=app --regid=app --init-groups env HOME=/home/app "$0" "$@"
fi

# --- далі від імені app ---

# Хостової шини в контейнері немає, тому своя.
eval "$(dbus-launch --sh-syntax)"
export DBUS_SESSION_BUS_ADDRESS

# Свіжий том порожній: chain.py пише туди схему, agy запускається з
# AGY_WORKDIR як cwd. Без цих тек застосунок падає на старті.
mkdir -p "$HOME/.local/share/keyrings" "$AGY_HOME" "$AGY_WORKDIR"

# Пароль кіраїнга має бути однаковий між перезапусками, інакше токен agy
# доведеться вводити заново. Не заданий — робимо один раз і тримаємо в томі.
if [ -z "$KEYRING_PW" ]; then
    if [ ! -f "$HOME/.keyring-pw" ]; then
        head -c 32 /dev/urandom | base64 | tr -d '\n' > "$HOME/.keyring-pw"
        chmod 600 "$HOME/.keyring-pw"
        echo "[entrypoint] згенеровано пароль кіраїнга"
    fi
    KEYRING_PW=$(cat "$HOME/.keyring-pw")
fi

# --unlock сам створює колекцію login, якщо її ще немає.
 printf '%s' "$KEYRING_PW" | gnome-keyring-daemon --unlock --components=secrets >/dev/null 2>&1 \
    || echo "[entrypoint] gnome-keyring не розблокувався"
sleep 1

exec "$@"
