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
import unicodedata
import uuid
from typing import Any
from urllib.parse import urlsplit

from PyQt6.QtCore import QSettings

PRIVATE_PROFILE_VERSION = 7
PUBLIC_PROFILE_VERSION = 3
LEGACY_HANDLE_RE = re.compile(r"^[a-f0-9]{20,40}$")
USERNAME_HANDLE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,30}[a-z0-9])?$")
HANDLE_RE = re.compile(r"^(?:[a-f0-9]{20,40}|[a-z0-9](?:[a-z0-9._-]{1,30}[a-z0-9])?)$")
AVATAR_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
STEAM_APP_ID_RE = re.compile(r"^[1-9][0-9]{0,15}$")
STEAM_ARTWORK_RE = re.compile(
    r"^https://(?:cdn\.akamai\.steamstatic\.com|shared\.akamai\.steamstatic\.com|"
    r"steamcdn-a\.akamaihd\.net)/(?:steam/apps|store_item_assets/steam/apps)/"
    r"[1-9][0-9]{0,15}/capsule_616x353\.jpg$"
)
STEAM_HERO_RE = re.compile(
    r"^https://(?:cdn\.akamai\.steamstatic\.com|shared\.akamai\.steamstatic\.com|"
    r"steamcdn-a\.akamaihd\.net)/(?:steam/apps|store_item_assets/steam/apps)/"
    r"[1-9][0-9]{0,15}/(?:library_hero|page_bg_raw|page_bg_generated_v6)\.jpg$"
)
STEAM_ARTWORK_HOSTS = frozenset({
    "cdn.akamai.steamstatic.com",
    "shared.akamai.steamstatic.com",
    "steamcdn-a.akamaihd.net",
})
MAX_NAME_LENGTH = 64
MAX_BIO_LENGTH = 160
MAX_PUBLIC_GAMES = 60
MAX_PUBLIC_ACHIEVEMENTS = 20
MAX_PUBLIC_GAME_ACHIEVEMENTS = 20
MAX_PUBLIC_FRIENDS = 100
MAX_PUBLIC_FRIEND_REQUESTS = 50
MAX_AVATAR_ASSET_NUMBER = 1_000_000

# These numbers are part of the public profile contract. Never reorder or
# reuse them; labels and artwork can change, but a published reference must
# continue to identify the same developer-owned asset.
PANEL_THEME_IDS = {
    "grey": 1,
    "aurora": 2,
    "sunset": 3,
    "bubble": 4,
    "glassmorphism": 5,
}
PANEL_THEME_KEYS = {value: key for key, value in PANEL_THEME_IDS.items()}
BACKGROUND_PRESET_IDS = {
    "midnight": 1,
    "ember": 2,
    "forest": 3,
    "violet": 4,
    "slate": 5,
}
BACKGROUND_PRESET_KEYS = {value: key for key, value in BACKGROUND_PRESET_IDS.items()}

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


def normalize_username_handle(value: Any) -> str:
    """Turn an Auth0 username or entered name into a stable public handle."""
    raw = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    raw = raw.strip().lstrip("@").casefold()
    raw = re.sub(r"[^a-z0-9._-]+", "-", raw)
    raw = re.sub(r"[-_.]{2,}", "-", raw).strip("-_.")[:32].strip("-_.")
    return raw if USERNAME_HANDLE_RE.fullmatch(raw) else ""


def profile_username_suggestion(identity: Any) -> str:
    """Choose a non-secret Auth0 field as the initial handle suggestion."""
    identity = identity if isinstance(identity, dict) else {}
    # Never derive a public handle from an email address.  Some Auth0 social
    # connections omit ``preferred_username``; in that case the user chooses
    # a handle from a safe display-name suggestion instead.
    for key in ("preferred_username", "nickname", "name"):
        candidate = normalize_username_handle(identity.get(key))
        if candidate:
            return candidate
    return "player"


def normalize_avatar_id(value: Any) -> str:
    """Normalize a legacy/catalog avatar slug or numeric compatibility ID."""
    candidate = str(value or "").strip().casefold()
    return candidate if AVATAR_ID_RE.fullmatch(candidate) else ""


def normalize_avatar_asset_id(value: Any) -> int | None:
    """Normalize the immutable numeric ID of a developer-owned avatar."""
    if isinstance(value, bool):
        return None
    raw = str(value or "").strip()
    if not raw.isdigit():
        return None
    try:
        number = int(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 1 <= number <= MAX_AVATAR_ASSET_NUMBER else None


def normalize_panel_theme_id(value: Any) -> int:
    """Normalize a public panel theme reference; unknown reads use Grey."""
    if isinstance(value, str):
        key = value.strip().casefold()
        if key in PANEL_THEME_IDS:
            return PANEL_THEME_IDS[key]
    if not isinstance(value, bool):
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            number = 0
        if number in PANEL_THEME_KEYS:
            return number
    return PANEL_THEME_IDS["grey"]


def panel_theme_key(value: Any) -> str:
    return PANEL_THEME_KEYS.get(normalize_panel_theme_id(value), "grey")


def normalize_background_preset_id(value: Any, background: Any = None) -> int | None:
    """Return the stable ID for a built-in background, if applicable."""
    if isinstance(value, str):
        key = value.strip().casefold()
        if key in BACKGROUND_PRESET_IDS:
            return BACKGROUND_PRESET_IDS[key]
    if not isinstance(value, bool):
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            number = 0
        if number in BACKGROUND_PRESET_KEYS:
            return number
    normalized = normalize_background(background) if background is not None else None
    if normalized is not None:
        for key, candidate in BACKGROUND_PRESETS.items():
            if normalized == normalize_background(candidate):
                return BACKGROUND_PRESET_IDS[key]
    return None


def steam_app_id(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if STEAM_APP_ID_RE.fullmatch(candidate) else ""


def steam_artwork_url(app_id: Any) -> str:
    """Return large public Steam capsule artwork for a validated AppID."""
    app_id = steam_app_id(app_id)
    return (
        f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/capsule_616x353.jpg"
        if app_id else ""
    )


def steam_hero_url(app_id: Any) -> str:
    """Return the canonical public Steam hero artwork URL for an AppID."""
    app_id = steam_app_id(app_id)
    return (
        f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/library_hero.jpg"
        if app_id else ""
    )


def steam_hero_urls(app_id: Any) -> tuple[str, ...]:
    """Return fixed Steam hero endpoints, ordered from preferred to fallback."""
    app_id = steam_app_id(app_id)
    if not app_id:
        return ()
    return (
        steam_hero_url(app_id),
        f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/library_hero.jpg",
        f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/page_bg_raw.jpg",
        f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/page_bg_generated_v6.jpg",
    )


def steam_banner_url(app_id: Any) -> str:
    """Backward-compatible alias for callers that still use banner terminology."""
    return steam_artwork_url(app_id)


def valid_public_artwork_url(value: Any, app_id: Any = None) -> bool:
    """Accept only fixed Steam CDN artwork or hero routes for the same AppID."""
    if not isinstance(value, str) or not (STEAM_ARTWORK_RE.fullmatch(value) or STEAM_HERO_RE.fullmatch(value)):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.hostname not in STEAM_ARTWORK_HOSTS or parsed.query or parsed.fragment:
        return False
    if app_id is not None:
        expected = steam_app_id(app_id)
        if not expected or f"/{expected}/" not in parsed.path:
            return False
    return True


def valid_public_banner_url(value: Any, app_id: Any = None) -> bool:
    """Backward-compatible validator for fixed Steam artwork routes."""
    return valid_public_artwork_url(value, app_id)


def _clean_name(value: Any, fallback: str = "Player") -> str:
    value = " ".join(str(value or "").strip().split())[:MAX_NAME_LENGTH]
    return value or fallback


def _clean_bio(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    return " ".join(value.strip().split())[:MAX_BIO_LENGTH]


def normalize_background(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = BACKGROUND_PRESETS.get(value, DEFAULT_BACKGROUND)
    if not isinstance(value, dict):
        return dict(DEFAULT_BACKGROUND)
    kind = str(value.get("kind", "gradient"))
    if kind == "steam_hero":
        app_id = steam_app_id(value.get("app_id"))
        if app_id:
            return {"kind": "steam_hero", "app_id": app_id, "url": steam_hero_url(app_id)}
        return dict(DEFAULT_BACKGROUND)
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
    legacy_avatar_id = normalize_avatar_id(value.get("avatar_id"))
    avatar_asset_id = normalize_avatar_asset_id(value.get("avatar_asset_id"))
    if avatar_asset_id is None and legacy_avatar_id:
        avatar_asset_id = normalize_avatar_asset_id(legacy_avatar_id)
    avatar_id = str(avatar_asset_id) if avatar_asset_id is not None else legacy_avatar_id
    panel_theme_id = normalize_panel_theme_id(value.get("panel_theme_id", value.get("profile_theme")))
    try:
        changed_at = max(0.0, float(value.get("changed_at", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        changed_at = 0.0
    return {
        "display_name": _clean_name(value.get("display_name"), fallback_name),
        "bio": _clean_bio(value.get("bio")),
        "avatar_id": avatar_id,
        "avatar_asset_id": avatar_asset_id,
        "panel_theme_id": panel_theme_id,
        "profile_theme": panel_theme_key(panel_theme_id),
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
    # The old key contained user-supplied base64 image data.  It is deliberately
    # removed on read; custom avatars are no longer part of the profile model.
    legacy_avatar = settings.value("profile_avatar", "", type=str)
    if legacy_avatar:
        settings.remove("profile_avatar")
        settings.sync()
    return normalize_profile_settings({
        "display_name": settings.value("profile_display_name", fallback_name, type=str),
        "bio": settings.value("profile_bio", "", type=str),
        "avatar_id": settings.value("profile_avatar_id", "", type=str),
        # Do not request type=int here: older QSettings backends represent a
        # missing value as an invalid QVariant that PyQt cannot convert.
        "avatar_asset_id": settings.value("profile_avatar_asset_id", ""),
        "panel_theme_id": settings.value(
            "profile_panel_theme_id",
            settings.value("profile_theme", "grey", type=str),
        ),
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
    settings.setValue("profile_bio", normalized["bio"])
    settings.setValue("profile_avatar_id", normalized["avatar_id"])
    settings.setValue("profile_avatar_asset_id", normalized["avatar_asset_id"] or "")
    settings.setValue("profile_panel_theme_id", normalized["panel_theme_id"])
    # Retain the readable key for older desktop builds. The public wire
    # format uses the numeric panel_theme_id.
    settings.setValue("profile_theme", normalized["profile_theme"])
    settings.remove("profile_avatar")
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
        return left if left["display_name"] != "Player" or left["avatar_id"] else right
    return left


def build_public_projection(db, settings: dict[str, Any], *, now: int | None = None) -> dict[str, Any]:
    """Build the only payload allowed to leave the desktop for public viewing."""
    profile = normalize_profile_settings(settings)
    # Read the game table once. This function runs during profile navigation
    # and before publishing; repeated full-table reads made the UI scale
    # poorly as the local library grew.
    all_games = db.get_all_games()
    game_names = {
        db.profile_identity(game.name, game.steam_id): str(game.name)
        for game in all_games
    }
    current_local_identities = {
        db.profile_identity(game.name, "") for game in all_games
        if str(game.steam_id or "").strip()
    }
    profile_games = {str(x.get("identity_key")): x for x in db.get_profile_games() if isinstance(x, dict)}
    games_by_app: dict[str, dict[str, Any]] = {}
    total_playtime = 0
    for game in all_games:
        # The live game row includes both the baseline and finalized sessions;
        # use it for installed games so totals neither omit sessions nor count
        # the baseline twice.
        total_playtime += max(0, int(game.playtime_seconds or 0))
        app_id = steam_app_id(game.steam_id)
        if app_id:
            candidate = {
                "name": str(game.name or f"Steam App {app_id}")[:120],
                "app_id": app_id,
                "artwork_url": steam_hero_url(app_id),
                "playtime_seconds": max(0, int(game.playtime_seconds or 0)),
                "last_played": max(0, int(game.last_played or 0)),
                "favorite": bool(game.is_favorite),
            }
            existing = games_by_app.get(app_id)
            if existing is None:
                games_by_app[app_id] = candidate
            else:
                existing["favorite"] = bool(existing["favorite"] or candidate["favorite"])
                existing["playtime_seconds"] = max(existing["playtime_seconds"], candidate["playtime_seconds"])
                existing["last_played"] = max(existing["last_played"], candidate["last_played"])
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
        app_id = steam_app_id(value.get("app_id"))
        if app_id and app_id not in games_by_app:
            games_by_app[app_id] = {
                "name": f"Steam App {app_id}",
                "app_id": app_id,
                "artwork_url": steam_hero_url(app_id),
                "playtime_seconds": max(0, int(value.get("playtime_baseline_seconds", 0) or 0)),
                "last_played": max(0, int(value.get("last_played", 0) or 0)),
                "favorite": bool(value.get("favorite")),
            }
        elif app_id and app_id in games_by_app:
            games_by_app[app_id]["favorite"] = bool(games_by_app[app_id]["favorite"] or value.get("favorite"))
            games_by_app[app_id]["last_played"] = max(
                games_by_app[app_id]["last_played"], max(0, int(value.get("last_played", 0) or 0))
            )

    records = db.get_profile_unlock_records(include_pending=False)
    recent = []
    known_by_app: dict[str, set[str]] = {}
    display_names = {}
    app_game_names = {}
    achievement_rows_by_game = db.get_profile_achievement_rows()
    for game in all_games:
        app_id = steam_app_id(game.steam_id)
        if not app_id:
            continue
        rows = achievement_rows_by_game.get(game.id, [])
        app_game_names.setdefault(app_id, game.name)
        for row in rows:
            api_name = str(row.get("api_name", ""))
            if api_name:
                known_by_app.setdefault(app_id, set()).add(api_name)
                display_names[(app_id, api_name)] = str(row.get("display_name") or api_name or "Achievement")
    for app_id, values in records.items():
        app_id = steam_app_id(app_id)
        if not app_id:
            continue
        games_by_app.setdefault(app_id, {
            "name": str(app_game_names.get(app_id, f"Steam App {app_id}"))[:120],
            "app_id": app_id,
            "artwork_url": steam_hero_url(app_id),
            "playtime_seconds": 0,
            "last_played": 0,
            "favorite": False,
        })
        for api_name, value in values.items():
            if not isinstance(value, dict):
                continue
            recent.append({
                "app_id": app_id,
                "api_name": str(api_name)[:128],
                "name": display_names.get((str(app_id), str(api_name)), str(api_name))[:120],
                "game": str(app_game_names.get(str(app_id), f"Steam App {app_id}"))[:120],
                "unlocked_at": max(0, int(float(value.get("unlock_time", 0) or 0))),
            })
    recent.sort(key=lambda item: (-item["unlocked_at"], item["game"].casefold(), item["name"].casefold()))

    games = sorted(
        games_by_app.values(),
        key=lambda item: (
            not bool(item.get("favorite")),
            -int(item.get("playtime_seconds", 0) or 0),
            -int(item.get("last_played", 0) or 0),
            str(item.get("name", "")).casefold(),
        ),
    )[:MAX_PUBLIC_GAMES]
    for game in games:
        app_id = game["app_id"]
        game_unlocks = [item for item in recent if item["app_id"] == app_id][:MAX_PUBLIC_GAME_ACHIEVEMENTS]
        unlocked_count = sum(1 for item in records.get(app_id, {}).values() if isinstance(item, dict))
        known_count = len(known_by_app.get(app_id, set()))
        total_count = max(known_count, unlocked_count)
        game["achievements"] = {
            "unlocked_count": unlocked_count,
            "total_count": total_count,
            "percentage": round((unlocked_count / total_count) * 100.0, 1) if total_count else 0.0,
            "recent": game_unlocks,
        }
    favorites = [
        {"name": item["name"], "app_id": item["app_id"]}
        for item in games if item.get("favorite")
    ]
    unlocked = sum(int(item["achievements"]["unlocked_count"]) for item in games)
    known = sum(int(item["achievements"]["total_count"]) for item in games)

    return {
        "schema_version": PUBLIC_PROFILE_VERSION,
        "handle": profile["public_handle"],
        "display_name": profile["display_name"],
        "bio": profile["bio"],
        "avatar_asset_id": profile["avatar_asset_id"],
        # Keep the legacy slug only while a local profile has not yet been
        # mapped to the central numeric catalog. The compatibility deployment
        # can migrate this field server-side without losing the selection.
        "avatar_id": profile["avatar_id"] if profile["avatar_asset_id"] is None else None,
        "panel_theme_id": profile["panel_theme_id"],
        "background_preset_id": normalize_background_preset_id(None, profile["background"]),
        "background": profile["background"],
        "stats": {
            "games_count": len(games),
            "favorite_count": len(favorites),
            "playtime_seconds": total_playtime,
            "achievements_unlocked": unlocked,
            "achievements_known": max(known, unlocked),
        },
        "games": games,
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
    avatar_asset_id = normalize_avatar_asset_id(value.get("avatar_asset_id"))
    avatar_id = normalize_avatar_id(value.get("avatar_id"))
    if avatar_asset_id is None:
        avatar_asset_id = normalize_avatar_asset_id(avatar_id)
    if avatar_asset_id is not None:
        avatar_id = str(avatar_asset_id)
    panel_theme_id = normalize_panel_theme_id(value.get("panel_theme_id", value.get("profile_theme")))
    background = normalize_background(value.get("background"))
    background_preset_id = normalize_background_preset_id(value.get("background_preset_id"), background)
    raw_stats = value.get("stats") if isinstance(value.get("stats"), dict) else {}
    stats = {}
    for key in ("games_count", "favorite_count", "playtime_seconds", "achievements_unlocked", "achievements_known"):
        try:
            stats[key] = max(0, min(10_000_000, int(raw_stats.get(key, 0) or 0)))
        except (TypeError, ValueError, OverflowError):
            stats[key] = 0
    stats["achievements_known"] = max(stats["achievements_known"], stats["achievements_unlocked"])

    def bounded_int(raw: Any, maximum: int) -> int:
        try:
            return max(0, min(maximum, int(raw or 0)))
        except (TypeError, ValueError, OverflowError):
            return 0

    def normalize_game_achievements(raw: Any, app_id: str) -> dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {}
        unlocked_count = bounded_int(raw.get("unlocked_count"), 10_000_000)
        total_count = max(unlocked_count, bounded_int(raw.get("total_count"), 10_000_000))
        recent = []
        raw_recent = raw.get("recent", []) if isinstance(raw.get("recent"), list) else []
        for item in raw_recent[:MAX_PUBLIC_GAME_ACHIEVEMENTS]:
            if not isinstance(item, dict):
                continue
            api_name = str(item.get("api_name", "") or "")[:128]
            if not api_name:
                continue
            achievement_app_id = steam_app_id(item.get("app_id"))
            if achievement_app_id and achievement_app_id != app_id:
                # A public document must not attach one game's achievement
                # records to another game's detail page.
                continue
            recent.append({
                "app_id": achievement_app_id or app_id,
                "api_name": api_name,
                "name": _clean_name(item.get("name"), "Achievement")[:120],
                "game": _clean_name(item.get("game"), "Game")[:120],
                "unlocked_at": bounded_int(item.get("unlocked_at"), 4_000_000_000),
            })
        return {
            "unlocked_count": unlocked_count,
            "total_count": total_count,
            "percentage": round((unlocked_count / total_count) * 100.0, 1) if total_count else 0.0,
            "recent": recent,
        }

    raw_games = value.get("games", []) if isinstance(value.get("games"), list) else []
    games = []
    seen_apps = set()
    for item in raw_games[:MAX_PUBLIC_GAMES]:
        if not isinstance(item, dict):
            continue
        app_id = steam_app_id(item.get("app_id"))
        if not app_id or app_id in seen_apps:
            continue
        seen_apps.add(app_id)
        # Older documents may contain a capsule or header URL. Ignore all
        # client-provided artwork and derive the same widescreen hero route
        # from the validated AppID for every public card.
        artwork = steam_hero_url(app_id)
        games.append({
            "name": _clean_name(item.get("name"), f"Steam App {app_id}")[:120],
            "app_id": app_id,
            "artwork_url": artwork,
            "playtime_seconds": bounded_int(item.get("playtime_seconds"), 3_200_000_000),
            "last_played": bounded_int(item.get("last_played"), 4_000_000_000),
            "favorite": bool(item.get("favorite")),
            "achievements": normalize_game_achievements(item.get("achievements"), app_id),
        })

    raw_favorites = value.get("favorite_games", [])
    raw_favorites = raw_favorites if isinstance(raw_favorites, list) else []
    favorites = []
    for item in raw_favorites[:MAX_PUBLIC_GAMES]:
        if isinstance(item, dict):
            favorites.append({"name": _clean_name(item.get("name"), "Favorite game")[:120], "app_id": steam_app_id(item.get("app_id"))})
    if not games:
        # Profiles published by older clients have no library field. Preserve
        # their public favorites as minimal library cards until they publish
        # again from a newer client.
        for item in favorites:
            app_id = item.get("app_id", "")
            if not app_id or app_id in seen_apps:
                continue
            seen_apps.add(app_id)
            games.append({
                "name": item["name"],
                "app_id": app_id,
                "artwork_url": steam_hero_url(app_id),
                "playtime_seconds": 0,
                "last_played": 0,
                "favorite": True,
                "achievements": {
                    "unlocked_count": 0,
                    "total_count": 0,
                    "percentage": 0.0,
                    "recent": [],
                },
            })
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
                "app_id": steam_app_id(item.get("app_id")),
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
        "bio": _clean_bio(value.get("bio")),
        "avatar_asset_id": avatar_asset_id,
        "avatar_id": avatar_id or None,
        "panel_theme_id": panel_theme_id,
        "profile_theme": panel_theme_key(panel_theme_id),
        "background_preset_id": background_preset_id,
        "background": background,
        "stats": stats,
        "games": games,
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
