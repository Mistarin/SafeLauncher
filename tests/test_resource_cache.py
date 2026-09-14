"""Tests for Phase 2 resource caching and stale refresh behavior."""

from __future__ import annotations

import tempfile
import time
import unittest
from unittest.mock import patch

from core.request_contracts import RequestKey, RequestSpec, ResourceStatus
from core.request_manager import RequestManager
from core.resource_cache import ResourceCache


class ResourceCacheTests(unittest.TestCase):
    def test_json_and_bytes_survive_disk_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            key = RequestKey("asset", "logo", "png")
            cache = ResourceCache(directory)
            cache.put(key, b"image", content_type="image/png")
            loaded = ResourceCache(directory).get(key)
            self.assertEqual(loaded.value, b"image")
            self.assertEqual(loaded.content_type, "image/png")
            self.assertEqual(loaded.source, "disk")

    def test_memory_cache_is_bounded(self):
        cache = ResourceCache(max_entries=1)
        cache.put(RequestKey("data", "one"), 1)
        cache.put(RequestKey("data", "two"), 2)
        self.assertIsNone(cache.get(RequestKey("data", "one")))
        self.assertEqual(cache.get(RequestKey("data", "two")).value, 2)

    def test_disk_write_failure_degrades_to_memory_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ResourceCache(directory)
            key = RequestKey("data", "disk-failure")
            with patch.object(cache, "_write_atomic", side_effect=OSError("read-only")):
                entry = cache.put(key, {"cached": True})
            self.assertEqual(entry.value, {"cached": True})
            self.assertEqual(cache.get(key).value, {"cached": True})
            self.assertIsNone(cache.directory)

    def test_invalidate_removes_memory_and_disk_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            key = RequestKey("data", "invalidate")
            cache = ResourceCache(directory)
            cache.put(key, {"value": 1})
            cache.invalidate(key)
            self.assertIsNone(cache.get(key))
            self.assertIsNone(ResourceCache(directory).get(key))

    def test_fresh_cache_avoids_loader(self):
        cache = ResourceCache()
        manager = RequestManager(max_workers=1, cache=cache)
        try:
            key = RequestKey("data", "fresh")
            cache.put(key, {"value": 1})
            called = []
            result = manager.request_cached(
                key, lambda _token: called.append(True), max_age_seconds=60
            ).future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.READY)
            self.assertTrue(result.from_cache)
            self.assertEqual(result.cache_source, "memory")
            self.assertEqual(called, [])
        finally:
            manager.shutdown()

    def test_stale_cache_is_emitted_before_refresh(self):
        manager = RequestManager(max_workers=1)
        try:
            cache = ResourceCache()
            key = RequestKey("data", "stale")
            cache.put(key, "old", stored_at=time.time() - 100)
            seen = []
            manager.subscribe(key, seen.append)
            result = manager.cached_request(
                RequestSpec(key, lambda _token: "new"), cache, max_age_seconds=1
            ).future.result(timeout=2)
            self.assertEqual(result.value, "new")
            self.assertEqual(
                [item.status for item in seen],
                [ResourceStatus.STALE, ResourceStatus.LOADING, ResourceStatus.READY],
            )
            self.assertEqual(cache.get(key).value, "new")
        finally:
            manager.shutdown()

    def test_stale_value_is_preserved_when_refresh_is_offline(self):
        manager = RequestManager(max_workers=1, offline_check=lambda: True)
        try:
            key = RequestKey("library", "offline-stale")
            cache = ResourceCache(max_entries=4)
            cache.put(key, {"games": ["cached"]}, stored_at=time.time() - 100)
            seen = []
            manager.subscribe(key, seen.append, emit_current=False)
            result = manager.cached_request(
                RequestSpec(key, lambda _token: {"games": ["network"]}),
                cache,
                max_age_seconds=1,
            ).future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.OFFLINE)
            self.assertEqual(manager.state(key).status, ResourceStatus.STALE)
            self.assertEqual(manager.state(key).value, {"games": ["cached"]})
            self.assertEqual(seen[-1].status, ResourceStatus.STALE)
        finally:
            manager.shutdown()

    def test_stale_value_is_preserved_when_refresh_fails(self):
        manager = RequestManager(max_workers=1)
        try:
            key = RequestKey("library", "failed-stale")
            cache = ResourceCache(max_entries=4)
            cache.put(key, {"games": ["cached"]}, stored_at=time.time() - 100)
            result = manager.cached_request(
                RequestSpec(
                    key,
                    lambda _token: (_ for _ in ()).throw(RuntimeError("backend down")),
                ),
                cache,
                max_age_seconds=1,
            ).future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.ERROR)
            state = manager.state(key)
            self.assertEqual(state.status, ResourceStatus.STALE)
            self.assertEqual(state.value, {"games": ["cached"]})
            self.assertIsInstance(state.error, RuntimeError)
            self.assertEqual(state.cache_source, "memory")
        finally:
            manager.shutdown()


if __name__ == "__main__":
    unittest.main()
