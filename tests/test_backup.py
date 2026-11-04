"""Тесты переноса базы на постоянный диск и резервных копий."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database.backup import (
    backup_sqlite_db,
    migrate_legacy_db,
    sqlite_path,
)


def _make_db(path: Path, users: int) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
        for user_id in range(1, users + 1):
            connection.execute("INSERT INTO users (id, username) VALUES (?, ?)", (user_id, f"u{user_id}"))
        connection.commit()
    finally:
        connection.close()


def _users(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        connection.close()


class BackupTests(unittest.TestCase):
    def setUp(self):
        self._old_cwd = Path.cwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cwd = self.root / "app"
        self.data = self.root / "data"
        self.cwd.mkdir()
        self.data.mkdir()
        os.chdir(self.cwd)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def test_sqlite_path_parsing(self):
        self.assertEqual(sqlite_path("sqlite+aiosqlite:///bot.db"), Path("bot.db"))
        self.assertEqual(
            sqlite_path("sqlite+aiosqlite:////app/data/bot.db"),
            Path("/app/data/bot.db"),
        )
        self.assertIsNone(sqlite_path("sqlite+aiosqlite:///:memory:"))
        self.assertIsNone(sqlite_path("postgresql+asyncpg://localhost/db"))

    def test_legacy_db_moves_to_persistent_dir(self):
        _make_db(self.cwd / "bot.db", users=3)
        target = self.data / "bot.db"

        moved = migrate_legacy_db(target)

        self.assertEqual(moved, target)
        self.assertTrue(target.exists())
        self.assertEqual(_users(target), 3)

    def test_existing_db_with_users_is_not_overwritten(self):
        _make_db(self.cwd / "bot.db", users=5)
        target = self.data / "bot.db"
        _make_db(target, users=1)

        self.assertIsNone(migrate_legacy_db(target))
        self.assertEqual(_users(target), 1)

    def test_empty_persistent_db_is_replaced_by_legacy(self):
        _make_db(self.cwd / "bot.db", users=4)
        target = self.data / "bot.db"
        _make_db(target, users=0)

        self.assertEqual(migrate_legacy_db(target), target)
        self.assertEqual(_users(target), 4)
        # Перед заменой пустой базы должна остаться копия.
        self.assertTrue(list((self.data / "backups").glob("bot-*.db")))

    def test_backup_is_stored_next_to_database(self):
        target = self.data / "bot.db"
        _make_db(target, users=2)

        backup = backup_sqlite_db(target)

        self.assertIsNotNone(backup)
        self.assertEqual(backup.parent, self.data / "backups")
        self.assertEqual(_users(backup), 2)


if __name__ == "__main__":
    unittest.main()
