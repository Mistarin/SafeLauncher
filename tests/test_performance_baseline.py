"""Tests for the deterministic performance baseline runner."""

from __future__ import annotations

import unittest

from ci.performance_baseline import collect_baseline


class PerformanceBaselineTests(unittest.TestCase):
    def test_baseline_contains_library_and_request_measurements(self):
        report = collect_baseline(games=40, requests=12, repetitions=2, workers=2)
        self.assertEqual(report["workload"]["synthetic_games"], 40)
        self.assertEqual(report["workload"]["managed_requests"], 12)
        self.assertLessEqual(report["library_snapshot_ms"]["min"], report["library_snapshot_ms"]["max"])
        self.assertLessEqual(report["managed_batch_ms"]["min"], report["managed_batch_ms"]["max"])
        self.assertEqual(report["request_metrics"]["completed"], 12)
        self.assertLessEqual(report["request_metrics"]["workers_peak"], 2)


if __name__ == "__main__":
    unittest.main()
