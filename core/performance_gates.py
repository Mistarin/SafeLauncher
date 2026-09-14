"""Deterministic release-gate evaluation for performance snapshots."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class PerformanceGateThresholds:
    """Explicit thresholds supplied by CI or a measured library baseline."""

    max_time_to_first_library_render_seconds: float | None = None
    max_time_to_first_visible_artwork_seconds: float | None = None
    max_error_rate: float | None = None
    max_duplicate_request_ratio: float | None = None
    max_workers_peak: int | None = None
    require_library_render: bool = True
    require_visible_artwork: bool = False


@dataclass(frozen=True, slots=True)
class PerformanceGateResult:
    passed: bool
    failures: tuple[str, ...]
    observed: dict[str, object]
    thresholds: PerformanceGateThresholds

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "failures": list(self.failures),
            "observed": dict(self.observed),
            "thresholds": asdict(self.thresholds),
        }


def _value(snapshot: Mapping[str, object], key: str):
    if key in snapshot:
        return snapshot[key]
    return snapshot.get(f"requests_{key}")


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def evaluate_performance_gates(
    snapshot: Mapping[str, object],
    thresholds: PerformanceGateThresholds,
) -> PerformanceGateResult:
    """Evaluate explicit thresholds without mutating or collecting state."""
    failures: list[str] = []
    observed: dict[str, object] = {}

    render = _number(snapshot.get("time_to_first_library_render_seconds"))
    artwork = _number(snapshot.get("time_to_first_visible_artwork_seconds"))
    submitted = _number(_value(snapshot, "submitted")) or 0.0
    errors = _number(_value(snapshot, "errors")) or 0.0
    duplicates = _number(_value(snapshot, "deduplicated")) or 0.0
    workers_peak = _number(_value(snapshot, "workers_peak"))

    observed["time_to_first_library_render_seconds"] = render
    observed["time_to_first_visible_artwork_seconds"] = artwork
    observed["error_rate"] = errors / submitted if submitted else 0.0
    observed["duplicate_request_ratio"] = duplicates / submitted if submitted else 0.0
    observed["workers_peak"] = workers_peak

    if thresholds.require_library_render and render is None:
        failures.append("library render milestone missing")
    if thresholds.require_visible_artwork and artwork is None:
        failures.append("visible artwork milestone missing")
    if (
        thresholds.max_time_to_first_library_render_seconds is not None
        and render is not None
        and render > thresholds.max_time_to_first_library_render_seconds
    ):
        failures.append("time to first library render exceeded threshold")
    if (
        thresholds.max_time_to_first_visible_artwork_seconds is not None
        and artwork is not None
        and artwork > thresholds.max_time_to_first_visible_artwork_seconds
    ):
        failures.append("time to first visible artwork exceeded threshold")
    if thresholds.max_error_rate is not None and observed["error_rate"] > thresholds.max_error_rate:
        failures.append("request error rate exceeded threshold")
    if (
        thresholds.max_duplicate_request_ratio is not None
        and observed["duplicate_request_ratio"] > thresholds.max_duplicate_request_ratio
    ):
        failures.append("duplicate request ratio exceeded threshold")
    if (
        thresholds.max_workers_peak is not None
        and (workers_peak is None or workers_peak > thresholds.max_workers_peak)
    ):
        failures.append("worker peak missing or exceeded threshold")

    return PerformanceGateResult(
        passed=not failures,
        failures=tuple(failures),
        observed=observed,
        thresholds=thresholds,
    )


__all__ = [
    "PerformanceGateResult",
    "PerformanceGateThresholds",
    "evaluate_performance_gates",
]
