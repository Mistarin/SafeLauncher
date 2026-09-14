"""Tests for the Qt-free Phase 8 performance observations."""

from __future__ import annotations

import unittest

from core.performance_metrics import ResourcePerformanceTracker


class ResourcePerformanceTrackerTests(unittest.TestCase):
    def test_milestones_and_request_metrics_are_reported(self):
        now = [100.0]
        tracker = ResourcePerformanceTracker(lambda: now[0])
        tracker.mark_library_refresh()
        tracker.mark_library_render()
        tracker.mark_visible_artwork()
        tracker.mark_visible_artwork()
        now[0] = 101.25

        snapshot = tracker.snapshot({
            "submitted": 4,
            "deduplicated": 2,
            "cache_hit_rate": 0.5,
            "active_peak": 3,
        })
        self.assertEqual(snapshot["time_to_first_library_render_seconds"], 0.0)
        self.assertEqual(snapshot["time_to_first_visible_artwork_seconds"], 0.0)
        self.assertEqual(snapshot["library_refresh_count"], 1)
        self.assertEqual(snapshot["visible_artwork_updates"], 2)
        self.assertEqual(snapshot["measurement_window_seconds"], 1.25)
        self.assertEqual(snapshot["library_refreshes_per_second"], 0.8)
        self.assertEqual(snapshot["visible_artwork_updates_per_second"], 1.6)
        self.assertEqual(snapshot["requests_deduplicated"], 2)

    def test_unavailable_milestones_remain_explicit(self):
        tracker = ResourcePerformanceTracker(lambda: 10.0)
        tracker.mark_library_render(usable=False)
        snapshot = tracker.snapshot()
        self.assertIsNone(snapshot["time_to_first_library_render_seconds"])
        self.assertIsNone(snapshot["time_to_first_visible_artwork_seconds"])


if __name__ == "__main__":
    unittest.main()
