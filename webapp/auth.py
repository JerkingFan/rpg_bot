"""Проверка Telegram initData для Mini App.

Данные из `Telegram.WebApp.initData` подписаны токеном бота. Клиенту
доверять нельзя, поэтому все запросы к API проходят через эту проверку.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram.utils.web_app import WebAppInitData, safe_parse_webapp_init_data

from config import BOT_TOKEN

logger = logging.getLogger(__name__)

MAX_AUTH_AGE_SECONDS = 24 * 60 * 60


def parse_init_data(
    init_data: str | None,
    *,
    token: str | None = None,
    max_age_seconds: int = MAX_AUTH_AGE_SECONDS,
) -> WebAppInitData | None:
    """Возвращает проверенные данные или None, если подпись/срок неверные."""
    if not init_data:
        return None

    try:
        data = safe_parse_webapp_init_data(token or BOT_TOKEN, init_data)
    except Exception:
        return None

    auth_date = data.auth_date
    if auth_date.tzinfo is None:
        auth_date = auth_date.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - auth_date).total_seconds()
    if max_age_seconds and age > max_age_seconds:
        return None
    return data
