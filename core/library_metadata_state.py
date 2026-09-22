"""Local presentation state for library metadata compatibility paths.

Remote Steam metadata is fetched through the resource services.  This object
only owns the small local projection still needed by existing library/detail
renderers, plus a read-compatible migration reader for the old combined
``metadata_cache.json`` file.  It is deliberately not a second request or
remote-resource cache.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


class LibraryMetadataState:
    """Own library metadata projections and compatibility migration state."""

    def __init__(self) -> None:
        self.attempted_builds: set[int] = set()
        self.attempted_tags: set[int] = set()
        self.update_status_by_game_id: dict[int, bool] = {}
        self.game_status_by_id: dict[int, object] = {}
        self.steam_check_results: dict[int, tuple] = {}
        self.local_version_by_game_id: dict[int, tuple[str, int]] = {}
        self.steam_build_checked_at: dict[int, float] = {}

    def clear_game(self, game_id: int) -> None:
        game_id = int(game_id)
        self.attempted_builds.discard(game_id)
        self.attempted_tags.discard(game_id)
        self.update_status_by_game_id.pop(game_id, None)
        self.game_status_by_id.pop(game_id, None)
        self.steam_check_results.pop(game_id, None)
        self.local_version_by_game_id.pop(game_id, None)
        self.steam_build_checked_at.pop(game_id, None)

    def load_legacy_cache(self, cache_file, achievement_state) -> bool:
        """Read old metadata projections without making them authoritative.

        The file is intentionally read-only from the perspective of this
        migration helper.  Current writes remain an explicitly marked,
        credential-free compatibility snapshot until all old callers are gone.
        Cloud save status is not loaded here; ``CloudStatusService`` owns that
        resource and its dedicated cache.
        """
        path = Path(cache_file)
        if not path.is_file():
            return False
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False

        return self.load_payload(data, achievement_state)

    def load_payload(self, data, achievement_state) -> bool:
        """Load a validated local projection from the shared resource cache."""
        if not isinstance(data, dict):
            return False

        now = time.time()
        self.attempted_tags = {
            int(value) for value in data.get("attempted_tags", [])
            if str(value).isdigit()
        }
        for raw_game_id, entry in data.get("steam_builds", {}).items():
            try:
                game_id = int(raw_game_id)
                entry = entry if isinstance(entry, dict) else {}
                self.steam_check_results[game_id] = (
                    entry.get("latest_build_id", ""),
                    entry.get("latest_build_date", 0),
                    entry.get("is_update", False),
                    entry.get("error", ""),
                )
                self.steam_build_checked_at[game_id] = float(
                    entry.get("checked_at", now)
                )
                self.attempted_builds.add(game_id)
            except (TypeError, ValueError, OverflowError):
                continue

        for raw_game_id, entry in data.get("achievements", {}).items():
            try:
                game_id = int(raw_game_id)
                entry = entry if isinstance(entry, dict) else {}
                achievement_state.status[game_id] = (
                    entry.get("unlocked_count", 0),
                    entry.get("total_count", 0),
                    float(entry.get("pct", 0.0)),
                    entry.get("recent", [])
                    if isinstance(entry.get("recent", []), list) else [],
                )
                achievement_state.checked_at[game_id] = float(
                    entry.get("checked_at", now)
                )
            except (TypeError, ValueError, OverflowError):
                continue
        return True

    def cache_payload(
        self,
        achievement_state,
        *,
        cache_kind: str = "library-metadata-projection",
    ) -> dict:
        """Serialize the local projection for a ResourceCache JSON value."""
        builds = {}
        now = time.time()
        for game_id, result in self.steam_check_results.items():
            build_id, build_date, is_update, error = result
            builds[str(int(game_id))] = {
                "latest_build_id": build_id,
                "latest_build_date": build_date,
                "is_update": bool(is_update),
                "error": str(error or ""),
                "checked_at": self.steam_build_checked_at.get(int(game_id), now),
            }

        achievements = {}
        for game_id, value in achievement_state.status.items():
            unlocked, total, percentage, recent = value
            achievements[str(int(game_id))] = {
                "unlocked_count": unlocked,
                "total_count": total,
                "pct": percentage,
                "recent": recent if isinstance(recent, list) else [],
                "checked_at": achievement_state.checked_at.get(int(game_id), now),
            }

        return {
            "cache_kind": str(cache_kind),
            "attempted_tags": sorted(self.attempted_tags),
            "steam_builds": builds,
            "achievements": achievements,
            "saved_at": now,
        }

    def save_legacy_cache(self, cache_file, achievement_state) -> None:
        """Atomically write the credential-free compatibility projection."""
        path = Path(cache_file)
        temporary = None
        fd = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = self.cache_payload(
                achievement_state,
                cache_kind="legacy-library-metadata-projection",
            )
            fd, temporary = tempfile.mkstemp(
                prefix=".metadata-", suffix=".tmp", dir=str(path.parent)
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = None
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        except (OSError, TypeError, ValueError) as exc:
            # A compatibility snapshot must never break a user operation.
            from core.logger import get_logger
            get_logger("LibraryMetadataState").warning(
                "Could not persist legacy metadata projection: %s", exc
            )
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass


__all__ = ["LibraryMetadataState"]
