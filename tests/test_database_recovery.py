"""Recovery behavior for the persistent local game library."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import DatabaseRecoveryError, GameDatabase


class DatabaseRecoveryTests(unittest.TestCase):
    def test_invalid_database_and_backup_fail_visibly_without_overwriting_either(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "library.db"
            backup_path = Path(f"{path}.bak")
            original_database = b"not a valid sqlite database"
            original_backup = b"not a valid recovery copy"
            path.write_bytes(original_database)
            backup_path.write_bytes(original_backup)

            with self.assertRaisesRegex(DatabaseRecoveryError, "recovery copy was preserved"):
                GameDatabase(str(path))

            self.assertEqual(path.read_bytes(), original_database)
            self.assertEqual(backup_path.read_bytes(), original_backup)

    def test_consistent_backup_is_restored(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "library.db"
            backup_path = Path(f"{path}.bak")
            path.write_bytes(b"corrupt database")
            Path(f"{path}-wal").write_bytes(b"stale wal sidecar")
            Path(f"{path}-shm").write_bytes(b"stale shm sidecar")
            with sqlite3.connect(backup_path) as backup:
                backup.execute("CREATE TABLE recovery_marker (value TEXT NOT NULL)")
                backup.execute("INSERT INTO recovery_marker VALUES ('restored')")

            database = GameDatabase(str(path))
            try:
                value = database.conn.execute(
                    "SELECT value FROM recovery_marker"
                ).fetchone()[0]
                self.assertEqual(value, "restored")
                self.assertEqual(database.conn.execute("PRAGMA quick_check(1)").fetchone()[0], "ok")
                for suffix, stale_contents in (
                    ("-wal", b"stale wal sidecar"),
                    ("-shm", b"stale shm sidecar"),
                ):
                    sidecar = Path(f"{path}{suffix}")
                    self.assertFalse(
                        sidecar.exists() and sidecar.read_bytes() == stale_contents
                    )
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
