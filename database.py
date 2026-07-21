import sqlite3
import os

class GameDatabase:
    def __init__(self, db_path: str = "library.db"):
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