import sqlite3
import os
import json
import shutil
import time
import uuid
import re
import threading
import math
from core.logger import get_logger

logger = get_logger("Database")

_XDG_DATA_HOME = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
_APP_DATA_DIR = os.path.join(_XDG_DATA_HOME, "safelauncher")
DEFAULT_DB_PATH = os.path.join(_APP_DATA_DIR, "library.db")

_LEGACY_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library.db")
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
_SCHEMA_INITIALIZED: set = set()


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
    _BACKUP_CREATED.add(db_path)
    bak_path = f"{db_path}.bak"
    try:
        # A file copy of a WAL-mode SQLite database can omit committed pages
        # which have not been checkpointed into library.db yet. SQLite's backup
        # API takes a consistent snapshot across the main database and WAL.
        source = source_connection
        owns_source = source is None
        if source is None:
            source = sqlite3.connect(db_path, timeout=10)
        destination = sqlite3.connect(bak_path, timeout=10)
        try:
            source.backup(destination)
        finally:
            destination.close()
            if owns_source:
                source.close()
        logger.debug(f"Created database backup: {bak_path}")
    except Exception as e:
        logger.warning(f"Could not create database backup: {e}")


from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple, Any

_ACHIEVEMENT_DB_LOCK = threading.RLock()
_ACHIEVEMENT_API_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ACHIEVEMENT_PROVENANCES = {
    "steam_verified", "local_emulator", "cloud_profile", "cache", "unknown",
}
_ACHIEVEMENT_APP_RE = re.compile(r"^[0-9]{1,16}$")


def _valid_achievement_api_name(value: Any) -> str:
    name = str(value or "").strip()
    return name if _ACHIEVEMENT_API_RE.fullmatch(name) else ""


def _normalise_achievement_provenance(value: Any) -> str:
    candidate = str(value or "unknown").strip()
    return candidate if candidate in _ACHIEVEMENT_PROVENANCES else "unknown"


def _safe_achievement_timestamp(value: Any, fallback: Optional[float] = None) -> float:
    """Return a finite, non-negative achievement timestamp."""
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        timestamp = 0.0
    if not math.isfinite(timestamp) or timestamp <= 0:
        return float(fallback if fallback is not None else time.time())
    return timestamp



@dataclass
class GameRecord:
    id: int
    name: str
    path: str
    executable: str
    mode: str
    banner_url: Optional[str] = ""
    steam_id: Optional[str] = ""
    playtime_seconds: int = 0
    is_favorite: int = 0
    last_played: int = 0
    tags: str = ""
    build_id: str = ""
    proton_path: str = ""
    collection: str = ""
    install_date: int = 0
    version_override: str = ""
    patch_notes_url: str = ""
    is_archived: int = 0
    icon_url: str = ""
    env_vars: str = "{}"
    build_date: int = 0

    def __getitem__(self, idx):
        fields = (
            self.id, self.name, self.path, self.executable, self.mode,
            self.banner_url or "", self.steam_id or "", self.playtime_seconds,
            self.is_favorite, self.last_played, self.tags, self.build_id,
            self.proton_path, self.collection, self.install_date,
            self.version_override, self.patch_notes_url,
            self.is_archived, self.icon_url, self.env_vars or "{}", self.build_date
        )
        return fields[idx]

    def __len__(self):
        return 21

    def __hash__(self):
        return hash(self.id)

    def __iter__(self):
        return iter((
            self.id, self.name, self.path, self.executable, self.mode,
            self.banner_url or "", self.steam_id or "", self.playtime_seconds,
            self.is_favorite, self.last_played, self.tags, self.build_id,
            self.proton_path, self.collection, self.install_date,
            self.version_override, self.patch_notes_url,
            self.is_archived, self.icon_url, self.env_vars or "{}", self.build_date
        ))


class GameDatabase:
    GAME_COLUMNS = (
        "id, name, path, executable, mode, banner_url, steam_id, "
        "playtime_seconds, is_favorite, last_played, tags, build_id"
        ", proton_path, collection, install_date"
        ", version_override, patch_notes_url"
        ", is_archived, icon_url, env_vars, build_date"
    )

    def __init__(self, db_path: str = None):
        if db_path is None or db_path == "library.db":
            db_path = DEFAULT_DB_PATH

        self.db_path = db_path

        if db_path != ":memory:":
            os.makedirs(os.path.dirname(db_path), mode=0o700, exist_ok=True)
            _migrate_legacy_db(db_path)

        self.conn = None
        self._connect_with_retry()

        self._create_table()

        if db_path != ":memory:":
            try:
                os.chmod(db_path, 0o600)
            except Exception:
                pass
            # Back up only after the connection was verified consistent, so the
            # recovery copy always holds the newest healthy snapshot.
            _create_database_backup(db_path, self.conn)

    def _connect_with_retry(self):
        """Connect to SQLite database with self-healing restore from .bak on corruption."""
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
            self.conn = None
            bak_path = f"{self.db_path}.bak"
            if os.path.isfile(bak_path):
                logger.warning(f"Attempting self-healing recovery from backup: {bak_path}")
                try:
                    shutil.copy2(bak_path, self.db_path)
                    conn = sqlite3.connect(self.db_path, timeout=5)
                    conn.execute("PRAGMA busy_timeout = 5000")
                    if _is_consistent(conn):
                        self.conn = conn
                        logger.info("Successfully restored database from backup.")
                        return
                    conn.close()
                    logger.error("Restored backup also failed its integrity check.")
                except Exception as restore_err:
                    logger.error(f"Backup restore failed: {restore_err}")
            # If all fails, fall back to in-memory database to prevent launcher crash
            logger.critical("Falling back to fresh in-memory database instance.")
            self.conn = sqlite3.connect(":memory:")

    def _create_table(self):
        """Create/migrate the shared schema without racing worker connections."""
        with _ACHIEVEMENT_DB_LOCK:
            self._create_table_locked()

    def _create_table_locked(self):
        if self.db_path != ":memory:" and self.db_path in _SCHEMA_INITIALIZED:
            return
        try:
            with self.conn:
                self.conn.execute('''
                    CREATE TABLE IF NOT EXISTS games (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        path TEXT NOT NULL,
                        executable TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        banner_url TEXT,
                        steam_id TEXT
                    )
                ''')

                cursor = self.conn.cursor()
                cursor.execute("PRAGMA table_info(games)")
                columns = [column[1] for column in cursor.fetchall()]

                if "banner_url" not in columns:
                    cursor.execute("ALTER TABLE games ADD COLUMN banner_url TEXT")
                if "steam_id" not in columns:
                    cursor.execute("ALTER TABLE games ADD COLUMN steam_id TEXT")
                if "playtime_seconds" not in columns:
                    cursor.execute("ALTER TABLE games ADD COLUMN playtime_seconds INTEGER DEFAULT 0")
                if "is_favorite" not in columns:
                    cursor.execute("ALTER TABLE games ADD COLUMN is_favorite INTEGER DEFAULT 0")
                if "last_played" not in columns:
                    cursor.execute("ALTER TABLE games ADD COLUMN last_played INTEGER DEFAULT 0")
                if "tags" not in columns:
                    cursor.execute("ALTER TABLE games ADD COLUMN tags TEXT DEFAULT ''")
                if "build_id" not in columns:
                    cursor.execute("ALTER TABLE games ADD COLUMN build_id TEXT DEFAULT ''")
                if "proton_path" not in columns:
                    cursor.execute("ALTER TABLE games ADD COLUMN proton_path TEXT DEFAULT ''")
                if "collection" not in columns:
                    cursor.execute("ALTER TABLE games ADD COLUMN collection TEXT DEFAULT ''")
                if "install_date" not in columns:
                    cursor.execute("ALTER TABLE games ADD COLUMN install_date INTEGER DEFAULT 0")
                if "version_override" not in columns:
                    cursor.execute("ALTER TABLE games ADD COLUMN version_override TEXT DEFAULT ''")
                if "patch_notes_url" not in columns:
                    cursor.execute("ALTER TABLE games ADD COLUMN patch_notes_url TEXT DEFAULT ''")
                if "is_archived" not in columns:
                    cursor.execute("ALTER TABLE games ADD COLUMN is_archived INTEGER DEFAULT 0")
                if "icon_url" not in columns:
                    cursor.execute("ALTER TABLE games ADD COLUMN icon_url TEXT DEFAULT ''")
                if "env_vars" not in columns:
                    cursor.execute("ALTER TABLE games ADD COLUMN env_vars TEXT DEFAULT '{}'")
                if "build_date" not in columns:
                    cursor.execute("ALTER TABLE games ADD COLUMN build_date INTEGER DEFAULT 0")
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS collections (
                        name TEXT PRIMARY KEY
                    )
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS achievements (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        game_id INTEGER NOT NULL,
                        app_id TEXT NOT NULL,
                        api_name TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        description TEXT DEFAULT '',
                        icon_path TEXT DEFAULT '',
                        icongray_path TEXT DEFAULT '',
                        unlocked INTEGER DEFAULT 0,
                        unlock_time REAL DEFAULT 0,
                        hidden INTEGER DEFAULT 0,
                        UNIQUE(game_id, api_name)
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_achievements_game ON achievements(game_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_achievements_game_unlocked ON achievements(game_id, unlocked)")

                cursor.execute("PRAGMA table_info(achievements)")
                achievement_columns = {column[1] for column in cursor.fetchall()}
                if "unlock_provenance" not in achievement_columns:
                    cursor.execute("ALTER TABLE achievements ADD COLUMN unlock_provenance TEXT DEFAULT 'unknown'")
                if "unlock_verified" not in achievement_columns:
                    cursor.execute("ALTER TABLE achievements ADD COLUMN unlock_verified INTEGER DEFAULT 0")
                if "unlock_source_format" not in achievement_columns:
                    cursor.execute("ALTER TABLE achievements ADD COLUMN unlock_source_format TEXT DEFAULT ''")
                if "unlock_source_path" not in achievement_columns:
                    cursor.execute("ALTER TABLE achievements ADD COLUMN unlock_source_path TEXT DEFAULT ''")
                if "notification_sent" not in achievement_columns:
                    cursor.execute("ALTER TABLE achievements ADD COLUMN notification_sent INTEGER DEFAULT 1")

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS achievement_profile (
                        app_id TEXT NOT NULL,
                        api_name TEXT NOT NULL,
                        unlock_time REAL DEFAULT 0,
                        first_seen_at REAL NOT NULL,
                        last_seen_at REAL DEFAULT 0,
                        provenance TEXT DEFAULT 'local_emulator',
                        verified INTEGER DEFAULT 0,
                        validation_state TEXT DEFAULT 'validated',
                        source_format TEXT DEFAULT '',
                        source_path TEXT DEFAULT '',
                        PRIMARY KEY (app_id, api_name)
                    )
                """)
                cursor.execute("PRAGMA table_info(achievement_profile)")
                profile_columns = {column[1] for column in cursor.fetchall()}
                legacy_profile_contract = "validation_state" not in profile_columns
                if "last_seen_at" not in profile_columns:
                    cursor.execute("ALTER TABLE achievement_profile ADD COLUMN last_seen_at REAL DEFAULT 0")
                if "provenance" not in profile_columns:
                    cursor.execute("ALTER TABLE achievement_profile ADD COLUMN provenance TEXT DEFAULT 'local_emulator'")
                if "verified" not in profile_columns:
                    cursor.execute("ALTER TABLE achievement_profile ADD COLUMN verified INTEGER DEFAULT 0")
                if "validation_state" not in profile_columns:
                    cursor.execute("ALTER TABLE achievement_profile ADD COLUMN validation_state TEXT DEFAULT 'validated'")
                if "source_format" not in profile_columns:
                    cursor.execute("ALTER TABLE achievement_profile ADD COLUMN source_format TEXT DEFAULT ''")
                if "source_path" not in profile_columns:
                    cursor.execute("ALTER TABLE achievement_profile ADD COLUMN source_path TEXT DEFAULT ''")
                # Rows written before provenance existed were schema-backed
                # local observations. Keep them visible, but explicitly mark
                # them as unverified rather than implying Steam proof.
                cursor.execute("""
                    UPDATE achievement_profile
                    SET provenance = CASE WHEN provenance IS NULL OR provenance = '' OR provenance = 'unknown'
                                          THEN 'local_emulator' ELSE provenance END,
                        verified = COALESCE(verified, 0),
                        validation_state = CASE WHEN validation_state IS NULL OR validation_state = ''
                                                THEN 'validated' ELSE validation_state END,
                        last_seen_at = CASE WHEN COALESCE(last_seen_at, 0) <= 0 THEN first_seen_at ELSE last_seen_at END
                    WHERE provenance IS NULL OR provenance = '' OR provenance = 'unknown'
                       OR validation_state IS NULL OR validation_state = ''
                       OR COALESCE(last_seen_at, 0) <= 0
                """)
                if legacy_profile_contract:
                    # Historical rows have no schema fingerprint. Preserve
                    # them, but quarantine until the current schema confirms
                    # the API names.
                    cursor.execute("UPDATE achievement_profile SET validation_state = 'pending_schema' WHERE validation_state = 'validated'")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_achievement_profile_app ON achievement_profile(app_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_achievement_profile_validation ON achievement_profile(app_id, validation_state)")
                cursor.execute("""
                    UPDATE achievements
                    SET unlock_provenance = 'local_emulator', unlock_verified = 0, notification_sent = 1
                    WHERE unlocked = 1 AND (unlock_provenance IS NULL OR unlock_provenance = '' OR unlock_provenance = 'unknown')
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS profile_games (
                        identity_key TEXT PRIMARY KEY,
                        app_id TEXT DEFAULT '',
                        favorite INTEGER DEFAULT 0,
                        favorite_changed_at REAL DEFAULT 0,
                        favorite_change_id TEXT DEFAULT '',
                        playtime_baseline_seconds INTEGER DEFAULT 0,
                        last_played INTEGER DEFAULT 0,
                        first_seen_at REAL NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_profile_games_app ON profile_games(app_id)")

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS playtime_sessions (
                        session_id TEXT PRIMARY KEY,
                        game_id INTEGER NOT NULL,
                        started_at INTEGER NOT NULL,
                        ended_at INTEGER DEFAULT 0,
                        duration_seconds INTEGER DEFAULT 0,
                        finalized INTEGER DEFAULT 0,
                        updated_at INTEGER NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_playtime_sessions_game ON playtime_sessions(game_id)")


                # Sanitize any accidental combo box formatting in executable column
                cursor.execute("SELECT id, executable FROM games WHERE executable LIKE '%·%'")
                for row_id, row_exe in cursor.fetchall():
                    clean_exe = row_exe.split(" · ")[0].split("  ·  ")[0].strip()
                    cursor.execute("UPDATE games SET executable = ? WHERE id = ?", (clean_exe, row_id))
            if self.db_path != ":memory:":
                _SCHEMA_INITIALIZED.add(self.db_path)
        except Exception as e:
            logger.error(f"Error initializing database schema: {e}")

    def update_build_id(self, game_id: int, build_id: str):
        try:
            with self.conn:
                self.conn.execute("UPDATE games SET build_id = ? WHERE id = ?", (build_id, game_id))
        except Exception as e:
            logger.error(f"Error updating build_id for game {game_id}: {e}")

    def update_build_date(self, game_id: int, build_date: int):
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE games SET build_date = ? WHERE id = ?",
                    (int(build_date or 0), game_id),
                )
        except Exception as e:
            logger.error(f"Error updating build_date for game {game_id}: {e}")

    def update_game_version_metadata(self, game_id: int, version_override: str, patch_notes_url: str) -> None:
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE games SET version_override = ?, patch_notes_url = ? WHERE id = ?",
                    (version_override or "", patch_notes_url or "", game_id),
                )
        except Exception as e:
            logger.error(f"Failed to update version metadata for game {game_id}: {e}")

    def add_game(self, name: str, path: str, executable: str, mode: str, banner_url: str = None, steam_id: str = None):
        try:
            with self.conn:
                cursor = self.conn.execute('''
                    INSERT INTO games (name, path, executable, mode, banner_url, steam_id, install_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (name, path, executable, mode, banner_url, steam_id, int(time.time())))
                logger.info(f"Added game '{name}' (path: {path}) to database.")
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"Failed to add game '{name}': {e}")
            return None

    def toggle_favorite(self, game_id: int) -> bool:
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT is_favorite FROM games WHERE id = ?", (game_id,))
            row = cursor.fetchone()
            current = row[0] if row and row[0] else 0
            new_val = 0 if current else 1
            with self.conn:
                self.conn.execute("UPDATE games SET is_favorite = ? WHERE id = ?", (new_val, game_id))
                row = self.conn.execute(
                    "SELECT name, steam_id FROM games WHERE id = ?", (game_id,)
                ).fetchone()
                if row:
                    identity = self.profile_identity(row[0], row[1])
                    self.conn.execute(
                        """INSERT INTO profile_games
                           (identity_key, app_id, favorite, favorite_changed_at, favorite_change_id, first_seen_at)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(identity_key) DO UPDATE SET
                             app_id = excluded.app_id,
                             favorite = excluded.favorite,
                             favorite_changed_at = excluded.favorite_changed_at,
                             favorite_change_id = excluded.favorite_change_id""",
                        (identity, str(row[1] or "").strip(), new_val, time.time(), str(uuid.uuid4()), time.time()),
                    )
            return bool(new_val)
        except Exception as e:
            logger.error(f"Failed to toggle favorite for game {game_id}: {e}")
            return False

    def update_last_played(self, game_id: int, timestamp: int):
        try:
            with self.conn:
                self.conn.execute("UPDATE games SET last_played = ? WHERE id = ?", (timestamp, game_id))
        except Exception as e:
            logger.error(f"Failed to update last_played for game {game_id}: {e}")

    def update_game_tags(self, game_id: int, tags: str):
        try:
            with self.conn:
                self.conn.execute("UPDATE games SET tags = ? WHERE id = ?", (tags, game_id))
        except Exception as e:
            logger.error(f"Failed to update tags for game {game_id}: {e}")

    def add_playtime(self, game_id: int, seconds: int) -> None:
        if seconds > 0:
            try:
                with self.conn:
                    self.conn.execute(
                        'UPDATE games SET playtime_seconds = COALESCE(playtime_seconds, 0) + ? WHERE id = ?',
                        (seconds, game_id)
                    )
                logger.debug(f"Added {seconds}s playtime to game {game_id}.")
            except Exception as e:
                logger.error(f"Failed to add playtime to game {game_id}: {e}")

    def get_playtime(self, game_id: int) -> int:
        try:
            cursor = self.conn.cursor()
            cursor.execute('SELECT playtime_seconds FROM games WHERE id = ?', (game_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] else 0
        except Exception as e:
            logger.error(f"Failed to get playtime for game {game_id}: {e}")
            return 0

    def merge_playtime_metadata(self, game_id: int, playtime_seconds: int, last_played: int) -> None:
        """Merge a cloud snapshot without double-counting sessions."""
        try:
            with self.conn:
                self.conn.execute(
                    """UPDATE games SET
                       playtime_seconds = MAX(COALESCE(playtime_seconds, 0), ?),
                       last_played = MAX(COALESCE(last_played, 0), ?)
                       WHERE id = ?""",
                    (max(0, int(playtime_seconds or 0)), max(0, int(last_played or 0)), game_id),
                )
        except Exception as e:
            logger.error(f"Failed to merge playtime metadata for game {game_id}: {e}")

    @staticmethod
    def profile_identity(name: str, app_id: str = "") -> str:
        """Return the portable account-profile identity for a game."""
        sid = str(app_id or "").strip()
        if sid and sid not in ("0", "None"):
            return f"steam:{sid}"
        normalized = re.sub(r"[^a-z0-9]+", "-", str(name or "").casefold()).strip("-")
        return f"local:{normalized or 'unnamed-game'}"

    def get_profile_games(self) -> List[dict]:
        rows = self.conn.execute(
            """SELECT identity_key, app_id, favorite, favorite_changed_at,
                      favorite_change_id, playtime_baseline_seconds, last_played
               FROM profile_games ORDER BY identity_key"""
        ).fetchall()
        return [{"identity_key": r[0], "app_id": r[1] or "", "favorite": bool(r[2]),
                 "favorite_changed_at": float(r[3] or 0), "favorite_change_id": r[4] or "",
                 "playtime_baseline_seconds": int(r[5] or 0), "last_played": int(r[6] or 0)} for r in rows]

    def merge_profile_game(self, value: dict) -> None:
        """Merge one generalized profile record and project it to matching games."""
        identity = str(value.get("identity_key", "")).strip()
        if not identity:
            return
        incoming_ts = float(value.get("favorite_changed_at", 0) or 0)
        incoming_id = str(value.get("favorite_change_id", "") or "")
        with self.conn:
            current = self.conn.execute(
                "SELECT favorite, favorite_changed_at, favorite_change_id FROM profile_games WHERE identity_key = ?",
                (identity,),
            ).fetchone()
            if current:
                current_key = (float(current[1] or 0), str(current[2] or ""))
                incoming_key = (incoming_ts, incoming_id)
                if current_key == incoming_key == (0, ""):
                    # Legacy records have no causal marker. Treat an old
                    # favorite as a positive fact instead of allowing a
                    # marker-less remote false value to erase it.
                    favorite = int(bool(current[0]) or bool(value.get("favorite")))
                elif incoming_key < current_key:
                    favorite = int(bool(current[0]))
                    incoming_ts, incoming_id = current_key[0], current_key[1]
                else:
                    favorite = int(bool(value.get("favorite")))
            else:
                favorite = int(bool(value.get("favorite")))
            self.conn.execute(
                """INSERT INTO profile_games
                   (identity_key, app_id, favorite, favorite_changed_at, favorite_change_id,
                    playtime_baseline_seconds, last_played, first_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(identity_key) DO UPDATE SET
                     app_id = excluded.app_id,
                     favorite = excluded.favorite,
                     favorite_changed_at = excluded.favorite_changed_at,
                     favorite_change_id = excluded.favorite_change_id,
                     playtime_baseline_seconds = MAX(profile_games.playtime_baseline_seconds, excluded.playtime_baseline_seconds),
                     last_played = MAX(profile_games.last_played, excluded.last_played)""",
                (identity, str(value.get("app_id", "") or ""), favorite, incoming_ts, incoming_id,
                 max(0, int(value.get("playtime_baseline_seconds", 0) or 0)),
                 max(0, int(value.get("last_played", 0) or 0)), time.time()),
            )

    def project_profile_game(self, game_id: int, value: dict) -> None:
        identity = str(value.get("identity_key", "")).strip()
        with self.conn:
            self.conn.execute(
                "UPDATE games SET is_favorite = ?, playtime_seconds = MAX(COALESCE(playtime_seconds, 0), ?), last_played = MAX(COALESCE(last_played, 0), ?) WHERE id = ?",
                (int(bool(value.get("favorite"))), max(0, int(value.get("playtime_seconds", 0) or 0)),
                 max(0, int(value.get("last_played", 0) or 0)), game_id),
            )

    def create_playtime_session(self, game_id: int, started_at: Optional[int] = None, session_id: str = "") -> str:
        """Create an idempotent playtime event for metadata synchronization."""
        sid = session_id.strip() or str(uuid.uuid4())
        started = max(0, int(started_at or time.time()))
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR IGNORE INTO playtime_sessions(session_id, game_id, started_at, updated_at) VALUES (?, ?, ?, ?)",
                    (sid, game_id, started, started),
                )
        except Exception as e:
            logger.error(f"Failed to create playtime session for game {game_id}: {e}")
        return sid

    def checkpoint_playtime_session(self, session_id: str, duration_seconds: int, finalized: bool = False, ended_at: int = 0) -> None:
        """Persist progress and keep the aggregate derived from the session ledger."""
        try:
            with self.conn:
                self.conn.execute(
                    """UPDATE playtime_sessions SET duration_seconds = MAX(duration_seconds, ?),
                       finalized = MAX(finalized, ?), ended_at = MAX(ended_at, ?), updated_at = ?
                       WHERE session_id = ?""",
                    (max(0, int(duration_seconds or 0)), int(bool(finalized)), max(0, int(ended_at or 0)), int(time.time()), session_id),
                )
                row = self.conn.execute(
                    "SELECT game_id FROM playtime_sessions WHERE session_id = ?", (session_id,)
                ).fetchone()
                if row:
                    game_id = int(row[0])
                    total = self.conn.execute(
                        "SELECT COALESCE(SUM(duration_seconds), 0) FROM playtime_sessions WHERE game_id = ?",
                        (game_id,),
                    ).fetchone()[0]
                    # Existing installations may have a pre-ledger aggregate.
                    # Preserve that baseline while making all tracked sessions
                    # idempotent rather than adding the same elapsed time twice.
                    self.conn.execute(
                        "UPDATE games SET playtime_seconds = MAX(COALESCE(playtime_seconds, 0), ?) WHERE id = ?",
                        (int(total or 0), game_id),
                    )
        except Exception as e:
            logger.error(f"Failed to checkpoint playtime session {session_id}: {e}")

    def get_playtime_sessions(self, game_id: int) -> List[dict]:
        try:
            rows = self.conn.execute(
                "SELECT session_id, started_at, ended_at, duration_seconds, finalized FROM playtime_sessions WHERE game_id = ? ORDER BY started_at",
                (game_id,),
            ).fetchall()
            return [{"session_id": r[0], "started_at": int(r[1] or 0), "ended_at": int(r[2] or 0),
                     "duration_seconds": int(r[3] or 0), "finalized": bool(r[4])} for r in rows]
        except Exception as e:
            logger.error(f"Failed to read playtime sessions for game {game_id}: {e}")
            return []

    def merge_playtime_sessions(self, game_id: int, sessions: List[dict]) -> None:
        """Merge remote sessions by ID and rebuild the aggregate counter."""
        if not sessions:
            return
        try:
            with self.conn:
                for item in sessions:
                    sid = str(item.get("session_id", "")).strip()
                    if not sid:
                        continue
                    self.conn.execute(
                        """INSERT INTO playtime_sessions(session_id, game_id, started_at, ended_at, duration_seconds, finalized, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(session_id) DO UPDATE SET
                             ended_at = MAX(playtime_sessions.ended_at, excluded.ended_at),
                             duration_seconds = MAX(playtime_sessions.duration_seconds, excluded.duration_seconds),
                             finalized = MAX(playtime_sessions.finalized, excluded.finalized),
                             updated_at = MAX(playtime_sessions.updated_at, excluded.updated_at)""",
                        (sid, game_id, max(0, int(item.get("started_at", 0) or 0)), max(0, int(item.get("ended_at", 0) or 0)),
                         max(0, int(item.get("duration_seconds", 0) or 0)), int(bool(item.get("finalized"))), int(time.time())),
                    )
                total = self.conn.execute("SELECT COALESCE(SUM(duration_seconds), 0) FROM playtime_sessions WHERE game_id = ?", (game_id,)).fetchone()[0]
                self.conn.execute("UPDATE games SET playtime_seconds = MAX(COALESCE(playtime_seconds, 0), ?) WHERE id = ?", (int(total or 0), game_id))
        except Exception as e:
            logger.error(f"Failed to merge playtime sessions for game {game_id}: {e}")

    def update_game(self, game_id: int, name: str, path: str, executable: str, mode: str, banner_url: str = None):
        try:
            with self.conn:
                self.conn.execute('''
                    UPDATE games 
                    SET name = ?, path = ?, executable = ?, mode = ?, banner_url = ?
                    WHERE id = ?
                ''', (name, path, executable, mode, banner_url, game_id))
                logger.info(f"Updated game {game_id} ('{name}').")
        except Exception as e:
            logger.error(f"Failed to update game {game_id}: {e}")

    def update_game_mode(self, game_id: int, mode: str):
        try:
            with self.conn:
                self.conn.execute("UPDATE games SET mode = ? WHERE id = ?", (mode, game_id))
        except Exception as e:
            logger.error(f"Failed to update game mode for game {game_id}: {e}")

    def update_game_banner(self, game_id: int, banner_url: str):
        try:
            with self.conn:
                self.conn.execute('''
                    UPDATE games SET banner_url = ? WHERE id = ?
                ''', (banner_url, game_id))
        except Exception as e:
            logger.error(f"Failed to update banner for game {game_id}: {e}")

    def update_game_steam_id(self, game_id: int, steam_id: str) -> None:
        """Persist the Steam AppID discovered by metadata fetchers."""
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE games SET steam_id = ? WHERE id = ?",
                    (str(steam_id), game_id),
                )
        except Exception as e:
            logger.error(f"Failed to update Steam ID for game {game_id}: {e}")

    def update_game_proton_path(self, game_id: int, proton_path: str) -> None:
        try:
            with self.conn:
                self.conn.execute("UPDATE games SET proton_path = ? WHERE id = ?", (proton_path or "", game_id))
        except Exception as e:
            logger.error(f"Failed to update Proton path for game {game_id}: {e}")

    def update_game_collection(self, game_id: int, collection: str) -> None:
        try:
            with self.conn:
                self.conn.execute("UPDATE games SET collection = ? WHERE id = ?", (collection or "", game_id))
        except Exception as e:
            logger.error(f"Failed to update collection for game {game_id}: {e}")

    def add_collection(self, name: str) -> None:
        name = name.strip()
        if not name:
            return
        try:
            with self.conn:
                self.conn.execute("INSERT OR IGNORE INTO collections (name) VALUES (?)", (name,))
        except Exception as e:
            logger.error(f"Failed to add collection '{name}': {e}")

    def delete_collection(self, name: str) -> None:
        name = name.strip()
        if not name:
            return
        try:
            with self.conn:
                self.conn.execute("DELETE FROM collections WHERE name = ?", (name,))
                self.conn.execute("UPDATE games SET collection = '' WHERE collection = ?", (name,))
        except Exception as e:
            logger.error(f"Failed to delete collection '{name}': {e}")

    def rename_collection(self, old_name: str, new_name: str) -> None:
        old_name = old_name.strip()
        new_name = new_name.strip()
        if not old_name or not new_name:
            return
        try:
            with self.conn:
                self.conn.execute("DELETE FROM collections WHERE name = ?", (old_name,))
                self.conn.execute("INSERT OR REPLACE INTO collections (name) VALUES (?)", (new_name,))
                self.conn.execute("UPDATE games SET collection = ? WHERE collection = ?", (new_name, old_name))
        except Exception as e:
            logger.error(f"Failed to rename collection '{old_name}' -> '{new_name}': {e}")

    def get_all_collections(self) -> List[str]:
        try:
            cursor = self.conn.cursor()
            cols = set()
            for row in cursor.execute("SELECT name FROM collections"):
                if row[0]:
                    cols.add(str(row[0]).strip())
            for row in cursor.execute("SELECT DISTINCT collection FROM games WHERE collection != ''"):
                if row[0]:
                    cols.add(str(row[0]).strip())
            return sorted(list(cols), key=lambda x: x.lower())
        except Exception as e:
            logger.error(f"Failed to fetch collections: {e}")
            return []

    def archive_game(self, game_id: int, is_archived: bool = True) -> bool:
        """Mark a game as archived (or unarchived) without losing its data."""
        try:
            with self.conn:
                cursor = self.conn.execute(
                    "UPDATE games SET is_archived = ? WHERE id = ?",
                    (1 if is_archived else 0, game_id),
                )
                if cursor.rowcount != 1:
                    return False
                logger.info(f"{'Archived' if is_archived else 'Restored'} game ID {game_id}")
                return True
        except Exception as e:
            logger.error(f"Failed to set archived status for game {game_id}: {e}")
            return False

    def restore_game(self, game_id: int) -> bool:
        """Restore an archived game back to the active library."""
        return self.archive_game(game_id, is_archived=False)

    def update_game_icon(self, game_id: int, icon_url: str) -> None:
        try:
            with self.conn:
                self.conn.execute("UPDATE games SET icon_url = ? WHERE id = ?", (icon_url or "", game_id))
        except Exception as e:
            logger.error(f"Failed to update icon for game {game_id}: {e}")

    def update_game_env_vars(self, game_id: int, env_vars: dict | str) -> None:
        """Update per-game environment variables and presets (stored as JSON string)."""
        try:
            val_str = json.dumps(env_vars) if isinstance(env_vars, dict) else str(env_vars or "{}")
            with self.conn:
                self.conn.execute("UPDATE games SET env_vars = ? WHERE id = ?", (val_str, game_id))
            logger.info(f"Updated environment variables for game {game_id}")
        except Exception as e:
            logger.error(f"Failed to update env_vars for game {game_id}: {e}")

    def get_game_env_vars(self, game_id: int) -> dict:
        """Retrieve per-game environment variables as a dictionary."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT env_vars FROM games WHERE id = ?", (game_id,))
            row = cursor.fetchone()
            if row and row[0]:
                return json.loads(row[0])
            return {}
        except Exception as e:
            logger.error(f"Failed to get env_vars for game {game_id}: {e}")
            return {}

    def get_all_games(self) -> List[GameRecord]:
        try:
            cursor = self.conn.cursor()
            cursor.execute(f'SELECT {self.GAME_COLUMNS} FROM games')
            rows = cursor.fetchall()
            return [
                GameRecord(
                    id=r[0], name=r[1], path=r[2], executable=r[3], mode=r[4],
                    banner_url=r[5] or "", steam_id=r[6] or "", playtime_seconds=r[7] or 0,
                    is_favorite=r[8] or 0, last_played=r[9] or 0, tags=r[10] or "",
                    build_id=r[11] or "", proton_path=r[12] or "", collection=r[13] or "",
                    install_date=r[14] or 0, version_override=r[15] or "", patch_notes_url=r[16] or "",
                    is_archived=r[17] if len(r) > 17 and r[17] else 0,
                    icon_url=r[18] if len(r) > 18 and r[18] else "",
                    env_vars=r[19] if len(r) > 19 and r[19] else "{}",
                    build_date=r[20] if len(r) > 20 and r[20] else 0,
                )
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Failed to fetch games list: {e}")
            return []

    def remove_game(self, game_id: int) -> bool:
        """Remove a launcher game row while retaining append-only profile history."""
        try:
            with self.conn:
                cursor = self.conn.execute('DELETE FROM games WHERE id = ?', (game_id,))
                if cursor.rowcount != 1:
                    return False
                self.conn.execute('DELETE FROM achievements WHERE game_id = ?', (game_id,))
                logger.info(f"Removed game {game_id} and its achievements from database.")
                return True
        except Exception as e:
            logger.error(f"Failed to remove game {game_id}: {e}")
            return False

    def save_achievement_schema(self, game_id: int, app_id: str, achievements: List[dict]) -> int:
        """Insert or update achievement schema definitions for a game, preserving existing unlocked state."""
        app_id = str(app_id or "").strip()
        if not _ACHIEVEMENT_APP_RE.fullmatch(app_id) or app_id == "0":
            return 0
        if not achievements:
            return 0
        inserted = 0
        valid_names = set()
        try:
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                for ach in achievements:
                    api_name = _valid_achievement_api_name(ach.get("api_name", ""))
                    if not api_name:
                        continue
                    valid_names.add(api_name)
                    display_name = str(ach.get("display_name", api_name)).strip()
                    desc = str(ach.get("description", "")).strip()
                    icon_path = str(ach.get("icon_path") or ach.get("icon_url", "")).strip()
                    icongray_path = str(ach.get("icongray_path") or ach.get("icongray_url", "")).strip()
                    hidden = int(ach.get("hidden", 0))

                    self.conn.execute("""
                        INSERT INTO achievements (game_id, app_id, api_name, display_name, description, icon_path, icongray_path, hidden)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(game_id, api_name) DO UPDATE SET
                            app_id = excluded.app_id,
                            display_name = excluded.display_name,
                            description = excluded.description,
                            icon_path = CASE WHEN excluded.icon_path != '' THEN excluded.icon_path ELSE achievements.icon_path END,
                            icongray_path = CASE WHEN excluded.icongray_path != '' THEN excluded.icongray_path ELSE achievements.icongray_path END,
                            hidden = excluded.hidden
                    """, (game_id, str(app_id), api_name, display_name, desc, icon_path, icongray_path, hidden))
                    inserted += 1
                # A state file can be observed before the network/local
                # schema arrives. Promote only matching pending records.
                if valid_names:
                    placeholders = ",".join("?" for _ in valid_names)
                    self.conn.execute(
                        f"UPDATE achievement_profile SET validation_state = 'validated' WHERE app_id = ? AND api_name IN ({placeholders})",
                        (str(app_id), *sorted(valid_names)),
                    )
            # Rehydrate a newly added/re-added game from the account-wide
            # append-only ledger without allowing the schema to delete it.
            self.project_profile_achievements(game_id, app_id)
            return inserted
        except Exception as e:
            logger.error(f"Error saving achievement schema for game {game_id}: {e}")
            return 0

    def unlock_achievement(
        self, game_id: int, api_name: str, unlock_time: float = 0.0,
        *, provenance: str = "local_emulator", verified: bool = False,
        source_format: str = "", source_path: str = "",
    ) -> bool:
        """Mark a schema-backed achievement as unlocked with provenance."""
        api_name = _valid_achievement_api_name(api_name)
        if not api_name:
            return False
        provenance = _normalise_achievement_provenance(provenance)
        verified = provenance == "steam_verified"
        unlock_time = _safe_achievement_timestamp(unlock_time)
        try:
            app_row = self.conn.execute("SELECT app_id FROM achievements WHERE game_id = ? AND api_name = ?", (game_id, api_name)).fetchone()
            if app_row and app_row[0]:
                profile_row = self.conn.execute(
                    "SELECT provenance, verified FROM achievement_profile WHERE app_id = ? AND api_name = ?",
                    (str(app_row[0]), api_name),
                ).fetchone()
                if profile_row and bool(profile_row[1]):
                    provenance = _normalise_achievement_provenance(profile_row[0] or "steam_verified")
                    verified = provenance == "steam_verified"
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                cursor = self.conn.execute("""
                    UPDATE achievements 
                    SET unlocked = 1, unlock_time = ?, unlock_provenance = ?,
                        unlock_verified = ?, unlock_source_format = ?, unlock_source_path = ?, notification_sent = 0
                    WHERE game_id = ? AND api_name = ? AND unlocked = 0
                """, (unlock_time, provenance, int(bool(verified)), source_format or "", source_path or "", game_id, api_name))
                changed = cursor.rowcount > 0
            if changed:
                if app_row and app_row[0]:
                    self.merge_profile_unlocks(
                        str(app_row[0]), {api_name: unlock_time},
                        provenance=provenance, verified=verified,
                        validation_state="validated", source_format=source_format,
                        source_path=source_path,
                    )
            return changed
        except Exception as e:
            logger.error(f"Error unlocking achievement {api_name} for game {game_id}: {e}")
            return False

    def unlock_achievements_batch(
        self, game_id: int, unlocks: Dict[str, float], *, app_id: str = "",
        provenance: str = "local_emulator", verified: bool = False,
        source_format: str = "", source_path: str = "",
    ) -> int:
        """Record a batch, promoting only schema-backed names to visible rows.

        Unknown names are retained in the profile as ``pending_schema`` when
        an AppID is available, but never affect per-game counts.
        """
        if not unlocks:
            return 0
        provenance = _normalise_achievement_provenance(provenance)
        verified = provenance == "steam_verified"
        now = time.time()
        normalized = {}
        for api_name, raw_value in unlocks.items():
            name = _valid_achievement_api_name(api_name)
            if not name:
                continue
            raw_time = raw_value.get("unlock_time", 0) if isinstance(raw_value, dict) else raw_value
            try:
                stamp = float(raw_time or 0)
            except (TypeError, ValueError, OverflowError):
                stamp = 0.0
            normalized[name] = _safe_achievement_timestamp(stamp, now)
        if not normalized:
            return 0
        if app_id and (not _ACHIEVEMENT_APP_RE.fullmatch(str(app_id).strip()) or str(app_id).strip() == "0"):
            return 0
        try:
            schema_rows = self.conn.execute(
                "SELECT api_name, app_id FROM achievements WHERE game_id = ?", (game_id,)
            ).fetchall()
            known_names = {str(row[0]) for row in schema_rows}
            if not app_id:
                app_id = str(schema_rows[0][1] or "") if schema_rows else ""
            params = [
                (stamp, provenance, int(bool(verified)), source_format or "", source_path or "", game_id, api_name)
                for api_name, stamp in normalized.items() if api_name in known_names
            ]
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                cursor = self.conn.executemany("""
                    UPDATE achievements
                    SET unlocked = 1, unlock_time = ?, unlock_provenance = ?,
                        unlock_verified = ?, unlock_source_format = ?, unlock_source_path = ?
                    WHERE game_id = ? AND api_name = ? AND unlocked = 0
                """, params)
                changed = cursor.rowcount
            if app_id:
                self.merge_profile_unlocks(
                    app_id, normalized, provenance=provenance, verified=verified,
                    validation_state="validated", source_format=source_format,
                    source_path=source_path, allowed_api_names=known_names,
                )
            return changed
        except Exception as e:
            logger.error(f"Error in batch achievement unlock for game {game_id}: {e}")
            return 0

    def claim_achievement_notification(self, game_id: int, api_name: str) -> bool:
        """Atomically claim the one user-facing notification for an unlock."""
        api_name = _valid_achievement_api_name(api_name)
        if not api_name:
            return False
        try:
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                cursor = self.conn.execute("""
                    UPDATE achievements SET notification_sent = 1
                    WHERE game_id = ? AND api_name = ? AND unlocked = 1 AND notification_sent = 0
                """, (game_id, api_name))
                return cursor.rowcount > 0
        except Exception as e:
            logger.debug(f"Could not claim achievement notification {api_name}: {e}")
            return False

    def arm_achievement_notification(self, game_id: int, api_name: str) -> None:
        """Mark a schema-late live event as pending user notification."""
        api_name = _valid_achievement_api_name(api_name)
        if not api_name:
            return
        try:
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                self.conn.execute(
                    "UPDATE achievements SET notification_sent = 0 WHERE game_id = ? AND api_name = ? AND unlocked = 1",
                    (game_id, api_name),
                )
        except Exception as e:
            logger.debug(f"Could not arm achievement notification {api_name}: {e}")

    def merge_profile_unlocks(
        self, app_id: str, unlocks: Dict[str, float], *, provenance: str = "local_emulator",
        verified: bool = False, validation_state: str = "validated", source_format: str = "",
        source_path: str = "", allowed_api_names: Optional[set[str]] = None,
    ) -> int:
        """Append observations to the account ledger without deleting proof."""
        app_id = str(app_id or "").strip()
        if not _ACHIEVEMENT_APP_RE.fullmatch(app_id) or app_id == "0" or not unlocks:
            return 0
        provenance = _normalise_achievement_provenance(provenance)
        verified = provenance == "steam_verified"
        now = time.time()
        changed = 0
        try:
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                for api_name, raw_value in unlocks.items():
                    name = _valid_achievement_api_name(api_name)
                    if not name:
                        continue
                    is_record = isinstance(raw_value, dict)
                    value = raw_value if is_record else {}
                    incoming_provenance = _normalise_achievement_provenance(
                        value.get("provenance", provenance)
                    )
                    incoming_verified = incoming_provenance == "steam_verified"
                    incoming_state = str(value.get("validation_state", validation_state) or validation_state)
                    if allowed_api_names is not None and name not in allowed_api_names:
                        incoming_state = "pending_schema"
                    if incoming_state not in {"validated", "pending_schema"}:
                        incoming_state = "pending_schema"
                    raw_time = value.get("unlock_time", 0) if is_record else raw_value
                    try:
                        stamp = float(raw_time or 0) if raw_time else 0.0
                    except (TypeError, ValueError, OverflowError):
                        stamp = 0.0
                    stamp = _safe_achievement_timestamp(stamp, now)
                    existing = self.conn.execute(
                        "SELECT unlock_time, provenance, verified, validation_state, source_format, source_path FROM achievement_profile WHERE app_id = ? AND api_name = ?",
                        (app_id, name),
                    ).fetchone()
                    if existing:
                        old_time, old_provenance, old_verified, old_state, old_format, old_path = existing
                        from core.achievement_models import provenance_priority
                        old_stamp = _safe_achievement_timestamp(old_time, 0.0)
                        old_priority = provenance_priority(str(old_provenance or "unknown"))
                        new_priority = provenance_priority(incoming_provenance)
                        chosen_provenance = incoming_provenance if new_priority >= old_priority else str(old_provenance or incoming_provenance)
                        chosen_format = source_format or (value.get("source_format", "") if isinstance(value, dict) else "") or old_format or ""
                        chosen_path = source_path or (value.get("source_path", "") if isinstance(value, dict) else "") or old_path or ""
                        chosen_state = "validated" if str(old_state) == "validated" or incoming_state == "validated" else "pending_schema"
                        chosen_time = min(old_stamp, stamp) if old_stamp > 0 and stamp > 0 else max(old_stamp, stamp)
                        chosen_verified = chosen_provenance == "steam_verified"
                        self.conn.execute("""
                            UPDATE achievement_profile
                            SET unlock_time = ?, last_seen_at = ?, provenance = ?, verified = ?,
                                validation_state = ?, source_format = ?, source_path = ?
                            WHERE app_id = ? AND api_name = ?
                        """, (chosen_time, now, chosen_provenance, int(chosen_verified), chosen_state, chosen_format, chosen_path, app_id, name))
                        changed += int(
                            old_stamp != chosen_time
                            or str(old_provenance or "") != chosen_provenance
                            or bool(old_verified) != chosen_verified
                            or str(old_state or "") != chosen_state
                            or str(old_format or "") != chosen_format
                            or str(old_path or "") != chosen_path
                        )
                    else:
                        self.conn.execute("""
                            INSERT INTO achievement_profile
                              (app_id, api_name, unlock_time, first_seen_at, last_seen_at, provenance, verified, validation_state, source_format, source_path)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (app_id, name, stamp, now, now, incoming_provenance, int(incoming_verified), incoming_state, source_format or (value.get("source_format", "") if isinstance(value, dict) else ""), source_path or (value.get("source_path", "") if isinstance(value, dict) else "")))
                        changed += 1
            return changed
        except Exception as e:
            logger.error(f"Error merging achievement profile for AppID {app_id}: {e}")
            return 0

    def record_achievement_state(
        self, game_id: int, app_id: str, state: Dict[str, float], *,
        pending: Optional[Dict[str, float]] = None,
        provenance: str = "local_emulator", verified: bool = False,
        source_format: str = "", source_path: str = "",
        provenance_by_name: Optional[Dict[str, str]] = None,
        verified_by_name: Optional[Dict[str, bool]] = None,
    ) -> int:
        """Persist one resolver snapshot atomically at the two DB layers."""
        app_id = str(app_id or "").strip()
        if not _ACHIEVEMENT_APP_RE.fullmatch(app_id) or app_id == "0":
            return 0
        provenance = _normalise_achievement_provenance(provenance)
        verified = provenance == "steam_verified"
        state = state or {}
        pending = pending or {}
        provenance_by_name = provenance_by_name or {}
        verified_by_name = verified_by_name or {}
        try:
            schema_names = {
                str(row[0]) for row in self.conn.execute(
                    "SELECT api_name FROM achievements WHERE game_id = ?", (game_id,)
                ).fetchall()
            }
            known = {name: stamp for name, stamp in state.items() if name in schema_names}
            unknown = {name: stamp for name, stamp in state.items() if name not in schema_names}
            unknown.update(pending)
            total = 0
            # Update each known item with its own source. Official Steam data
            # must be able to upgrade a previous local/unverified projection.
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                for api_name, raw_time in known.items():
                    name = _valid_achievement_api_name(api_name)
                    if not name:
                        continue
                    try:
                        stamp = float(raw_time or 0)
                    except (TypeError, ValueError, OverflowError):
                        stamp = 0.0
                    stamp = _safe_achievement_timestamp(stamp)
                    item_provenance = _normalise_achievement_provenance(
                        provenance_by_name.get(name, provenance) or provenance
                    )
                    item_verified = item_provenance == "steam_verified"
                    total += self.conn.execute("""
                        UPDATE achievements
                        SET unlocked = 1,
                            unlock_time = CASE WHEN unlock_time <= 0 THEN ? ELSE MIN(unlock_time, ?) END,
                            unlock_provenance = CASE WHEN ? OR unlock_verified = 0 THEN ? ELSE unlock_provenance END,
                            unlock_verified = MAX(unlock_verified, ?),
                            unlock_source_format = CASE WHEN ? != '' THEN ? ELSE unlock_source_format END,
                            unlock_source_path = CASE WHEN ? != '' THEN ? ELSE unlock_source_path END,
                            notification_sent = CASE WHEN unlocked = 1 THEN notification_sent ELSE 1 END
                        WHERE game_id = ? AND api_name = ?
                    """, (stamp, stamp, int(item_verified), item_provenance, int(item_verified), source_format or "", source_format or "", source_path or "", source_path or "", game_id, name)).rowcount
            observations = {
                name: {
                    "unlock_time": stamp,
                    "provenance": provenance_by_name.get(name, provenance),
                    "verified": _normalise_achievement_provenance(
                        provenance_by_name.get(name, provenance) or provenance
                    ) == "steam_verified",
                    "validation_state": "validated",
                    "source_format": source_format,
                    "source_path": source_path,
                }
                for name, stamp in known.items()
            }
            observations.update({
                name: {
                    "unlock_time": stamp,
                    "provenance": provenance_by_name.get(name, provenance),
                    "verified": _normalise_achievement_provenance(
                        provenance_by_name.get(name, provenance) or provenance
                    ) == "steam_verified",
                    "validation_state": "pending_schema",
                    "source_format": source_format,
                    "source_path": source_path,
                }
                for name, stamp in unknown.items()
            })
            if observations:
                total += self.merge_profile_unlocks(
                    app_id, observations, allowed_api_names=schema_names,
                )
            return total
        except Exception as e:
            logger.error(f"Error recording achievement state for game {game_id}: {e}")
            return 0

    def get_profile_unlocks(self, app_id: Optional[str] = None, *, include_pending: bool = False) -> Dict[str, Dict[str, float]]:
        """Return schema-validated append-only unlocks grouped by AppID."""
        try:
            if app_id:
                query = "SELECT app_id, api_name, unlock_time FROM achievement_profile WHERE app_id = ?"
                if not include_pending:
                    query += " AND validation_state = 'validated'"
                rows = self.conn.execute(query, (str(app_id),)).fetchall()
            else:
                query = "SELECT app_id, api_name, unlock_time FROM achievement_profile"
                if not include_pending:
                    query += " WHERE validation_state = 'validated'"
                rows = self.conn.execute(query).fetchall()
            result: Dict[str, Dict[str, float]] = {}
            for sid, name, stamp in rows:
                result.setdefault(str(sid), {})[str(name)] = float(stamp or 0)
            return result
        except Exception as e:
            logger.error(f"Error reading achievement profile: {e}")
            return {}

    def get_profile_unlock_records(self, app_id: Optional[str] = None, *, include_pending: bool = True) -> Dict[str, Dict[str, dict]]:
        """Return provenance-aware profile records for cloud sync/diagnostics."""
        try:
            query = "SELECT app_id, api_name, unlock_time, provenance, verified, validation_state, source_format FROM achievement_profile"
            args: tuple = ()
            if app_id:
                query += " WHERE app_id = ?"
                args = (str(app_id),)
            if not include_pending:
                query += " AND " if args else " WHERE "
                query += "validation_state = 'validated'"
            result: Dict[str, Dict[str, dict]] = {}
            for sid, name, stamp, provenance, verified, validation_state, source_format in self.conn.execute(query, args).fetchall():
                result.setdefault(str(sid), {})[str(name)] = {
                    "unlock_time": float(stamp or 0),
                    "provenance": str(provenance or "unknown"),
                    "verified": _normalise_achievement_provenance(provenance) == "steam_verified",
                    "validation_state": str(validation_state or "pending_schema"),
                    "source_format": str(source_format or ""),
                }
            return result
        except Exception as e:
            logger.error(f"Error reading achievement profile records: {e}")
            return {}

    def collect_profile_from_games(self) -> int:
        """Promote all existing per-game unlock projections into the ledger."""
        rows = self.conn.execute("SELECT app_id, api_name, unlock_time, unlock_provenance, unlock_verified, unlock_source_format, unlock_source_path FROM achievements WHERE unlocked = 1 AND app_id != ''").fetchall()
        grouped: Dict[str, Dict[str, dict]] = {}
        for app_id, api_name, stamp, provenance, verified, source_format, source_path in rows:
            grouped.setdefault(str(app_id), {})[str(api_name)] = {
                "unlock_time": float(stamp or 0), "provenance": provenance or "local_emulator",
                "verified": _normalise_achievement_provenance(provenance) == "steam_verified", "validation_state": "validated",
                "source_format": source_format or "", "source_path": source_path or "",
            }
        total = 0
        for app_id, unlocks in grouped.items():
            total += self.merge_profile_unlocks(app_id, unlocks)
        return total

    def project_profile_achievements(self, game_id: int, app_id: str = "") -> int:
        """Apply profile unlocks to matching current game schema rows only."""
        app_id = str(app_id or "").strip()
        if not app_id:
            row = self.conn.execute("SELECT app_id FROM achievements WHERE game_id = ? LIMIT 1", (game_id,)).fetchone()
            app_id = str(row[0] or "") if row else ""
        unlocks = self.get_profile_unlock_records(app_id, include_pending=False)
        values = unlocks.get(app_id, {})
        if not values:
            return 0
        changed = 0
        try:
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                for api_name, value in values.items():
                    changed += self.conn.execute("""
                        UPDATE achievements
                        SET unlocked = 1,
                            unlock_time = CASE WHEN unlock_time <= 0 THEN ? ELSE MIN(unlock_time, ?) END,
                            unlock_provenance = ?, unlock_verified = ?,
                            unlock_source_format = ?, notification_sent = 1
                        WHERE game_id = ? AND api_name = ?
                    """, (float(value.get("unlock_time", 0) or time.time()), float(value.get("unlock_time", 0) or time.time()), _normalise_achievement_provenance(value.get("provenance", "unknown")), int(_normalise_achievement_provenance(value.get("provenance", "unknown")) == "steam_verified"), value.get("source_format", ""), game_id, api_name)).rowcount
            return changed
        except Exception as e:
            logger.error(f"Error projecting achievement profile for game {game_id}: {e}")
            return 0

    def get_game_achievements(self, game_id: int) -> List[dict]:
        """Return all achievements for a game, ordered by unlocked status and name."""
        try:
            cursor = self.conn.execute("""
                SELECT id, game_id, app_id, api_name, display_name, description, icon_path, icongray_path, unlocked, unlock_time, hidden, unlock_provenance, unlock_verified, unlock_source_format
                FROM achievements
                WHERE game_id = ?
                ORDER BY unlocked DESC, unlock_time DESC, display_name ASC
            """, (game_id,))
            rows = cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "game_id": r[1],
                    "app_id": r[2],
                    "api_name": r[3],
                    "display_name": r[4],
                    "description": r[5],
                    "icon_path": r[6],
                    "icongray_path": r[7],
                    "unlocked": bool(r[8]),
                    "unlock_time": float(r[9]),
                    "hidden": bool(r[10]),
                    "provenance": _normalise_achievement_provenance(r[11]),
                    "verified": _normalise_achievement_provenance(r[11]) == "steam_verified",
                    "source_format": r[13] or "",
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Error getting achievements for game {game_id}: {e}")
            return []

    def get_profile_achievement_rows(self) -> Dict[int, List[dict]]:
        """Return the small achievement projection needed by public profiles.

        Public profile rendering used to issue one full achievement query per
        game. Keep the existing detailed API for the achievements page, but
        provide this narrow bulk read for profile generation so a large local
        library does not create an N+1 query pattern.
        """
        try:
            rows = self.conn.execute("""
                SELECT game_id, app_id, api_name, display_name
                FROM achievements
                ORDER BY game_id, unlocked DESC, unlock_time DESC, display_name ASC
            """).fetchall()
            result: Dict[int, List[dict]] = {}
            for game_id, app_id, api_name, display_name in rows:
                result.setdefault(int(game_id), []).append({
                    "app_id": str(app_id or ""),
                    "api_name": str(api_name or ""),
                    "display_name": str(display_name or ""),
                })
            return result
        except Exception as e:
            logger.error(f"Error getting profile achievement rows: {e}")
            return {}

    def get_achievement_stats(self, game_id: int) -> Tuple[int, int, float]:
        """Return (unlocked_count, total_count, percentage)."""
        try:
            cursor = self.conn.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN unlocked = 1 THEN 1 ELSE 0 END) as unlocked
                FROM achievements
                WHERE game_id = ?
            """, (game_id,))
            row = cursor.fetchone()
            if not row or not row[0]:
                return 0, 0, 0.0
            total = int(row[0])
            unlocked = int(row[1] or 0)
            pct = round((unlocked / total) * 100.0, 1) if total > 0 else 0.0
            return unlocked, total, pct
        except Exception as e:
            logger.error(f"Error getting achievement stats for game {game_id}: {e}")
            return 0, 0, 0.0

    def get_global_achievement_stats(self) -> Dict[str, Any]:
        """Return global achievement statistics across all games."""
        try:
            cursor = self.conn.execute("""
                SELECT 
                    COUNT(DISTINCT game_id) as total_games_with_achs,
                    COUNT(*) as total_achievements,
                    SUM(CASE WHEN unlocked = 1 THEN 1 ELSE 0 END) as total_unlocked
                FROM achievements
            """)
            row = cursor.fetchone()
            if not row or not row[1]:
                return {"games_count": 0, "total_achievements": 0, "total_unlocked": 0, "percentage": 0.0}
            games_cnt = int(row[0] or 0)
            total_achs = int(row[1] or 0)
            total_unlocked = int(row[2] or 0)
            pct = round((total_unlocked / total_achs) * 100.0, 1) if total_achs > 0 else 0.0
            return {
                "games_count": games_cnt,
                "total_achievements": total_achs,
                "total_unlocked": total_unlocked,
                "percentage": pct
            }
        except Exception as e:
            logger.error(f"Error getting global achievement stats: {e}")
            return {"games_count": 0, "total_achievements": 0, "total_unlocked": 0, "percentage": 0.0}

    def get_recent_unlocked_achievements(self, game_id: int, limit: int = 6) -> List[dict]:
        """Return the most recently unlocked achievements for a game."""
        try:
            cursor = self.conn.execute("""
                SELECT id, game_id, app_id, api_name, display_name, description, icon_path, icongray_path, unlocked, unlock_time, hidden, unlock_provenance, unlock_verified, unlock_source_format
                FROM achievements
                WHERE game_id = ? AND unlocked = 1
                ORDER BY unlock_time DESC, id DESC
                LIMIT ?
            """, (game_id, limit))
            rows = cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "game_id": r[1],
                    "app_id": r[2],
                    "api_name": r[3],
                    "display_name": r[4],
                    "description": r[5],
                    "icon_path": r[6],
                    "icongray_path": r[7],
                    "unlocked": bool(r[8]),
                    "unlock_time": float(r[9]),
                    "hidden": bool(r[10]),
                    "provenance": _normalise_achievement_provenance(r[11]),
                    "verified": _normalise_achievement_provenance(r[11]) == "steam_verified",
                    "source_format": r[13] or "",
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Error getting recent unlocked achievements: {e}")
            return []

    def reset_game_achievements(self, game_id: int):
        """Reset unlocked status of all achievements for a game (for testing/re-locking)."""
        try:
            with self.conn:
                self.conn.execute("""
                    UPDATE achievements
                    SET unlocked = 0, unlock_time = 0, unlock_provenance = 'unknown',
                        unlock_verified = 0, unlock_source_format = '', unlock_source_path = '', notification_sent = 1
                    WHERE game_id = ?
                """, (game_id,))
        except Exception as e:
            logger.error(f"Error resetting achievements for game {game_id}: {e}")


    def close(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
