"""Process-wide coordination for achievement resolution.

The launch path, achievements dialog, and periodic recheck can all ask for
the same AppID at nearly the same time. A short, path-aware cache plus a
per-target lock prevents duplicate network requests while still letting the
watcher observe fresh local state independently.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

from core.achievement_providers import AchievementResolution, resolve_achievements

_CACHE_TTL_SECONDS = 1.5
_CACHE_LOCK = threading.RLock()
_TARGET_LOCKS = defaultdict(threading.Lock)
_CACHE: dict[tuple[str, str, str], tuple[float, AchievementResolution]] = {}


def _key(app_id: str, game_path: str, proton_path: str) -> tuple[str, str, str]:
    return (str(app_id or "").strip(), str(game_path or ""), str(proton_path or ""))


def coordinated_resolve(app_id: str, game_path: str = "", proton_path: str = "", *, download_icons: bool = False) -> AchievementResolution:
    """Resolve one target once for overlapping callers."""
    key = _key(app_id, game_path, proton_path)
    lock = _TARGET_LOCKS[key]
    with lock:
        now = time.monotonic()
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached and now - cached[0] < _CACHE_TTL_SECONDS and (not download_icons or cached[1].schema):
                return cached[1]
        result = resolve_achievements(app_id, game_path, proton_path, download_icons=download_icons)
        with _CACHE_LOCK:
            _CACHE[key] = (time.monotonic(), result)
            if len(_CACHE) > 256:
                oldest = sorted(_CACHE, key=lambda item: _CACHE[item][0])[:64]
                for old_key in oldest:
                    _CACHE.pop(old_key, None)
        return result


def invalidate(app_id: str = "", game_path: str = "", proton_path: str = "") -> None:
    """Drop a cached snapshot after an explicit user refresh."""
    with _CACHE_LOCK:
        if app_id or game_path or proton_path:
            _CACHE.pop(_key(app_id, game_path, proton_path), None)
        else:
            _CACHE.clear()
