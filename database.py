import sqlite3
import os

class GameDatabase:
    def __init__(self, db_path: str = None):
        if db_path is None or db_path == "library.db":
            base_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(base_dir, "library.db")
        
        self.conn = sqlite3.connect(db_path)
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

    def add_game(self, name: str, path: str, executable: str, mode: str, banner_url: str = None, steam_id: str = None):
        with self.conn:
            self.conn.execute('''
                INSERT INTO games (name, path, executable, mode, banner_url, steam_id)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (name, path, executable, mode, banner_url, steam_id))

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