"""Tests for managed private library/profile reconciliation."""

from __future__ import annotations

import unittest
from concurrent.futures import Future
from unittest.mock import patch

from core.cloud_context import CloudContext
from core.cloud_metadata_service import (
    CloudMetadataService,
    CloudMetadataTarget,
)
from core.request_contracts import ResourceStatus
from core.request_manager import RequestHandle, RequestManager


class _Database:
    def __init__(self, path):
        self.path = path
        self.closed = False

    def close(self):
        self.closed = True


class _RecordingManager:
    """Small request-manager double for lifecycle/coalescing tests."""

    def __init__(self):
        self.handles = []

    def submit(self, spec):
        future = Future()
        handle = RequestHandle(
            spec.key,
            f"request-{len(self.handles) + 1}",
            spec.generation,
            future,
            lambda: False,
        )
        self.handles.append(handle)
        return handle


class CloudMetadataServiceTests(unittest.TestCase):
    @staticmethod
    def _context_provider(generation):
        return CloudContext(
            mode="convex",
            endpoint="https://private.example",
            fingerprint="opaque-metadata-context",
            generation=generation,
            network_allowed=True,
            backend_active=True,
            authentication_configured=True,
        )

    def test_profile_request_is_manager_backed_and_context_scoped(self):
        manager = RequestManager(max_workers=1)
        database = _Database("profile.db")
        try:
            service = CloudMetadataService(
                manager,
                context_provider=self._context_provider,
                db_factory=lambda path: database,
            )
            with patch("core.cloud_metadata_service.CloudMetadataSync.sync_profile", return_value=True) as sync:
                result = service.request_profile("profile.db", force=True).future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.READY)
            self.assertTrue(result.value.success)
            self.assertEqual(result.value.operation, "profile")
            self.assertEqual(sync.call_args.kwargs, {"force": True})
            self.assertNotIn("private.example", result.key.cache_key())
            self.assertTrue(database.closed)
        finally:
            manager.shutdown()

    def test_profile_and_game_reconcile_in_order(self):
        manager = RequestManager(max_workers=1)
        database = _Database("library.db")
        try:
            service = CloudMetadataService(
                manager,
                context_provider=self._context_provider,
                db_factory=lambda path: database,
            )
            with patch(
                "core.cloud_metadata_service.CloudMetadataSync.sync_profile",
                return_value=True,
            ) as profile, patch(
                "core.cloud_metadata_service.CloudMetadataSync.sync_game",
                return_value=True,
            ) as game:
                result = service.request_profile_and_game(
                    CloudMetadataTarget(4, "Example", "480"),
                    "library.db",
                ).future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.READY)
            self.assertTrue(result.value.success)
            self.assertFalse(result.value.queued)
            self.assertEqual(profile.call_count, 1)
            self.assertEqual(game.call_args.args[1:4], (4, "Example", "480"))
            self.assertTrue(database.closed)
        finally:
            manager.shutdown()

    def test_failed_reconciliation_is_marked_queued_for_retry(self):
        manager = RequestManager(max_workers=1)
        database = _Database("offline.db")
        try:
            service = CloudMetadataService(
                manager,
                context_provider=self._context_provider,
                db_factory=lambda path: database,
            )
            with patch("core.cloud_metadata_service.CloudMetadataSync.sync_profile", return_value=False):
                result = service.request_profile("offline.db").future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.READY)
            self.assertFalse(result.value.success)
            self.assertTrue(result.value.queued)
        finally:
            manager.shutdown()

    def test_latest_game_coalesces_repeated_edits_and_runs_one_follow_up(self):
        manager = _RecordingManager()
        service = CloudMetadataService(
            manager,
            context_provider=self._context_provider,
        )
        target = CloudMetadataTarget(8, "Example", "480")

        first = service.request_latest_game(target, "library.db")
        repeated = service.request_latest_game(target, "library.db")
        self.assertIs(first, repeated)
        self.assertEqual(len(manager.handles), 1)

        first.future.set_result(object())
        self.assertEqual(len(manager.handles), 2)
        self.assertIsNot(manager.handles[1], first)

    def test_context_invalidation_cancels_coalescing_state(self):
        class _CancellableManager(_RecordingManager):
            def submit(self, spec):
                handle = super().submit(spec)
                cancelled = []
                handle = RequestHandle(
                    handle.key,
                    handle.request_id,
                    handle.generation,
                    handle.future,
                    lambda: cancelled.append(True) or True,
                )
                self.handles[-1] = handle
                return handle

        manager = _CancellableManager()
        service = CloudMetadataService(manager, context_provider=self._context_provider)
        target = CloudMetadataTarget(9, "Example", "730")
        service.request_latest_game(target)
        service.request_latest_game(target)
        service.invalidate_context()
        self.assertFalse(service._latest_handles)
        self.assertFalse(service._latest_pending)


if __name__ == "__main__":
    unittest.main()
