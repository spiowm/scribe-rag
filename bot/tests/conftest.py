import os

# Обробники читають налаштування під час імпорту, а `.env` у тестах немає.
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("BACKEND_URL", "http://backend")
