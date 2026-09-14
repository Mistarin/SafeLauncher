"""Tests for the Phase 3 managed cloud mutation boundary."""

from __future__ import annotations

import unittest
from threading import Event

from core.cloud_context import CloudContext
from core.cloud_operations import CloudPreflightResult, CloudStatusResult
from core.cloud_operation_service import CloudOperationService, CloudOperationTarget
from core.cloud_exit_sync_service import CloudExitSyncService
from core.cloud_operation_records import CloudOperationState
from core.cloud_save_sync import SaveStats, SyncStatus
from core.save_models import SaveOperationResult
from core.request_contracts import ResourceStatus
from core.request_manager import RequestManager


class _Coordinator:
    def __init__(self):
        self._generation = 3
        self.calls = []

    @property
    def generation(self):
        return self._generation

    def invalidate_context(self):
        self._generation += 1
        return self._generation

    def upload_local_save(self, game_id, game_name, game_path, **kwargs):
        self.calls.append(("upload", game_id, kwargs))
        if kwargs.get("progress_callback") is not None:
            kwargs["progress_callback"](0.6)
        return SaveOperationResult(True, "Cloud upload", game_name)

    def restore_cloud_save(self, game_id, game_name, game_path, **kwargs):
        self.calls.append(("restore", game_id, kwargs))
        if kwargs.get("progress_callback") is not None:
            kwargs["progress_callback"](0.7)
        return SaveOperationResult(True, "Cloud restore", game_name)

    def preflight(self, game_id, game_name, game_path, steam_id=""):
        self.calls.append(("preflight", game_id))
        return CloudPreflightResult(
            status=SyncStatus.CLOUD_NEWER,
            local_stats=SaveStats(exists=True),
            cloud_stats=SaveStats(exists=True),
        )

    def check_status(self, game_id, game_name, game_path, steam_id=""):
        self.calls.append(("status", game_id))
        return CloudStatusResult(game_name, SyncStatus.LOCAL_NEWER, SaveStats(exists=True), SaveStats(exists=True))

    def load_history(self, game_id, game_name, game_path, steam_id=""):
        self.calls.append(("history", game_id))
        return [{"version": 2}], None


class _BlockingCoordinator(_Coordinator):
    def __init__(self):
        super().__init__()
        self.started = Event()

    def upload_local_save(self, game_id, game_name, game_path, **kwargs):
        self.calls.append(("upload", game_id, kwargs))
        self.started.set()
        cancel_check = kwargs["cancel_check"]
        while not cancel_check():
            self.started.wait(0.01)
        return SaveOperationResult(
            False,
            "Cloud upload",
            game_name,
            error="Cloud save restore was cancelled.",
            category="cancelled",
        )


class _FailingCoordinator(_Coordinator):
    def upload_local_save(self, game_id, game_name, game_path, **kwargs):
        self.calls.append(("upload", game_id, kwargs))
        return SaveOperationResult(
            False,
            "Cloud upload",
            game_name,
            error="Backend unavailable",
            category="backend_unavailable",
        )


class CloudOperationServiceTests(unittest.TestCase):
    @staticmethod
    def _context_provider(generation):
        return CloudContext(
            mode="convex",
            endpoint="https://private.example",
            fingerprint="opaque-operation-context",
            generation=generation,
            network_allowed=True,
            backend_active=True,
            authentication_configured=True,
        )

    def test_upload_is_manager_backed_and_typed(self):
        coordinator = _Coordinator()
        manager = RequestManager(max_workers=1)
        try:
            service = CloudOperationService(
                manager,
                coordinator=coordinator,
                context_provider=self._context_provider,
            )
            target = CloudOperationTarget(7, "Example", "/games/example", "480")
            result = service.request_upload(target).future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.READY)
            self.assertTrue(result.value.success)
            self.assertEqual(coordinator.calls[0][0], "upload")
            self.assertNotIn("private.example", result.key.cache_key())
            record = service.operation(result.request_id)
            self.assertIsNotNone(record)
            self.assertEqual(record.state, CloudOperationState.COMPLETED)
            self.assertEqual(record.game_id, 7)
            self.assertNotIn("private.example", record.context_fingerprint)
        finally:
            manager.shutdown()

    def test_exit_sync_service_normalizes_operation_payload(self):
        coordinator = _Coordinator()
        manager = RequestManager(max_workers=1)
        try:
            operations = CloudOperationService(
                manager,
                coordinator=coordinator,
                context_provider=self._context_provider,
            )
            service = CloudExitSyncService(operations)
            target = CloudOperationTarget(70, "Exit Game", "/games/exit")
            handle = service.request(target, settle_seconds=0)
            result = service.resolve(target, handle.future)
            self.assertEqual(result.game_id, 70)
            self.assertEqual(result.game_name, "Exit Game")
            self.assertEqual(result.outcome, "uploaded")
            self.assertEqual(result.as_payload()["game_id"], 70)
            self.assertEqual(result.presentation().kind, "success")
            self.assertTrue(result.presentation().refresh_status)
        finally:
            manager.shutdown()

    def test_failed_operation_records_shared_error_category(self):
        coordinator = _FailingCoordinator()
        manager = RequestManager(max_workers=1)
        try:
            service = CloudOperationService(
                manager,
                coordinator=coordinator,
                context_provider=self._context_provider,
            )
            target = CloudOperationTarget(71, "Unavailable Game")
            result = service.request_upload(target).future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.READY)
            record = service.operation(result.request_id)
            self.assertEqual(record.error_category, "unavailable")
        finally:
            manager.shutdown()

    def test_identical_mutation_requests_are_deduplicated(self):
        coordinator = _Coordinator()
        manager = RequestManager(max_workers=1)
        try:
            service = CloudOperationService(
                manager,
                coordinator=coordinator,
                context_provider=self._context_provider,
            )
            target = CloudOperationTarget(8, "Example")
            first = service.request_upload(target)
            second = service.request_upload(target)
            self.assertEqual(first.request_id, second.request_id)
            first.future.result(timeout=2)
            self.assertEqual([call[0] for call in coordinator.calls], ["upload"])
            self.assertEqual(len(service.operations()), 1)
        finally:
            manager.shutdown()

    def test_preflight_resolution_keeps_conflict_decision_typed(self):
        coordinator = _Coordinator()
        manager = RequestManager(max_workers=1)
        try:
            service = CloudOperationService(
                manager,
                coordinator=coordinator,
                context_provider=self._context_provider,
            )
            target = CloudOperationTarget(9, "Example")
            result = service.request_prelaunch_resolution(target).future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.READY)
            self.assertTrue(result.value["needs_conflict"])
            self.assertEqual(result.value["status"], SyncStatus.CLOUD_NEWER)
            self.assertEqual([call[0] for call in coordinator.calls], ["preflight"])
        finally:
            manager.shutdown()

    def test_auto_prefer_newer_runs_restore_inside_managed_request(self):
        coordinator = _Coordinator()
        manager = RequestManager(max_workers=1)
        try:
            service = CloudOperationService(
                manager,
                coordinator=coordinator,
                context_provider=self._context_provider,
            )
            target = CloudOperationTarget(10, "Example")
            result = service.request_prelaunch_resolution(
                target,
                auto_prefer_newer=True,
            ).future.result(timeout=2)
            self.assertTrue(result.value["cloud_result"].success)
            self.assertEqual([call[0] for call in coordinator.calls], ["preflight", "restore"])
        finally:
            manager.shutdown()

    def test_exit_sync_checks_then_uploads_local_newer_save(self):
        coordinator = _Coordinator()
        manager = RequestManager(max_workers=1)
        try:
            service = CloudOperationService(
                manager,
                coordinator=coordinator,
                context_provider=self._context_provider,
            )
            target = CloudOperationTarget(11, "Example")
            result = service.request_exit_sync(target, settle_seconds=0).future.result(timeout=2)
            self.assertEqual(result.value["outcome"], "uploaded")
            self.assertEqual([call[0] for call in coordinator.calls], ["status", "upload"])
        finally:
            manager.shutdown()

    def test_operation_progress_is_bounded_and_stops_after_completion(self):
        coordinator = _Coordinator()
        manager = RequestManager(max_workers=1)
        try:
            service = CloudOperationService(
                manager,
                coordinator=coordinator,
                context_provider=self._context_provider,
            )
            target = CloudOperationTarget(12, "Example")
            handle = service.request_upload(target)
            self.assertTrue(service.update_progress(handle.request_id, 2.0))
            self.assertEqual(service.operation(handle.request_id).progress, 1.0)
            handle.future.result(timeout=2)
            self.assertFalse(service.update_progress(handle.request_id, 0.5))
        finally:
            manager.shutdown()

    def test_engine_progress_is_recorded_without_save_payloads(self):
        coordinator = _Coordinator()
        manager = RequestManager(max_workers=1)
        try:
            service = CloudOperationService(
                manager,
                coordinator=coordinator,
                context_provider=self._context_provider,
            )
            handle = service.request_upload(CloudOperationTarget(13, "Example"))
            handle.future.result(timeout=2)
            record = service.operation(handle.request_id)
            self.assertEqual(record.state, CloudOperationState.COMPLETED)
            self.assertEqual(record.progress, 1.0)
            self.assertFalse(hasattr(record, "payload"))
        finally:
            manager.shutdown()

    def test_cancelled_cooperative_operation_has_cancelled_terminal_record(self):
        coordinator = _BlockingCoordinator()
        manager = RequestManager(max_workers=1)
        try:
            service = CloudOperationService(
                manager,
                coordinator=coordinator,
                context_provider=self._context_provider,
            )
            handle = service.request_upload(CloudOperationTarget(14, "Example"))
            self.assertTrue(coordinator.started.wait(timeout=2))
            self.assertTrue(service.cancel(handle.request_id))
            result = handle.future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.CANCELLED)
            record = service.operation(handle.request_id)
            self.assertEqual(record.state, CloudOperationState.CANCELLED)
            self.assertIn(record.progress, {0.05, 0.6})
        finally:
            manager.shutdown()

    def test_domain_failure_is_not_reported_as_completed_request(self):
        coordinator = _FailingCoordinator()
        manager = RequestManager(max_workers=1)
        try:
            service = CloudOperationService(
                manager,
                coordinator=coordinator,
                context_provider=self._context_provider,
            )
            handle = service.request_upload(CloudOperationTarget(15, "Example"))
            result = handle.future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.READY)
            self.assertFalse(result.value.success)
            record = service.operation(handle.request_id)
            self.assertEqual(record.state, CloudOperationState.FAILED)
            self.assertEqual(record.error_category, "unavailable")
        finally:
            manager.shutdown()


if __name__ == "__main__":
    unittest.main()
