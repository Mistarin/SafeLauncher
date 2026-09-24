"""Focused behavior tests for the Phase 1 request manager."""

from __future__ import annotations

import threading
import time
import unittest

from core.request_contracts import (
    RequestKey,
    RequestPriority,
    RequestSpec,
    RequestTimeout,
    ResourceStatus,
    RetryPolicy,
)
from core.request_manager import RequestManager, ResourceManager
from core.resource_cache import ResourceCache


class RequestManagerTests(unittest.TestCase):
    def test_resource_manager_is_the_public_alias(self):
        self.assertIs(ResourceManager, RequestManager)
    def test_identical_active_requests_are_deduplicated(self):
        manager = RequestManager(max_workers=1)
        try:
            calls = []
            key = RequestKey("data", "same")
            first = manager.request(key, lambda _token: calls.append(1) or "value")
            second = manager.request(key, lambda _token: calls.append(2) or "other")
            self.assertIs(first.future, second.future)
            self.assertEqual(first.future.result(timeout=2).value, "value")
            self.assertEqual(calls, [1])
        finally:
            manager.shutdown()

    def test_priority_is_applied_to_queued_work(self):
        manager = RequestManager(max_workers=1)
        started = threading.Event()
        release = threading.Event()
        order = []
        try:
            manager.request(
                RequestKey("data", "blocker"),
                lambda _token: (started.set(), release.wait(2), order.append("blocker"))[2],
            )
            self.assertTrue(started.wait(2))
            low = manager.request(
                RequestKey("data", "low"), lambda _token: order.append("low"), priority=RequestPriority.BACKGROUND
            )
            high = manager.request(
                RequestKey("data", "high"), lambda _token: order.append("high"), priority=RequestPriority.CRITICAL
            )
            release.set()
            high.future.result(timeout=2)
            low.future.result(timeout=2)
            self.assertEqual(order, ["blocker", "high", "low"])
        finally:
            release.set()
            manager.shutdown()

    def test_retries_failures_with_backoff(self):
        sleeps = []
        manager = RequestManager(max_workers=1, sleep=sleeps.append, random_value=lambda: 0.5)
        try:
            calls = [0]

            def loader(_token):
                calls[0] += 1
                if calls[0] < 3:
                    raise RuntimeError("temporary")
                return "ok"

            result = manager.request(
                RequestKey("data", "retry"), loader,
                retry_policy=RetryPolicy(
                    max_attempts=3,
                    base_delay_seconds=1,
                    max_delay_seconds=2,
                    retry_if=lambda _error: True,
                ),
            ).future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.READY)
            self.assertEqual(calls[0], 3)
            self.assertEqual(sleeps, [1, 2])
        finally:
            manager.shutdown()

    def test_offline_requests_do_not_call_loader(self):
        manager = RequestManager(max_workers=1, offline_check=lambda: True)
        try:
            called = []
            result = manager.request(RequestKey("data", "offline"), lambda _token: called.append(True)).future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.OFFLINE)
            self.assertEqual(called, [])
        finally:
            manager.shutdown()

    def test_permanent_loader_errors_are_not_retried_by_default(self):
        manager = RequestManager(max_workers=1)
        try:
            calls = []

            def loader(_token):
                calls.append(True)
                raise ValueError("invalid response")

            result = manager.request(RequestKey("data", "permanent"), loader).future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.ERROR)
            self.assertEqual(len(calls), 1)
            self.assertEqual(manager.metrics()["retries"], 0)
        finally:
            manager.shutdown()

    def test_http_authentication_failure_has_explicit_resource_state(self):
        class AuthFailure(RuntimeError):
            status_code = 401

        manager = RequestManager(max_workers=1)
        try:
            result = manager.request(
                RequestKey("profile", "auth"),
                lambda _token: (_ for _ in ()).throw(AuthFailure("expired")),
            ).future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.AUTHENTICATION_REQUIRED)
            self.assertEqual(result.error_category, "authentication_required")
        finally:
            manager.shutdown()

    def test_stale_cache_survives_unavailable_refresh_with_category(self):
        class MissingService(RuntimeError):
            status_code = 404

        cache = ResourceCache()
        manager = RequestManager(max_workers=1, cache=cache)
        try:
            key = RequestKey("profile", "stale")
            cache.put(key, {"name": "cached"}, stored_at=0)
            result = manager.request_cached(
                key,
                lambda _token: (_ for _ in ()).throw(MissingService("gone")),
                max_age_seconds=0,
            ).future.result(timeout=2)
            # The future reports the transport outcome; subscribers/state use
            # the stale projection so the UI keeps usable cached data.
            self.assertEqual(result.status, ResourceStatus.UNAVAILABLE)
            self.assertEqual(manager.state(key).status, ResourceStatus.STALE)
            self.assertEqual(manager.state(key).value, {"name": "cached"})
            self.assertEqual(manager.state(key).error_category, "unavailable")
        finally:
            manager.shutdown()

    def test_state_reports_loading_then_ready(self):
        manager = RequestManager(max_workers=1)
        try:
            key = RequestKey("data", "state")
            self.assertEqual(manager.state(key).status, ResourceStatus.IDLE)
            handle = manager.request(key, lambda _token: "value")
            result = handle.future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.READY)
            self.assertEqual(manager.state(key).value, "value")
        finally:
            manager.shutdown()

    def test_batch_progress_reports_each_result(self):
        manager = RequestManager(max_workers=2)
        try:
            progress = []
            completed_batches = []
            handles = manager.request_many(
                [
                    RequestSpec(RequestKey("data", "one"), lambda _token: 1),
                    RequestSpec(RequestKey("data", "two"), lambda _token: 2),
                ],
                progress=lambda completed, total, result: progress.append((completed, total, result.value)),
                on_complete=lambda results: completed_batches.append(results),
            )
            for handle in handles:
                handle.future.result(timeout=2)
            self.assertEqual(sorted(progress), [(1, 2, 1), (2, 2, 2)])
            self.assertEqual(len(completed_batches), 1)
            self.assertEqual(
                [result.value for result in completed_batches[0]],
                [1, 2],
            )
        finally:
            manager.shutdown()

    def test_empty_batch_completes_without_submitting_work(self):
        manager = RequestManager(max_workers=1)
        try:
            completed = []
            self.assertEqual(
                manager.request_many([], on_complete=completed.append),
                [],
            )
            self.assertEqual(completed, [[]])
            self.assertEqual(manager.metrics()["submitted"], 0)
        finally:
            manager.shutdown()

    def test_cached_batch_uses_cache_and_reports_progress(self):
        cache = ResourceCache()
        manager = RequestManager(max_workers=2, cache=cache)
        try:
            keys = [RequestKey("data", "cached-one"), RequestKey("data", "cached-two")]
            cache.put(keys[0], "from-cache")
            progress = []
            handles = manager.request_many_cached(
                keys,
                lambda key, _token: key.identity,
                max_age_seconds=60,
                progress=lambda completed, total, result: progress.append(
                    (completed, total, result.value)
                ),
            )
            for handle in handles:
                handle.future.result(timeout=2)
            self.assertEqual(handles[0].future.result().value, "from-cache")
            self.assertEqual(handles[1].future.result().value, "cached-two")
            self.assertEqual([item[0] for item in progress], [1, 2])
            self.assertEqual(sorted(item[2] for item in progress), ["cached-two", "from-cache"])
        finally:
            manager.shutdown()

    def test_forced_cached_request_bypasses_fresh_entry(self):
        cache = ResourceCache()
        manager = RequestManager(max_workers=1, cache=cache)
        try:
            key = RequestKey("data", "forced")
            cache.put(key, "old")
            calls = []
            result = manager.request_cached(
                key,
                lambda _token: calls.append(True) or "new",
                max_age_seconds=60,
                force_network=True,
            ).future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.READY)
            self.assertEqual(result.value, "new")
            self.assertFalse(result.from_cache)
            self.assertEqual(calls, [True])
        finally:
            manager.shutdown()

    def test_cached_request_codec_round_trips_disk_values_and_rejects_invalid_results(self):
        cache = ResourceCache()
        manager = RequestManager(max_workers=1, cache=cache)
        try:
            key = RequestKey("typed", "round-trip")
            first = manager.cached_request(
                RequestSpec(key, lambda _token: {"number": 7}),
                cache,
                max_age_seconds=60,
                cache_validator=lambda value: isinstance(value, dict) and value.get("number") == 7,
                cache_encoder=lambda value: {"encoded": value["number"]},
                cache_decoder=lambda value: {"number": value["encoded"]},
            ).future.result(timeout=2)
            self.assertEqual(first.value, {"number": 7})
            self.assertEqual(cache.get(key).value, {"encoded": 7})

            second = manager.cached_request(
                RequestSpec(key, lambda _token: {"number": 99}),
                cache,
                max_age_seconds=60,
                cache_validator=lambda value: isinstance(value, dict) and value.get("number") == 7,
                cache_encoder=lambda value: {"encoded": value["number"]},
                cache_decoder=lambda value: {"number": value["encoded"]},
            ).future.result(timeout=2)
            self.assertTrue(second.from_cache)
            self.assertEqual(second.value, {"number": 7})

            invalid_key = RequestKey("typed", "invalid")
            invalid = manager.cached_request(
                RequestSpec(invalid_key, lambda _token: ""),
                cache,
                max_age_seconds=60,
                cache_validator=bool,
            ).future.result(timeout=2)
            self.assertEqual(invalid.value, "")
            self.assertIsNone(cache.get(invalid_key))
        finally:
            manager.shutdown()

    def test_key_batch_api_and_invalidate(self):
        manager = RequestManager(max_workers=1)
        try:
            keys = [RequestKey("data", "one"), RequestKey("data", "two")]
            handles = manager.request_many(keys, lambda key, _token: key.identity.upper())
            self.assertEqual(
                [handle.future.result(timeout=2).value for handle in handles],
                ["ONE", "TWO"],
            )
            self.assertTrue(manager.invalidate(keys[0]))
            self.assertEqual(manager.state(keys[0]).status, ResourceStatus.IDLE)
            self.assertEqual(manager.metrics()["invalidated"], 1)
            self.assertFalse(manager.invalidate(RequestKey("data", "missing")))
        finally:
            manager.shutdown()

    def test_projection_maps_shared_source_and_deduplicates_consumers(self):
        manager = RequestManager(max_workers=1)
        try:
            source = manager.request(RequestKey("source", "account"), lambda _token: {"value": 7})
            first = manager.project(
                source,
                RequestKey("projection", "one"),
                lambda value: value["value"] * 2,
            )
            second = manager.project(
                source,
                RequestKey("projection", "one"),
                lambda value: value["value"] * 3,
            )
            result = first.future.result(timeout=2)
            self.assertEqual(first.request_id, second.request_id)
            self.assertEqual(result.status, ResourceStatus.READY)
            self.assertEqual(result.value, 14)
            self.assertEqual(manager.state(first.key).value, 14)
        finally:
            manager.shutdown()

    def test_projection_mapper_failure_is_a_managed_error(self):
        manager = RequestManager(max_workers=1)
        try:
            source = manager.request(RequestKey("source", "mapper-error"), lambda _token: 7)
            projected = manager.project(
                source,
                RequestKey("projection", "mapper-error"),
                lambda _value: (_ for _ in ()).throw(ValueError("bad projection")),
            )
            result = projected.future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.ERROR)
            self.assertIsInstance(result.error, ValueError)
        finally:
            manager.shutdown()

    def test_cancelling_projection_does_not_cancel_source(self):
        manager = RequestManager(max_workers=1)
        try:
            source = manager.request(RequestKey("source", "projection-cancel"), lambda _token: "ok")
            projected = manager.project(
                source,
                RequestKey("projection", "projection-cancel"),
                lambda value: value,
            )
            self.assertTrue(projected.cancel())
            self.assertEqual(projected.future.result(timeout=2).status, ResourceStatus.CANCELLED)
            self.assertEqual(source.future.result(timeout=2).status, ResourceStatus.READY)
        finally:
            manager.shutdown()

    def test_manager_cancel_detaches_projection_for_resource_bindings(self):
        manager = RequestManager(max_workers=1)
        try:
            source = manager.request(RequestKey("source", "binding-cancel"), lambda _token: "ok")
            projected = manager.project(
                source,
                RequestKey("projection", "binding-cancel"),
                lambda value: value,
            )
            self.assertTrue(manager.cancel(projected.key, projected.generation))
            self.assertEqual(projected.future.result(timeout=2).status, ResourceStatus.CANCELLED)
            self.assertEqual(source.future.result(timeout=2).status, ResourceStatus.READY)
        finally:
            manager.shutdown()

    def test_invalidating_source_retires_all_projections(self):
        manager = RequestManager(max_workers=1)
        try:
            source = manager.request(RequestKey("source", "invalidate-projections"), lambda _token: "ok")
            first = manager.project(
                source,
                RequestKey("projection", "invalidate-one"),
                lambda value: value,
            )
            second = manager.project(
                source,
                RequestKey("projection", "invalidate-two"),
                lambda value: value,
            )
            self.assertTrue(manager.invalidate(source.key))
            self.assertEqual(first.future.result(timeout=2).status, ResourceStatus.CANCELLED)
            self.assertEqual(second.future.result(timeout=2).status, ResourceStatus.CANCELLED)
        finally:
            manager.shutdown()

    def test_manager_invalidate_evicts_shared_cache(self):
        cache = ResourceCache()
        manager = RequestManager(max_workers=1, cache=cache)
        try:
            key = RequestKey("data", "cached")
            cache.put(key, "old")
            manager.request_cached(key, lambda _token: "new", max_age_seconds=60)
            self.assertTrue(manager.invalidate(key))
            self.assertIsNone(cache.get(key))
        finally:
            manager.shutdown()

    def test_invalidate_allows_default_generation_refresh(self):
        manager = RequestManager(max_workers=2)
        started = threading.Event()
        release = threading.Event()
        key = RequestKey("data", "invalidate-refresh")
        try:
            old = manager.request(
                key,
                lambda token: (
                    started.set(),
                    release.wait(2),
                    token.raise_if_cancelled(),
                    "old",
                )[-1],
            )
            self.assertTrue(started.wait(2))
            self.assertTrue(manager.invalidate(key))

            refreshed = manager.request(key, lambda _token: "new")
            self.assertEqual(refreshed.future.result(timeout=2).value, "new")
            release.set()
            self.assertEqual(old.future.result(timeout=2).status, ResourceStatus.CANCELLED)
            self.assertEqual(manager.state(key).value, "new")
            self.assertGreater(refreshed.generation, old.generation)
        finally:
            release.set()
            manager.shutdown()

    def test_cache_metrics_distinguish_fresh_hit_and_miss(self):
        cache = ResourceCache()
        manager = RequestManager(max_workers=1, cache=cache)
        try:
            cached_key = RequestKey("data", "metric-cache")
            missing_key = RequestKey("data", "metric-miss")
            cache.put(cached_key, "cached")
            self.assertTrue(
                manager.request_cached(
                    cached_key, lambda _token: "network", max_age_seconds=60
                ).future.result(timeout=2).from_cache
            )
            self.assertEqual(
                manager.request_cached(
                    missing_key, lambda _token: "loaded", max_age_seconds=60
                ).future.result(timeout=2).value,
                "loaded",
            )
            metrics = manager.metrics()
            self.assertEqual(metrics["cache_hits"], 1)
            self.assertEqual(metrics["cache_memory_hits"], 1)
            self.assertEqual(metrics["cache_misses"], 1)
            self.assertEqual(metrics["cache_hit_rate"], 0.5)
        finally:
            manager.shutdown()

    def test_metrics_capture_deduplication_and_completion(self):
        manager = RequestManager(max_workers=1)
        try:
            key = RequestKey("data", "metrics")
            first = manager.request(key, lambda _token: "ok")
            second = manager.request(key, lambda _token: "ignored")
            first.future.result(timeout=2)
            second.future.result(timeout=2)
            metrics = manager.metrics()
            self.assertEqual(metrics["submitted"], 1)
            self.assertEqual(metrics["deduplicated"], 1)
            self.assertEqual(metrics["completed"], 1)
            self.assertGreaterEqual(metrics["duration_seconds_total"], 0)
            self.assertGreaterEqual(metrics["duration_seconds_max"], 0)
            self.assertEqual(metrics["active_peak"], 1)
        finally:
            manager.shutdown()

    def test_cooperative_request_timeout_is_reported(self):
        manager = RequestManager(max_workers=1)
        try:
            calls = []

            def loader(token):
                time.sleep(0.01)
                calls.append(True)
                token.raise_if_cancelled()

            result = manager.request(
                RequestKey("data", "timeout"),
                loader,
                retry_policy=RetryPolicy(max_attempts=1),
                timeout_seconds=0.001,
            ).future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.ERROR)
            self.assertIsInstance(result.error, RequestTimeout)
            self.assertEqual(calls, [True])
            self.assertEqual(manager.metrics()["retries"], 0)
        finally:
            manager.shutdown()

    def test_timeout_budget_starts_when_queued_request_begins(self):
        manager = RequestManager(max_workers=1)
        blocker_started = threading.Event()
        release_blocker = threading.Event()
        calls = []
        try:
            blocker = manager.request(
                RequestKey("data", "timeout-queue-blocker"),
                lambda _token: (
                    blocker_started.set(),
                    release_blocker.wait(2),
                    "blocker",
                )[2],
            )
            self.assertTrue(blocker_started.wait(2))

            queued = manager.request(
                RequestKey("data", "timeout-queue"),
                lambda _token: calls.append(True) or "ready",
                retry_policy=RetryPolicy(max_attempts=1),
                timeout_seconds=0.05,
            )
            time.sleep(0.1)
            release_blocker.set()

            queued_result = queued.future.result(timeout=2)
            self.assertEqual(queued_result.status, ResourceStatus.READY)
            self.assertEqual(queued_result.value, "ready")
            self.assertEqual(blocker.future.result(timeout=2).value, "blocker")
            self.assertEqual(calls, [True])
            self.assertGreaterEqual(manager.metrics()["queue_wait_seconds_max"], 0.1)
        finally:
            release_blocker.set()
            manager.shutdown()

    def test_queued_cancellation_still_prevents_loader_execution(self):
        manager = RequestManager(max_workers=1)
        blocker_started = threading.Event()
        release_blocker = threading.Event()
        calls = []
        try:
            blocker = manager.request(
                RequestKey("data", "cancel-queue-blocker"),
                lambda _token: (
                    blocker_started.set(),
                    release_blocker.wait(2),
                    "blocker",
                )[2],
            )
            self.assertTrue(blocker_started.wait(2))

            queued = manager.request(
                RequestKey("data", "cancel-queue"),
                lambda _token: calls.append(True),
            )
            self.assertTrue(queued.cancel())
            release_blocker.set()

            self.assertEqual(queued.future.result(timeout=2).status, ResourceStatus.CANCELLED)
            self.assertEqual(blocker.future.result(timeout=2).value, "blocker")
            self.assertEqual(calls, [])
        finally:
            release_blocker.set()
            manager.shutdown()

    def test_shutdown_cancels_active_cooperative_request(self):
        manager = RequestManager(max_workers=1)
        gate = threading.Event()

        def loader(token):
            while not gate.wait(0.005):
                token.raise_if_cancelled()
            token.raise_if_cancelled()

        handle = manager.request(RequestKey("data", "shutdown"), loader)
        manager.shutdown(wait=True)
        self.assertEqual(handle.future.result(timeout=2).status, ResourceStatus.CANCELLED)

    def test_cancel_interrupts_retry_backoff(self):
        manager = RequestManager(max_workers=1)
        try:
            handle = manager.request(
                RequestKey("data", "backoff-cancel"),
                lambda _token: (_ for _ in ()).throw(RuntimeError("temporary")),
                retry_policy=RetryPolicy(
                    max_attempts=3,
                    base_delay_seconds=5,
                    max_delay_seconds=5,
                    retry_if=lambda _error: True,
                ),
            )
            time.sleep(0.05)
            self.assertTrue(handle.cancel())
            self.assertEqual(handle.future.result(timeout=1).status, ResourceStatus.CANCELLED)
        finally:
            manager.shutdown()

    def test_cancellation_and_newer_generation_suppress_stale_listener(self):
        manager = RequestManager(max_workers=1)
        gate = threading.Event()
        seen = []
        key = RequestKey("data", "generation")
        try:
            manager.subscribe(key, seen.append)
            old = manager.submit(RequestSpec(key, lambda token: (gate.wait(2), token.raise_if_cancelled())[0], generation=1))
            newer = manager.submit(RequestSpec(key, lambda _token: "new", generation=2))
            gate.set()
            self.assertEqual(newer.future.result(timeout=2).status, ResourceStatus.READY)
            self.assertEqual(old.future.result(timeout=2).status, ResourceStatus.CANCELLED)
            self.assertEqual(
                [(item.generation, item.status) for item in seen],
                [
                    (1, ResourceStatus.LOADING),
                    (2, ResourceStatus.LOADING),
                    (2, ResourceStatus.READY),
                ],
            )
        finally:
            gate.set()
            manager.shutdown()

    def test_late_superseded_result_cannot_overwrite_newer_state(self):
        manager = RequestManager(max_workers=2)
        started_old = threading.Event()
        release_old = threading.Event()
        key = RequestKey("data", "late-generation")
        try:
            old = manager.submit(RequestSpec(
                key,
                lambda _token: (started_old.set(), release_old.wait(2), "old")[2],
                generation=1,
            ))
            self.assertTrue(started_old.wait(2))
            newer = manager.submit(RequestSpec(key, lambda _token: "new", generation=2))
            self.assertEqual(newer.future.result(timeout=2).value, "new")
            release_old.set()
            self.assertEqual(old.future.result(timeout=2).status, ResourceStatus.CANCELLED)
            self.assertEqual(manager.state(key).value, "new")
            self.assertEqual(manager.state(key).generation, 2)
        finally:
            release_old.set()
            manager.shutdown()


if __name__ == "__main__":
    unittest.main()
