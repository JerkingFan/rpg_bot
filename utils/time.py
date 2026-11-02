"""Единый источник времени для бота.

В БД храним naive UTC (так проще с SQLite), а все «человеческие» даты
(квест дня, стрик, расписание) считаем в часовом поясе из конфига.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from config import TIMEZONE

LOCAL_TZ = ZoneInfo(TIMEZONE)


def utcnow() -> datetime:
    """Naive UTC — для хранения в DateTime-колонках."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)


def local_today() -> date:
    return local_now().date()


def period_key(value: date | None = None) -> str:
    return (value or local_today()).isoformat()
