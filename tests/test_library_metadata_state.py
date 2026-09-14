"""Tests for the isolated library metadata projection state."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.achievement_state_store import AchievementStateStore
from core.library_metadata_state import LibraryMetadataState


class LibraryMetadataStateTests(unittest.TestCase):
    def test_legacy_cache_loads_only_local_projection_data(self):
        state = LibraryMetadataState()
        achievements = AchievementStateStore()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata_cache.json"
            path.write_text(json.dumps({
                "cloud_save_status": {"4": {"status": "cloud_only"}},
                "attempted_tags": [4, "9", "invalid"],
                "steam_builds": {
                    "4": {
                        "latest_build_id": "build-1",
                        "latest_build_date": 12,
                        "is_update": True,
                        "checked_at": 20,
                    }
                },
                "achievements": {
                    "4": {
                        "unlocked_count": 2,
                        "total_count": 5,
                        "pct": 40.0,
                        "recent": [],
                        "checked_at": 30,
                    }
                },
            }), encoding="utf-8")
            self.assertTrue(state.load_legacy_cache(path, achievements))

        self.assertEqual(state.attempted_tags, {4, 9})
        self.assertEqual(state.steam_check_results[4][:3], ("build-1", 12, True))
        self.assertEqual(state.steam_build_checked_at[4], 20.0)
        self.assertEqual(achievements.status[4][:3], (2, 5, 40.0))

    def test_save_is_atomic_and_does_not_include_cloud_payload(self):
        state = LibraryMetadataState()
        state.attempted_tags.add(4)
        state.steam_check_results[4] = ("build-1", 12, False, "")
        achievements = AchievementStateStore()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata_cache.json"
            state.save_legacy_cache(path, achievements)
            data = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(data["cache_kind"], "legacy-library-metadata-projection")
        self.assertNotIn("cloud_save_status", data)
        self.assertEqual(data["steam_builds"]["4"]["latest_build_id"], "build-1")

    def test_clear_game_removes_all_projection_entries(self):
        state = LibraryMetadataState()
        state.attempted_builds.add(4)
        state.attempted_tags.add(4)
        state.update_status_by_game_id[4] = True
        state.game_status_by_id[4] = object()
        state.steam_check_results[4] = ("", 0, False, "")
        state.local_version_by_game_id[4] = ("local", 1)
        state.steam_build_checked_at[4] = 2
        state.clear_game(4)
        self.assertFalse(state.attempted_builds)
        self.assertFalse(state.attempted_tags)
        self.assertFalse(state.update_status_by_game_id)
        self.assertFalse(state.game_status_by_id)
        self.assertFalse(state.steam_check_results)
        self.assertFalse(state.local_version_by_game_id)
        self.assertFalse(state.steam_build_checked_at)


if __name__ == "__main__":
    unittest.main()
