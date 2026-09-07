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
from typing import Optional

from core.logger import get_logger

logger = get_logger("CloudMetadata")


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


class CloudMetadataSync:
    """Build, merge, and persist SafeLauncher metadata for one game."""

    @staticmethod
    def _local_payload(db, game_id: int, game_key: str, app_id: str) -> dict:
        row = db.conn.execute("SELECT playtime_seconds, last_played FROM games WHERE id = ?", (game_id,)).fetchone()
        total = int(row[0] or 0) if row else 0
        last_played = int(row[1] or 0) if row else 0
        sessions = db.get_playtime_sessions(game_id)
        session_total = sum(int(x.get("duration_seconds", 0) or 0) for x in sessions)
        achievements = {
            str(x["api_name"]): {"unlock_time": int(float(x.get("unlock_time", 0) or 0))}
            for x in db.get_game_achievements(game_id) if x.get("unlocked")
        }
        return {
            "format_version": 1,
            "game_key": game_key,
            "app_id": str(app_id or ""),
            "last_played": max(0, last_played),
            "playtime_baseline_seconds": max(0, total - session_total),
            "achievement_unlocks": achievements,
            "playtime_sessions": sessions,
        }

    @staticmethod
    def _merge(local: dict, remote: dict) -> dict:
        sessions = _merge_sessions(local.get("playtime_sessions"), remote.get("playtime_sessions"))
        return {
            "format_version": 1,
            "game_key": local.get("game_key") or remote.get("game_key", ""),
            "app_id": str(local.get("app_id") or remote.get("app_id", "")),
            "last_played": max(int(local.get("last_played", 0) or 0), int(remote.get("last_played", 0) or 0)),
            "playtime_baseline_seconds": max(int(local.get("playtime_baseline_seconds", 0) or 0), int(remote.get("playtime_baseline_seconds", 0) or 0)),
            "achievement_unlocks": _merge_unlocks(local.get("achievement_unlocks", {}), remote.get("achievement_unlocks", {})),
            "playtime_sessions": sessions,
        }

    @staticmethod
    def _apply(db, game_id: int, payload: dict) -> None:
        db.merge_playtime_sessions(game_id, payload.get("playtime_sessions", []))
        sessions_total = sum(int(x.get("duration_seconds", 0) or 0) for x in payload.get("playtime_sessions", []))
        db.merge_playtime_metadata(game_id, sessions_total + int(payload.get("playtime_baseline_seconds", 0) or 0), int(payload.get("last_played", 0) or 0))
        unlocks = {str(k): float((v or {}).get("unlock_time", 0) or 0) for k, v in (payload.get("achievement_unlocks") or {}).items()}
        if unlocks:
            db.unlock_achievements_batch(game_id, unlocks)

    @classmethod
    def sync_game(cls, db, game_id: int, game_name: str, app_id: str = "") -> bool:
        from core.cloud_save_sync import backend_active, resolve_name_key

        key = f"{resolve_name_key(game_name)}-{str(app_id).strip()}" if str(app_id).strip() else resolve_name_key(game_name)
        local = cls._local_payload(db, game_id, key, app_id)
        try:
            if backend_active():
                from core.cloud_save_sync import _backend
                backend = _backend()
                for attempt in range(2):
                    remote_result = backend.get_game_metadata(key)
                    merged = cls._merge(local, remote_result.get("metadata") or {})
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
            merged = cls._merge(local, remote)
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

