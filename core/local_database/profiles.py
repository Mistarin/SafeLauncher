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

class ProfileRepository:
    def __init__(self, session):
        self.session = session

    @property
    def conn(self):
        return self.session.conn

    @property
    def db_path(self):
        return self.session.db_path

    def get_profile_games(self) -> List[dict]:
        rows = self.conn.execute(
            """SELECT identity_key, app_id, favorite, favorite_changed_at,
                      favorite_change_id, playtime_baseline_seconds, last_played,
                      display_name
               FROM profile_games ORDER BY identity_key"""
        ).fetchall()
        return [{"identity_key": r[0], "app_id": r[1] or "", "favorite": bool(r[2]),
                 "favorite_changed_at": float(r[3] or 0), "favorite_change_id": r[4] or "",
                 "playtime_baseline_seconds": int(r[5] or 0), "last_played": int(r[6] or 0),
                 "display_name": meaningful_game_name(r[7], r[1])} for r in rows]

    def merge_profile_game(self, value: dict) -> None:
        """Merge one generalized profile record and project it to matching games."""
        identity = str(value.get("identity_key", "")).strip()
        if not identity:
            return
        incoming_ts = float(value.get("favorite_changed_at", 0) or 0)
        incoming_id = str(value.get("favorite_change_id", "") or "")
        app_id = str(value.get("app_id", "") or "").strip()
        incoming_name = preferred_game_name(
            app_id,
            value.get("name"),
            value.get("display_name"),
        )
        with self.conn:
            current = self.conn.execute(
                "SELECT favorite, favorite_changed_at, favorite_change_id, display_name FROM profile_games WHERE identity_key = ?",
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
                   (identity_key, app_id, display_name, favorite, favorite_changed_at, favorite_change_id,
                    playtime_baseline_seconds, last_played, first_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(identity_key) DO UPDATE SET
                     app_id = excluded.app_id,
                     display_name = CASE WHEN excluded.display_name != '' THEN excluded.display_name ELSE profile_games.display_name END,
                     favorite = excluded.favorite,
                     favorite_changed_at = excluded.favorite_changed_at,
                     favorite_change_id = excluded.favorite_change_id,
                     playtime_baseline_seconds = MAX(profile_games.playtime_baseline_seconds, excluded.playtime_baseline_seconds),
                     last_played = MAX(profile_games.last_played, excluded.last_played)""",
                (identity, app_id, incoming_name, favorite, incoming_ts, incoming_id,
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

    def project_profile_library(self, game_id: int, value: dict) -> None:
        """Apply portable cloud catalog fields without touching local paths."""
        try:
            row = self.conn.execute(
                "SELECT name, steam_id FROM games WHERE id = ?", (game_id,)
            ).fetchone()
            current_name = str(row[0] or "") if row else ""
            app_id = str(value.get("app_id", "") or (row[1] if row else "") or "").strip()
            incoming_name = preferred_game_name(
                app_id,
                value.get("name"),
                value.get("display_name"),
            )
            # Local meaningful names win.  Remote metadata may repair a
            # generated placeholder, but must not rename a user's local title.
            name_update = (
                incoming_name
                if not meaningful_game_name(current_name, app_id)
                else ""
            )
            with self.conn:
                self.conn.execute(
                    """UPDATE games SET name = COALESCE(NULLIF(?, ''), name),
                       banner_url = COALESCE(NULLIF(?, ''), banner_url),
                       mode = COALESCE(NULLIF(?, ''), mode),
                       collection = ?, tags = ? WHERE id = ?""",
                    (
                        name_update[:MAX_GAME_NAME_LENGTH],
                        str(value.get("banner_url", "") or "")[:1024],
                        str(value.get("mode", "") or "")[:32],
                        str(value.get("collection", "") or ""),
                        str(value.get("tags", "") or "")[:2048],
                        game_id,
                    ),
                )
        except Exception as e:
            logger.error(f"Failed to project cloud library metadata for game {game_id}: {e}")
