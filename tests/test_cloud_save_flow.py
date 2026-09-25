import unittest
from unittest.mock import patch

from core.cloud_backend import normalize_name_key
from core.cloud_models import SaveStats
from core.cloud_save_sync import CloudSaveSyncEngine, resolve_name_key


class CloudSaveFlowTests(unittest.TestCase):
    def test_local_mode_name_resolution_never_fetches_remote_listing(self):
        with patch("core.cloud_save_sync.backend_active", return_value=False), \
             patch("core.cloud_save_sync._get_cloud_listing") as listing:
            self.assertEqual(resolve_name_key("Example: Game"), normalize_name_key("Example: Game"))
            listing.assert_not_called()

    def test_remote_stats_uses_version_file_count_not_version_count(self):
        snapshot = {
            "displayName": "Example Game",
            "versions": [
                {
                    "version": 4,
                    "sourceMaxMtime": 100.0,
                    "sizeBytes": 512,
                    "fileCount": 23,
                },
                {"version": 3, "sourceMaxMtime": 90.0, "fileCount": 19},
            ],
        }
        with patch.object(CloudSaveSyncEngine, "_remote_game_snapshot", return_value=snapshot), \
             patch("core.cloud_save_sync.get_active_save_version", return_value=None), \
             patch("core.cloud_save_sync.get_active_cloud_top_version", return_value=None):
            stats, _ = CloudSaveSyncEngine._remote_stats("example-game")

        self.assertIsInstance(stats, SaveStats)
        self.assertEqual(stats.file_count, 23)


if __name__ == "__main__":
    unittest.main()
