#!/bin/sh
# Дві фази: root прибирає міну й віддає секрет користувачеві, далі все під app.
set -e

if [ "$(id -u)" = "0" ]; then
    # agy визначає контейнер ЄДИНОЮ ознакою — файлом /.dockerenv, — і тоді
    # перемикається на файлове сховище токена, яке зламане: пише, але не читає
    # (antigravity-cli#479). podman цього файлу не створює, Docker — створює.
    if [ -f /.dockerenv ]; then
        rm -f /.dockerenv
        echo "[entrypoint] /.dockerenv прибрано: інакше agy не побачив би кіраїнг"
    fi

    # Секрет читаємо ще root-ом: після переходу на app монтований файл може
    # стати недоступним через мапінг uid у rootless-режимі.
    if [ -f /run/secrets/agy-token ]; then
        cp /run/secrets/agy-token /home/app/.agy-token-seed
        chown app:app /home/app/.agy-token-seed
        chmod 600 /home/app/.agy-token-seed
    fi
    chown app:app /home/app 2>/dev/null || true

    exec setpriv --reuid=app --regid=app --init-groups env HOME=/home/app "$0" "$@"
fi

# --- далі від імені app ---

# Своя сесійна шина: у контейнері хостової немає.
eval "$(dbus-launch --sh-syntax)"
export DBUS_SESSION_BUS_ADDRESS

# Кіраїнг, розблокований без графічного запиту. --unlock сам створює
# колекцію login, якщо її ще немає.
mkdir -p "$HOME/.local/share/keyrings"

# Пароль кіраїнга: не заданий — створюємо один раз і тримаємо поруч, у томі.
# Окремої цінності він не має (захищає сховище всередині контейнера, а хто
# дістався тому, дістався і його), важливо лише, щоб між перезапусками він був
# той самий — інакше кіраїнг не відкриється й доведеться логінитись заново.
if [ -z "$KEYRING_PW" ]; then
    if [ ! -f "$HOME/.keyring-pw" ]; then
        head -c 32 /dev/urandom | base64 | tr -d '\n' > "$HOME/.keyring-pw"
        chmod 600 "$HOME/.keyring-pw"
        echo "[entrypoint] згенеровано пароль кіраїнга"
    fi
    KEYRING_PW=$(cat "$HOME/.keyring-pw")
fi

if [ -n "$KEYRING_PW" ]; then
    echo -n "$KEYRING_PW" | gnome-keyring-daemon --unlock --components=secrets >/dev/null 2>&1 \
        || echo "[entrypoint] gnome-keyring не розблокувався"
    sleep 1
fi

# Одноразова пересадка токена. Далі він живе в кіраїнгу (том), і agy сам
# освіжає його через refresh_token, тому секрет потрібен лише при першому запуску.
if [ -f "$HOME/.agy-token-seed" ]; then
    if secret-tool lookup service gemini username antigravity >/dev/null 2>&1; then
        echo "[entrypoint] токен у кіраїнгу вже є, пересадка не потрібна"
    else
        secret-tool store --label="Password for 'antigravity' on 'gemini'" \
            service gemini username antigravity < "$HOME/.agy-token-seed"
        echo "[entrypoint] токен пересаджено в кіраїнг"
    fi
    rm -f "$HOME/.agy-token-seed"
fi

exec "$@"
