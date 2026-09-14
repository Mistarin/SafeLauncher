"""Tests for the Phase 1 private cloud status application boundary."""

from __future__ import annotations

import tempfile
import unittest

from core.cloud_context import CloudContext
from core.cloud_operations import CloudStatusResult
from core.cloud_status_service import CloudStatusService, CloudStatusTarget
from core.cloud_save_sync import SaveStats, SyncStatus
from core.request_contracts import ResourceStatus
from core.request_manager import RequestManager


class _Coordinator:
    def __init__(self, result: CloudStatusResult):
        self.result = result
        self._generation = 4
        self.calls = []

    @property
    def generation(self):
        return self._generation

    def invalidate_context(self):
        self._generation += 1
        return self._generation

    def check_status(self, game_id, game_name, game_path, steam_id):
        self.calls.append((game_id, game_name, game_path, steam_id))
        return self.result

    def find_changed_games(self, games, cached_statuses):
        return list(games[:1])


class CloudStatusServiceTests(unittest.TestCase):
    def _context_provider(self, fingerprint="opaque-a"):
        def provider(generation):
            return CloudContext(
                mode="convex",
                endpoint="https://private.example",
                fingerprint=fingerprint,
                generation=generation,
                network_allowed=True,
                backend_active=True,
                authentication_configured=True,
            )

        return provider

    def test_context_key_contains_only_opaque_identity(self):
        context = CloudContext(
            mode="convex",
            endpoint="https://private.example",
            fingerprint="opaque-context",
            generation=2,
        )
        key = context.request_key("cloud-save-status", "42", "v1")
        self.assertEqual(key.identity, "opaque-context:42")
        self.assertNotIn("secret", key.cache_key().lower())
        self.assertNotIn(context.endpoint, key.cache_key())

    def test_status_request_is_manager_backed_and_typed(self):
        expected = CloudStatusResult(
            "Example Game",
            SyncStatus.IN_SYNC,
            SaveStats(exists=True),
            SaveStats(exists=True),
        )
        coordinator = _Coordinator(expected)
        manager = RequestManager(max_workers=1)
        try:
            service = CloudStatusService(
                manager,
                coordinator=coordinator,
                context_provider=self._context_provider(),
            )
            handle = service.request_status(
                CloudStatusTarget(42, "Example Game", "/games/example", "480")
            )
            result = handle.future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.READY)
            self.assertEqual(result.value.status, SyncStatus.IN_SYNC)
            self.assertEqual(coordinator.calls, [(42, "Example Game", "/games/example", "480")])
            self.assertTrue(result.key.identity.startswith("opaque-a:"))
        finally:
            manager.shutdown()

    def test_context_change_retires_old_generation_and_changes_key(self):
        expected = CloudStatusResult("Example Game", SyncStatus.NO_SAVES)
        coordinator = _Coordinator(expected)
        manager = RequestManager(max_workers=1)
        fingerprints = iter(("opaque-a", "opaque-b", "opaque-b"))

        def provider(generation):
            return CloudContext(
                mode="convex",
                endpoint="https://private.example",
                fingerprint=next(fingerprints),
                generation=generation,
                network_allowed=True,
                backend_active=True,
                authentication_configured=True,
            )

        try:
            service = CloudStatusService(manager, coordinator=coordinator, context_provider=provider)
            first = service.status_spec(CloudStatusTarget(1, "One"))
            second = service.status_spec(CloudStatusTarget(1, "One"))
            self.assertNotEqual(first.key, second.key)
            self.assertNotEqual(first.generation, second.generation)
            self.assertEqual(coordinator.generation, 5)
        finally:
            manager.shutdown()

    def test_failed_domain_status_is_reported_as_request_error(self):
        from core.save_models import SaveOperationResult

        failure = SaveOperationResult(
            success=False,
            operation="status",
            game_name="Example Game",
            error="Cloud authentication is required",
            category="authentication",
        )
        coordinator = _Coordinator(CloudStatusResult("Example Game", error=failure))
        manager = RequestManager(max_workers=1)
        try:
            service = CloudStatusService(
                manager,
                coordinator=coordinator,
                context_provider=self._context_provider(),
            )
            result = service.request_status(CloudStatusTarget(7, "Example Game")).future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.AUTHENTICATION_REQUIRED)
            self.assertEqual(getattr(result.error, "category", ""), "authentication")
            self.assertEqual(result.error_category, "authentication_required")
        finally:
            manager.shutdown()

    def test_status_projection_is_persisted_and_reloaded_by_service(self):
        expected = CloudStatusResult("Example Game", SyncStatus.IN_SYNC)
        coordinator = _Coordinator(expected)
        with tempfile.TemporaryDirectory() as directory:
            manager = RequestManager(max_workers=1)
            try:
                path = f"{directory}/cloud-status.json"
                service = CloudStatusService(
                    manager,
                    coordinator=coordinator,
                    context_provider=self._context_provider(),
                    cache_path=path,
                )
                local = SaveStats(exists=True, size_bytes=12, file_count=2)
                cloud = SaveStats(exists=True, size_bytes=12)
                service.record_status(42, SyncStatus.IN_SYNC, local, cloud, checked_at=123.0)

                restored = CloudStatusService(
                    manager,
                    coordinator=coordinator,
                    context_provider=self._context_provider(),
                    cache_path=path,
                )
                cached = restored.cached_status(42)
                self.assertEqual(cached[0], SyncStatus.IN_SYNC)
                self.assertEqual(cached[1].size_bytes, 12)
                self.assertEqual(restored.checked_at(42), 123.0)
            finally:
                manager.shutdown()

    def test_record_status_accepts_explicit_context_generation(self):
        coordinator = _Coordinator(CloudStatusResult("Example Game", SyncStatus.IN_SYNC))
        manager = RequestManager(max_workers=1)
        try:
            service = CloudStatusService(
                manager,
                coordinator=coordinator,
                context_provider=self._context_provider(),
            )
            service.record_status(
                42,
                SyncStatus.IN_SYNC,
                checked_at=123.0,
                generation=coordinator.generation,
            )
            self.assertEqual(service.cached_status(42)[0], SyncStatus.IN_SYNC)
            self.assertEqual(
                service.status_store.state(42).context_generation,
                coordinator.generation,
            )
        finally:
            manager.shutdown()

    def test_recheck_planning_prioritizes_uncached_then_offline_then_stale(self):
        coordinator = _Coordinator(CloudStatusResult("Example", SyncStatus.NO_SAVES))
        manager = RequestManager(max_workers=1)
        try:
            service = CloudStatusService(
                manager,
                coordinator=coordinator,
                context_provider=self._context_provider(),
            )
            service.record_status(2, SyncStatus.CLOUD_OFFLINE, checked_at=100.0)
            service.record_status(3, SyncStatus.IN_SYNC, checked_at=100.0)
            targets = [
                CloudStatusTarget(3, "Stale"),
                CloudStatusTarget(1, "Uncached"),
                CloudStatusTarget(2, "Offline"),
            ]
            plan = service.plan_recheck(targets, None, stale_after_seconds=1)
            self.assertEqual([target.game_id for target in plan.targets], [1, 2, 3])
        finally:
            manager.shutdown()

    def test_changed_listing_is_manager_backed_and_deduplicated(self):
        coordinator = _Coordinator(CloudStatusResult("Example", SyncStatus.NO_SAVES))
        manager = RequestManager(max_workers=1)
        try:
            service = CloudStatusService(
                manager,
                coordinator=coordinator,
                context_provider=self._context_provider(),
            )
            targets = [CloudStatusTarget(9, "Example", "/games/example", "9")]
            first = service.request_changed_diff(targets)
            second = service.request_changed_diff(targets)
            self.assertIs(first, second)
            changed = first.future.result(timeout=2).value
            self.assertEqual([target.game_id for target in changed], [9])
        finally:
            manager.shutdown()


if __name__ == "__main__":
    unittest.main()
