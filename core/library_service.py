"""Application service for the local library projection.

This boundary keeps database reads, query inputs, collection projection, and
archive/install transitions out of the MainWindow orchestration layer.  The
database remains authoritative; this service does not introduce a second
library cache or a remote source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import time
from typing import Mapping, Any

from core.game_status import GameStatusState
from core.library_controller import LibraryQuery, LibrarySnapshot
from core.library_state import LibraryStateStore


@dataclass(frozen=True)
class LibraryProjection:
    games: tuple[tuple, ...]
    games_by_id: Mapping[int, tuple]
    snapshot: LibrarySnapshot
    collection_counts: tuple[tuple[str, int], ...]


class LibraryService:
    """Own local library projection/query coordination for application code."""

    def __init__(self, database, state_store: LibraryStateStore):
        self.database = database
        self.state_store = state_store

    def refresh(
        self,
        query: LibraryQuery,
        *,
        games: tuple[tuple, ...] | None = None,
        update_status: Mapping[int, bool] | None = None,
        cloud_status: Mapping[int, Any] | None = None,
        status: Mapping[int, GameStatusState] | None = None,
    ) -> LibraryProjection:
        """Read SQLite once and derive the shared immutable library snapshot."""
        games = tuple(self.read_games() if games is None else games)
        snapshot = self.state_store.set_inputs(
            games,
            query,
            update_status=update_status,
            cloud_status=cloud_status,
            status=status,
        )
        return LibraryProjection(
            games=games,
            games_by_id={int(game[0]): game for game in games if game},
            snapshot=snapshot,
            collection_counts=self._collection_counts(games),
        )

    def read_games(self) -> tuple[tuple, ...]:
        """Read the current local projection without deriving UI state."""
        return tuple(self.database.get_all_games())

    @property
    def selected_ids(self) -> set[int]:
        return self.state_store.selected_ids

    def replace_selection(self, game_ids) -> set[int]:
        return self.state_store.selection.replace(game_ids)

    def toggle_selection(self, game_id: int, *, additive: bool = False) -> set[int]:
        return self.state_store.selection.click(int(game_id), additive=additive)

    def clear_selection(self) -> None:
        self.state_store.selection.clear()

    def retain_selection(self, game_ids) -> set[int]:
        allowed = {int(game_id) for game_id in game_ids}
        return self.replace_selection(self.selected_ids.intersection(allowed))

    def remove_from_selection(self, game_id: int) -> set[int]:
        return self.replace_selection(self.selected_ids - {int(game_id)})

    def reconcile_statuses(
        self,
        games,
        *,
        current: Mapping[int, GameStatusState] | None = None,
        update_status: Mapping[int, bool] | None = None,
        cloud_status: Mapping[int, Any] | None = None,
        build_results: Mapping[int, Any] | None = None,
    ) -> dict[int, GameStatusState]:
        """Combine persisted/remote status inputs before snapshot derivation."""
        current = current or {}
        update_status = update_status or {}
        cloud_status = cloud_status or {}
        build_results = build_results or {}
        result: dict[int, GameStatusState] = {}
        for game in games:
            game_id = int(game[0])
            existing = current.get(game_id, GameStatusState())
            cached_cloud = cloud_status.get(game_id)
            build = build_results.get(game_id)
            result[game_id] = replace(
                existing,
                update_available=bool(update_status.get(game_id, False)),
                update_error=(str(build[3] or "") if build and len(build) > 3 else existing.update_error),
                update_build_id=(str(build[0] or "") if build else existing.update_build_id),
                update_build_date=(int(build[1] or 0) if build else existing.update_build_date),
                cloud_status=(cached_cloud[0] if cached_cloud else existing.cloud_status),
                local_stats=(cached_cloud[1] if cached_cloud else existing.local_stats),
                cloud_stats=(cached_cloud[2] if cached_cloud else existing.cloud_stats),
            )
        return result

    def _collection_counts(self, games: tuple[tuple, ...]) -> tuple[tuple[str, int], ...]:
        known = {str(value).strip() for value in self.database.get_all_collections() if str(value).strip()}
        counts = {name: 0 for name in known}
        for game in games:
            if len(game) > 17 and game[17]:
                continue
            name = str(game[13]).strip() if len(game) > 13 else ""
            if name:
                counts[name] = counts.get(name, 0) + 1
        return tuple(sorted(counts.items(), key=lambda item: item[0].lower()))

    def create_collection(self, name: str) -> bool:
        value = str(name or "").strip()
        if not value:
            return False
        self.database.add_collection(value)
        return True

    def update_collection_membership(self, game_id: int, collection: str) -> bool:
        self.database.update_game_collection(int(game_id), str(collection or "").strip())
        return True

    def rename_collection(self, old_name: str, new_name: str) -> bool:
        old_value = str(old_name or "").strip()
        new_value = str(new_name or "").strip()
        if not old_value or not new_value:
            return False
        self.database.rename_collection(old_value, new_value)
        return True

    def delete_collection(self, name: str) -> bool:
        value = str(name or "").strip()
        if not value:
            return False
        self.database.delete_collection(value)
        return True

    def toggle_favorite(self, game_id: int) -> bool:
        """Toggle and return the local favorite projection for one game."""
        return bool(self.database.toggle_favorite(int(game_id)))

    def create_playtime_session(self, game_id: int, started_at: int | None = None) -> str:
        """Create a durable launch session through the library boundary."""
        return str(self.database.create_playtime_session(int(game_id), started_at=started_at))

    def archive_game(self, game_id: int) -> bool:
        return bool(self.database.archive_game(int(game_id), True))

    def restore_game(self, game_id: int) -> bool:
        return bool(self.database.restore_game(int(game_id)))

    def remove_from_archive(self, game_id: int) -> bool:
        """Restore an archived record to the active library projection."""
        return bool(self.database.restore_game(int(game_id)))

    def remove_game(self, game_id: int) -> bool:
        """Remove a local library row while preserving database authority."""
        return bool(self.database.remove_game(int(game_id)))

    def delete_all_game_data(self, game_id: int) -> bool:
        """Purge one game's local record, history, achievements, and sessions."""
        return bool(self.database.delete_all_game_data(int(game_id)))

    def update_runtime_settings(
        self,
        game_id: int,
        *,
        mode: str | None = None,
        proton_path: str | None = None,
        env_vars: dict | str | None = None,
    ) -> None:
        """Persist launch/runtime settings as one library boundary."""
        game_id = int(game_id)
        if mode is not None:
            self.database.update_game_mode(game_id, str(mode))
        if proton_path is not None:
            self.database.update_game_proton_path(game_id, str(proton_path or ""))
        if env_vars is not None:
            self.database.update_game_env_vars(game_id, env_vars)

    def runtime_env_vars(self, game_id: int) -> dict:
        """Read launch environment settings through the local library boundary."""
        value = self.database.get_game_env_vars(int(game_id))
        return dict(value or {})

    def update_game_details(
        self,
        game_id: int,
        name: str,
        path: str,
        executable: str,
        mode: str,
        banner_url: str = "",
        steam_id: str | None = None,
        version_override: str | None = None,
        patch_notes_url: str | None = None,
    ) -> None:
        """Persist editable game identity/settings without exposing DB calls to UI."""
        game_id = int(game_id)
        self.database.update_game(
            game_id,
            str(name),
            str(path),
            str(executable),
            str(mode),
            str(banner_url or ""),
        )
        if steam_id is not None:
            self.database.update_game_steam_id(game_id, str(steam_id or ""))
        if version_override is not None or patch_notes_url is not None:
            self.database.update_game_version_metadata(
                game_id,
                str(version_override or ""),
                str(patch_notes_url or ""),
            )

    def apply_metadata_name(self, game_id: int, name: str) -> bool:
        """Apply one verified remote display name to the local projection."""
        value = str(name or "").strip()
        if not value:
            return False
        return bool(self.database.update_game_name_from_metadata(int(game_id), value))

    def upsert_game(
        self,
        name: str,
        path: str,
        executable: str,
        mode: str,
        banner_url: str = "",
        steam_id: str | None = None,
    ) -> int | None:
        """Update an existing identity or create one new local game row."""
        identity = self.database.profile_identity(name, steam_id or "")
        existing = self.database.find_game_by_profile_identity(identity)
        if existing is not None:
            self.update_game_details(
                existing.id,
                name,
                path,
                executable,
                mode,
                banner_url,
                steam_id=steam_id or existing.steam_id or "",
            )
            self.restore_game(existing.id)
            return int(existing.id)
        return self.database.add_game(
            str(name),
            str(path),
            str(executable),
            str(mode),
            str(banner_url or ""),
            steam_id or None,
        )

    def set_build_reference(self, game_id: int, build_id: str, build_date: int = 0) -> None:
        """Persist the installed build reference used by Steam comparisons."""
        game_id = int(game_id)
        self.database.update_build_id(game_id, str(build_id or ""))
        self.database.update_build_date(game_id, int(build_date or 0))

    def set_artwork_identity(
        self,
        game_id: int,
        *,
        banner_url: str | None = None,
        icon_url: str | None = None,
        steam_id: str | int | None = None,
    ) -> None:
        """Persist downloaded artwork and an optionally discovered AppID."""
        game_id = int(game_id)
        if banner_url:
            self.database.update_game_banner(game_id, str(banner_url))
        if icon_url:
            self.database.update_game_icon(game_id, str(icon_url))
        if steam_id:
            self.database.update_game_steam_id(game_id, str(steam_id))

    def set_tags(self, game_id: int, tags: str) -> None:
        self.database.update_game_tags(int(game_id), str(tags or ""))

    def record_playtime_finished(self, game_id: int, timestamp: int | None = None) -> int:
        """Persist last-played state and return the authoritative total."""
        game_id = int(game_id)
        self.database.update_last_played(game_id, int(timestamp or time.time()))
        return int(self.database.get_playtime(game_id) or 0)

    def checkpoint_playtime_session(
        self,
        session_id: str,
        elapsed_seconds: int,
        *,
        finalized: bool = False,
        ended_at: int = 0,
    ) -> int | None:
        """Persist an idempotent session checkpoint and return its game ID."""
        session_id = str(session_id or "").strip()
        if not session_id:
            return None
        self.database.checkpoint_playtime_session(
            session_id,
            int(elapsed_seconds or 0),
            bool(finalized),
            int(ended_at or 0),
        )
        return self.database.get_playtime_session_game_id(session_id)


__all__ = ["LibraryProjection", "LibraryService"]
