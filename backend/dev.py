from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    # Змінні з того самого .env, з якого їх читає compose. Знання про корінь
    # монорепозиторію живе тут, у dev-скрипті, а не в src/config.py.
    uvicorn.run("src.api.main:app", reload=True, env_file=ROOT / ".env")
