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

from dataclasses import dataclass, field
from enum import Enum
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

from core.logger import get_logger
from core.achievement_models import AchievementProvenance
from core.network_policy import automatic_network_allowed

logger = get_logger("AchievementProviders")
_APP_ID_RE = re.compile(r"^[0-9]{1,16}$")


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
    checked_at: float = 0.0
    state_provenance: str = AchievementProvenance.UNKNOWN.value
    state_verified: bool = False
    state_format: str = ""
    state_available: bool = False
    state_ambiguous: bool = False
    candidate_paths: List[str] = field(default_factory=list)
    pending_state: Dict[str, float] = field(default_factory=dict)
    provenance_by_name: Dict[str, str] = field(default_factory=dict)
    verified_by_name: Dict[str, bool] = field(default_factory=dict)


class AchievementProvider:
    name = "base"

    def get_schema(self, app_id: str, game_path: str, proton_path: str, download_icons: bool = False, request_manager=None, schema_cache=None) -> List[dict]:
        return []


class LanzadorSchemaProvider(AchievementProvider):
    """Offline schemas generated from Goldberg/Steam cache files."""
    name = "lanzador-local-schema"

    def get_schema(self, app_id: str, game_path: str, proton_path: str, download_icons: bool = False, request_manager=None, schema_cache=None) -> List[dict]:
        from core.achievement_schema import find_local_achievement_schema
        return find_local_achievement_schema(game_path, proton_path, app_id)


class SteamSchemaProvider(AchievementProvider):
    """Cached/public Steam schema fallback used by Sentinel-like setups."""
    name = "sentinel-steam-schema"

    def get_schema(self, app_id: str, game_path: str, proton_path: str, download_icons: bool = False, request_manager=None, schema_cache=None) -> List[dict]:
        if not automatic_network_allowed():
            return []
        from core.achievement_schema import fetch_steam_achievements_schema
        return fetch_steam_achievements_schema(
            app_id, game_path=game_path, proton_path=proton_path,
            download_icons=download_icons,
            request_manager=request_manager,
            schema_cache=schema_cache,
        )


class AchievementProviderRegistry:
    """Resolve schema and local state using deterministic provider priority."""

    # Unlock state is strictly local: it comes from the game/emulator files
    # discovered by AchievementWatcher. Public schema metadata is a separate
    # concern and can never assert that a player unlocked an achievement.
    schema_providers = (LanzadorSchemaProvider(), SteamSchemaProvider())

    @classmethod
    def resolve(cls, app_id: str, game_path: str = "", proton_path: str = "", download_icons: bool = False, request_manager=None, schema_cache=None) -> AchievementResolution:
        app_id = str(app_id or "").strip()
        if not _APP_ID_RE.fullmatch(app_id) or app_id == "0":
            return AchievementResolution([], {}, None, "", "", AchievementAvailability.MISSING,
                                         "No Steam AppID is configured", time.time())

        from core.achievement_watcher import (
            achievement_state_candidates,
            locate_achievements_file,
            parse_achievements_state_detailed,
            filter_achievement_state,
        )

        candidates = achievement_state_candidates(proton_path, game_path, app_id)
        existing_candidates = [p for p in candidates if p.is_file()]
        state_path = locate_achievements_file(proton_path, game_path, app_id)
        parsed = parse_achievements_state_detailed(state_path) if state_path else None
        state = parsed.state if parsed and parsed.valid else {}
        state_source = "local-state" if state_path and parsed and parsed.valid else ""
        state_format = parsed.format if parsed else ""
        state_available = bool(state_path and parsed and parsed.valid)
        state_ambiguous = len([p for p in existing_candidates if p.is_file()]) > 1
        state_provenance = AchievementProvenance.LOCAL_EMULATOR.value if state_source else AchievementProvenance.UNKNOWN.value
        state_verified = False
        provenance_by_name = {name: AchievementProvenance.LOCAL_EMULATOR.value for name in state}
        verified_by_name = {name: False for name in state}
        schema: List[dict] = []
        schema_source = ""
        for provider in cls.schema_providers:
            try:
                schema = provider.get_schema(
                    app_id, game_path, proton_path,
                    download_icons=download_icons,
                    request_manager=request_manager,
                    schema_cache=schema_cache,
                )
            except Exception as exc:
                logger.debug("Achievement provider %s failed for %s: %s", provider.name, app_id, exc)
                continue
            if schema:
                schema_source = provider.name
                break

        allowed_names = {
            str(item.get("api_name", "")).strip()
            for item in schema
            if isinstance(item, dict) and str(item.get("api_name", "")).strip()
        }
        if schema and existing_candidates:
            # Once the schema is known, use it to disambiguate a generic
            # ``stats.json`` from the real achievement file. A newer file
            # with unrelated counters must not hide an older matching state.
            scored = []
            for candidate in existing_candidates:
                candidate_result = parse_achievements_state_detailed(candidate)
                if not candidate_result.valid:
                    continue
                matches = len(set(candidate_result.state) & allowed_names)
                try:
                    stat = candidate.stat()
                    name_score = int("achieve" in candidate.stem.lower())
                    scored.append((matches, int(bool(candidate_result.state)), name_score, stat.st_mtime_ns, candidate_result, candidate))
                except OSError:
                    continue
            if scored:
                selected = max(scored, key=lambda item: item[:4])
                matching_states = [item for item in scored if item[0] > 0]
                if matching_states:
                    # Local emulator files are append-only in practice and
                    # may be left behind in more than one prefix/root. Union
                    # schema-matching observations so a stale empty/current
                    # file cannot make previously seen unlocks disappear.
                    combined = {}
                    for item in matching_states:
                        for name, stamp in item[4].state.items():
                            if name in allowed_names:
                                combined[name] = min(combined.get(name, stamp), stamp)
                    state = combined
                    state_path = selected[5]
                    parsed = selected[4]
                    state_format = parsed.format
                    state_source = "local-state"
                    state_provenance = AchievementProvenance.LOCAL_EMULATOR.value
                    state_verified = False
                    provenance_by_name = {name: AchievementProvenance.LOCAL_EMULATOR.value for name in state}
                    verified_by_name = {name: False for name in state}
                    state_available = True
                if selected[5] != state_path:
                    state_path = selected[5]
                    parsed = selected[4]
                    state = parsed.state
                    state_format = parsed.format
                    state_source = "local-state"
                    state_provenance = AchievementProvenance.LOCAL_EMULATOR.value
                    state_verified = False
                    provenance_by_name = {name: AchievementProvenance.LOCAL_EMULATOR.value for name in state}
                    verified_by_name = {name: False for name in state}
                    state_available = True

        state, pending_state = filter_achievement_state(state, allowed_names)
        provenance_by_name = {name: provenance_by_name.get(name, state_provenance) for name in state}
        verified_by_name = {name: verified_by_name.get(name, state_verified) for name in state}

        if schema:
            return AchievementResolution(
                schema, state, state_path, schema_source, state_source,
                AchievementAvailability.AVAILABLE,
                checked_at=time.time(),
                state_provenance=state_provenance,
                state_verified=state_verified,
                state_format=state_format,
                state_available=state_available,
                state_ambiguous=state_ambiguous,
                candidate_paths=[str(p) for p in existing_candidates[:32]],
                pending_state=pending_state,
                provenance_by_name=provenance_by_name,
                verified_by_name=verified_by_name,
            )

        # An empty result is not proof that the game has no achievements:
        # unsupported emulators, offline providers, and rate limits all look
        # identical at this layer.  Keep the UI honest and say data is missing.
        missing_reason = "No authoritative achievement schema was available"
        if parsed and not parsed.valid and parsed.reason:
            missing_reason += f"; local state unavailable: {parsed.reason}"
        return AchievementResolution(
            [], state, state_path, "", state_source,
            AchievementAvailability.MISSING,
            missing_reason,
            time.time(),
            state_provenance=state_provenance,
            state_verified=state_verified,
            state_format=state_format,
            state_available=state_available,
            state_ambiguous=state_ambiguous,
            candidate_paths=[str(p) for p in existing_candidates[:32]],
            pending_state=state,
            provenance_by_name={name: provenance_by_name.get(name, state_provenance) for name in state},
            verified_by_name={name: verified_by_name.get(name, state_verified) for name in state},
        )


def resolve_achievements(app_id: str, game_path: str = "", proton_path: str = "", download_icons: bool = False, request_manager=None, schema_cache=None) -> AchievementResolution:
    """Public provider entry point used by workers and future UI surfaces."""
    return AchievementProviderRegistry.resolve(
        app_id, game_path, proton_path,
        download_icons=download_icons,
        request_manager=request_manager,
        schema_cache=schema_cache,
    )
