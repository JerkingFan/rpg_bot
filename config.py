import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot.db")
DAILY_TASK_TIME = os.getenv("DAILY_TASK_TIME", "18:00")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Minsk")


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
