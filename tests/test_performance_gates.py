"""Release-gate evaluation tests."""

from __future__ import annotations

import unittest

from core.performance_gates import PerformanceGateThresholds, evaluate_performance_gates


class PerformanceGateTests(unittest.TestCase):
    def test_measured_snapshot_passes_explicit_thresholds(self):
        result = evaluate_performance_gates(
            {
                "time_to_first_library_render_seconds": 1.2,
                "time_to_first_visible_artwork_seconds": 2.4,
                "requests_submitted": 100,
                "requests_errors": 2,
                "requests_deduplicated": 8,
                "requests_workers_peak": 3,
            },
            PerformanceGateThresholds(
                max_time_to_first_library_render_seconds=2,
                max_time_to_first_visible_artwork_seconds=3,
                max_error_rate=0.05,
                max_duplicate_request_ratio=0.10,
                max_workers_peak=3,
                require_visible_artwork=True,
            ),
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.observed["error_rate"], 0.02)
        self.assertEqual(result.observed["duplicate_request_ratio"], 0.08)

    def test_gate_report_lists_missing_and_exceeded_conditions(self):
        result = evaluate_performance_gates(
            {
                "time_to_first_library_render_seconds": 5,
                "requests_submitted": 10,
                "requests_errors": 3,
                "requests_deduplicated": 5,
                "requests_workers_peak": 6,
            },
            PerformanceGateThresholds(
                max_time_to_first_library_render_seconds=2,
                max_time_to_first_visible_artwork_seconds=3,
                max_error_rate=0.1,
                max_duplicate_request_ratio=0.2,
                max_workers_peak=4,
                require_visible_artwork=True,
            ),
        )
        self.assertFalse(result.passed)
        self.assertIn("visible artwork milestone missing", result.failures)
        self.assertIn("time to first library render exceeded threshold", result.failures)
        self.assertIn("request error rate exceeded threshold", result.failures)
        self.assertIn("duplicate request ratio exceeded threshold", result.failures)
        self.assertIn("worker peak missing or exceeded threshold", result.failures)
        self.assertEqual(result.as_dict()["passed"], False)


if __name__ == "__main__":
    unittest.main()
