"""Versioned local and public profile models.

Private profile metadata may contain the complete launcher ledger.  Public
profiles are always built through :func:`build_public_projection`, which is a
deliberate privacy boundary and never includes installation paths or secrets.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
import uuid
from typing import Any

from PyQt6.QtCore import QSettings

from core.profile_assets import validate_avatar_payload


PRIVATE_PROFILE_VERSION = 4
PUBLIC_PROFILE_VERSION = 1
HANDLE_RE = re.compile(r"^[a-f0-9]{20,40}$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_NAME_LENGTH = 64
MAX_PUBLIC_GAMES = 24
MAX_PUBLIC_ACHIEVEMENTS = 20
MAX_PUBLIC_FRIENDS = 100
MAX_PUBLIC_FRIEND_REQUESTS = 50

DEFAULT_BACKGROUND = {
    "kind": "gradient",
    "stops": ["#1C3158", "#121214"],
    "angle": 135,
}
BACKGROUND_PRESETS = {
    "midnight": {"kind": "gradient", "stops": ["#1C3158", "#121214"], "angle": 135},
    "ember": {"kind": "gradient", "stops": ["#5C2630", "#171417"], "angle": 135},
    "forest": {"kind": "gradient", "stops": ["#17483F", "#101817"], "angle": 135},
    "violet": {"kind": "gradient", "stops": ["#3D2A67", "#14131D"], "angle": 135},
    "slate": {"kind": "solid", "color": "#20242C"},
}


def generate_profile_handle() -> str:
    return secrets.token_hex(12)


def _clean_name(value: Any, fallback: str = "Player") -> str:
    value = " ".join(str(value or "").strip().split())[:MAX_NAME_LENGTH]
    return value or fallback


def normalize_background(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = BACKGROUND_PRESETS.get(value, DEFAULT_BACKGROUND)
    if not isinstance(value, dict):
        return dict(DEFAULT_BACKGROUND)
    kind = str(value.get("kind", "gradient"))
    if kind == "solid" and COLOR_RE.fullmatch(str(value.get("color", ""))):
        return {"kind": "solid", "color": str(value["color"]).upper()}
    stops = value.get("stops")
    if isinstance(stops, list) and 2 <= len(stops) <= 3 and all(COLOR_RE.fullmatch(str(x)) for x in stops):
        try:
            angle = max(0, min(360, int(value.get("angle", 135))))
        except (TypeError, ValueError, OverflowError):
            angle = 135
        return {"kind": "gradient", "stops": [str(x).upper() for x in stops], "angle": angle}
    return dict(DEFAULT_BACKGROUND)


def normalize_profile_settings(value: Any, fallback_name: str = "Player") -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    handle = str(value.get("public_handle", "") or "").strip().lower()
    if not HANDLE_RE.fullmatch(handle):
        handle = ""
    try:
        changed_at = max(0.0, float(value.get("changed_at", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        changed_at = 0.0
    avatar = validate_avatar_payload(value.get("avatar"))
    return {
        "display_name": _clean_name(value.get("display_name"), fallback_name),
        "avatar": avatar,
        "background": normalize_background(value.get("background")),
        "public_handle": handle,
        "published": bool(value.get("published", False)),
        "changed_at": changed_at,
        "change_id": str(value.get("change_id", "") or "")[:64],
    }


def load_profile_settings(settings: QSettings | None = None, fallback_name: str = "Player") -> dict[str, Any]:
    settings = settings or QSettings("SafeLauncher", "SafeLauncher")
    raw_background = settings.value("profile_background", "", type=str)
    try:
        background = json.loads(raw_background) if raw_background else DEFAULT_BACKGROUND
    except (TypeError, ValueError):
        background = DEFAULT_BACKGROUND
    raw_avatar = settings.value("profile_avatar", "", type=str)
    try:
        avatar = json.loads(raw_avatar) if raw_avatar else None
    except (TypeError, ValueError):
        avatar = None
    return normalize_profile_settings({
        "display_name": settings.value("profile_display_name", fallback_name, type=str),
        "avatar": avatar,
        "background": background,
        "public_handle": settings.value("profile_public_handle", "", type=str),
        "published": settings.value("profile_published", False, type=bool),
        "changed_at": settings.value("profile_changed_at", 0.0, type=float),
        "change_id": settings.value("profile_change_id", "", type=str),
    }, fallback_name)


def save_profile_settings(settings: QSettings, value: dict[str, Any], *, mark_changed: bool = True) -> dict[str, Any]:
    normalized = normalize_profile_settings(value)
    if mark_changed:
        normalized["changed_at"] = time.time()
        normalized["change_id"] = str(uuid.uuid4())
    settings.setValue("profile_display_name", normalized["display_name"])
    settings.setValue("profile_avatar", json.dumps(normalized["avatar"], sort_keys=True) if normalized["avatar"] else "")
    settings.setValue("profile_background", json.dumps(normalized["background"], sort_keys=True))
    settings.setValue("profile_public_handle", normalized["public_handle"])
    settings.setValue("profile_published", normalized["published"])
    settings.setValue("profile_changed_at", normalized["changed_at"])
    settings.setValue("profile_change_id", normalized["change_id"])
    settings.sync()
    return normalized


def private_profile_settings(value: dict[str, Any]) -> dict[str, Any]:
    """Return profile fields safe to put inside the encrypted private ledger."""
    normalized = normalize_profile_settings(value)
    return normalized


def merge_profile_settings(local: Any, remote: Any) -> dict[str, Any]:
    left = normalize_profile_settings(local)
    right = normalize_profile_settings(remote)
    left_key = (left["changed_at"], left["change_id"])
    right_key = (right["changed_at"], right["change_id"])
    if right_key > left_key:
        return right
    if left_key > right_key:
        return left
    # Markerless legacy profiles should not erase a populated local profile.
    if left_key == (0, "") and right_key == (0, ""):
        return left if left["display_name"] != "Player" or left["avatar"] else right
    return left


def _game_name_map(db) -> dict[str, str]:
    result = {}
    for game in db.get_all_games():
        result[db.profile_identity(game.name, game.steam_id)] = str(game.name)
    return result


def build_public_projection(db, settings: dict[str, Any], *, now: int | None = None) -> dict[str, Any]:
    """Build the only payload allowed to leave the desktop for public viewing."""
    profile = normalize_profile_settings(settings)
    game_names = _game_name_map(db)
    current_local_identities = {
        db.profile_identity(game.name, "") for game in db.get_all_games()
        if str(game.steam_id or "").strip()
    }
    profile_games = {str(x.get("identity_key")): x for x in db.get_profile_games() if isinstance(x, dict)}
    favorites = []
    total_playtime = 0
    for identity, value in profile_games.items():
        if not value.get("favorite"):
            continue
        app_id = str(value.get("app_id", "") or "").strip()
        name = game_names.get(identity) or (f"Steam App {app_id}" if app_id else "Favorite game")
        favorites.append({"name": name[:120], "app_id": app_id[:16]})
    for game in db.get_all_games():
        identity = db.profile_identity(game.name, game.steam_id)
        # The live game row includes both the baseline and finalized sessions;
        # use it for installed games so totals neither omit sessions nor count
        # the baseline twice.
        total_playtime += max(0, int(game.playtime_seconds or 0))
        if game.is_favorite and not any(x["name"] == game.name for x in favorites):
            favorites.append({"name": str(game.name)[:120], "app_id": str(game.steam_id or "")[:16]})
    for identity, value in profile_games.items():
        if identity in game_names:
            continue
        # A pre-AppID local identity may remain as a migration alias after a
        # game receives its stable Steam identity. Its history is represented
        # by the live row and must not be counted a second time publicly.
        if identity in current_local_identities:
            continue
        sessions = value.get("playtime_sessions", []) if isinstance(value.get("playtime_sessions"), list) else []
        total_playtime += max(0, int(value.get("playtime_baseline_seconds", 0) or 0))
        total_playtime += sum(max(0, int(item.get("duration_seconds", 0) or 0)) for item in sessions if isinstance(item, dict))
    favorites = sorted(favorites, key=lambda item: item["name"].casefold())[:MAX_PUBLIC_GAMES]

    records = db.get_profile_unlock_records(include_pending=False)
    unlocked = 0
    recent = []
    known_keys = set()
    display_names = {}
    app_game_names = {}
    for game in db.get_all_games():
        app_id = str(game.steam_id or "").strip()
        if not app_id:
            continue
        rows = db.get_game_achievements(game.id)
        app_game_names.setdefault(app_id, game.name)
        for row in rows:
            api_name = str(row.get("api_name", ""))
            if api_name:
                known_keys.add((app_id, api_name))
                display_names[(app_id, api_name)] = str(row.get("display_name") or api_name or "Achievement")
    for app_id, values in records.items():
        for api_name, value in values.items():
            if not isinstance(value, dict):
                continue
            unlocked += 1
            recent.append({
                "app_id": str(app_id)[:16],
                "api_name": str(api_name)[:128],
                "name": display_names.get((str(app_id), str(api_name)), str(api_name))[:120],
                "game": str(app_game_names.get(str(app_id), f"Steam App {app_id}"))[:120],
                "unlocked_at": max(0, int(float(value.get("unlock_time", 0) or 0))),
            })
    recent.sort(key=lambda item: (-item["unlocked_at"], item["game"].casefold(), item["name"].casefold()))

    return {
        "schema_version": PUBLIC_PROFILE_VERSION,
        "handle": profile["public_handle"],
        "display_name": profile["display_name"],
        "avatar": profile["avatar"],
        "background": profile["background"],
        "stats": {
            "games_count": len(game_names),
            "favorite_count": len(favorites),
            "playtime_seconds": total_playtime,
            "achievements_unlocked": unlocked,
            "achievements_known": max(len(known_keys), unlocked),
        },
        "favorite_games": favorites,
        "recent_achievements": recent[:MAX_PUBLIC_ACHIEVEMENTS],
        "updated_at": max(0, int(now if now is not None else time.time())),
    }


def normalize_public_document(value: Any) -> dict[str, Any] | None:
    """Validate untrusted central-service data before it reaches Qt."""
    if not isinstance(value, dict):
        return None
    handle = str(value.get("handle", "") or "").strip().lower()
    if not HANDLE_RE.fullmatch(handle):
        return None
    name = _clean_name(value.get("display_name"))
    avatar = validate_avatar_payload(value.get("avatar"))
    background = normalize_background(value.get("background"))
    raw_stats = value.get("stats") if isinstance(value.get("stats"), dict) else {}
    stats = {}
    for key in ("games_count", "favorite_count", "playtime_seconds", "achievements_unlocked", "achievements_known"):
        try:
            stats[key] = max(0, min(10_000_000, int(raw_stats.get(key, 0) or 0)))
        except (TypeError, ValueError, OverflowError):
            stats[key] = 0
    stats["achievements_known"] = max(stats["achievements_known"], stats["achievements_unlocked"])
    raw_favorites = value.get("favorite_games", [])
    raw_favorites = raw_favorites if isinstance(raw_favorites, list) else []
    favorites = []
    for item in raw_favorites[:MAX_PUBLIC_GAMES]:
        if isinstance(item, dict):
            favorites.append({"name": _clean_name(item.get("name"), "Favorite game")[:120], "app_id": str(item.get("app_id", ""))[:16]})
    raw_recent = value.get("recent_achievements", [])
    raw_recent = raw_recent if isinstance(raw_recent, list) else []
    recent = []
    for item in raw_recent[:MAX_PUBLIC_ACHIEVEMENTS]:
        if isinstance(item, dict):
            try:
                stamp = max(0, int(item.get("unlocked_at", 0) or 0))
            except (TypeError, ValueError, OverflowError):
                stamp = 0
            recent.append({
                "app_id": str(item.get("app_id", ""))[:16],
                "api_name": str(item.get("api_name", ""))[:128],
                "name": _clean_name(item.get("name"), "Achievement")[:120],
                "game": _clean_name(item.get("game"), "Game")[:120],
                "unlocked_at": stamp,
            })
    try:
        updated_at = max(0, int(value.get("updated_at", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        updated_at = 0
    return {
        "schema_version": PUBLIC_PROFILE_VERSION,
        "handle": handle,
        "display_name": name,
        "avatar": avatar,
        "background": background,
        "stats": stats,
        "favorite_games": favorites,
        "recent_achievements": recent,
        "updated_at": updated_at,
    }


def normalize_profile_summary(value: Any) -> dict[str, Any] | None:
    """Normalize the small, private social-list representation from the service."""
    if not isinstance(value, dict):
        return None
    handle = str(value.get("handle", "") or "").strip().lower()
    if not HANDLE_RE.fullmatch(handle):
        return None
    try:
        updated_at = max(0, int(value.get("updated_at", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        updated_at = 0
    return {
        "handle": handle,
        "display_name": _clean_name(value.get("display_name"), "Player"),
        "updated_at": updated_at,
    }


def normalize_social_snapshot(value: Any) -> dict[str, Any] | None:
    """Validate an owner-authenticated social response before it reaches Qt."""
    if not isinstance(value, dict):
        return None

    def summaries(key: str) -> list[dict[str, Any]]:
        raw = value.get(key)
        if not isinstance(raw, list):
            return []
        result = []
        for item in raw[:MAX_PUBLIC_FRIENDS]:
            normalized = normalize_profile_summary(item)
            if normalized is not None:
                result.append(normalized)
        return result

    def requests(key: str) -> list[dict[str, Any]]:
        raw = value.get(key)
        if not isinstance(raw, list):
            return []
        result = []
        for item in raw[:MAX_PUBLIC_FRIEND_REQUESTS]:
            if not isinstance(item, dict):
                continue
            request_id = str(item.get("request_id", "") or "").strip()
            summary = normalize_profile_summary(item)
            if not request_id or summary is None:
                continue
            summary["request_id"] = request_id[:128]
            try:
                summary["created_at"] = max(0, int(item.get("created_at", 0) or 0))
            except (TypeError, ValueError, OverflowError):
                summary["created_at"] = 0
            result.append(summary)
        return result

    blocked = value.get("blocked_handles")
    blocked_handles = []
    if isinstance(blocked, list):
        for handle in blocked[:MAX_PUBLIC_FRIENDS]:
            handle = str(handle or "").strip().lower()
            if HANDLE_RE.fullmatch(handle):
                blocked_handles.append(handle)
    return {
        "friends": summaries("friends"),
        "incoming_requests": requests("incoming_requests"),
        "outgoing_requests": requests("outgoing_requests"),
        "blocked_handles": blocked_handles,
    }


def public_digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
