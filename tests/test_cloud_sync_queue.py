"""Durable local-first cloud sync queue tests."""

from __future__ import annotations

import os
import tempfile
import unittest

from core.cloud_sync_queue import PendingCloudSyncQueue


class PendingCloudSyncQueueTests(unittest.TestCase):
    def test_repeated_changes_coalesce_and_newer_digest_survives_old_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = PendingCloudSyncQueue(os.path.join(directory, "pending.json"))
            self.assertTrue(queue.enqueue("account-a", local_digest="old", queued_at=1))
            self.assertTrue(queue.enqueue("account-a", local_digest="new", queued_at=2))
            self.assertEqual(len(queue.pending()), 1)
            self.assertEqual(queue.pending()[0]["local_digest"], "new")
            self.assertFalse(queue.acknowledge("account-a", local_digest="old"))
            self.assertEqual(len(queue.pending()), 1)
            self.assertTrue(queue.acknowledge("account-a", local_digest="new"))
            self.assertEqual(queue.pending(), [])

    def test_queue_round_trips_with_private_file_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "pending.json")
            queue = PendingCloudSyncQueue(path)
            self.assertTrue(queue.enqueue("context", "profile", local_digest="digest"))
            restored = PendingCloudSyncQueue(path)
            self.assertEqual(restored.pending()[0]["operation"], "profile")
            self.assertEqual(restored.pending()[0]["local_digest"], "digest")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_malformed_queue_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "pending.json")
            with open(path, "w", encoding="utf-8") as stream:
                stream.write("not-json")
            self.assertEqual(PendingCloudSyncQueue(path).pending(), [])


if __name__ == "__main__":
    unittest.main()
