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
from .validation import (
    _ACHIEVEMENT_DB_LOCK, _ACHIEVEMENT_APP_RE, _valid_achievement_api_name,
    _normalise_achievement_provenance, _safe_achievement_timestamp,
)
logger = get_logger("Database")

class PlaytimeRepository:
    def __init__(self, session):
        self.session = session

    @property
    def conn(self):
        return self.session.conn

    @property
    def db_path(self):
        return self.session.db_path

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

    def get_playtime_session_game_id(self, session_id: str) -> Optional[int]:
        """Return the owning game for one session-ledger event."""
        try:
            row = self.conn.execute(
                "SELECT game_id FROM playtime_sessions WHERE session_id = ?",
                (str(session_id or ""),),
            ).fetchone()
            return int(row[0]) if row else None
        except Exception as e:
            logger.error(f"Failed to resolve playtime session {session_id}: {e}")
            return None

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
