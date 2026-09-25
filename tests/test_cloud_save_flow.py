import os
import tempfile
import unittest
from unittest.mock import patch

import requests

from core.cloud_backend import _ProgressUploadFile, normalize_name_key
from core.cloud_models import SaveStats, SyncStatus
from core.cloud_save_sync import CloudSaveSyncEngine, resolve_name_key
from core.cloud_repository import CloudSaveRepository
from core.save_crypto import decrypt_save_file, encrypt_save_file, generate_data_key_b64


class CloudSaveFlowTests(unittest.TestCase):
    def test_upload_body_uses_content_length_without_chunked_transfer(self):
        with tempfile.NamedTemporaryFile() as handle:
            payload = b"encrypted-save"
            handle.write(payload)
            handle.flush()
            body = _ProgressUploadFile(handle.name, len(payload))
            try:
                prepared = requests.Request(
                    "POST",
                    "https://uploads.example",
                    data=body,
                    headers={"Content-Type": "application/octet-stream"},
                ).prepare()
            finally:
                body.close()

        self.assertEqual(prepared.headers.get("Content-Length"), str(len(payload)))
        self.assertNotIn("Transfer-Encoding", prepared.headers)

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

    def test_remote_stats_selects_newest_content_timestamp_across_devices(self):
        snapshot = {
            "displayName": "Example Game",
            "versions": [
                {
                    "version": 8,
                    "sourceMaxMtime": 100.0,
                    "sizeBytes": 512,
                    "fileCount": 3,
                    "uploadedDeviceName": "Desktop",
                },
                {
                    "version": 7,
                    "sourceMaxMtime": 200.0,
                    "sizeBytes": 1024,
                    "fileCount": 4,
                    "uploadedDeviceName": "Steam Deck",
                },
            ],
        }
        with patch.object(CloudSaveSyncEngine, "_remote_game_snapshot", return_value=snapshot), \
             patch("core.cloud_save_sync.get_active_save_version", return_value=8), \
             patch("core.cloud_save_sync.get_active_cloud_top_version", return_value=8):
            stats, _ = CloudSaveSyncEngine._remote_stats("example-game", game_name="Example Game")

        self.assertEqual(stats.last_modified, 200.0)
        self.assertEqual(stats.cloud_version, 7)
        self.assertEqual(stats.device_name, "Steam Deck")

    def test_active_cloud_version_converges_after_restore_timestamp_rounding(self):
        local = SaveStats(exists=True, last_modified=202.0)
        cloud = SaveStats(exists=True, last_modified=100.0, cloud_version=12)
        with patch("core.cloud_save_sync.backend_active", return_value=True), \
             patch("core.cloud_save_sync._cloud_auth_configured", return_value=True), \
             patch("core.cloud_save_sync.resolve_cloud_game_ref", return_value=type(
                 "CloudRef", (), {"name_key": "example-game"}
             )()), \
             patch.object(CloudSaveSyncEngine, "get_local_save_stats", return_value=(local, [])), \
             patch.object(CloudSaveSyncEngine, "_remote_stats", return_value=(cloud, {})), \
             patch("core.cloud_save_sync.get_active_save_version", return_value=12), \
             patch("core.cloud_save_sync.get_active_save_mtime", return_value=202.0):
            status, actual_local, actual_cloud = CloudSaveSyncEngine.check_sync_status(
                "Example Game", "/tmp/example"
            )

        self.assertEqual(status, SyncStatus.IN_SYNC)
        self.assertIs(actual_local, local)
        self.assertIs(actual_cloud, cloud)


if __name__ == "__main__":
    unittest.main()
