"""Tests for bounded UI-only decoded-value caching."""

from __future__ import annotations

import unittest

from core.presentation_cache import PresentationCache


class PresentationCacheTests(unittest.TestCase):
    def test_cache_is_bounded_and_least_recently_used(self):
        cache = PresentationCache(2)
        cache["a"] = b"a"
        cache["b"] = b"b"
        self.assertEqual(cache.get("a"), b"a")
        cache["c"] = b"c"
        self.assertNotIn("b", cache)
        self.assertIn("a", cache)
        self.assertIn("c", cache)


if __name__ == "__main__":
    unittest.main()
