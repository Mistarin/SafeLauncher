import tempfile
import unittest
from types import SimpleNamespace

from core.cloud_center_service import CloudCenterService, CloudOverview
from core.cloud_context import CloudContext
from core.request_contracts import RequestPriority, ResourceStatus
from core.request_manager import RequestManager
from core.resource_cache import ResourceCache


class _FakeAccountService:
    def __init__(self, context):
        self.context = context
        self.snapshot_calls = 0
        self.list_calls = 0
        self.health_calls = 0

    def current_context(self):
        return self.context

    def snapshot(self):
        self.snapshot_calls += 1
        return SimpleNamespace(
            overview={
                "email": "owner@example.invalid",
                "totalDevices": 2,
                "devices": [
                    {"deviceName": "Desktop", "platform": "Linux", "isOnline": True, "lastSeenAt": 2_000},
                    {"deviceName": "Deck", "platform": "SteamOS", "isOnline": False, "lastSeenAt": 1_000},
                ],
            },
            listing={
                "games": [{"name": "Example Game"}],
                "bytesUsed": 2048,
                "quotaBytes": 1024 * 1024,
            },
        )

    def list_games(self):
        self.list_calls += 1
        return {"games": [{"name": "Example Game", "versions": [1, 2]}]}

    def health(self, **_kwargs):
        self.health_calls += 1
        return {"healthy": True, "status": "ok", "version": "1.7.0", "latency_ms": 4}


class _FakeStatusService:
    def __init__(self, context):
        self.context = context

    def current_context(self):
        return self.context

    def status_snapshot(self):
        return {}


class _FakeMetadataService:
    def __init__(self):
        self.calls = []
        self.handle = object()

    def request_profile(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.handle


class CloudCenterServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = RequestManager(
            max_workers=1,
            cache=ResourceCache(self.temp_dir.name),
        )

    def tearDown(self):
        self.manager.shutdown()
        self.temp_dir.cleanup()

    @staticmethod
    def _context(*, network_allowed=True, generation=3):
        return CloudContext(
            mode="convex",
            endpoint="https://cloud.invalid",
            fingerprint="opaque-account-fingerprint",
            generation=generation,
            network_allowed=network_allowed,
            backend_active=True,
            authentication_configured=True,
        )

    def test_overview_is_composed_and_cached_without_secret_or_endpoint_in_key(self):
        context = self._context()
        account = _FakeAccountService(context)
        service = CloudCenterService(
            self.manager,
            account_service=account,
            status_service=_FakeStatusService(context),
        )

        first = service.request_overview(priority=RequestPriority.CRITICAL)
        first_result = first.future.result(timeout=2)
        second = service.request_overview()
        second_result = second.future.result(timeout=2)

        self.assertEqual(first_result.status, ResourceStatus.READY)
        self.assertEqual(second_result.status, ResourceStatus.READY)
        self.assertEqual(account.snapshot_calls, 1)
        overview = CloudOverview.from_payload(first_result.value)
        self.assertEqual(overview.account_label, "owner@example.invalid")
        self.assertEqual(overview.device_count, 2)
        self.assertEqual(overview.online_device_count, 1)
        self.assertEqual(overview.game_count, 1)
        self.assertNotIn("private.example", service.overview_key(context).cache_key())
        self.assertNotIn("secret", service.overview_key(context).cache_key().lower())
        self.assertNotIn("owner@example.invalid", repr(first_result.key))

    def test_offline_context_returns_local_state_without_transport_call(self):
        context = self._context(network_allowed=False)
        account = _FakeAccountService(context)
        service = CloudCenterService(
            self.manager,
            account_service=account,
            status_service=_FakeStatusService(context),
        )

        result = service.request_overview().future.result(timeout=2)

        self.assertEqual(result.status, ResourceStatus.READY)
        self.assertEqual(CloudOverview.from_payload(result.value).connection, "offline")
        self.assertEqual(account.snapshot_calls, 0)

    def test_sync_delegates_to_managed_metadata_service(self):
        context = self._context()
        metadata = _FakeMetadataService()
        service = CloudCenterService(
            self.manager,
            account_service=_FakeAccountService(context),
            status_service=_FakeStatusService(context),
            metadata_service=metadata,
        )

        handle = service.request_sync("/tmp/library.db")

        self.assertIs(handle, metadata.handle)
        self.assertEqual(metadata.calls[0][0], ("/tmp/library.db",))
        self.assertEqual(metadata.calls[0][1]["force"], True)
        self.assertEqual(metadata.calls[0][1]["priority"], RequestPriority.CRITICAL)
        self.assertEqual(metadata.calls[0][1]["tag"], "cloud_center_sync")

    def test_history_and_probe_are_managed_and_redacted(self):
        context = self._context()
        account = _FakeAccountService(context)
        service = CloudCenterService(
            self.manager,
            account_service=account,
            status_service=_FakeStatusService(context),
        )

        history = service.request_save_history(42).future.result(timeout=2)
        devices = service.request_devices().future.result(timeout=2)
        probe = service.request_connection_probe().future.result(timeout=2)

        self.assertEqual(history.status, ResourceStatus.READY)
        self.assertEqual(history.value["game_id"], 42)
        self.assertEqual(probe.status, ResourceStatus.READY)
        self.assertEqual(probe.value["version"], "1.7.0")
        self.assertEqual(devices.status, ResourceStatus.READY)
        self.assertEqual(len(devices.value["devices"]), 2)
        self.assertEqual(account.list_calls, 1)
        self.assertEqual(account.health_calls, 1)
        self.assertNotIn("private.example", history.key.cache_key())
        self.assertNotIn("secret", repr(probe.value).lower())


if __name__ == "__main__":
    unittest.main()
