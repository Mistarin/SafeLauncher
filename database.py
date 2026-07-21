import sqlite3
import os
import shutil

# [M1 FIX] Store database in XDG-compliant user data dir (~/.local/share/mglauncher/)
# instead of next to source files in the project directory. This keeps user data
# separate from application code and follows freedesktop.org conventions.
_XDG_DATA_HOME = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
_APP_DATA_DIR = os.path.join(_XDG_DATA_HOME, "mglauncher")
DEFAULT_DB_PATH = os.path.join(_APP_DATA_DIR, "library.db")

# Legacy path in the project dir - migrated automatically on first run.
_LEGACY_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library.db")


def _migrate_legacy_db(new_path: str) -> None:
    """If an old library.db exists in the project directory, move it to the new XDG path."""
    if os.path.isfile(_LEGACY_DB_PATH) and not os.path.isfile(new_path):
        try:
            shutil.move(_LEGACY_DB_PATH, new_path)
            print(f"[GameDatabase] Migrated library.db → {new_path}")
        except Exception as e:
            print(f"[GameDatabase] Could not migrate legacy DB: {e}")


class GameDatabase:
    def __init__(self, db_path: str = None):
        if db_path is None or db_path == "library.db":
            db_path = DEFAULT_DB_PATH

        # SQLite :memory: is a special in-memory database used in tests - skip all filesystem ops.
        if db_path != ":memory:":
            # Ensure parent directory exists with restrictive permissions (owner-only).
            os.makedirs(os.path.dirname(db_path), mode=0o700, exist_ok=True)

            # Migrate old project-dir DB if present.
            _migrate_legacy_db(db_path)

        self.conn = sqlite3.connect(db_path)

        if db_path != ":memory:":
            # Restrict DB file to owner-only after opening (rw-------)
            try:
                os.chmod(db_path, 0o600)
            except Exception:
                pass

        self._create_table()


    def _create_table(self):
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

            # Auto-migrate table schema if opening an older database file lacking columns
            cursor = self.conn.cursor()
            cursor.execute("PRAGMA table_info(games)")
            columns = [column[1] for column in cursor.fetchall()]

            if "banner_url" not in columns:
                cursor.execute("ALTER TABLE games ADD COLUMN banner_url TEXT")
            if "steam_id" not in columns:
                cursor.execute("ALTER TABLE games ADD COLUMN steam_id TEXT")
            if "playtime_seconds" not in columns:
                cursor.execute("ALTER TABLE games ADD COLUMN playtime_seconds INTEGER DEFAULT 0")

    def add_game(self, name: str, path: str, executable: str, mode: str, banner_url: str = None, steam_id: str = None):
        with self.conn:
            self.conn.execute('''
                INSERT INTO games (name, path, executable, mode, banner_url, steam_id)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (name, path, executable, mode, banner_url, steam_id))

    def add_playtime(self, game_id: int, seconds: int) -> None:
        """Atomically increment total playtime for a game by the given seconds."""
        if seconds > 0:
            with self.conn:
                self.conn.execute(
                    'UPDATE games SET playtime_seconds = COALESCE(playtime_seconds, 0) + ? WHERE id = ?',
                    (seconds, game_id)
                )

    def get_playtime(self, game_id: int) -> int:
        """Return total playtime in seconds for the given game, or 0 if not found."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT playtime_seconds FROM games WHERE id = ?', (game_id,))
        row = cursor.fetchone()
        return row[0] if row and row[0] else 0

    def update_game(self, game_id: int, name: str, path: str, executable: str, mode: str, banner_url: str = None):
        """Update existing game record in the database."""
        with self.conn:
            self.conn.execute('''
                UPDATE games 
                SET name = ?, path = ?, executable = ?, mode = ?, banner_url = ?
                WHERE id = ?
            ''', (name, path, executable, mode, banner_url, game_id))

    def update_game_banner(self, game_id: int, banner_url: str):
        with self.conn:
            self.conn.execute('''
                UPDATE games SET banner_url = ? WHERE id = ?
            ''', (banner_url, game_id))

    def get_all_games(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM games')
        return cursor.fetchall()

    def remove_game(self, game_id: int):
        with self.conn:
            self.conn.execute('DELETE FROM games WHERE id = ?', (game_id,))

    def close(self):
        self.conn.close()