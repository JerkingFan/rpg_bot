"""Резервные копии SQLite-базы перед миграциями/запуском.

База содержит прогресс учеников, поэтому перед любыми изменениями схемы
делаем снимок через sqlite3 backup API (он учитывает WAL).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from config import DATABASE_URL

logger = logging.getLogger(__name__)

BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"
MAX_BACKUPS = 10


def sqlite_path(url: str = DATABASE_URL) -> Path | None:
    if not url.startswith("sqlite"):
        return None
    if ":///" not in url:
        return None
    raw = url.split(":///", 1)[1]
    if not raw or raw == ":memory:":
        return None
    return Path(raw)


def backup_sqlite_db() -> Path | None:
    """Делает снимок текущей базы. Возвращает путь к копии или None."""
    path = sqlite_path()
    if path is None or not path.exists() or path.stat().st_size == 0:
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"{path.stem}-{stamp}.db"

    source = sqlite3.connect(path)
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    # Храним только последние MAX_BACKUPS копий.
    copies = sorted(BACKUP_DIR.glob(f"{path.stem}-*.db"))
    for old in copies[:-MAX_BACKUPS]:
        try:
            old.unlink()
        except OSError:
            logger.warning("Не удалось удалить старую копию %s", old)

    logger.info("Резервная копия базы: %s", dest)
    return dest
