import sqlite3
import os
import shutil
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


def _create_database_backup(db_path: str) -> None:
    """Create auto-backup copy (library.db.bak) on startup."""
    if db_path == ":memory:" or not os.path.isfile(db_path):
        return
    bak_path = f"{db_path}.bak"
    try:
        shutil.copy2(db_path, bak_path)
        logger.debug(f"Created database backup: {bak_path}")
    except Exception as e:
        logger.warning(f"Could not create database backup: {e}")


class GameDatabase:
    GAME_COLUMNS = (
        "id, name, path, executable, mode, banner_url, steam_id, "
        "playtime_seconds, is_favorite, last_played, tags, build_id"
    )

    def __init__(self, db_path: str = None):
        if db_path is None or db_path == "library.db":
            db_path = DEFAULT_DB_PATH

        self.db_path = db_path

        if db_path != ":memory:":
            os.makedirs(os.path.dirname(db_path), mode=0o700, exist_ok=True)
            _migrate_legacy_db(db_path)
            _create_database_backup(db_path)

        self.conn = None
        self._connect_with_retry()

        if db_path != ":memory:":
            try:
                os.chmod(db_path, 0o600)
            except Exception:
                pass

        self._create_table()

    def _connect_with_retry(self):
        """Connect to SQLite database with self-healing restore from .bak on corruption."""
        try:
            self.conn = sqlite3.connect(self.db_path)
        except sqlite3.DatabaseError as e:
            logger.error(f"Failed to open SQLite database {self.db_path}: {e}")
            bak_path = f"{self.db_path}.bak"
            if os.path.isfile(bak_path):
                logger.warning(f"Attempting self-healing recovery from backup: {bak_path}")
                try:
                    shutil.copy2(bak_path, self.db_path)
                    self.conn = sqlite3.connect(self.db_path)
                    logger.info("Successfully restored database from backup.")
                    return
                except Exception as restore_err:
                    logger.error(f"Backup restore failed: {restore_err}")
            # If all fails, fall back to in-memory database to prevent launcher crash
            logger.critical("Falling back to fresh in-memory database instance.")
            self.conn = sqlite3.connect(":memory:")

    def _create_table(self):
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
        except Exception as e:
            logger.error(f"Error initializing database schema: {e}")

    def update_build_id(self, game_id: int, build_id: str):
        try:
            with self.conn:
                self.conn.execute("UPDATE games SET build_id = ? WHERE id = ?", (build_id, game_id))
        except Exception as e:
            logger.error(f"Error updating build_id for game {game_id}: {e}")

    def add_game(self, name: str, path: str, executable: str, mode: str, banner_url: str = None, steam_id: str = None):
        try:
            with self.conn:
                self.conn.execute('''
                    INSERT INTO games (name, path, executable, mode, banner_url, steam_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (name, path, executable, mode, banner_url, steam_id))
                logger.info(f"Added game '{name}' (path: {path}) to database.")
        except Exception as e:
            logger.error(f"Failed to add game '{name}': {e}")

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

    def get_all_games(self):
        try:
            cursor = self.conn.cursor()
            cursor.execute(f'SELECT {self.GAME_COLUMNS} FROM games')
            return cursor.fetchall()
        except Exception as e:
            logger.error(f"Failed to fetch games list: {e}")
            return []

    def remove_game(self, game_id: int):
        try:
            with self.conn:
                self.conn.execute('DELETE FROM games WHERE id = ?', (game_id,))
                logger.info(f"Removed game {game_id} from database.")
        except Exception as e:
            logger.error(f"Failed to remove game {game_id}: {e}")

    def close(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
