"""Achievement provider orchestration.

Providers are deliberately small and ordered.  SafeLauncher owns the common
schema/state model while compatibility profiles capture the formats used by
the established open-source tools:

* Lanzador/Achievements: local Goldberg schema/config files
* Achievement Watcher Fixed: broad emulator state roots and formats
* Playnite Achievement Tracker: CODEX/RUNE/FLT/Goldberg compatibility
* Sentinel: Linux/Wine/Proton local-first and public-schema fallback

The profiles are compatibility knowledge, not runtime dependencies.  This
keeps SafeLauncher maintainable and avoids shipping another UI or daemon.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
from typing import Dict, List, Optional

from core.logger import get_logger

logger = get_logger("AchievementProviders")


class AchievementAvailability(str, Enum):
    AVAILABLE = "available"
    NO_ACHIEVEMENTS = "no_achievements"
    MISSING = "missing"


@dataclass
class AchievementResolution:
    schema: List[dict]
    state: Dict[str, float]
    state_path: Optional[Path]
    schema_source: str
    state_source: str
    availability: AchievementAvailability
    reason: str = ""


class AchievementProvider:
    name = "base"

    def get_schema(self, app_id: str, game_path: str, proton_path: str) -> List[dict]:
        return []


class LanzadorSchemaProvider(AchievementProvider):
    """Offline schemas generated from Goldberg/Steam cache files."""
    name = "lanzador-local-schema"

    def get_schema(self, app_id: str, game_path: str, proton_path: str) -> List[dict]:
        from core.achievement_schema import find_local_achievement_schema
        return find_local_achievement_schema(game_path, proton_path, app_id)


class SteamSchemaProvider(AchievementProvider):
    """Cached/public Steam schema fallback used by Sentinel-like setups."""
    name = "sentinel-steam-schema"

    def get_schema(self, app_id: str, game_path: str, proton_path: str) -> List[dict]:
        from core.achievement_schema import fetch_steam_achievements_schema
        return fetch_steam_achievements_schema(
            app_id, game_path=game_path, proton_path=proton_path,
            download_icons=False,
        )


def _authenticated_steam_player_state(app_id: str) -> Dict[str, float]:
    """Read native Steam unlock state only with explicit user credentials.

    Steam does not expose a local, stable achievement-state file for every
    native title.  The official player endpoint is the reliable fallback, but
    it requires both a Web API key and the player's SteamID.  Environment-only
    configuration keeps this opt-in and avoids storing either credential in
    SafeLauncher settings.
    """
    api_key = os.environ.get("STEAM_WEB_API_KEY", "").strip()
    steam_user_id = os.environ.get("STEAM_USER_ID", "").strip()
    if not api_key or not steam_user_id:
        return {}
    try:
        import requests
        response = requests.get(
            "https://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v0001/",
            params={"key": api_key, "steamid": steam_user_id, "appid": str(app_id)},
            timeout=8,
        )
        if response.status_code != 200:
            return {}
        achievements = (response.json() or {}).get("playerstats", {}).get("achievements", [])
        return {
            str(item.get("apiname", "")).strip(): float(item.get("unlocktime", 0) or 1)
            for item in achievements
            if item.get("achieved") and str(item.get("apiname", "")).strip()
        }
    except Exception as exc:
        logger.debug("Authenticated Steam achievement state unavailable for %s: %s", app_id, exc)
        return {}


class AchievementProviderRegistry:
    """Resolve schema and local state using deterministic provider priority."""

    # Local truth always wins over a network result.  The fallback provider
    # itself uses cache before network, so an offline launch remains cheap.
    schema_providers = (LanzadorSchemaProvider(), SteamSchemaProvider())

    @classmethod
    def resolve(cls, app_id: str, game_path: str = "", proton_path: str = "") -> AchievementResolution:
        app_id = str(app_id or "").strip()
        if not app_id or app_id == "0":
            return AchievementResolution([], {}, None, "", "", AchievementAvailability.MISSING,
                                         "No Steam AppID is configured")

        from core.achievement_watcher import locate_achievements_file, parse_achievements_state

        state_path = locate_achievements_file(proton_path, game_path, app_id)
        state = parse_achievements_state(state_path) if state_path else {}
        state_source = "local-state" if state_path else ""
        remote_state = _authenticated_steam_player_state(app_id)
        if remote_state:
            # Emulator files are local truth when both sources exist; native
            # Steam fills only unknown entries and supports pure native titles.
            merged_state = dict(remote_state)
            merged_state.update(state)
            state = merged_state
            state_source = f"{state_source}+steam-player" if state_source else "steam-player"

        schema: List[dict] = []
        schema_source = ""
        for provider in cls.schema_providers:
            try:
                schema = provider.get_schema(app_id, game_path, proton_path)
            except Exception as exc:
                logger.debug("Achievement provider %s failed for %s: %s", provider.name, app_id, exc)
                continue
            if schema:
                schema_source = provider.name
                break

        if schema:
            return AchievementResolution(
                schema, state, state_path, schema_source, state_source,
                AchievementAvailability.AVAILABLE,
            )

        # An empty result is not proof that the game has no achievements:
        # unsupported emulators, offline providers, and rate limits all look
        # identical at this layer.  Keep the UI honest and say data is missing.
        return AchievementResolution(
            [], state, state_path, "", state_source,
            AchievementAvailability.MISSING,
            "No authoritative achievement schema was available",
        )


def resolve_achievements(app_id: str, game_path: str = "", proton_path: str = "") -> AchievementResolution:
    """Public provider entry point used by workers and future UI surfaces."""
    return AchievementProviderRegistry.resolve(app_id, game_path, proton_path)
