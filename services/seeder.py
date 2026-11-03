"""Первичное наполнение базы темами и заданиями.

На bothost/любом хостинге база `bot.db` не лежит в git (она в .gitignore),
поэтому после деплоя бот стартует с пустой базой. Этот модуль при первом
запуске загружает темы из `seed_topics.py` и задания из `tasks_*.txt`.
"""
from __future__ import annotations

import glob
import logging
from pathlib import Path

from sqlalchemy import func, select, update

from database.base import async_session
from database.models import Task, Topic
from seed_topics import TOPICS
from services.tasks_importer import import_from_file

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent


async def _counts() -> tuple[int, int]:
    async with async_session() as session:
        topics = (await session.execute(
            select(func.count()).select_from(Topic)
        )).scalar() or 0
        tasks = (await session.execute(
            select(func.count()).select_from(Task)
        )).scalar() or 0
    return topics, tasks


async def seed_missing(*, force: bool = False) -> dict:
    """Досоздаёт темы и импортирует задания, не плодя дубликаты.

    force=True — импортировать файлы даже если база уже не пустая.
    """
    topics_before, tasks_before = await _counts()
    stats = {"topics_created": 0, "tasks_added": 0, "errors": []}

    if topics_before == 0 or force:
        async with async_session() as session:
            existing = {
                row[0] for row in (await session.execute(select(Topic.title))).all()
            }
            for index, title in enumerate(TOPICS, start=1):
                if title in existing:
                    continue
                session.add(Topic(
                    title=title,
                    is_active=False,
                    order_index=index,
                ))
                stats["topics_created"] += 1
            await session.commit()

    if not force and topics_before > 0 and tasks_before > 0:
        return stats

    for path in sorted(glob.glob(str(BASE_DIR / "tasks_*.txt"))):
        try:
            content = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            stats["errors"].append(f"{Path(path).name}: {exc}")
            continue
        result = await import_from_file(content, skip_existing=True)
        stats["topics_created"] += result["topics_created"]
        stats["tasks_added"] += result["tasks_added"]
        stats["errors"].extend(result["errors"])

    # Новым темам без порядка даём порядок по id, чтобы карта была стабильной.
    async with async_session() as session:
        await session.execute(
            update(Topic)
            .where((Topic.order_index == 0) | (Topic.order_index.is_(None)))
            .values(order_index=Topic.id)
        )
        await session.commit()

    logger.info(
        "Наполнение базы: тем +%s, заданий +%s",
        stats["topics_created"], stats["tasks_added"],
    )
    return stats


async def seed_if_empty() -> dict | None:
    """Автозапуск при старте: только если база пустая."""
    topics, tasks = await _counts()
    if topics > 0 and tasks > 0:
        return None
    return await seed_missing()
