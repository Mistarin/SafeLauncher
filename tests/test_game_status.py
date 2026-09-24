import unittest

from core.cloud_save_sync import SyncStatus
from core.game_status import GameStatusState, cloud_indicator, is_cloud_conflict


class CloudConflictActionStateTests(unittest.TestCase):
    def test_only_conflict_status_enables_resolution(self):
        self.assertTrue(is_cloud_conflict(SyncStatus.CONFLICT))
        self.assertTrue(is_cloud_conflict((SyncStatus.CONFLICT, None, None)))
        self.assertTrue(is_cloud_conflict(GameStatusState(cloud_status=SyncStatus.CONFLICT)))
        self.assertFalse(is_cloud_conflict(SyncStatus.IN_SYNC))
        self.assertFalse(is_cloud_conflict(None))

    def test_online_backend_failure_is_not_labelled_as_explicit_offline_mode(self):
        indicator = cloud_indicator(SyncStatus.CLOUD_UNAVAILABLE)

        self.assertEqual(indicator.label, "Cloud Save: Unavailable")
        self.assertIn("Online mode", indicator.tooltip)


if __name__ == "__main__":
    unittest.main()
