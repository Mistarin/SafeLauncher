"""Unit tests for the local achievement presentation projection."""

from __future__ import annotations

import unittest

from core.achievement_state_store import AchievementStateStore


class AchievementStateStoreTests(unittest.TestCase):
    def test_status_and_resolution_are_separate_and_timestamped(self):
        store = AchievementStateStore()
        store.set_status(7, (1, 2, 50.0, []))
        store.set_resolution(7, "resolution")

        self.assertEqual(store.get_status(7)[0], 1)
        self.assertEqual(store.resolution(7), "resolution")
        self.assertFalse(store.is_stale(7, 60))

    def test_pending_unlocks_and_clear_game_are_owned_by_store(self):
        store = AchievementStateStore()
        store.pending_for(8)["ACH"] = 123
        self.assertEqual(store.pop_pending(8), {"ACH": 123})
        store.set_status(8, (0, 1, 0.0, []))
        store.set_resolution(8, "resolution")
        store.pending_for(8)["OTHER"] = 456

        store.clear_game(8)

        self.assertIsNone(store.get_status(8))
        self.assertIsNone(store.resolution(8))
        self.assertEqual(store.pop_pending(8), {})


if __name__ == "__main__":
    unittest.main()
