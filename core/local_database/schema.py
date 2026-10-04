import json
import math
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from core.logger import get_logger
from core.game_names import (
    MAX_GAME_NAME_LENGTH, fallback_game_name, display_name_key,
    is_identity_placeholder_name, local_profile_identity, meaningful_game_name,
    preferred_game_name,
)
from .records import GameRecord, GAME_COLUMNS, profile_identity
from .connection import DatabaseRecoveryError
from .validation import (
    _ACHIEVEMENT_DB_LOCK, _ACHIEVEMENT_APP_RE, _valid_achievement_api_name,
    _normalise_achievement_provenance, _safe_achievement_timestamp,
)
logger = get_logger("Database")

_SCHEMA_INITIALIZED: set = set()
_SCHEMA_VERSION = 1
_SCHEMA_SIGNATURES: dict = {}

class SchemaMigrator:
    def __init__(self, session):
        self.session = session

    @property
    def conn(self):
        return self.session.conn

    @property
    def db_path(self):
        return self.session.db_path

    def _signature(self):
        """Read the actual schema, rather than trusting a previously opened path."""
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        objects = self.conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        return version, tuple(objects)

    def apply(self):
        try:
            if self.db_path != ":memory:":
                signature = self._signature()
                if (signature[0] == _SCHEMA_VERSION
                        and _SCHEMA_SIGNATURES.get(self.db_path) == signature):
                    return
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
                        display_name TEXT DEFAULT '',
                        favorite INTEGER DEFAULT 0,
                        favorite_changed_at REAL DEFAULT 0,
                        favorite_change_id TEXT DEFAULT '',
                        playtime_baseline_seconds INTEGER DEFAULT 0,
                        last_played INTEGER DEFAULT 0,
                        first_seen_at REAL NOT NULL
                    )
                """)
                cursor.execute("PRAGMA table_info(profile_games)")
                profile_game_columns = {column[1] for column in cursor.fetchall()}
                if "display_name" not in profile_game_columns:
                    cursor.execute(
                        "ALTER TABLE profile_games ADD COLUMN display_name TEXT DEFAULT ''"
                    )
                # Recover meaningful titles for history rows created before
                # display_name existed.  A Steam identity is stable even when
                # the current row has since been archived or removed.
                cursor.execute(
                    """
                    UPDATE profile_games
                    SET display_name = (
                        SELECT name FROM games
                        WHERE profile_games.app_id != ''
                          AND games.steam_id = profile_games.app_id
                        ORDER BY games.id ASC LIMIT 1
                    )
                    WHERE COALESCE(display_name, '') = ''
                      AND app_id != ''
                    """
                )
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
                cursor.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            if self.db_path != ":memory:":
                _SCHEMA_SIGNATURES[self.db_path] = self._signature()
                _SCHEMA_INITIALIZED.add(self.db_path)
        except Exception as e:
            logger.error(f"Error initializing database schema: {e}")
            raise DatabaseRecoveryError("The library schema could not be initialized; the database was not replaced.") from e
