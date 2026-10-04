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

class GameRepository:
    def is_archived(self, game_id):
        row = self.conn.execute("SELECT is_archived FROM games WHERE id = ?", (int(game_id),)).fetchone()
        return bool(row and row[0])

    def __init__(self, session, profiles):
        self.session = session
        self.profiles = profiles

    @property
    def conn(self):
        return self.session.conn

    @property
    def db_path(self):
        return self.session.db_path

    GAME_COLUMNS = GAME_COLUMNS
    profile_identity = staticmethod(profile_identity)

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

    def find_game_by_profile_identity(self, identity_key: str):
        """Find a local game matching an account-profile identity."""
        identity_key = str(identity_key or "").strip()
        if not identity_key:
            return None
        for game in self.get_all_games():
            if self.profile_identity(game.name, game.steam_id) == identity_key:
                return game
        return None

    def ensure_cloud_game(self, value: dict):
        """Materialize a cloud-only game as a not-installed local record."""
        identity = str(value.get("identity_key", "")).strip()
        app_id = str(value.get("app_id", "") or "").strip()
        incoming_name = preferred_game_name(
            app_id,
            value.get("name"),
            value.get("display_name"),
        ) or fallback_game_name(app_id, identity)

        # Profile reconciliation can be invoked by several independent UI
        # resources.  Keep the identity lookup and possible INSERT in one
        # process-wide critical section; otherwise two worker connections can
        # both observe the missing row and materialize it.
        with _ACHIEVEMENT_DB_LOCK:
            existing = self.find_game_by_profile_identity(identity)
            if existing is None and not app_id:
                # Older profile documents may retain a local alias alongside a
                # later Steam identity.  The alias repair can remove the
                # local row before this method runs, so match only a unique,
                # archived, pathless Steam row by its meaningful title.  An
                # installed game is deliberately excluded to avoid merging
                # unrelated non-Steam titles.
                incoming_key = display_name_key(incoming_name, "")
                candidates = [
                    game for game in self.get_all_games()
                    if str(game.steam_id or "").strip()
                    and bool(game.is_archived)
                    and not str(game.path or "").strip()
                    and not str(game.executable or "").strip()
                    and display_name_key(game.name, game.steam_id) == incoming_key
                ]
                if len(candidates) == 1:
                    existing = candidates[0]
                    # Preserve the canonical Steam identity when the legacy
                    # alias is only a projection of the same cloud game.
                    value = dict(value)
                    value["identity_key"] = self.profile_identity(
                        existing.name, existing.steam_id
                    )

            if existing:
                # A previously materialized placeholder must be upgraded in
                # place; the Steam AppID remains the identity and no duplicate
                # is made.
                self.profiles.project_profile_library(existing.id, value)
                if is_identity_placeholder_name(existing.name):
                    self.update_game_name_from_metadata(
                        existing.id,
                        fallback_game_name(app_id or existing.steam_id, identity),
                    )
                return existing.id

            game_id = self.add_game(
                incoming_name[:MAX_GAME_NAME_LENGTH], "", "",
                str(value.get("mode", "linux") or "linux"),
                str(value.get("banner_url", "") or "")[:1024], app_id or None,
            )
            if game_id:
                self.archive_game(game_id, True)
            return game_id

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
                           (identity_key, app_id, display_name, favorite, favorite_changed_at, favorite_change_id, first_seen_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(identity_key) DO UPDATE SET
                             app_id = excluded.app_id,
                             display_name = CASE WHEN excluded.display_name != '' THEN excluded.display_name ELSE profile_games.display_name END,
                             favorite = excluded.favorite,
                             favorite_changed_at = excluded.favorite_changed_at,
                             favorite_change_id = excluded.favorite_change_id""",
                        (
                            identity,
                            str(row[1] or "").strip(),
                            meaningful_game_name(row[0], row[1]),
                            new_val,
                            time.time(),
                            str(uuid.uuid4()),
                            time.time(),
                        ),
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

    def update_game_name_from_metadata(self, game_id: int, name: str) -> bool:
        """Repair a generated Steam title without replacing a local title."""
        try:
            with self.conn:
                row = self.conn.execute(
                    "SELECT name, steam_id FROM games WHERE id = ?", (game_id,)
                ).fetchone()
                if not row:
                    return False
                app_id = str(row[1] or "").strip()
                candidate = meaningful_game_name(name, app_id)
                if not candidate or meaningful_game_name(row[0], app_id):
                    return False
                self.conn.execute(
                    "UPDATE games SET name = ? WHERE id = ?",
                    (candidate[:MAX_GAME_NAME_LENGTH], game_id),
                )
                identity = self.profile_identity(candidate, app_id)
                self.conn.execute(
                    "UPDATE profile_games SET display_name = ? WHERE identity_key = ?",
                    (candidate[:MAX_GAME_NAME_LENGTH], identity),
                )
                return True
        except Exception as e:
            logger.error(f"Failed to update metadata name for game {game_id}: {e}")
            return False

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

    def delete_all_game_data(self, game_id: int) -> bool:
        """Permanently purge one game's local projection and history.

        This is intentionally separate from :meth:`remove_game`, whose
        append-only profile history is needed for cross-device reconciliation.
        Cloud save generations are remote account data and require an explicit
        cloud-save operation; they are never silently deleted here.
        """
        try:
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                row = self.conn.execute(
                    "SELECT name, steam_id FROM games WHERE id = ?", (int(game_id),)
                ).fetchone()
                if not row:
                    return False
                identity = self.profile_identity(row[0], row[1])
                app_id = str(row[1] or "").strip()
                self.conn.execute("DELETE FROM achievements WHERE game_id = ?", (int(game_id),))
                self.conn.execute("DELETE FROM playtime_sessions WHERE game_id = ?", (int(game_id),))
                self.conn.execute(
                    "DELETE FROM profile_games WHERE identity_key = ?", (identity,)
                )
                # The account-wide achievement ledger is local persistence for
                # this AppID. Only remove it when no other local projection
                # still references the same Steam game.
                if app_id and not self.conn.execute(
                    "SELECT 1 FROM games WHERE id != ? AND steam_id = ? LIMIT 1",
                    (int(game_id), app_id),
                ).fetchone():
                    self.conn.execute(
                        "DELETE FROM achievement_profile WHERE app_id = ?", (app_id,)
                    )
                deleted = self.conn.execute(
                    "DELETE FROM games WHERE id = ?", (int(game_id),)
                ).rowcount
                if deleted:
                    logger.info("Deleted all local data for game %s", int(game_id))
                return deleted == 1
        except Exception as e:
            logger.error(f"Failed to delete all local data for game {game_id}: {e}")
            return False
