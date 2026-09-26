"""Tests for the profile application/transport boundary."""

from __future__ import annotations

import tempfile
import unittest

from core.profile_resource_service import ProfileResourceService
from core.profile_service import ProfileServiceError
from core.request_contracts import RequestPriority, ResourceStatus
from core.request_manager import RequestManager
from core.resource_cache import ResourceCache


class _FakeClient:
    def __init__(self, _url, *, auth_session=None):
        self.auth_session = auth_session
        self.configured = True
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def fetch(self, handle):
        self.calls.append(("fetch", handle))
        return {"handle": handle}

    def current_profile(self):
        self.calls.append(("current_profile",))
        return None

    def check_handle_availability(self, handle):
        self.calls.append(("availability", handle))
        return True

    def get_social(self, handle):
        self.calls.append(("social", handle))
        return {"friends": [], "handle": handle}

    def send_friend_request(self, owner, target):
        self.calls.append(("send", owner, target))
        return {"ok": True}

    def respond_friend_request(self, owner, request_id, action):
        self.calls.append(("respond", owner, request_id, action))
        return {"ok": True}

    def remove_friend(self, owner, target):
        self.calls.append(("remove", owner, target))
        return {"ok": True}

    def block_user(self, owner, target):
        self.calls.append(("block", owner, target))
        return {"ok": True}

    def unblock_user(self, owner, target):
        self.calls.append(("unblock", owner, target))
        return {"ok": True}


class _HandleConflictClient(_FakeClient):
    def create_profile(self, _document):
        self.calls.append(("create_profile",))
        raise ProfileServiceError("Profile handle is already in use.", "exists", 409)


class _RenameClient(_FakeClient):
    def current_profile(self):
        self.calls.append(("current_profile",))
        return {"handle": "old-name", "revision": 4}

    def update_profile(self, document, revision):
        self.calls.append(("update_profile", document["handle"], revision))
        return {"handle": "new-name", "revision": revision + 1}


class _SchemaCompatibilityClient(_FakeClient):
    def create_profile(self, document):
        if document["schema_version"] == 4:
            raise ProfileServiceError("Profile schema or handle is invalid.", "invalid_profile", 400)
        return {"handle": document["handle"], "revision": 1}


class ProfileResourceServiceTests(unittest.TestCase):
    def test_endpoint_keys_are_stable_without_exposing_endpoint_text(self):
        first = ProfileResourceService.endpoint_fingerprint("https://profile.example")
        second = ProfileResourceService.endpoint_fingerprint("https://profile.example/")
        self.assertEqual(first, second)
        self.assertNotIn("profile.example", first)

    def test_context_invalidation_advances_generation(self):
        service = ProfileResourceService(client_factory=_FakeClient)
        self.assertEqual(service.context_generation, 0)
        self.assertEqual(service.invalidate_context(), 1)
        self.assertEqual(service.invalidate_context(), 2)

    def test_resource_keys_are_context_isolated_without_endpoint_text(self):
        service = ProfileResourceService(client_factory=_FakeClient)
        key = service.request_key("profile-artwork", "player", "image")
        self.assertEqual(key.resource, "profile-artwork")
        self.assertEqual(key.variant, "image")
        self.assertNotIn("profile.example", key.cache_key())
        self.assertTrue(key.identity.startswith(service.endpoint_fingerprint()))

    def test_request_specs_carry_profile_generation_and_metadata(self):
        service = ProfileResourceService(client_factory=_FakeClient)
        service.invalidate_context()
        key = service.request_key("profile-social", "player")
        spec = service.request_spec(
            key,
            lambda token: (token.raise_if_cancelled(), {"ok": True})[1],
            priority=RequestPriority.CRITICAL,
            timeout_seconds=4,
            tag="social",
        )
        self.assertEqual(spec.generation, 1)
        self.assertEqual(spec.priority, RequestPriority.CRITICAL)
        self.assertEqual(spec.timeout_seconds, 4)
        self.assertEqual(spec.metadata["profile_generation"], 1)
        self.assertEqual(spec.metadata["tag"], "social")
        self.assertEqual(service.context_generation, 1)

    def test_profile_spec_uses_shared_cache_without_duplicate_load(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            cache = ResourceCache(directory, max_entries=16, max_disk_bytes=1024 * 1024)
            manager = RequestManager(max_workers=1, cache=cache)
            try:
                service = ProfileResourceService(client_factory=_FakeClient)
                key = service.request_key("profile-social", "player")

                def loader(token):
                    token.raise_if_cancelled()
                    calls.append("load")
                    return {"handle": "player", "friends": []}

                first = manager.cached_request(
                    service.request_spec(key, loader, tag="social"),
                    cache,
                    max_age_seconds=60,
                ).future.result(timeout=2)
                second = manager.cached_request(
                    service.request_spec(key, loader, tag="social"),
                    cache,
                    max_age_seconds=60,
                ).future.result(timeout=2)

                self.assertEqual(first.status, ResourceStatus.READY)
                self.assertEqual(second.status, ResourceStatus.READY)
                self.assertTrue(second.from_cache)
                self.assertEqual(calls, ["load"])
            finally:
                manager.shutdown()

    def test_transport_calls_are_owned_by_the_service(self):
        clients = []

        def factory(url, *, auth_session=None):
            client = _FakeClient(url, auth_session=auth_session)
            clients.append(client)
            return client

        service = ProfileResourceService(auth_session=object(), client_factory=factory)
        self.assertEqual(service.fetch_public("player", "https://profile.example")["handle"], "player")
        self.assertTrue(service.check_handle_availability("player", "https://profile.example"))
        self.assertEqual(service.get_social("player", "https://profile.example")["friends"], [])
        self.assertEqual(
            service.social_operation(
                "send_friend_request",
                "player",
                target_handle="friend",
                service_url="https://profile.example",
            ),
            {"ok": True},
        )
        self.assertEqual(len(clients), 4)
        self.assertTrue(all(client.auth_session is not None for client in clients))

    def test_named_social_operations_cover_dialog_actions(self):
        service = ProfileResourceService(client_factory=_FakeClient)
        operations = (
            ("send_friend_request", {"target_handle": "friend"}),
            ("respond_friend_request", {"request_id": "req-1", "action": "accept"}),
            ("remove_friend", {"target_handle": "friend"}),
            ("block_user", {"target_handle": "friend"}),
            ("unblock_user", {"target_handle": "friend"}),
        )
        for operation, kwargs in operations:
            result = service.social_operation(operation, "owner", **kwargs)
            self.assertEqual(result, {"ok": True})

    def test_publish_normalizes_handle_conflict_when_identity_has_no_profile(self):
        service = ProfileResourceService(client_factory=_HandleConflictClient)
        with self.assertRaises(ProfileServiceError) as raised:
            service.publish(
                {"handle": "taken-name", "display_name": "Player"},
                "taken-name",
                service_url="https://profile.example",
            )
        self.assertEqual(raised.exception.code, "handle_taken")
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.extra["original_code"], "exists")
        self.assertIn("choose a different handle", str(raised.exception).lower())

    def test_publish_keeps_an_intentional_username_change(self):
        service = ProfileResourceService(client_factory=_RenameClient)
        document = {"handle": "new-name", "display_name": "Player"}

        result = service.publish(document, "old-name", service_url="https://profile.example")

        self.assertEqual(result["document"]["handle"], "new-name")

    def test_publish_retries_schema_four_once_for_an_older_gateway(self):
        service = ProfileResourceService(client_factory=_SchemaCompatibilityClient)
        document = {"schema_version": 4, "handle": "new-name", "display_name": "Player"}
        result = service.publish(document, "new-name", service_url="https://profile.example")
        self.assertEqual(result["document"]["schema_version"], 3)


if __name__ == "__main__":
    unittest.main()
