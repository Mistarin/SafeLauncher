"""Compatibility facade over local SQLite lifetime and domain repositories."""
import os
from typing import Any, Dict, List, Optional, Tuple
from core.local_database.connection import (
    DatabaseSession, DatabaseRecoveryError, DEFAULT_DB_PATH, _APP_DATA_DIR,
    _migrate_legacy_db, _create_database_backup, _BACKUP_CREATED,
)
from core.local_database.records import GameRecord, GAME_COLUMNS, profile_identity
from core.local_database.validation import _ACHIEVEMENT_DB_LOCK
from core.local_database.schema import SchemaMigrator, _SCHEMA_INITIALIZED
from core.local_database.games import GameRepository
from core.local_database.profiles import ProfileRepository
from core.local_database.playtime import PlaytimeRepository
from core.local_database.achievements import AchievementRepository
from core.local_database.reconciliation import IdentityReconciler, _DUPLICATE_REPAIR_DONE


class GameDatabase:
    """Stable API with serialized repository calls and one connection lifetime.

    The raw conn property is retained for legacy callers and diagnostics only.
    """
    GAME_COLUMNS = GAME_COLUMNS
    profile_identity = staticmethod(profile_identity)

    def __init__(self, db_path: str = None):
        if db_path is None or db_path == "library.db":
            db_path = DEFAULT_DB_PATH
        self.db_path = db_path
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(db_path) or ".", mode=0o700, exist_ok=True)
            _migrate_legacy_db(db_path)
        self._session = DatabaseSession(db_path)
        try:
            self._connect_with_retry()
            self._schema = SchemaMigrator(self._session)
            self._profiles = ProfileRepository(self._session)
            self._games = GameRepository(self._session, self._profiles)
            self._playtime = PlaytimeRepository(self._session)
            self._achievements = AchievementRepository(self._session)
            self._reconciliation = IdentityReconciler(self._session, self._games, self._profiles)
            self._create_table()
            self.consolidate_duplicate_games()
            if db_path != ":memory:":
                try:
                    os.chmod(db_path, 0o600)
                except OSError:
                    pass
                _create_database_backup(db_path, self.conn)
        except BaseException:
            self._session.close()
            raise

    @property
    def conn(self):
        return self._session.conn

    def _connect_with_retry(self):
        self._session.open()

    def _create_table(self):
        with self._session.lock, _ACHIEVEMENT_DB_LOCK:
            self._create_table_locked()

    def _create_table_locked(self):
        return self._schema.apply()

    def close(self):
        self._session.close()

    def is_game_archived(self, game_id: int) -> bool:
        with self._session.lock:
            return self._games.is_archived(game_id)

    def update_build_id(self, game_id: int, build_id: str):
        with self._session.lock:
            return self._games.update_build_id(game_id, build_id)

    def update_build_date(self, game_id: int, build_date: int):
        with self._session.lock:
            return self._games.update_build_date(game_id, build_date)

    def update_game_version_metadata(self, game_id: int, version_override: str, patch_notes_url: str) -> None:
        with self._session.lock:
            return self._games.update_game_version_metadata(game_id, version_override, patch_notes_url)

    def add_game(self, name: str, path: str, executable: str, mode: str, banner_url: str = None, steam_id: str = None):
        with self._session.lock:
            return self._games.add_game(name, path, executable, mode, banner_url, steam_id)

    def find_game_by_profile_identity(self, identity_key: str):
        'Find a local game matching an account-profile identity.'
        with self._session.lock:
            return self._games.find_game_by_profile_identity(identity_key)

    def ensure_cloud_game(self, value: dict):
        'Materialize a cloud-only game as a not-installed local record.'
        with self._session.lock:
            return self._games.ensure_cloud_game(value)

    def toggle_favorite(self, game_id: int) -> bool:
        with self._session.lock:
            return self._games.toggle_favorite(game_id)

    def update_last_played(self, game_id: int, timestamp: int):
        with self._session.lock:
            return self._games.update_last_played(game_id, timestamp)

    def update_game_tags(self, game_id: int, tags: str):
        with self._session.lock:
            return self._games.update_game_tags(game_id, tags)

    def add_playtime(self, game_id: int, seconds: int) -> None:
        with self._session.lock:
            return self._playtime.add_playtime(game_id, seconds)

    def get_playtime(self, game_id: int) -> int:
        with self._session.lock:
            return self._playtime.get_playtime(game_id)

    def merge_playtime_metadata(self, game_id: int, playtime_seconds: int, last_played: int) -> None:
        'Merge a cloud snapshot without double-counting sessions.'
        with self._session.lock:
            return self._playtime.merge_playtime_metadata(game_id, playtime_seconds, last_played)

    def get_profile_games(self) -> List[dict]:
        with self._session.lock:
            return self._profiles.get_profile_games()

    def merge_profile_game(self, value: dict) -> None:
        'Merge one generalized profile record and project it to matching games.'
        with self._session.lock:
            return self._profiles.merge_profile_game(value)

    def project_profile_game(self, game_id: int, value: dict) -> None:
        with self._session.lock:
            return self._profiles.project_profile_game(game_id, value)

    def project_profile_library(self, game_id: int, value: dict) -> None:
        'Apply portable cloud catalog fields without touching local paths.'
        with self._session.lock:
            return self._profiles.project_profile_library(game_id, value)

    def update_game_name_from_metadata(self, game_id: int, name: str) -> bool:
        'Repair a generated Steam title without replacing a local title.'
        with self._session.lock:
            return self._games.update_game_name_from_metadata(game_id, name)

    def create_playtime_session(self, game_id: int, started_at: Optional[int] = None, session_id: str = "") -> str:
        'Create an idempotent playtime event for metadata synchronization.'
        with self._session.lock:
            return self._playtime.create_playtime_session(game_id, started_at, session_id)

    def checkpoint_playtime_session(self, session_id: str, duration_seconds: int, finalized: bool = False, ended_at: int = 0) -> None:
        'Persist progress and keep the aggregate derived from the session ledger.'
        with self._session.lock:
            return self._playtime.checkpoint_playtime_session(session_id, duration_seconds, finalized, ended_at)

    def get_playtime_session_game_id(self, session_id: str) -> Optional[int]:
        'Return the owning game for one session-ledger event.'
        with self._session.lock:
            return self._playtime.get_playtime_session_game_id(session_id)

    def get_playtime_sessions(self, game_id: int) -> List[dict]:
        with self._session.lock:
            return self._playtime.get_playtime_sessions(game_id)

    def merge_playtime_sessions(self, game_id: int, sessions: List[dict]) -> None:
        'Merge remote sessions by ID and rebuild the aggregate counter.'
        with self._session.lock:
            return self._playtime.merge_playtime_sessions(game_id, sessions)

    def update_game(self, game_id: int, name: str, path: str, executable: str, mode: str, banner_url: str = None):
        with self._session.lock:
            return self._games.update_game(game_id, name, path, executable, mode, banner_url)

    def update_game_mode(self, game_id: int, mode: str):
        with self._session.lock:
            return self._games.update_game_mode(game_id, mode)

    def update_game_banner(self, game_id: int, banner_url: str):
        with self._session.lock:
            return self._games.update_game_banner(game_id, banner_url)

    def update_game_steam_id(self, game_id: int, steam_id: str) -> None:
        'Persist the Steam AppID discovered by metadata fetchers.'
        with self._session.lock:
            return self._games.update_game_steam_id(game_id, steam_id)

    def update_game_proton_path(self, game_id: int, proton_path: str) -> None:
        with self._session.lock:
            return self._games.update_game_proton_path(game_id, proton_path)

    def update_game_collection(self, game_id: int, collection: str) -> None:
        with self._session.lock:
            return self._games.update_game_collection(game_id, collection)

    def add_collection(self, name: str) -> None:
        with self._session.lock:
            return self._games.add_collection(name)

    def delete_collection(self, name: str) -> None:
        with self._session.lock:
            return self._games.delete_collection(name)

    def rename_collection(self, old_name: str, new_name: str) -> None:
        with self._session.lock:
            return self._games.rename_collection(old_name, new_name)

    def get_all_collections(self) -> List[str]:
        with self._session.lock:
            return self._games.get_all_collections()

    def archive_game(self, game_id: int, is_archived: bool = True) -> bool:
        'Mark a game as archived (or unarchived) without losing its data.'
        with self._session.lock:
            return self._games.archive_game(game_id, is_archived)

    def restore_game(self, game_id: int) -> bool:
        'Restore an archived game back to the active library.'
        with self._session.lock:
            return self._games.restore_game(game_id)

    def update_game_icon(self, game_id: int, icon_url: str) -> None:
        with self._session.lock:
            return self._games.update_game_icon(game_id, icon_url)

    def update_game_env_vars(self, game_id: int, env_vars: dict | str) -> None:
        'Update per-game environment variables and presets (stored as JSON string).'
        with self._session.lock:
            return self._games.update_game_env_vars(game_id, env_vars)

    def get_game_env_vars(self, game_id: int) -> dict:
        'Retrieve per-game environment variables as a dictionary.'
        with self._session.lock:
            return self._games.get_game_env_vars(game_id)

    def get_all_games(self) -> List[GameRecord]:
        with self._session.lock:
            return self._games.get_all_games()

    def _merge_profile_identity_alias(self, source_identity: str, target_identity: str, app_id: str) -> None:
        'Move one legacy profile alias onto a canonical identity.\n\nProfile history is append-only for ordinary removal, but an identity\nalias is not a separate game.  Merge it before deleting the alias so\nfavorites, playtime baselines, and causal favorite markers survive.'
        with self._session.lock:
            return self._reconciliation._merge_profile_identity_alias(source_identity, target_identity, app_id)

    def consolidate_duplicate_games(self, *, force: bool = False) -> int:
        'Consolidate rows that resolve to the same portable game identity.\n\nOlder cloud-only materialization treated ``local:<slug>`` as a title,\nthen derived ``local:local-<slug>`` on the next pass.  It could also\nleave a non-Steam placeholder beside the later-known Steam identity.\nThis repair runs once per persistent database during startup and can be\nforced by a sync pass.  It keeps the best row, merges dependent\nachievement/session ledgers, preserves profile history, and only then\nremoves redundant rows.'
        with self._session.lock:
            return self._reconciliation.consolidate_duplicate_games(force=force)

    def remove_game(self, game_id: int) -> bool:
        'Remove a launcher game row while retaining append-only profile history.'
        with self._session.lock:
            return self._games.remove_game(game_id)

    def delete_all_game_data(self, game_id: int) -> bool:
        "Permanently purge one game's local projection and history.\n\nThis is intentionally separate from :meth:`remove_game`, whose\nappend-only profile history is needed for cross-device reconciliation.\nCloud save generations are remote account data and require an explicit\ncloud-save operation; they are never silently deleted here."
        with self._session.lock:
            return self._games.delete_all_game_data(game_id)

    def save_achievement_schema(self, game_id: int, app_id: str, achievements: List[dict]) -> int:
        'Insert or update achievement schema definitions for a game, preserving existing unlocked state.'
        with self._session.lock:
            return self._achievements.save_achievement_schema(game_id, app_id, achievements)

    def unlock_achievement(
        self, game_id: int, api_name: str, unlock_time: float = 0.0,
        *, provenance: str = "local_emulator", verified: bool = False,
        source_format: str = "", source_path: str = "",
    ) -> bool:
        'Mark a schema-backed achievement as unlocked with provenance.'
        with self._session.lock:
            return self._achievements.unlock_achievement(game_id, api_name, unlock_time, provenance=provenance, verified=verified, source_format=source_format, source_path=source_path)

    def unlock_achievements_batch(
        self, game_id: int, unlocks: Dict[str, float], *, app_id: str = "",
        provenance: str = "local_emulator", verified: bool = False,
        source_format: str = "", source_path: str = "",
    ) -> int:
        'Record a batch, promoting only schema-backed names to visible rows.\n\nUnknown names are retained in the profile as ``pending_schema`` when\nan AppID is available, but never affect per-game counts.'
        with self._session.lock:
            return self._achievements.unlock_achievements_batch(game_id, unlocks, app_id=app_id, provenance=provenance, verified=verified, source_format=source_format, source_path=source_path)

    def claim_achievement_notification(self, game_id: int, api_name: str) -> bool:
        'Atomically claim the one user-facing notification for an unlock.'
        with self._session.lock:
            return self._achievements.claim_achievement_notification(game_id, api_name)

    def arm_achievement_notification(self, game_id: int, api_name: str) -> None:
        'Mark a schema-late live event as pending user notification.'
        with self._session.lock:
            return self._achievements.arm_achievement_notification(game_id, api_name)

    def merge_profile_unlocks(
        self, app_id: str, unlocks: Dict[str, float], *, provenance: str = "local_emulator",
        verified: bool = False, validation_state: str = "validated", source_format: str = "",
        source_path: str = "", allowed_api_names: Optional[set[str]] = None,
    ) -> int:
        'Append observations to the account ledger without deleting proof.'
        with self._session.lock:
            return self._achievements.merge_profile_unlocks(app_id, unlocks, provenance=provenance, verified=verified, validation_state=validation_state, source_format=source_format, source_path=source_path, allowed_api_names=allowed_api_names)

    def record_achievement_state(
        self, game_id: int, app_id: str, state: Dict[str, float], *,
        pending: Optional[Dict[str, float]] = None,
        provenance: str = "local_emulator", verified: bool = False,
        source_format: str = "", source_path: str = "",
        provenance_by_name: Optional[Dict[str, str]] = None,
        verified_by_name: Optional[Dict[str, bool]] = None,
    ) -> int:
        'Persist one resolver snapshot atomically at the two DB layers.'
        with self._session.lock:
            return self._achievements.record_achievement_state(game_id, app_id, state, pending=pending, provenance=provenance, verified=verified, source_format=source_format, source_path=source_path, provenance_by_name=provenance_by_name, verified_by_name=verified_by_name)

    def get_profile_unlocks(self, app_id: Optional[str] = None, *, include_pending: bool = False) -> Dict[str, Dict[str, float]]:
        'Return schema-validated append-only unlocks grouped by AppID.'
        with self._session.lock:
            return self._achievements.get_profile_unlocks(app_id, include_pending=include_pending)

    def get_profile_unlock_records(self, app_id: Optional[str] = None, *, include_pending: bool = True) -> Dict[str, Dict[str, dict]]:
        'Return provenance-aware profile records for cloud sync/diagnostics.'
        with self._session.lock:
            return self._achievements.get_profile_unlock_records(app_id, include_pending=include_pending)

    def collect_profile_from_games(self) -> int:
        'Promote all existing per-game unlock projections into the ledger.'
        with self._session.lock:
            return self._achievements.collect_profile_from_games()

    def project_profile_achievements(self, game_id: int, app_id: str = "") -> int:
        'Apply profile unlocks to matching current game schema rows only.'
        with self._session.lock:
            return self._achievements.project_profile_achievements(game_id, app_id)

    def get_game_achievements(self, game_id: int) -> List[dict]:
        'Return all achievements for a game, ordered by unlocked status and name.'
        with self._session.lock:
            return self._achievements.get_game_achievements(game_id)

    def get_profile_achievement_rows(self) -> Dict[int, List[dict]]:
        'Return the small achievement projection needed by public profiles.\n\nPublic profile rendering used to issue one full achievement query per\ngame. Keep the existing detailed API for the achievements page, but\nprovide this narrow bulk read for profile generation so a large local\nlibrary does not create an N+1 query pattern.'
        with self._session.lock:
            return self._achievements.get_profile_achievement_rows()

    def get_achievement_stats(self, game_id: int) -> Tuple[int, int, float]:
        'Return (unlocked_count, total_count, percentage).'
        with self._session.lock:
            return self._achievements.get_achievement_stats(game_id)

    def get_global_achievement_stats(self) -> Dict[str, Any]:
        'Return global achievement statistics across all games.'
        with self._session.lock:
            return self._achievements.get_global_achievement_stats()

    def get_recent_unlocked_achievements(self, game_id: int, limit: int = 6) -> List[dict]:
        'Return the most recently unlocked achievements for a game.'
        with self._session.lock:
            return self._achievements.get_recent_unlocked_achievements(game_id, limit)

    def reset_game_achievements(self, game_id: int):
        'Reset unlocked status of all achievements for a game (for testing/re-locking).'
        with self._session.lock:
            return self._achievements.reset_game_achievements(game_id)
