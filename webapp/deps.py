"""Общие зависимости FastAPI: проверка Telegram и роли."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from config import ADMIN_IDS, WEBAPP_DEV_MODE
from database.base import async_session
from database.models import User
from webapp.auth import parse_init_data


async def current_user_id(
    x_telegram_init_data: Annotated[str | None, Header()] = None,
    x_dev_user_id: Annotated[str | None, Header()] = None,
) -> int:
    if x_telegram_init_data:
        data = parse_init_data(x_telegram_init_data)
        if data and data.user:
            return data.user.id
        raise HTTPException(status_code=401, detail="Неверные данные Telegram")

    if WEBAPP_DEV_MODE and x_dev_user_id:
        try:
            return int(x_dev_user_id)
        except ValueError:
            pass

    raise HTTPException(status_code=401, detail="Открой приложение через Telegram")


async def require_user(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=404,
            detail="Сначала открой бота и нажми /start",
        )
    return user


def is_teacher(user: User) -> bool:
    return user.id in ADMIN_IDS or user.role == "teacher"


async def teacher_user_id(user_id: int = Depends(current_user_id)) -> int:
    async with async_session() as session:
        user = await session.get(User, user_id)
        if not user:
            raise HTTPException(
                status_code=404,
                detail="Сначала открой бота и нажми /start",
            )
        if not is_teacher(user):
            raise HTTPException(status_code=403, detail="Только для учителя")
    return user_id
