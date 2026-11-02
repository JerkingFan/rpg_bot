"""Лёгкие миграции для SQLite.

Проект исторически использует `create_all`, поэтому новые колонки в
существующих таблицах не появляются сами. Здесь мы аккуратно добавляем их
через `ALTER TABLE` и создаём индексы. Для больших изменений лучше перейти
на Alembic, но для текущего масштаба этого достаточно.
"""
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)


# table -> {column: ddl}
_COLUMNS: dict[str, dict[str, str]] = {
    "users": {
        "coins": "INTEGER NOT NULL DEFAULT 0",
        "daily_xp": "INTEGER NOT NULL DEFAULT 0",
        "daily_coins": "INTEGER NOT NULL DEFAULT 0",
        "daily_reset_date": "DATE",
    },
    "topics": {
        "lore": "TEXT",
        "emoji": "VARCHAR(16) DEFAULT '📚'",
        "order_index": "INTEGER NOT NULL DEFAULT 0",
        "required_topic_id": "INTEGER",
    },
    "tasks": {
        "skill": "VARCHAR(20) NOT NULL DEFAULT 'algebra'",
        "hint": "TEXT",
        "solution": "TEXT",
        "is_boss": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "daily_assignments": {
        "attempts": "INTEGER NOT NULL DEFAULT 0",
        "used_hint": "BOOLEAN NOT NULL DEFAULT 0",
        "manually_accepted": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "completed_tasks": {
        "attempts": "INTEGER NOT NULL DEFAULT 1",
        "used_hint": "BOOLEAN NOT NULL DEFAULT 0",
        "first_clear": "BOOLEAN NOT NULL DEFAULT 1",
        "source": "VARCHAR(20) NOT NULL DEFAULT 'legacy'",
        "skill": "VARCHAR(20)",
        "coins_earned": "INTEGER NOT NULL DEFAULT 0",
    },
}

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_daily_user ON daily_assignments(user_id)",
    "CREATE INDEX IF NOT EXISTS ix_tasks_topic ON tasks(topic_id)",
    "CREATE INDEX IF NOT EXISTS ix_completed_user ON completed_tasks(user_id)",
    "CREATE INDEX IF NOT EXISTS ix_skill_progress_user ON skill_progress(user_id)",
    "CREATE INDEX IF NOT EXISTS ix_topic_progress_user ON topic_progress(user_id)",
    "CREATE INDEX IF NOT EXISTS ix_user_quests_user ON user_quests(user_id)",
    "CREATE INDEX IF NOT EXISTS ix_boss_fights_user ON boss_fights(user_id)",
]


async def _table_columns(conn: AsyncConnection, table: str) -> set[str]:
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in result.fetchall()}


async def run_migrations(conn: AsyncConnection) -> None:
    if conn.dialect.name != "sqlite":
        # Для Postgres схему лучше вести через Alembic.
        return

    await conn.execute(text("PRAGMA foreign_keys=ON"))

    added: set[tuple[str, str]] = set()
    for table, columns in _COLUMNS.items():
        existing = await _table_columns(conn, table)
        if not existing:
            continue
        for name, ddl in columns.items():
            if name not in existing:
                logger.info("Миграция: добавляю %s.%s", table, name)
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                added.add((table, name))

    # Чистим дубли перед уникальным индексом (оставляем самую позднюю запись).
    await conn.execute(text(
        """
        DELETE FROM daily_assignments
        WHERE id NOT IN (
            SELECT MAX(id) FROM daily_assignments
            GROUP BY user_id, task_id, assigned_date
        )
        """
    ))
    await conn.execute(text(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_user_task_date
        ON daily_assignments(user_id, task_id, assigned_date)
        """
    ))

    for ddl in _INDEXES:
        await conn.execute(text(ddl))

    # Навык задачи определяем по названию темы — этого достаточно для MVP.
    # Только при первом добавлении колонки, чтобы не затирать ручные правки.
    if ("tasks", "skill") in added:
        await conn.execute(text(
            """
            UPDATE tasks
            SET skill = CASE
                WHEN (SELECT title FROM topics WHERE topics.id = tasks.topic_id) LIKE '%Геометрия%'
                    THEN 'geometry'
                WHEN (SELECT title FROM topics WHERE topics.id = tasks.topic_id) LIKE '%производн%'
                    THEN 'derivatives'
                WHEN (SELECT title FROM topics WHERE topics.id = tasks.topic_id) LIKE '%тригон%'
                  OR (SELECT title FROM topics WHERE topics.id = tasks.topic_id) LIKE '%sin%'
                  OR (SELECT title FROM topics WHERE topics.id = tasks.topic_id) LIKE '%cos%'
                  OR (SELECT title FROM topics WHERE topics.id = tasks.topic_id) LIKE '%tg%'
                  OR (SELECT title FROM topics WHERE topics.id = tasks.topic_id) LIKE '%ctg%'
                    THEN 'trigonometry'
                ELSE 'algebra'
            END
            """
        ))

    if ("topics", "order_index") in added:
        await conn.execute(text(
            "UPDATE topics SET order_index = id WHERE order_index = 0 OR order_index IS NULL"
        ))
    await conn.execute(text(
        "UPDATE completed_tasks SET first_clear = 1 WHERE first_clear IS NULL"
    ))
    await conn.execute(text(
        "UPDATE completed_tasks SET source = 'legacy' WHERE source IS NULL"
    ))
