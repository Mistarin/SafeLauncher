"""Phase 8 reliability and bounded-resource coverage."""

from __future__ import annotations

import threading
import time
import unittest

from core.request_contracts import RequestKey, RequestSpec, ResourceStatus
from core.request_manager import RequestManager


class RequestManagerReliabilityTests(unittest.TestCase):
    def test_large_batch_stays_within_worker_bound(self):
        manager = RequestManager(max_workers=3)
        active = 0
        peak = 0
        lock = threading.Lock()

        def load(token):
            nonlocal active, peak
            token.raise_if_cancelled()
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                time.sleep(0.003)
                token.raise_if_cancelled()
                return "ready"
            finally:
                with lock:
                    active -= 1

        try:
            keys = [RequestKey("large-library", str(index)) for index in range(48)]
            handles = manager.request_many(keys, lambda _key, token: load(token))
            results = [handle.future.result(timeout=5) for handle in handles]
            self.assertTrue(all(result.status == ResourceStatus.READY for result in results))
            self.assertLessEqual(peak, 3)
            self.assertGreaterEqual(manager.metrics()["active_peak"], 3)
            self.assertLessEqual(manager.metrics()["workers_peak"], 3)
            self.assertEqual(manager.metrics()["completed"], len(keys))
        finally:
            manager.shutdown()

    def test_duplicate_keys_in_a_batch_share_one_loader(self):
        manager = RequestManager(max_workers=2)
        calls = []
        key = RequestKey("duplicate-game", "same-app")

        try:
            handles = manager.request_many([
                RequestSpec(key, lambda _token: calls.append(True) or "value"),
                RequestSpec(key, lambda _token: calls.append(False) or "wrong"),
            ])
            results = [handle.future.result(timeout=2) for handle in handles]
            self.assertEqual([result.value for result in results], ["value", "value"])
            self.assertEqual(calls, [True])
            self.assertEqual(manager.metrics()["submitted"], 1)
            self.assertEqual(manager.metrics()["deduplicated"], 1)
        finally:
            manager.shutdown()

    def test_shutdown_cancels_queued_large_batch(self):
        manager = RequestManager(max_workers=1)
        started = threading.Event()
        release = threading.Event()

        def blocker(token):
            started.set()
            while not release.wait(0.005):
                token.raise_if_cancelled()
            token.raise_if_cancelled()

        try:
            first = manager.request(RequestKey("shutdown", "blocker"), blocker)
            self.assertTrue(started.wait(2))
            queued = [
                manager.request(RequestKey("shutdown", str(index)), lambda _token: "late")
                for index in range(12)
            ]
            manager.shutdown(wait=True)
            release.set()
            self.assertEqual(first.future.result(timeout=2).status, ResourceStatus.CANCELLED)
            self.assertTrue(all(handle.future.result(timeout=2).status == ResourceStatus.CANCELLED for handle in queued))
        finally:
            release.set()
            manager.shutdown()


if __name__ == "__main__":
    unittest.main()
