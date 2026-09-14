"""Safe, metadata-only diagnostics export for request/resource health.

This report is intentionally different from launch diagnostics. Launch reports
may contain game-specific paths and process output; runtime diagnostics are
safe to attach to a support request and contain only version, platform,
network-policy, and numeric performance data.
"""

from __future__ import annotations

import json
import math
import os
import platform
import tempfile
import time
from pathlib import Path
from typing import Mapping

from core.version import APP_VERSION


_METRIC_KEYS = frozenset({
    "submitted",
    "deduplicated",
    "completed",
    "errors",
    "retries",
    "cancelled",
    "offline",
    "invalidated",
    "cache_hits",
    "cache_stale",
    "cache_misses",
    "cache_memory_hits",
    "cache_disk_hits",
    "cache_hit_rate",
    "active_peak",
    "active_current",
    "workers_peak",
    "workers_current",
    "workers_configured",
    "duration_seconds_total",
    "duration_seconds_max",
})


def _safe_metric_mapping(values: Mapping[str, object] | None) -> dict[str, int | float | None]:
    """Keep only known numeric metrics; never serialize arbitrary values."""
    if not isinstance(values, Mapping):
        return {}
    safe: dict[str, int | float | None] = {}
    for key in _METRIC_KEYS:
        value = values.get(key)
        if value is None or isinstance(value, bool):
            if value is not None:
                safe[key] = int(value)
            continue
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            safe[key] = value
    return dict(sorted(safe.items()))


def build_runtime_diagnostics(
    *,
    app_version: str = APP_VERSION,
    request_metrics: Mapping[str, object] | None = None,
    performance_metrics: Mapping[str, object] | None = None,
    offline: bool | None = None,
    generated_at: float | None = None,
) -> dict:
    """Build a support-safe runtime report without paths, secrets, or values."""
    timestamp = time.time() if generated_at is None else float(generated_at)
    performance = _safe_metric_mapping(performance_metrics)
    request_values = dict(request_metrics) if isinstance(request_metrics, Mapping) else {}
    # MainWindow performance snapshots prefix manager values with
    # ``requests_``. Merge that shape as a fallback without allowing it to
    # overwrite a direct manager reading taken at the same moment.
    for key, value in (performance_metrics or {}).items():
        key = str(key)
        if key.startswith("requests_"):
            request_values.setdefault(key.removeprefix("requests_"), value)
    requests = _safe_metric_mapping(request_values)
    return {
        "schema_version": 1,
        "generated_at_unix": timestamp,
        "app_version": str(app_version or APP_VERSION),
        "platform": {
            "system": platform.system() or "unknown",
            "release": platform.release() or "unknown",
            "architecture": platform.machine() or "unknown",
            "python": platform.python_version(),
        },
        "network": {"offline": None if offline is None else bool(offline)},
        "performance": performance,
        "requests": requests,
    }


def export_runtime_diagnostics(path: str | os.PathLike, report: Mapping[str, object]) -> str:
    """Atomically write a support-safe report with user-only permissions."""
    target = Path(path)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(dict(report), stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    return str(target)


__all__ = ["build_runtime_diagnostics", "export_runtime_diagnostics"]
