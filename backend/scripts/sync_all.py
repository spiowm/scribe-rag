"""Інкрементальний синк Notion і Drive та оновлення прав дисків.

Запуск у контейнері backend, наприклад як запланована задача:

    uv run --no-dev python scripts/sync_all.py

Кроки йдуть через внутрішній API backend, а не напряму в пайплайн: блокування
синку живе в процесі backend, і окремий процес розминувся б із синком,
запущеним із бота. Кожен крок виконується навіть після збою попереднього;
будь-який збій дає код виходу 1.
"""

import os
import sys

import httpx

BASE_URL = os.environ.get("SYNC_BASE_URL", "http://127.0.0.1:8000")


def main() -> int:
    headers = {"X-Internal-Token": os.environ.get("INTERNAL_API_TOKEN", "")}
    steps: list[tuple[str, dict | None]] = [
        ("/sync/notion", None),
        ("/sync/gdrive", None),
    ]
    admin = os.environ.get("ADMIN_TELEGRAM_ID", "").strip()
    if admin:
        steps.append(("/admin/drives/refresh", {"telegram_id": int(admin)}))
    else:
        print("ADMIN_TELEGRAM_ID не задано: права дисків не оновлюються")

    failed = False
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=None) as client:
        for path, body in steps:
            try:
                response = client.post(path, json=body)
            except httpx.HTTPError as e:
                print(f"{path}: {e!r}", flush=True)
                failed = True
                continue
            text = response.text
            if path == "/admin/drives/refresh" and response.is_success:
                text = f"дисків: {len(response.json().get('drives', []))}"
            print(f"{path}: {response.status_code} {text}", flush=True)
            failed |= response.is_error
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
