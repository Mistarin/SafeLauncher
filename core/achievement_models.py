"""Shared achievement provenance and validation vocabulary.

Achievement data can come from a trusted Steam account endpoint, a local
emulator, a cache, or a cloud profile.  Keeping that distinction explicit is
important: local emulator files are useful and supported, but they are not
cryptographic proof of a Steam unlock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Dict, Optional


class AchievementProvenance(str, Enum):
    STEAM = "steam_verified"
    LOCAL_EMULATOR = "local_emulator"
    CLOUD_PROFILE = "cloud_profile"
    CACHE = "cache"
    UNKNOWN = "unknown"

    @property
    def verified(self) -> bool:
        return self is AchievementProvenance.STEAM


class AchievementValidation(str, Enum):
    VALIDATED = "validated"
    PENDING_SCHEMA = "pending_schema"
    INVALID = "invalid"


@dataclass(frozen=True)
class AchievementObservation:
    """One observed unlock, before/after schema validation."""

    api_name: str
    unlock_time: float
    provenance: AchievementProvenance = AchievementProvenance.UNKNOWN
    verified: bool = False
    validation: AchievementValidation = AchievementValidation.PENDING_SCHEMA
    source_format: str = ""
    source_path: str = ""


@dataclass
class AchievementStateParseResult:
    """Bounded parser output, including records not yet schema validated."""

    state: Dict[str, float] = field(default_factory=dict)
    unknown: Dict[str, float] = field(default_factory=dict)
    format: str = ""
    valid: bool = False
    reason: str = ""


def provenance_priority(value: str) -> int:
    """Return the merge priority for an achievement source."""

    try:
        provenance = AchievementProvenance(str(value))
    except ValueError:
        provenance = AchievementProvenance.UNKNOWN
    return {
        AchievementProvenance.STEAM: 50,
        AchievementProvenance.CLOUD_PROFILE: 40,
        AchievementProvenance.LOCAL_EMULATOR: 30,
        AchievementProvenance.CACHE: 20,
        AchievementProvenance.UNKNOWN: 0,
    }[provenance]


def merge_observations(left: Optional[dict], right: Optional[dict]) -> dict:
    """Union two profile records without allowing weaker data to overwrite proof."""

    left = left if isinstance(left, dict) else {}
    right = right if isinstance(right, dict) else {}
    valid_sources = {item.value for item in AchievementProvenance}

    def _safe_time(value) -> float:
        try:
            value = float(value or 0)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return value if math.isfinite(value) and value >= 0 else 0.0

    def _safe_source(value) -> str:
        candidate = str(value or AchievementProvenance.UNKNOWN.value)
        return candidate if candidate in valid_sources else AchievementProvenance.UNKNOWN.value

    left_time = _safe_time(left.get("unlock_time", 0))
    right_time = _safe_time(right.get("unlock_time", 0))
    left_source = _safe_source(left.get("provenance", AchievementProvenance.UNKNOWN.value))
    right_source = _safe_source(right.get("provenance", AchievementProvenance.UNKNOWN.value))
    chosen_source = right_source if provenance_priority(right_source) >= provenance_priority(left_source) else left_source
    validation = (
        AchievementValidation.VALIDATED.value
        if left.get("validation_state") == AchievementValidation.VALIDATED.value
        or right.get("validation_state") == AchievementValidation.VALIDATED.value
        else AchievementValidation.PENDING_SCHEMA.value
    )
    return {
        "unlock_time": min(x for x in (left_time, right_time) if x > 0) if left_time > 0 and right_time > 0 else max(left_time, right_time),
        "provenance": chosen_source,
        # Verification is a property of the source, never a caller-provided
        # boolean that can be attached to an untrusted/local record.
        "verified": chosen_source == AchievementProvenance.STEAM.value,
        "validation_state": validation,
        "source_format": str((right if chosen_source == right_source else left).get("source_format", "") or ""),
    }
