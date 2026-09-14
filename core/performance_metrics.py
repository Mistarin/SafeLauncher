"""Small Qt-free performance tracker for library/resource loading."""

from __future__ import annotations

import threading
import time
from typing import Callable


class ResourcePerformanceTracker:
    """Record user-visible milestones and combine them with request metrics.

    The tracker intentionally records observations only. It does not schedule
    work or retain resource values, which keeps performance reporting separate
    from both the database and the request manager.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started_at = float(clock())
        self._first_library_render: float | None = None
        self._first_visible_artwork: float | None = None
        self._library_refreshes = 0
        self._visible_artwork_updates = 0
        self._lock = threading.Lock()

    def mark_library_refresh(self) -> None:
        with self._lock:
            self._library_refreshes += 1

    def mark_library_render(self, *, usable: bool = True) -> None:
        if not usable:
            return
        with self._lock:
            if self._first_library_render is None:
                self._first_library_render = float(self._clock())

    def mark_visible_artwork(self) -> None:
        with self._lock:
            self._visible_artwork_updates += 1
            if self._first_visible_artwork is None:
                self._first_visible_artwork = float(self._clock())

    def snapshot(self, request_metrics: dict | None = None) -> dict[str, int | float | None]:
        """Return a serializable point-in-time performance snapshot."""
        now = float(self._clock())
        with self._lock:
            first_render = self._first_library_render
            first_artwork = self._first_visible_artwork
            refreshes = self._library_refreshes
            artwork_updates = self._visible_artwork_updates

        result: dict[str, int | float | None] = {
            "time_to_first_library_render_seconds": (
                max(0.0, first_render - self._started_at)
                if first_render is not None else None
            ),
            "time_to_first_visible_artwork_seconds": (
                max(0.0, first_artwork - self._started_at)
                if first_artwork is not None else None
            ),
            "library_refresh_count": refreshes,
            "visible_artwork_updates": artwork_updates,
            "measurement_window_seconds": max(0.0, now - self._started_at),
        }
        window = result["measurement_window_seconds"]
        result["library_refreshes_per_second"] = (
            refreshes / window if window else 0.0
        )
        result["visible_artwork_updates_per_second"] = (
            artwork_updates / window if window else 0.0
        )
        if request_metrics:
            for key in (
                "submitted",
                "deduplicated",
                "errors",
                "retries",
                "cancelled",
                "cache_hit_rate",
                "active_peak",
                "active_current",
                "workers_configured",
                "duration_seconds_total",
                "duration_seconds_max",
            ):
                if key in request_metrics:
                    result[f"requests_{key}"] = request_metrics[key]
        return result


__all__ = ["ResourcePerformanceTracker"]
