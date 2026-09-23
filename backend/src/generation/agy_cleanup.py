import asyncio
import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Стан однієї розмови agy розкиданий по чотирьох теках: сама база, робочі
# файли, лок присутності й анотації. Усі названі за id розмови.
STATE_DIRS = ("conversations", "brain", "presence", "annotations")


def clean_once(home: str, ttl_days: float) -> tuple[int, int]:
    """Видаляє стан розмов, старший за ttl_days. Вертає (скільки, скільки байтів)."""
    root = Path(home) / ".gemini" / "antigravity-cli"
    deadline = time.time() - ttl_days * 86400
    removed = freed = 0
    for name in STATE_DIRS:
        folder = root / name
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            try:
                if entry.stat().st_mtime > deadline:
                    continue
                if entry.is_dir():
                    freed += sum(
                        f.stat().st_size for f in entry.rglob("*") if f.is_file()
                    )
                    shutil.rmtree(entry)
                else:
                    freed += entry.stat().st_size
                    entry.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("прибирання agy: не вдалось %s (%s)", entry, exc)
    return removed, freed


async def cleanup_loop(home: str, ttl_days: float, every_hours: float) -> None:
    """Фонове прибирання: одразу на старті й далі раз на every_hours."""
    while True:
        removed, freed = await asyncio.to_thread(clean_once, home, ttl_days)
        if removed:
            logger.info(
                "прибирання agy: видалено %d, звільнено %.1f МБ",
                removed,
                freed / 1048576,
            )
        await asyncio.sleep(every_hours * 3600)
