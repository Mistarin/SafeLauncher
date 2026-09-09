"""Separate SafeLauncher-owned cloud metadata synchronization.

Game save archives contain game-owned files.  This module synchronizes
SafeLauncher-owned achievements and playtime independently, so metadata also
works for games without save files and is not coupled to save conflicts.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import threading
from typing import Optional

from core.logger import get_logger

logger = get_logger("CloudMetadata")
_PROFILE_SYNC_LOCK = threading.Lock()


def _merge_unlocks(local: dict, remote: dict) -> dict:
    merged = {}
    for api_name in set(local) | set(remote):
        left = local.get(api_name) or {}
        right = remote.get(api_name) or {}
        times = [int(x.get("unlock_time", 0) or 0) for x in (left, right) if isinstance(x, dict) and int(x.get("unlock_time", 0) or 0) > 0]
        merged[api_name] = {"unlock_time": min(times) if times else 0}
    return merged


def _merge_sessions(local: list, remote: list) -> list:
    by_id = {}
    for item in list(local or []) + list(remote or []):
        if not isinstance(item, dict) or not str(item.get("session_id", "")).strip():
            continue
        sid = str(item["session_id"])
        old = by_id.get(sid, {})
        by_id[sid] = {
            "session_id": sid,
            "started_at": min(int(old.get("started_at", 0) or 0), int(item.get("started_at", 0) or 0)) if old else int(item.get("started_at", 0) or 0),
            "ended_at": max(int(old.get("ended_at", 0) or 0), int(item.get("ended_at", 0) or 0)),
            "duration_seconds": max(int(old.get("duration_seconds", 0) or 0), int(item.get("duration_seconds", 0) or 0)),
            "finalized": bool(old.get("finalized", False) or item.get("finalized", False)),
        }
    return sorted(by_id.values(), key=lambda x: (x["started_at"], x["session_id"]))


def _merge_profiles(local: dict, remote: dict) -> dict:
    """Union achievement profiles by AppID/API name; never remove unlocks."""
    merged = {}
    for app_id in set((local or {}).get("achievements", {}) or {}) | set((remote or {}).get("achievements", {}) or {}):
        left = ((local or {}).get("achievements", {}) or {}).get(app_id, {}) or {}
        right = ((remote or {}).get("achievements", {}) or {}).get(app_id, {}) or {}
        merged[str(app_id)] = _merge_unlocks(left, right)
    return {"format_version": 1, "achievements": merged}


class CloudMetadataSync:
    """Build, merge, and persist SafeLauncher metadata for one game."""

    @staticmethod
    def _local_payload(db, game_id: int, game_key: str, app_id: str, include_legacy_achievements: bool = True) -> dict:
        row = db.conn.execute("SELECT playtime_seconds, last_played FROM games WHERE id = ?", (game_id,)).fetchone()
        total = int(row[0] or 0) if row else 0
        last_played = int(row[1] or 0) if row else 0
        sessions = db.get_playtime_sessions(game_id)
        session_total = sum(int(x.get("duration_seconds", 0) or 0) for x in sessions)
        payload = {
            "format_version": 1,
            "game_key": game_key,
            "app_id": str(app_id or ""),
            "last_played": max(0, last_played),
            "playtime_baseline_seconds": max(0, total - session_total),
            "playtime_sessions": sessions,
        }
        if include_legacy_achievements:
            payload["achievement_unlocks"] = {
                str(x["api_name"]): {"unlock_time": int(float(x.get("unlock_time", 0) or 0))}
                for x in db.get_game_achievements(game_id) if x.get("unlocked")
            }
        return payload

    @staticmethod
    def _merge(local: dict, remote: dict, include_legacy_achievements: bool = True) -> dict:
        sessions = _merge_sessions(local.get("playtime_sessions"), remote.get("playtime_sessions"))
        merged = {
            "format_version": 1,
            "game_key": local.get("game_key") or remote.get("game_key", ""),
            "app_id": str(local.get("app_id") or remote.get("app_id", "")),
            "last_played": max(int(local.get("last_played", 0) or 0), int(remote.get("last_played", 0) or 0)),
            "playtime_baseline_seconds": max(int(local.get("playtime_baseline_seconds", 0) or 0), int(remote.get("playtime_baseline_seconds", 0) or 0)),
            "playtime_sessions": sessions,
        }
        if include_legacy_achievements:
            merged["achievement_unlocks"] = _merge_unlocks(local.get("achievement_unlocks", {}), remote.get("achievement_unlocks", {}))
        return merged

    @staticmethod
    def _apply(db, game_id: int, payload: dict) -> None:
        db.merge_playtime_sessions(game_id, payload.get("playtime_sessions", []))
        sessions_total = sum(int(x.get("duration_seconds", 0) or 0) for x in payload.get("playtime_sessions", []))
        db.merge_playtime_metadata(game_id, sessions_total + int(payload.get("playtime_baseline_seconds", 0) or 0), int(payload.get("last_played", 0) or 0))
        unlocks = {str(k): float((v or {}).get("unlock_time", 0) or 0) for k, v in (payload.get("achievement_unlocks") or {}).items()}
        if unlocks:
            db.unlock_achievements_batch(game_id, unlocks)

    @staticmethod
    def _local_profile(db) -> dict:
        db.collect_profile_from_games()
        return {"format_version": 1, "achievements": db.get_profile_unlocks()}

    @staticmethod
    def _merge_legacy_game_unlocks(profile: dict, game_metadata: dict, app_id: str) -> None:
        legacy = game_metadata.get("achievement_unlocks") if isinstance(game_metadata, dict) else None
        if not legacy or not app_id:
            return
        current = profile.setdefault("achievements", {}).setdefault(str(app_id), {})
        merged = _merge_unlocks(current, legacy)
        profile["achievements"][str(app_id)] = merged

    @staticmethod
    def _apply_profile(db, profile: dict) -> None:
        for app_id, unlocks in (profile.get("achievements", {}) or {}).items():
            db.merge_profile_unlocks(str(app_id), {
                str(name): float((value or {}).get("unlock_time", 0) or 0)
                for name, value in (unlocks or {}).items() if isinstance(value, dict)
            })
        for game in db.get_all_games():
            app_id = str(game.steam_id or "").strip()
            if app_id:
                db.project_profile_achievements(game.id, app_id)

    @classmethod
    def sync_profile(cls, db) -> bool:
        """Union local and cloud account-wide unlocks, then project locally."""
        with _PROFILE_SYNC_LOCK:
            return cls._sync_profile_locked(db)

    @classmethod
    def _sync_profile_locked(cls, db) -> bool:
        """Serialized implementation of :meth:`sync_profile`."""
        local = cls._local_profile(db)
        try:
            from core.cloud_save_sync import backend_active, CloudSaveSyncEngine, resolve_name_key
            if backend_active():
                from core.cloud_save_sync import _backend
                backend = _backend()
                # One-time compatibility bridge: promote old per-game cloud
                # metadata before the profile record becomes authoritative.
                for game in db.get_all_games():
                    app_id = str(game.steam_id or "").strip()
                    if not app_id:
                        continue
                    try:
                        legacy = backend.get_game_metadata(f"{resolve_name_key(game.name)}-{app_id}")
                        cls._merge_legacy_game_unlocks(local, legacy.get("metadata") or {}, app_id)
                    except Exception as exc:
                        logger.debug("Legacy achievement migration unavailable for %s: %s", game.name, exc)
                for attempt in range(2):
                    remote_result = backend.get_achievement_profile()
                    merged = _merge_profiles(local, remote_result.get("profile") or {})
                    try:
                        backend.put_achievement_profile(merged, remote_result.get("revision"))
                        cls._apply_profile(db, merged)
                        return True
                    except Exception:
                        if attempt == 1:
                            raise
                return False

            root = os.path.join(CloudSaveSyncEngine.get_cloud_root(), "metadata")
            os.makedirs(root, mode=0o700, exist_ok=True)
            path = os.path.join(root, "achievement_profile.json")
            remote = {}
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        remote = json.load(fh)
                except (OSError, ValueError):
                    remote = {}
            for game in db.get_all_games():
                app_id = str(game.steam_id or "").strip()
                if not app_id:
                    continue
                legacy_path = os.path.join(root, f"{resolve_name_key(game.name)}-{app_id}.json")
                try:
                    if os.path.isfile(legacy_path):
                        with open(legacy_path, "r", encoding="utf-8") as fh:
                            cls._merge_legacy_game_unlocks(local, json.load(fh), app_id)
                except (OSError, ValueError):
                    continue
            merged = _merge_profiles(local, remote)
            fd, temp_path = tempfile.mkstemp(prefix=".achievement-profile-", suffix=".tmp", dir=root)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(merged, fh, indent=2, sort_keys=True)
                os.replace(temp_path, path)
            finally:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
            cls._apply_profile(db, merged)
            return True
        except Exception as exc:
            logger.warning("Achievement profile sync failed: %s", exc)
            return False

    @classmethod
    def sync_game(cls, db, game_id: int, game_name: str, app_id: str = "", drop_legacy_achievements: bool = False) -> bool:
        from core.cloud_save_sync import backend_active, resolve_name_key

        key = f"{resolve_name_key(game_name)}-{str(app_id).strip()}" if str(app_id).strip() else resolve_name_key(game_name)
        local = cls._local_payload(db, game_id, key, app_id, include_legacy_achievements=not drop_legacy_achievements)
        try:
            if backend_active():
                from core.cloud_save_sync import _backend
                backend = _backend()
                for attempt in range(2):
                    remote_result = backend.get_game_metadata(key)
                    merged = cls._merge(local, remote_result.get("metadata") or {}, include_legacy_achievements=not drop_legacy_achievements)
                    try:
                        backend.put_game_metadata(key, merged, remote_result.get("revision"))
                        cls._apply(db, game_id, merged)
                        return True
                    except Exception as exc:
                        if attempt == 1:
                            raise
                        logger.debug("Metadata revision conflict for %s; retrying merge: %s", key, exc)
                return False

            from core.cloud_save_sync import CloudSaveSyncEngine
            root = os.path.join(CloudSaveSyncEngine.get_cloud_root(), "metadata")
            os.makedirs(root, mode=0o700, exist_ok=True)
            path = os.path.join(root, f"{key}.json")
            remote = {}
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        remote = json.load(fh)
                except (OSError, ValueError):
                    remote = {}
            merged = cls._merge(local, remote, include_legacy_achievements=not drop_legacy_achievements)
            fd, temp_path = tempfile.mkstemp(prefix=".metadata-", suffix=".tmp", dir=root)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(merged, fh, indent=2, sort_keys=True)
                os.replace(temp_path, path)
            finally:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
            cls._apply(db, game_id, merged)
            return True
        except Exception as exc:
            logger.warning("SafeLauncher metadata sync failed for '%s': %s", game_name, exc)
            return False
