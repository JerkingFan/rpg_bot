"""Резервные копии SQLite-базы и перенос её на постоянный диск.

База содержит прогресс учеников, поэтому перед любыми изменениями схемы
делаем снимок через sqlite3 backup API (он учитывает WAL).

Копии кладём рядом с самой базой: на bothost это `/app/data/backups`,
то есть постоянный диск, а не пересоздаваемый каталог проекта.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from config import DATABASE_URL

logger = logging.getLogger(__name__)

MAX_BACKUPS = 10
LEGACY_DB_NAME = "bot.db"


def sqlite_path(url: str = DATABASE_URL) -> Path | None:
    if not url.startswith("sqlite"):
        return None
    if ":///" not in url:
        return None
    raw = url.split(":///", 1)[1]
    if not raw or raw == ":memory:":
        return None
    return Path(raw)


def ensure_db_dir() -> Path | None:
    """Создаёт каталог для базы (например, /app/data) до первого подключения."""
    path = sqlite_path()
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _user_count(path: Path) -> int | None:
    """Сколько учеников в базе. None — если файл не база или таблицы нет."""
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = connection.execute("SELECT COUNT(*) FROM users").fetchone()
            return int(row[0]) if row else 0
        finally:
            connection.close()
    except sqlite3.Error:
        return None


def migrate_legacy_db(target: Path | None = None) -> Path | None:
    """Переносит старую `./bot.db` в постоянный каталог.

    Нужно для первого запуска после переезда: раньше SQLite лежала в корне
    проекта и терялась при деплое. Если в постоянной базе уже есть ученики —
    ничего не трогаем. Если там только пустая схема, а в старой базе есть
    ученики, заменяем её (предварительно сделав копию).
    """
    path = target or sqlite_path()
    if path is None:
        return None

    base_dir = Path(__file__).resolve().parent.parent
    candidates = [Path.cwd() / LEGACY_DB_NAME, base_dir / LEGACY_DB_NAME]
    legacy = next(
        (
            candidate for candidate in candidates
            if candidate.exists() and candidate.stat().st_size > 0
        ),
        None,
    )
    if legacy is None or legacy.resolve() == path.resolve():
        return None

    if path.exists() and path.stat().st_size > 0:
        target_users = _user_count(path)
        legacy_users = _user_count(legacy)
        if target_users is None or target_users > 0:
            return None
        if not legacy_users:
            return None
        logger.warning(
            "В постоянной базе нет учеников, а в старой их %s — заменяю базу",
            legacy_users,
        )
        backup_sqlite_db(path)
        for suffix in ("-wal", "-shm"):
            stale = path.with_name(path.name + suffix)
            if stale.exists():
                stale.unlink()

    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(legacy, path)
    # WAL/SHM копируем вместе с базой, иначе потеряются последние транзакции.
    for suffix in ("-wal", "-shm"):
        src = legacy.with_name(legacy.name + suffix)
        if src.exists():
            shutil.copy2(src, path.with_name(path.name + suffix))

    logger.warning("Старая база перенесена: %s -> %s", legacy, path)
    return path


def backup_sqlite_db(path: Path | None = None) -> Path | None:
    """Делает снимок текущей базы. Возвращает путь к копии или None."""
    path = path or sqlite_path()
    if path is None or not path.exists() or path.stat().st_size == 0:
        return None

    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"{path.stem}-{stamp}.db"

    source = sqlite3.connect(path)
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    # Храним только последние MAX_BACKUPS копий.
    copies = sorted(backup_dir.glob(f"{path.stem}-*.db"))
    for old in copies[:-MAX_BACKUPS]:
        try:
            old.unlink()
        except OSError:
            logger.warning("Не удалось удалить старую копию %s", old)

    logger.info("Резервная копия базы: %s", dest)
    return dest
