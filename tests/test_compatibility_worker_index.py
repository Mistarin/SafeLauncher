"""Tests for explicit manager-less worker reference indexes."""

from __future__ import annotations

import unittest

from core.compatibility_worker_index import CompatibilityWorkerIndex


class CompatibilityWorkerIndexTests(unittest.TestCase):
    def test_index_tracks_references_without_owning_lifecycle(self):
        first, second = object(), object()
        index = CompatibilityWorkerIndex("artwork-fallback")
        index.append(first)
        index.append(second)
        self.assertEqual(len(index), 2)
        self.assertEqual(index.snapshot(), (first, second))
        self.assertIs(index.pop(0), first)
        index.clear()
        self.assertEqual(len(index), 0)


if __name__ == "__main__":
    unittest.main()
