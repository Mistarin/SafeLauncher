import os
import tempfile
import unittest
from unittest.mock import patch

from core.cloud_backend import normalize_name_key
from core.cloud_models import SaveStats
from core.cloud_save_sync import CloudSaveSyncEngine, resolve_name_key
from core.cloud_repository import CloudSaveRepository
from core.save_crypto import decrypt_save_file, encrypt_save_file, generate_data_key_b64


class CloudSaveFlowTests(unittest.TestCase):
    def test_streaming_envelope_round_trip_does_not_require_whole_archive(self):
        with tempfile.TemporaryDirectory() as root:
            plain = os.path.join(root, "save.zip")
            encrypted = os.path.join(root, "save.enc")
            restored = os.path.join(root, "restored.zip")
            payload = (b"safe-save-content-" * 200_000)
            with open(plain, "wb") as handle:
                handle.write(payload)
            key = generate_data_key_b64()
            digest, size, envelope_size = encrypt_save_file(plain, encrypted, key)
            self.assertEqual(size, len(payload))
            self.assertEqual(envelope_size, os.path.getsize(encrypted))
            self.assertEqual(len(digest), 64)
            self.assertEqual(decrypt_save_file(encrypted, restored, key), len(payload))
            with open(restored, "rb") as handle:
                self.assertEqual(handle.read(), payload)

    def test_repository_reuses_existing_legacy_key(self):
        repository = CloudSaveRepository()
        listing = {"games": [{"nameKey": "legacy-game", "displayName": "Legacy Game"}]}
        with patch.object(repository, "_is_remote_active", return_value=True), \
             patch.object(repository, "listing", return_value=listing), \
             patch("core.cloud_save_sync.cloud_context_fingerprint", return_value="ctx"):
            ref = repository.resolve("Legacy Game")
        self.assertEqual(ref.name_key, "legacy-game")
        self.assertEqual(ref.context_identity, "ctx")

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
