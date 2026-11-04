import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DAILY_TASK_TIME = os.getenv("DAILY_TASK_TIME", "18:00")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Minsk")

# Bothost (и похожие хостинги) сохраняют между деплоями только папку /app/data
# и передают её в DATA_DIR. Всё остальное пересоздаётся из git.

def _persistent_data_dir() -> str:
    configured = os.getenv("DATA_DIR", "").strip()
    if configured:
        return configured
    # Если хостинг не передал DATA_DIR, но постоянный каталог уже существует —
    # используем его, чтобы база не оказалась в пересоздаваемом /app.
    candidate = Path("/app/data")
    if candidate.is_dir():
        return str(candidate)
    return ""


DATA_DIR = _persistent_data_dir()


def _resolve_database_url() -> str:
    """Возвращает URL базы, привязывая относительный SQLite-путь к DATA_DIR.

    Так база не теряется, даже если в панели хостинга остался старый
    `DATABASE_URL=sqlite+aiosqlite:///bot.db`: на постоянном диске она
    окажется в `/app/data/bot.db`.
    """
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        raw = "sqlite+aiosqlite:///bot.db"

    if DATA_DIR and raw.startswith("sqlite") and ":///" in raw:
        path_part = raw.split(":///", 1)[1]
        if path_part and path_part != ":memory:" and not path_part.startswith("/"):
            raw = f"sqlite+aiosqlite:///{Path(DATA_DIR) / path_part}"
    return raw


DATABASE_URL = _resolve_database_url()


def validate_config() -> None:
    """Падаем сразу и понятно, а не где-то в глубине aiogram."""
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан. Скопируй .env.example в .env и вставь токен от @BotFather."
        )
    if not ADMIN_IDS:
        raise RuntimeError(
            "ADMIN_IDS пуст. Укажи хотя бы один Telegram ID учителя в .env."
        )
