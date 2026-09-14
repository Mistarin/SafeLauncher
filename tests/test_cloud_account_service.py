import unittest
from unittest.mock import patch

from core.cloud_account_service import CloudAccountService
from core.cloud_backend import normalize_site_url
from core.cloud_context import CloudContext
from core.request_contracts import ResourceStatus
from core.request_manager import RequestManager


class _FakeCloudClient:
    instances = []

    def __init__(self, *, site_url=None, secret_key=None):
        self.site_url = site_url
        self.secret_key = secret_key
        self.closed = False
        self.calls = []
        self.instances.append(self)

    def close(self):
        self.closed = True

    def list_games(self):
        self.calls.append("list_games")
        return {"games": [], "bytesUsed": 4}

    def account(self):
        self.calls.append("account")
        return {"quotaBytes": 1024 * 1024 * 128, "email": "owner@example.test"}

    def revoke_device(self, device_id):
        self.calls.append(("revoke", device_id))
        return True

    def delete_generation(self, name_key, version):
        self.calls.append(("delete", name_key, version))
        return True


class CloudAccountServiceTests(unittest.TestCase):
    def test_convex_client_url_normalizes_to_http_function_site_url(self):
        self.assertEqual(
            normalize_site_url("https://test-deployment.eu-west-1.convex.cloud/"),
            "https://test-deployment.eu-west-1.convex.site",
        )
        self.assertEqual(
            normalize_site_url("test-deployment.eu-west-1.convex.site"),
            "test-deployment.eu-west-1.convex.site",
        )
        self.assertEqual(normalize_site_url(""), "")

    def test_explicit_cloud_client_uses_normalized_http_function_url(self):
        from core.cloud_backend import ConvexSaveBackend

        client = ConvexSaveBackend(
            "https://test-deployment.eu-west-1.convex.cloud",
            "test-secret",
        )
        try:
            self.assertEqual(
                client.site_url,
                "https://test-deployment.eu-west-1.convex.site",
            )
        finally:
            client.close()

    def test_deploy_output_selects_the_deployed_convex_site_origin(self):
        from core.cloud_cli_wizard import _site_url_from_deploy_output

        output = (
            "Deploying to https://fresh-target.eu-west-1.convex.cloud...\n"
            "Finalizing push...\n"
            "Deployed functions"
        )
        self.assertEqual(
            _site_url_from_deploy_output(output),
            "https://fresh-target.eu-west-1.convex.site",
        )

    def test_deploy_output_does_not_extract_arbitrary_urls(self):
        from core.cloud_cli_wizard import _site_url_from_deploy_output

        self.assertEqual(
            _site_url_from_deploy_output("dashboard: https://example.test/project"),
            "",
        )

    def setUp(self):
        _FakeCloudClient.instances.clear()

    def test_snapshot_and_admin_operations_close_transport_clients(self):
        service = CloudAccountService(client_factory=_FakeCloudClient)
        snapshot = service.snapshot()
        self.assertEqual(snapshot.listing["bytesUsed"], 4)
        self.assertEqual(snapshot.overview["email"], "owner@example.test")
        self.assertTrue(service.revoke_device("device-1"))
        self.assertTrue(service.delete_generation("game-key", 3))
        self.assertEqual(len(_FakeCloudClient.instances), 3)
        self.assertTrue(all(client.closed for client in _FakeCloudClient.instances))

    def test_verify_connection_keeps_explicit_credentials_in_memory_only(self):
        health = {
            "healthy": True,
            "status": "connected",
            "version": "1.7.0",
            "is_outdated": False,
        }
        service = CloudAccountService(client_factory=_FakeCloudClient)
        with patch("core.cloud_account_service.check_backend_health", return_value=health):
            result = service.verify_connection(
                "https://private.example",
                "secret-value",
            )
        self.assertTrue(result.success)
        self.assertIn("128 MB", result.message)
        self.assertNotIn("secret-value", repr(result))
        client = _FakeCloudClient.instances[-1]
        self.assertEqual(client.site_url, "https://private.example")
        self.assertEqual(client.secret_key, "secret-value")
        self.assertTrue(client.closed)

    def test_explicit_probe_keys_isolate_endpoint_and_never_expose_secret(self):
        first = CloudAccountService.request_key(
            "cloud-health", "configured", site_url="https://one.example", secret_key="one-secret"
        )
        second = CloudAccountService.request_key(
            "cloud-health", "configured", site_url="https://two.example", secret_key="two-secret"
        )
        self.assertNotEqual(first, second)
        self.assertNotIn("one.example", first.cache_key())
        self.assertNotIn("one-secret", first.cache_key())
        self.assertNotIn("two-secret", second.cache_key())

    def test_device_revoke_uses_shared_manager_and_opaque_key(self):
        manager = RequestManager(max_workers=1)
        context = CloudContext(
            mode="convex",
            endpoint="https://private.example",
            fingerprint="opaque-account-context",
            generation=4,
            network_allowed=True,
            backend_active=True,
            authentication_configured=True,
        )
        try:
            service = CloudAccountService(
                client_factory=_FakeCloudClient,
                request_manager=manager,
            )
            with patch("core.cloud_account_service.CloudContext.current", return_value=context):
                handle = service.request_revoke_device("device-private-1")
                result = handle.future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.READY)
            self.assertTrue(result.value)
            self.assertNotIn("device-private-1", result.key.cache_key())
            self.assertNotIn("private.example", result.key.cache_key())
            self.assertTrue(_FakeCloudClient.instances[-1].closed)
        finally:
            manager.shutdown()

    def test_generation_delete_is_deduplicated_by_context_and_version(self):
        manager = RequestManager(max_workers=1)
        context = CloudContext(
            mode="convex",
            endpoint="https://private.example",
            fingerprint="opaque-account-context",
            generation=5,
            network_allowed=True,
            backend_active=True,
            authentication_configured=True,
        )
        try:
            service = CloudAccountService(
                client_factory=_FakeCloudClient,
                request_manager=manager,
            )
            with patch("core.cloud_account_service.CloudContext.current", return_value=context):
                first = service.request_delete_generation("private-game-key", 7)
                second = service.request_delete_generation("private-game-key", 7)
                result = first.future.result(timeout=2)
            self.assertEqual(first.request_id, second.request_id)
            self.assertEqual(result.status, ResourceStatus.READY)
            self.assertTrue(result.value)
            self.assertNotIn("private-game-key", result.key.cache_key())
            self.assertEqual(
                [call for client in _FakeCloudClient.instances for call in client.calls],
                [("delete", "private-game-key", 7)],
            )
        finally:
            manager.shutdown()

    def test_context_invalidation_advances_generation_for_admin_requests(self):
        manager = RequestManager(max_workers=1)

        def context_provider(generation):
            return CloudContext(
                mode="convex",
                endpoint="https://private.example",
                fingerprint=f"opaque-account-context-{generation}",
                generation=generation,
                network_allowed=True,
                backend_active=True,
                authentication_configured=True,
            )

        try:
            service = CloudAccountService(
                client_factory=_FakeCloudClient,
                request_manager=manager,
                context_provider=context_provider,
            )
            first = service.request_delete_generation("game-key", 1)
            first.future.result(timeout=2)
            previous = service.current_context()
            current = service.invalidate_context()
            second = service.request_delete_generation("game-key", 1)
            second.future.result(timeout=2)
            self.assertEqual(current.generation, previous.generation + 1)
            self.assertNotEqual(first.key, second.key)
            self.assertEqual(first.generation + 1, second.generation)
        finally:
            manager.shutdown()

    def test_verify_connection_preserves_legacy_and_outdated_states(self):
        service = CloudAccountService(client_factory=_FakeCloudClient)
        legacy = {
            "healthy": True,
            "status": "legacy",
            "version": "1.0.0",
            "is_outdated": True,
        }
        with patch("core.cloud_account_service.check_backend_health", return_value=legacy):
            result = service.verify_connection("https://private.example")
        self.assertFalse(result.success)
        self.assertIn("legacy backend", result.message.lower())
        self.assertEqual(_FakeCloudClient.instances, [])

        outdated = {
            "healthy": True,
            "status": "connected",
            "version": "1.0.0",
            "is_outdated": True,
        }
        with patch("core.cloud_account_service.check_backend_health", return_value=outdated):
            result = service.verify_connection("https://private.example")
        self.assertFalse(result.success)
        self.assertIn("outdated", result.message.lower())
        self.assertEqual(_FakeCloudClient.instances, [])


if __name__ == "__main__":
    unittest.main()
