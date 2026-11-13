"""
Конфигурация автономного трекера цен FazerCards.
Загружает параметры из файла .env в корневой директории скрипта.
"""

import os
from typing import List
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

# Загружаем переменные из .env
load_dotenv(ENV_PATH)

# Токен Telegram-бота
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()

# Telegram ID получателя уведомлений (поддерживает список через запятую)
_raw_chat_id = os.getenv("CHAT_ID", "").strip()
CHAT_IDS: List[int] = []
if _raw_chat_id:
    for item in _raw_chat_id.replace(";", ",").split(","):
        item = item.strip()
        if item.lstrip("-").isdigit():
            CHAT_IDS.append(int(item))

# Авторизация FazerCards MCP API
FAZERCARDS_API_KEY: str = (
    os.getenv("FAZERCARDS_API_KEY") or os.getenv("FAZER_API_KEY") or ""
).strip()

FAZERCARDS_API_URL: str = (
    os.getenv("FAZERCARDS_API_URL") or os.getenv("FAZER_API_URL") or ""
).strip()

# Минимальный порог изменения цены в долларах для отправки уведомления
try:
    MIN_DIFF: float = float(os.getenv("MIN_DIFF", "0.05"))
except ValueError:
    MIN_DIFF = 0.05

# Интервал проверки в секундах
try:
    CHECK_INTERVAL: int = int(os.getenv("CHECK_INTERVAL", "60"))
except ValueError:
    CHECK_INTERVAL = 60

# Фильтр по ключевым словам (через запятую)
_raw_keywords = os.getenv("FILTER_KEYWORDS", "").strip()
FILTER_KEYWORDS: List[str] = [
    kw.strip() for kw in _raw_keywords.split(",") if kw.strip()
]

# Путь к локальному файлу кэша предыдущих цен
CACHE_FILE: str = os.path.join(BASE_DIR, "prices_cache.json")


def validate_config() -> List[str]:
    """
    Проверяет корректность заполнения настроек в .env.
    Возвращает список сообщений об ошибках (пустой список, если всё в порядке).
    """
    errors = []
    if not BOT_TOKEN:
        errors.append("Не указан BOT_TOKEN в файле .env (получите у @BotFather).")
    elif ":" not in BOT_TOKEN:
        errors.append("BOT_TOKEN имеет некорректный формат.")

    if not CHAT_IDS:
        errors.append("Не указан CHAT_ID в файле .env (узнайте свой ID у @userinfobot).")

    if not FAZERCARDS_API_KEY:
        errors.append("Не указан FAZERCARDS_API_KEY в файле .env.")

    if not FAZERCARDS_API_URL:
        errors.append("Не указан FAZERCARDS_API_URL в файле .env (например: https://api.fzr.cards/mcp/ВАШ_SLUG).")
    elif not FAZERCARDS_API_URL.startswith("http"):
        errors.append("FAZERCARDS_API_URL должен начинаться с https:// или http://")

    if MIN_DIFF <= 0:
        errors.append("MIN_DIFF должен быть положительным числом (например, 0.05).")

    if CHECK_INTERVAL < 5:
        errors.append("CHECK_INTERVAL слишком мал (рекомендуется не менее 15-60 секунд).")

    return errors
