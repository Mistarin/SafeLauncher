"""Tests for the Qt-free cloud status polling lifecycle."""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

from core.cloud_status_polling_service import CloudStatusPollingService
from core.cloud_status_service import CloudStatusTarget


class _Handle:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        return True


class _StatusService:
    def __init__(self):
        self.calls = []
        self.generation = 7
        self.context = SimpleNamespace(
            generation=self.generation,
            remote_requests_allowed=True,
        )
        self.handle = _Handle()

    def current_context(self):
        return self.context

    def request_changed_diff(self, targets, *, generation, on_complete):
        self.calls.append((tuple(targets), generation))
        on_complete(list(targets[:1]))
        return self.handle


class CloudStatusPollingServiceTests(unittest.TestCase):
    def test_poll_uses_thread_safe_target_snapshot_and_callback(self):
        service = _StatusService()
        changed = []
        polling = CloudStatusPollingService(
            service,
            interval_seconds=60,
            on_changed=changed.extend,
        )
        targets = [CloudStatusTarget(1, "One"), CloudStatusTarget(2, "Two")]
        polling.set_targets(targets)

        handle = polling.poll_once()

        self.assertIs(handle, service.handle)
        self.assertEqual(service.calls, [(tuple(targets), 7)])
        self.assertEqual([target.game_id for target in changed], [1])
        self.assertEqual(polling.targets(), tuple(targets))

    def test_context_change_discards_late_poll_result(self):
        service = _StatusService()
        changed = []
        callback_holder = {}

        def request_changed_diff(targets, *, generation, on_complete):
            callback_holder["complete"] = on_complete
            return service.handle

        service.request_changed_diff = request_changed_diff
        polling = CloudStatusPollingService(
            service,
            interval_seconds=60,
            on_changed=changed.extend,
        )
        polling.set_targets([CloudStatusTarget(1, "One")])
        polling.poll_once()
        service.context = SimpleNamespace(
            generation=8,
            remote_requests_allowed=True,
        )
        callback_holder["complete"]([CloudStatusTarget(1, "One")])

        self.assertEqual(changed, [])

    def test_start_and_stop_cancel_periodic_loop(self):
        service = _StatusService()
        polling = CloudStatusPollingService(service, interval_seconds=0.02)
        polling.set_targets([CloudStatusTarget(1, "One")])

        self.assertTrue(polling.start())
        self.assertFalse(polling.start())
        time.sleep(0.05)
        polling.stop()

        self.assertFalse(polling.running)
        self.assertTrue(service.calls)
        self.assertTrue(service.handle.cancelled)


if __name__ == "__main__":
    unittest.main()
