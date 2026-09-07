import sqlite3
import os
import json
import shutil
import time
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


def _create_database_backup(db_path: str) -> None:
    """Create auto-backup copy (library.db.bak) on startup.

    Only called after the database file has passed a consistency check, so a
    corrupted database can never overwrite the last known-good recovery copy.
    Guarded to run at most once per process lifetime to avoid multi-thread I/O races.
    """
    if db_path == ":memory:" or not os.path.isfile(db_path):
        return
    if db_path in _BACKUP_CREATED:
        return
    _BACKUP_CREATED.add(db_path)
    bak_path = f"{db_path}.bak"
    try:
        shutil.copy2(db_path, bak_path)
        logger.debug(f"Created database backup: {bak_path}")
    except Exception as e:
        logger.warning(f"Could not create database backup: {e}")


from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple, Any



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

    def __getitem__(self, idx):
        fields = (
            self.id, self.name, self.path, self.executable, self.mode,
            self.banner_url or "", self.steam_id or "", self.playtime_seconds,
            self.is_favorite, self.last_played, self.tags, self.build_id,
            self.proton_path, self.collection, self.install_date,
            self.version_override, self.patch_notes_url,
            self.is_archived, self.icon_url, self.env_vars or "{}"
        )
        return fields[idx]

    def __len__(self):
        return 20

    def __hash__(self):
        return hash(self.id)

    def __iter__(self):
        return iter((
            self.id, self.name, self.path, self.executable, self.mode,
            self.banner_url or "", self.steam_id or "", self.playtime_seconds,
            self.is_favorite, self.last_played, self.tags, self.build_id,
            self.proton_path, self.collection, self.install_date,
            self.version_override, self.patch_notes_url,
            self.is_archived, self.icon_url, self.env_vars or "{}"
        ))


class GameDatabase:
    GAME_COLUMNS = (
        "id, name, path, executable, mode, banner_url, steam_id, "
        "playtime_seconds, is_favorite, last_played, tags, build_id"
        ", proton_path, collection, install_date"
        ", version_override, patch_notes_url"
        ", is_archived, icon_url, env_vars"
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

        if db_path != ":memory:":
            try:
                os.chmod(db_path, 0o600)
            except Exception:
                pass
            # Back up only after the connection was verified consistent, so the
            # recovery copy always holds the newest healthy snapshot.
            _create_database_backup(db_path)

        self._create_table()

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

    def archive_game(self, game_id: int, is_archived: bool = True) -> None:
        """Mark a game as archived (or unarchived) preserving its playtime, config, and save data."""
        try:
            with self.conn:
                self.conn.execute("UPDATE games SET is_archived = ? WHERE id = ?", (1 if is_archived else 0, game_id))
                logger.info(f"{'Archived' if is_archived else 'Restored'} game ID {game_id}")
        except Exception as e:
            logger.error(f"Failed to set archived status for game {game_id}: {e}")

    def restore_game(self, game_id: int) -> None:
        """Restore an archived game back to the active library."""
        self.archive_game(game_id, is_archived=False)

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
                    env_vars=r[19] if len(r) > 19 and r[19] else "{}"
                )
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Failed to fetch games list: {e}")
            return []

    def remove_game(self, game_id: int):
        try:
            with self.conn:
                self.conn.execute('DELETE FROM games WHERE id = ?', (game_id,))
                self.conn.execute('DELETE FROM achievements WHERE game_id = ?', (game_id,))
                logger.info(f"Removed game {game_id} and its achievements from database.")
        except Exception as e:
            logger.error(f"Failed to remove game {game_id}: {e}")

    def save_achievement_schema(self, game_id: int, app_id: str, achievements: List[dict]) -> int:
        """Insert or update achievement schema definitions for a game, preserving existing unlocked state."""
        if not achievements:
            return 0
        inserted = 0
        try:
            with self.conn:
                for ach in achievements:
                    api_name = str(ach.get("api_name", "")).strip()
                    if not api_name:
                        continue
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
            return inserted
        except Exception as e:
            logger.error(f"Error saving achievement schema for game {game_id}: {e}")
            return 0

    def unlock_achievement(self, game_id: int, api_name: str, unlock_time: float = 0.0) -> bool:
        """Mark an achievement as unlocked with a timestamp."""
        if not unlock_time or unlock_time <= 0:
            unlock_time = time.time()
        try:
            with self.conn:
                cursor = self.conn.execute("""
                    UPDATE achievements 
                    SET unlocked = 1, unlock_time = ?
                    WHERE game_id = ? AND api_name = ? AND unlocked = 0
                """, (unlock_time, game_id, api_name))
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error unlocking achievement {api_name} for game {game_id}: {e}")
            return False

    def unlock_achievements_batch(self, game_id: int, unlocks: Dict[str, float]) -> int:
        """Mark multiple achievements as unlocked in a single atomic database transaction."""
        if not unlocks:
            return 0
        now = time.time()
        params = [
            (unlock_time if unlock_time and unlock_time > 0 else now, game_id, api_name)
            for api_name, unlock_time in unlocks.items()
        ]
        try:
            with self.conn:
                cursor = self.conn.executemany("""
                    UPDATE achievements
                    SET unlocked = 1, unlock_time = ?
                    WHERE game_id = ? AND api_name = ? AND unlocked = 0
                """, params)
                return cursor.rowcount
        except Exception as e:
            logger.error(f"Error in batch achievement unlock for game {game_id}: {e}")
            return 0

    def get_game_achievements(self, game_id: int) -> List[dict]:
        """Return all achievements for a game, ordered by unlocked status and name."""
        try:
            cursor = self.conn.execute("""
                SELECT id, game_id, app_id, api_name, display_name, description, icon_path, icongray_path, unlocked, unlock_time, hidden
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
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Error getting achievements for game {game_id}: {e}")
            return []

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
                SELECT id, game_id, app_id, api_name, display_name, description, icon_path, icongray_path, unlocked, unlock_time, hidden
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
                self.conn.execute("UPDATE achievements SET unlocked = 0, unlock_time = 0 WHERE game_id = ?", (game_id,))
        except Exception as e:
            logger.error(f"Error resetting achievements for game {game_id}: {e}")


    def close(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass

