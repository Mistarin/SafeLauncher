"""Separate SafeLauncher-owned cloud metadata synchronization.

Game save archives contain game-owned files.  This module synchronizes
SafeLauncher-owned achievements and playtime independently, so metadata also
works for games without save files and is not coupled to save conflicts.
"""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
import time
import threading
import math
import re
from typing import Optional
from PyQt6.QtCore import QSettings

from core.logger import get_logger
from core.achievement_models import merge_observations
from core.profile_models import (
    PRIVATE_PROFILE_VERSION,
    load_profile_settings,
    merge_profile_settings,
    normalize_profile_settings,
    save_profile_settings,
)

logger = get_logger("CloudMetadata")
_PROFILE_SYNC_LOCK = threading.Lock()
_PROFILE_SYNC_TTL_SECONDS = 60.0
_PROFILE_SYNC_STATE = {
    "context": "",
    "db_key": "",
    "local_digest": "",
    "synced_at": 0.0,
}

_PROFILE_APP_RE = re.compile(r"^[0-9]{1,16}$")
_PROFILE_API_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MAX_PROFILE_APPS = 10_000
_MAX_PROFILE_UNLOCKS_PER_APP = 20_000


def _safe_int(value, default: int = 0) -> int:
    try:
        result = int(float(value or 0))
        return max(0, result)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        result = float(value or 0)
        return result if math.isfinite(result) and result >= 0 else default
    except (TypeError, ValueError, OverflowError):
        return default


def _normalise_unlock(value) -> dict:
    """Accept old numeric/dict records but return a bounded record."""
    if isinstance(value, dict):
        raw_time = value.get("unlock_time", 0)
        record = {
            "unlock_time": raw_time,
            "provenance": str(value.get("provenance", "unknown") or "unknown"),
            "verified": bool(value.get("verified", False)),
            "validation_state": str(value.get("validation_state", "pending_schema") or "pending_schema"),
            "source_format": str(value.get("source_format", "") or "")[:32],
        }
    else:
        record = {"unlock_time": value, "provenance": "local_emulator", "verified": False, "validation_state": "validated", "source_format": ""}
    try:
        stamp = float(record["unlock_time"] or 0)
    except (TypeError, ValueError, OverflowError):
        stamp = 0.0
    record["unlock_time"] = stamp if math.isfinite(stamp) and stamp >= 0 else 0.0
    if record["validation_state"] not in {"validated", "pending_schema"}:
        record["validation_state"] = "pending_schema"
    if record["provenance"] not in {"steam_verified", "local_emulator", "cloud_profile", "cache", "unknown"}:
        record["provenance"] = "unknown"
    record["verified"] = record["provenance"] == "steam_verified"
    return record


def _normalise_profile(profile: dict) -> dict:
    """Validate untrusted cloud JSON before it enters the merge path."""
    if not isinstance(profile, dict):
        return {"format_version": PRIVATE_PROFILE_VERSION, "profile": normalize_profile_settings({}), "games": {}, "achievements": {}}
    profile_settings = normalize_profile_settings(profile.get("profile"))
    achievements = {}
    raw_achievements = profile.get("achievements") if isinstance(profile.get("achievements"), dict) else {}
    for raw_app, raw_unlocks in list(raw_achievements.items())[:_MAX_PROFILE_APPS]:
        app_id = str(raw_app).strip()
        if not _PROFILE_APP_RE.fullmatch(app_id) or not isinstance(raw_unlocks, dict):
            continue
        clean = {}
        for raw_name, raw_value in list(raw_unlocks.items())[:_MAX_PROFILE_UNLOCKS_PER_APP]:
            name = str(raw_name).strip()
            if _PROFILE_API_RE.fullmatch(name):
                clean[name] = _normalise_unlock(raw_value)
        if clean:
            achievements[app_id] = clean
    games = {}
    raw_games = profile.get("games") if isinstance(profile.get("games"), dict) else {}
    for raw_identity, raw_value in list(raw_games.items())[:_MAX_PROFILE_APPS]:
        identity = str(raw_identity).strip()
        if identity and len(identity) <= 256 and isinstance(raw_value, dict):
            sessions = []
            for raw_session in list(raw_value.get("playtime_sessions", []) or [])[:2_000]:
                if not isinstance(raw_session, dict):
                    continue
                session_id = str(raw_session.get("session_id", "")).strip()
                if not session_id or len(session_id) > 128:
                    continue
                sessions.append({
                    "session_id": session_id,
                    "started_at": _safe_int(raw_session.get("started_at")),
                    "ended_at": _safe_int(raw_session.get("ended_at")),
                    "duration_seconds": _safe_int(raw_session.get("duration_seconds")),
                    "finalized": bool(raw_session.get("finalized", False)),
                })
            games[identity] = {
                "identity_key": identity,
                "app_id": str(raw_value.get("app_id", "") or "")[:32],
                "favorite": bool(raw_value.get("favorite", False)),
                "favorite_changed_at": _safe_float(raw_value.get("favorite_changed_at")),
                "favorite_change_id": str(raw_value.get("favorite_change_id", "") or "")[:128],
                "playtime_baseline_seconds": _safe_int(raw_value.get("playtime_baseline_seconds")),
                "playtime_sessions": sessions,
                "last_played": _safe_int(raw_value.get("last_played")),
            }
    return {"format_version": PRIVATE_PROFILE_VERSION, "profile": profile_settings, "games": games, "achievements": achievements}


def _merge_unlocks(local: dict, remote: dict) -> dict:
    merged = {}
    local = local if isinstance(local, dict) else {}
    remote = remote if isinstance(remote, dict) else {}
    for api_name in set(local) | set(remote):
        name = str(api_name).strip()
        if not _PROFILE_API_RE.fullmatch(name):
            continue
        left = _normalise_unlock(local.get(api_name)) if api_name in local else None
        right = _normalise_unlock(remote.get(api_name)) if api_name in remote else None
        merged[name] = merge_observations(left, right)
    return merged


def _merge_sessions(local: list, remote: list) -> list:
    by_id = {}
    local_items = local[:2_000] if isinstance(local, list) else []
    remote_items = remote[:2_000] if isinstance(remote, list) else []
    for item in local_items + remote_items:
        if not isinstance(item, dict) or not str(item.get("session_id", "")).strip():
            continue
        sid = str(item["session_id"]).strip()[:128]
        if not sid:
            continue
        old = by_id.get(sid, {})
        by_id[sid] = {
            "session_id": sid,
            "started_at": min(_safe_int(old.get("started_at")), _safe_int(item.get("started_at"))) if old else _safe_int(item.get("started_at")),
            "ended_at": max(_safe_int(old.get("ended_at")), _safe_int(item.get("ended_at"))),
            "duration_seconds": max(_safe_int(old.get("duration_seconds")), _safe_int(item.get("duration_seconds"))),
            "finalized": bool(old.get("finalized", False) or item.get("finalized", False)),
        }
    return sorted(by_id.values(), key=lambda x: (x["started_at"], x["session_id"]))


def _merge_profiles(local: dict, remote: dict) -> dict:
    """Merge the account profile without losing achievements or playtime."""
    local = _normalise_profile(local)
    remote = _normalise_profile(remote)
    merged_games = {}
    for identity in set((local or {}).get("games", {}) or {}) | set((remote or {}).get("games", {}) or {}):
        left = ((local or {}).get("games", {}) or {}).get(identity, {}) or {}
        right = ((remote or {}).get("games", {}) or {}).get(identity, {}) or {}
        left_key = (_safe_float(left.get("favorite_changed_at")), str(left.get("favorite_change_id", "") or ""))
        right_key = (_safe_float(right.get("favorite_changed_at")), str(right.get("favorite_change_id", "") or ""))
        markerless_favorite = left_key == right_key == (0, "")
        favorite_source = right if right_key >= left_key else left
        merged_games[str(identity)] = {
            "identity_key": str(identity),
            "app_id": str(left.get("app_id") or right.get("app_id") or ""),
            # Before favorite change markers existed, a remote default false
            # must not erase a local true favorite. Once either side has a
            # marker, the later marker remains the authoritative toggle.
            "favorite": (
                bool(left.get("favorite", False) or right.get("favorite", False))
                if markerless_favorite else bool(favorite_source.get("favorite", False))
            ),
            "favorite_changed_at": max(left_key[0], right_key[0]),
            "favorite_change_id": str(favorite_source.get("favorite_change_id", "") or ""),
            "playtime_baseline_seconds": max(_safe_int(left.get("playtime_baseline_seconds")), _safe_int(right.get("playtime_baseline_seconds"))),
            "playtime_sessions": _merge_sessions(left.get("playtime_sessions"), right.get("playtime_sessions")),
            "last_played": max(_safe_int(left.get("last_played")), _safe_int(right.get("last_played"))),
        }
    merged = {}
    for app_id in set((local or {}).get("achievements", {}) or {}) | set((remote or {}).get("achievements", {}) or {}):
        left = ((local or {}).get("achievements", {}) or {}).get(app_id, {}) or {}
        right = ((remote or {}).get("achievements", {}) or {}).get(app_id, {}) or {}
        merged[str(app_id)] = _merge_unlocks(left, right)
    return {
        "format_version": PRIVATE_PROFILE_VERSION,
        "profile": merge_profile_settings(local.get("profile"), remote.get("profile")),
        "games": merged_games,
        "achievements": merged,
    }


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
                str(x["api_name"]): {
                    "unlock_time": int(float(x.get("unlock_time", 0) or 0)),
                    "provenance": x.get("provenance", "local_emulator"),
                    "verified": bool(x.get("verified", False)),
                    "validation_state": "validated",
                    "source_format": x.get("source_format", ""),
                }
                for x in db.get_game_achievements(game_id) if x.get("unlocked")
            }
        return payload

    @staticmethod
    def _merge(local: dict, remote: dict, include_legacy_achievements: bool = True) -> dict:
        local = local if isinstance(local, dict) else {}
        remote = remote if isinstance(remote, dict) else {}
        sessions = _merge_sessions(local.get("playtime_sessions"), remote.get("playtime_sessions"))
        merged = {
            "format_version": 1,
            "game_key": str(local.get("game_key") or remote.get("game_key", ""))[:256],
            "app_id": str(local.get("app_id") or remote.get("app_id", ""))[:32],
            "last_played": max(_safe_int(local.get("last_played")), _safe_int(remote.get("last_played"))),
            "playtime_baseline_seconds": max(
                _safe_int(local.get("playtime_baseline_seconds")),
                _safe_int(remote.get("playtime_baseline_seconds")),
            ),
            "playtime_sessions": sessions,
        }
        if include_legacy_achievements:
            merged["achievement_unlocks"] = _merge_unlocks(local.get("achievement_unlocks", {}), remote.get("achievement_unlocks", {}))
        return merged

    @staticmethod
    def _apply(db, game_id: int, payload: dict) -> None:
        payload = payload if isinstance(payload, dict) else {}
        sessions = payload.get("playtime_sessions", [])
        sessions = sessions if isinstance(sessions, list) else []
        db.merge_playtime_sessions(game_id, sessions)
        sessions_total = sum(
            _safe_int(x.get("duration_seconds"))
            for x in sessions
            if isinstance(x, dict)
        )
        db.merge_playtime_metadata(
            game_id,
            sessions_total + _safe_int(payload.get("playtime_baseline_seconds")),
            _safe_int(payload.get("last_played")),
        )
        raw_unlocks = payload.get("achievement_unlocks")
        raw_unlocks = raw_unlocks if isinstance(raw_unlocks, dict) else {}
        unlocks = {str(k): v for k, v in list(raw_unlocks.items())[:_MAX_PROFILE_UNLOCKS_PER_APP] if isinstance(v, (dict, int, float))}
        if unlocks:
            db.record_achievement_state(
                game_id, str(payload.get("app_id", "") or ""), unlocks,
                provenance="cloud_profile", verified=False, source_format="cloud-profile",
            )

    @staticmethod
    def _local_profile(db) -> dict:
        db.collect_profile_from_games()
        games = {}
        for game in db.get_all_games():
            identity = db.profile_identity(game.name, game.steam_id)
            sessions = db.get_playtime_sessions(game.id)
            session_total = sum(int(x.get("duration_seconds", 0) or 0) for x in sessions)
            games[identity] = {
                "identity_key": identity,
                "app_id": str(game.steam_id or "").strip(),
                "favorite": bool(game.is_favorite),
                "favorite_changed_at": 0,
                "favorite_change_id": "",
                "playtime_baseline_seconds": max(0, int(game.playtime_seconds or 0) - session_total),
                "playtime_sessions": sessions,
                "last_played": max(0, int(game.last_played or 0)),
            }
        # Retain profile-only entries for games removed from this installation.
        for item in db.get_profile_games():
            current = games.get(item["identity_key"])
            if current:
                current["favorite_changed_at"] = item["favorite_changed_at"]
                current["favorite_change_id"] = item["favorite_change_id"]
            else:
                games[item["identity_key"]] = item
        # If a previously local game has now been assigned an AppID, carry
        # its profile history into the stable Steam identity.  The old local
        # record remains as a harmless alias until the next cleanup pass.
        for game in db.get_all_games():
            app_id = str(game.steam_id or "").strip()
            if not app_id:
                continue
            steam_identity = db.profile_identity(game.name, app_id)
            local_identity = db.profile_identity(game.name, "")
            if local_identity in games and local_identity != steam_identity:
                migrated = _merge_profiles(
                    {"games": {steam_identity: games.get(steam_identity, {})}},
                    {"games": {steam_identity: games[local_identity]}},
                )["games"][steam_identity]
                games[steam_identity] = migrated
        settings = QSettings("SafeLauncher", "SafeLauncher")
        fallback_name = str(settings.value("user_name", "Player", type=str) or "Player")
        return {
            "format_version": PRIVATE_PROFILE_VERSION,
            "profile": load_profile_settings(settings, fallback_name=fallback_name),
            "games": games,
            "achievements": db.get_profile_unlock_records(include_pending=True),
        }

    @staticmethod
    def _merge_legacy_game_unlocks(profile: dict, game_metadata: dict, app_id: str) -> None:
        legacy = game_metadata.get("achievement_unlocks") if isinstance(game_metadata, dict) else None
        if not legacy or not app_id:
            return
        current = profile.setdefault("achievements", {}).setdefault(str(app_id), {})
        # Legacy per-game metadata had no provenance. It is retained as a
        # local/unverified observation and never treated as Steam proof.
        merged = _merge_unlocks(current, legacy)
        profile["achievements"][str(app_id)] = merged

    @staticmethod
    def _apply_profile(db, profile: dict) -> None:
        profile_settings = profile.get("profile")
        if isinstance(profile_settings, dict):
            save_profile_settings(
                QSettings("SafeLauncher", "SafeLauncher"),
                profile_settings,
                mark_changed=False,
            )
        for identity, value in (profile.get("games", {}) or {}).items():
            if not isinstance(value, dict):
                continue
            item = dict(value)
            item["identity_key"] = str(identity)
            sessions = item.get("playtime_sessions", []) or []
            item["playtime_seconds"] = int(item.get("playtime_baseline_seconds", 0) or 0) + sum(
                int(x.get("duration_seconds", 0) or 0) for x in sessions if isinstance(x, dict)
            )
            db.merge_profile_game(item)
            for game in db.get_all_games():
                if db.profile_identity(game.name, game.steam_id) == str(identity):
                    db.merge_playtime_sessions(game.id, sessions)
                    db.project_profile_game(game.id, item)
        for app_id, unlocks in (profile.get("achievements", {}) or {}).items():
            if not isinstance(unlocks, dict):
                continue
            known_names = {
                str(row[0]) for row in db.conn.execute(
                    "SELECT DISTINCT api_name FROM achievements WHERE app_id = ?", (str(app_id),)
                ).fetchall()
            }
            db.merge_profile_unlocks(str(app_id), {
                str(name): value for name, value in unlocks.items()
                if isinstance(value, (dict, int, float))
            }, provenance="cloud_profile", validation_state="pending_schema", allowed_api_names=known_names)
        for game in db.get_all_games():
            app_id = str(game.steam_id or "").strip()
            if app_id:
                db.project_profile_achievements(game.id, app_id)

    @classmethod
    def sync_profile(cls, db, *, force: bool = False) -> bool:
        """Union local and cloud account-wide unlocks, then project locally."""
        with _PROFILE_SYNC_LOCK:
            return cls._sync_profile_locked(db, force=force)

    @classmethod
    def _sync_profile_locked(cls, db, *, force: bool = False) -> bool:
        """Serialized implementation of :meth:`sync_profile`."""
        from core.cloud_save_sync import backend_active, _cloud_auth_configured
        if backend_active() and not _cloud_auth_configured():
            # The backend rejects unauthenticated requests. Return quietly so
            # startup does not create one failed network operation per game.
            return False

        local = cls._local_profile(db)
        try:
            from core.cloud_save_sync import (
                backend_active,
                CloudSaveSyncEngine,
                resolve_name_key,
                cloud_context_fingerprint,
            )
            context = cloud_context_fingerprint()
            db_key = str(getattr(db, "db_path", "") or f"memory:{id(db)}")
            local_digest = hashlib.sha256(
                json.dumps(local, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            now = time.monotonic()
            if (
                not force
                and _PROFILE_SYNC_STATE["context"] == context
                and _PROFILE_SYNC_STATE["db_key"] == db_key
                and _PROFILE_SYNC_STATE["local_digest"] == local_digest
                and now - _PROFILE_SYNC_STATE["synced_at"] < _PROFILE_SYNC_TTL_SECONDS
            ):
                return True
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
                    remote_result = backend.get_profile()
                    merged = _merge_profiles(local, remote_result.get("profile") or {})
                    try:
                        backend.put_profile(merged, remote_result.get("revision"))
                        cls._apply_profile(db, merged)
                        _PROFILE_SYNC_STATE.update(
                            context=context,
                            db_key=db_key,
                            local_digest=local_digest,
                            synced_at=time.monotonic(),
                        )
                        return True
                    except Exception:
                        if attempt == 1:
                            raise
                return False

            root = os.path.join(CloudSaveSyncEngine.get_cloud_root(), "metadata")
            os.makedirs(root, mode=0o700, exist_ok=True)
            path = os.path.join(root, "launcher_profile.json")
            remote = {}
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        remote = json.load(fh)
                except (OSError, ValueError):
                    remote = {}
            # Read the old filename once so existing installations migrate
            # without requiring a cloud backend.
            legacy_profile_path = os.path.join(root, "achievement_profile.json")
            if not remote and os.path.isfile(legacy_profile_path):
                try:
                    with open(legacy_profile_path, "r", encoding="utf-8") as fh:
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
            _PROFILE_SYNC_STATE.update(
                context=context,
                db_key=db_key,
                local_digest=local_digest,
                synced_at=time.monotonic(),
            )
            return True
        except Exception as exc:
            logger.warning("Achievement profile sync failed: %s", exc)
            return False

    @classmethod
    def sync_game(cls, db, game_id: int, game_name: str, app_id: str = "", drop_legacy_achievements: bool = False) -> bool:
        from core.cloud_save_sync import backend_active, resolve_name_key, _cloud_auth_configured

        if backend_active() and not _cloud_auth_configured():
            # Check before resolve_name_key: resolving a cloud key can itself
            # perform a listing request, which is pointless without a secret.
            return False

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
