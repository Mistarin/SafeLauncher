import sqlite3
import os
import json
import shutil
import time
import uuid
import re
import threading
import math
import tempfile
from core.logger import get_logger
from core.game_names import (
    MAX_GAME_NAME_LENGTH,
    fallback_game_name,
    display_name_key,
    is_identity_placeholder_name,
    local_profile_identity,
    meaningful_game_name,
    preferred_game_name,
)

logger = get_logger("Database")

_XDG_DATA_HOME = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
_APP_DATA_DIR = os.path.join(_XDG_DATA_HOME, "safelauncher")
DEFAULT_DB_PATH = os.path.join(_APP_DATA_DIR, "library.db")

_LEGACY_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "library.db")
_OLD_APP_DB_PATH = os.path.join(_XDG_DATA_HOME, "mglauncher", "library.db")


def _migrate_legacy_db(new_path: str) -> None:
    """Move databases from pre-SafeLauncher locations into the current XDG path."""
    if os.path.isfile(new_path):
        return

    for legacy_path in (_LEGACY_DB_PATH, _OLD_APP_DB_PATH):
        if not os.path.isfile(legacy_path):
            continue
        try:
            shutil.move(legacy_path, new_path)
            logger.info(f"Migrated legacy database {legacy_path} → {new_path}")
            return
        except Exception as e:
            logger.error(f"Could not migrate legacy DB {legacy_path}: {e}")


_BACKUP_CREATED: set = set()


class DatabaseRecoveryError(RuntimeError):
    """Raised when the local library and its recovery copy are unusable."""


def _create_database_backup(db_path: str, source_connection=None, *, force: bool = False) -> None:
    """Create auto-backup copy (library.db.bak) on startup.

    Only called after the database file has passed a consistency check, so a
    corrupted database can never overwrite the last known-good recovery copy.
    Guarded to run at most once per process lifetime to avoid multi-thread I/O races.
    """
    if db_path == ":memory:" or not os.path.isfile(db_path):
        return
    if not force and db_path in _BACKUP_CREATED:
        return
    bak_path = f"{db_path}.bak"
    try:
        # A file copy of a WAL-mode SQLite database can omit committed pages
        # which have not been checkpointed into library.db yet. SQLite's backup
        # API takes a consistent snapshot across the main database and WAL.
        source = source_connection
        owns_source = source is None
        if source is None:
            source = sqlite3.connect(db_path, timeout=10)
        if source.in_transaction:
            # SQLite backup can wait indefinitely on its own write transaction.
            # Do not commit caller-owned work or mark this failed attempt done.
            logger.warning("Database backup deferred: source transaction is still open.")
            return
        destination = sqlite3.connect(bak_path, timeout=10)
        try:
            source.backup(destination)
        finally:
            destination.close()
            if owns_source:
                source.close()
        _BACKUP_CREATED.add(db_path)
        logger.debug(f"Created database backup: {bak_path}")
    except Exception as e:
        logger.warning(f"Could not create database backup: {e}")


class DatabaseSession:
    """One connection and one reentrant repository-call lock."""
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = None
        self.lock = threading.RLock()

    def open(self):
        """Open a consistent database or restore a verified backup atomically."""
        def _is_consistent(conn) -> bool:
            # sqlite3.connect does not touch data pages; corruption only surfaces
            # on the first real query, so force an explicit integrity check.
            try:
                row = conn.execute("PRAGMA quick_check(1)").fetchone()
            except sqlite3.DatabaseError:
                return False
            return bool(row) and str(row[0]).lower() == "ok"

        try:
            self.conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
            self.conn.execute("PRAGMA busy_timeout = 5000")
            self.conn.execute("PRAGMA foreign_keys = ON")
            if self.db_path != ":memory:":
                try:
                    self.conn.execute("PRAGMA journal_mode = WAL")
                    self.conn.execute("PRAGMA synchronous = NORMAL")
                except Exception:
                    pass
            if not _is_consistent(self.conn):
                raise sqlite3.DatabaseError(f"Integrity check failed for {self.db_path}")
        except sqlite3.DatabaseError as e:
            logger.error(f"Failed to open SQLite database {self.db_path}: {e}")
            if self.conn is not None:
                try:
                    self.conn.close()
                except sqlite3.DatabaseError:
                    pass
            self.conn = None
            bak_path = f"{self.db_path}.bak"
            if os.path.isfile(bak_path):
                logger.warning(f"Attempting self-healing recovery from backup: {bak_path}")
                candidate_path = None
                try:
                    descriptor, candidate_path = tempfile.mkstemp(
                        prefix=".safelauncher-db-restore-",
                        suffix=".tmp",
                        dir=os.path.dirname(self.db_path) or ".",
                    )
                    os.close(descriptor)
                    shutil.copy2(bak_path, candidate_path)
                    candidate = sqlite3.connect(candidate_path, timeout=5)
                    try:
                        candidate.execute("PRAGMA busy_timeout = 5000")
                        candidate_is_consistent = _is_consistent(candidate)
                    finally:
                        candidate.close()
                    if not candidate_is_consistent:
                        raise sqlite3.DatabaseError("Recovery copy failed its integrity check.")

                    # Do not replace the active database until the copied
                    # recovery file has passed an integrity check. Discard
                    # sidecars from the corrupt database so they cannot be
                    # replayed against the restored snapshot.
                    for suffix in ("-wal", "-shm"):
                        try:
                            os.unlink(f"{self.db_path}{suffix}")
                        except FileNotFoundError:
                            pass
                    os.replace(candidate_path, self.db_path)
                    candidate_path = None
                    conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
                    conn.execute("PRAGMA busy_timeout = 5000")
                    conn.execute("PRAGMA foreign_keys = ON")
                    try:
                        conn.execute("PRAGMA journal_mode = WAL")
                        conn.execute("PRAGMA synchronous = NORMAL")
                    except sqlite3.DatabaseError:
                        pass
                    if not _is_consistent(conn):
                        conn.close()
                        raise sqlite3.DatabaseError("Restored database failed its integrity check.")
                    self.conn = conn
                    logger.info("Successfully restored database from backup.")
                    return
                except Exception as restore_err:
                    logger.error(f"Backup restore failed: {restore_err}")
                finally:
                    if candidate_path is not None:
                        try:
                            os.unlink(candidate_path)
                        except OSError:
                            pass

            # Never present an empty in-memory library as if it were the
            # user's persistent database. Startup handles this error visibly.
            message = (
                f"Could not open the SafeLauncher library at {self.db_path}. "
                "Automatic recovery failed; no temporary in-memory library was created, "
                "and the recovery copy was preserved. "
                "Check their permissions or restore a known-good backup before retrying."
            )
            logger.critical(message)
            raise DatabaseRecoveryError(message) from e

    def close(self):
        with self.lock:
            if self.conn is not None:
                self.conn.close()
                self.conn = None
