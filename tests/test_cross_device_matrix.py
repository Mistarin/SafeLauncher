"""Cross-device private-library regression matrix.

These tests intentionally use the SQLite projection and private profile merge
path rather than UI fakes.  They protect the behavior users depend on when a
second device has the account history but not the installed game.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.cloud_metadata_sync import CloudMetadataSync, _merge_profiles
from database import GameDatabase


class CrossDeviceLibraryMatrixTests(unittest.TestCase):
    def test_device_a_stats_are_visible_on_device_b_before_and_after_install(self):
        with tempfile.TemporaryDirectory() as directory:
            game_root = Path(directory) / "device-a-game"
            game_root.mkdir()
            executable = game_root / "game.bin"
            executable.write_text("placeholder", encoding="utf-8")

            device_a = GameDatabase(":memory:")
            device_b = GameDatabase(":memory:")
            try:
                game_id_a = device_a.add_game(
                    "Cross Device Game", str(game_root), executable.name, "linux", "", "480"
                )
                device_a.save_achievement_schema(game_id_a, "480", [{
                    "api_name": "ACH_WIN",
                    "display_name": "Win",
                    "description": "Win once",
                }])
                device_a.unlock_achievement(game_id_a, "ACH_WIN", 1000)
                device_a.add_playtime(game_id_a, 3600)

                with patch(
                    "core.cloud_backend.get_device_identity",
                    return_value=("device-a", "A", "Linux"),
                ):
                    profile_a = CloudMetadataSync._local_profile(device_a)

                # Device B receives account history with no local installation.
                CloudMetadataSync._apply_profile(device_b, profile_a)
                history = device_b.find_game_by_profile_identity("steam:480")
                self.assertIsNotNone(history)
                self.assertTrue(history.is_archived)
                self.assertEqual(device_b.get_playtime(history.id), 3600)
                self.assertEqual(device_b.get_game_achievements(history.id), [])

                # Loading the schema on B projects the already-synced account
                # achievement without creating a second game identity.
                device_b.save_achievement_schema(history.id, "480", [{
                    "api_name": "ACH_WIN",
                    "display_name": "Win",
                }])
                self.assertEqual(device_b.get_achievement_stats(history.id)[:2], (1, 1))

                device_b.update_game(
                    history.id, "Cross Device Game", str(game_root), executable.name, "linux", ""
                )
                device_b.restore_game(history.id)
                self.assertFalse(device_b.find_game_by_profile_identity("steam:480").is_archived)
                self.assertEqual(
                    len([game for game in device_b.get_all_games() if game.steam_id == "480"]),
                    1,
                )
            finally:
                device_a.close()
                device_b.close()

    def test_device_installation_observations_merge_without_erasing_history(self):
        base = {
            "games": {
                "steam:730": {
                    "identity_key": "steam:730",
                    "app_id": "730",
                    "name": "Shared Game",
                    "playtime_baseline_seconds": 120,
                    "devices": {"device-a": {"installed": True, "observed_at": 10}},
                }
            },
            "achievements": {"730": {"ACH_ONE": {"unlock_time": 20}}},
        }
        device_b = {
            "games": {
                "steam:730": {
                    "identity_key": "steam:730",
                    "app_id": "730",
                    "name": "Shared Game",
                    "playtime_baseline_seconds": 300,
                    "devices": {"device-b": {"installed": False, "observed_at": 11}},
                }
            },
            "achievements": {"730": {"ACH_TWO": {"unlock_time": 30}}},
        }
        merged = _merge_profiles(base, device_b)
        game = merged["games"]["steam:730"]
        self.assertEqual(game["playtime_baseline_seconds"], 300)
        self.assertEqual(set(game["devices"]), {"device-a", "device-b"})
        self.assertEqual(set(merged["achievements"]["730"]), {"ACH_ONE", "ACH_TWO"})

    def test_account_context_switch_cannot_reuse_old_resource_key(self):
        from core.cloud_context import CloudContext

        first = CloudContext("convex", "https://a.example", "account-a", 1)
        second = CloudContext("convex", "https://b.example", "account-b", 2)
        self.assertNotEqual(
            first.request_key("private-library", "account", "v1"),
            second.request_key("private-library", "account", "v1"),
        )


if __name__ == "__main__":
    unittest.main()
