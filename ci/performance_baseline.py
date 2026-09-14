#!/usr/bin/env python3
"""Measure request/library performance without network access or user data.

The ``--games`` and ``--requests`` values should be set to representative
library sizes when establishing a release baseline. The script never reads a
database, contacts a backend, or serializes game records.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.library_controller import LibraryController, LibraryQuery
from core.performance_gates import PerformanceGateThresholds, evaluate_performance_gates
from core.request_manager import RequestManager
from core.request_contracts import RequestKey


def _synthetic_games(count: int) -> list[tuple]:
    return [
        (
            index,
            f"Baseline Game {index}",
            "",
            "",
            "linux",
            "",
            str(1000 + index),
            0,
            1 if index % 3 == 0 else 0,
            0,
            "tag",
            "",
            "",
            "Collection A" if index % 2 == 0 else "Collection B",
            index,
            "",
            "",
            0,
        )
        for index in range(count)
    ]


def _milliseconds(samples: list[float]) -> dict[str, float]:
    return {
        "min": min(samples) * 1000,
        "median": statistics.median(samples) * 1000,
        "max": max(samples) * 1000,
    }


def collect_baseline(*, games: int = 600, requests: int = 100, repetitions: int = 5, workers: int = 3) -> dict:
    """Collect a deterministic local baseline for representative workloads."""
    if min(games, requests, repetitions, workers) < 1:
        raise ValueError("games, requests, repetitions, and workers must be positive")

    records = _synthetic_games(int(games))
    controller = LibraryController()
    queries = (
        LibraryQuery(),
        LibraryQuery(search="game 1"),
        LibraryQuery(filter_mode="favorites"),
        LibraryQuery(filter_mode="archived"),
        LibraryQuery(collection="Collection B"),
    )
    library_samples = []
    visible_counts = []
    for index in range(int(repetitions)):
        started = time.perf_counter()
        snapshot = controller.build_snapshot(records, queries[index % len(queries)])
        library_samples.append(time.perf_counter() - started)
        visible_counts.append(len(snapshot.items))

    request_samples = []
    request_metrics = {}
    for _index in range(int(repetitions)):
        manager = RequestManager(max_workers=int(workers), offline_check=lambda: False)
        try:
            keys = [RequestKey("baseline", str(item)) for item in range(int(requests))]
            started = time.perf_counter()
            handles = manager.request_many(
                keys,
                lambda key, token: (token.raise_if_cancelled(), key.identity)[1],
            )
            for handle in handles:
                handle.future.result(timeout=10)
            request_samples.append(time.perf_counter() - started)
            request_metrics = manager.metrics()
        finally:
            manager.shutdown()

    snapshot = {
        "schema_version": 1,
        "workload": {
            "synthetic_games": int(games),
            "managed_requests": int(requests),
            "repetitions": int(repetitions),
            "workers": int(workers),
        },
        "library_snapshot_ms": _milliseconds(library_samples),
        "library_visible_counts": visible_counts,
        "managed_batch_ms": _milliseconds(request_samples),
        "request_metrics": {
            key: request_metrics.get(key)
            for key in (
                "submitted",
                "completed",
                "errors",
                "deduplicated",
                "workers_peak",
                "workers_configured",
                "duration_seconds_total",
                "duration_seconds_max",
            )
        },
        "gate_snapshot": {
            "time_to_first_library_render_seconds": statistics.median(library_samples),
            "requests_submitted": request_metrics.get("submitted", 0),
            "requests_errors": request_metrics.get("errors", 0),
            "requests_deduplicated": request_metrics.get("deduplicated", 0),
            "requests_workers_peak": request_metrics.get("workers_peak"),
        },
    }
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=600)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-render-ms", type=float)
    parser.add_argument("--max-workers-peak", type=int)
    args = parser.parse_args()

    try:
        report = collect_baseline(
            games=args.games,
            requests=args.requests,
            repetitions=args.repetitions,
            workers=args.workers,
        )
    except ValueError as error:
        parser.error(str(error))

    gate_result = None
    if args.max_render_ms is not None or args.max_workers_peak is not None:
        gate_result = evaluate_performance_gates(
            report["gate_snapshot"],
            PerformanceGateThresholds(
                max_time_to_first_library_render_seconds=(
                    args.max_render_ms / 1000 if args.max_render_ms is not None else None
                ),
                max_workers_peak=args.max_workers_peak,
            ),
        ).as_dict()
        report["gate"] = gate_result

    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if gate_result is not None and not gate_result["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
