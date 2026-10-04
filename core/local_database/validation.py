import math
import re
import threading
import time
from typing import Any, Optional

_ACHIEVEMENT_DB_LOCK = threading.RLock()
_ACHIEVEMENT_API_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ACHIEVEMENT_PROVENANCES = {
    "steam_verified", "local_emulator", "cloud_profile", "cache", "unknown",
}
_ACHIEVEMENT_APP_RE = re.compile(r"^[0-9]{1,16}$")


def _valid_achievement_api_name(value: Any) -> str:
    name = str(value or "").strip()
    return name if _ACHIEVEMENT_API_RE.fullmatch(name) else ""


def _normalise_achievement_provenance(value: Any) -> str:
    candidate = str(value or "unknown").strip()
    return candidate if candidate in _ACHIEVEMENT_PROVENANCES else "unknown"


def _safe_achievement_timestamp(value: Any, fallback: Optional[float] = None) -> float:
    """Return a finite, non-negative achievement timestamp."""
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        timestamp = 0.0
    if not math.isfinite(timestamp) or timestamp <= 0:
        return float(fallback if fallback is not None else time.time())
    return timestamp
