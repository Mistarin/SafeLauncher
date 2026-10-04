"""Tests for managed cloud-status orchestration outside MainWindow."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from core.cloud_status_service import CloudStatusTarget
from ui.cloud_status_controller import CloudStatusController


class _StatusService:
    def __init__(self, context):
        self.context = context
        self.diff_callback = None

    def current_context(self):
        return self.context

    def plan_recheck(self, targets, _game_ids, *, reason=""):
        return SimpleNamespace(targets=tuple(targets), reason=reason)

    def request_changed_diff(self, targets, *, generation, on_complete, force):
        self.diff_callback = on_complete
        self.diff_request = (targets, generation, force)


class CloudStatusControllerTests(unittest.TestCase):
    def setUp(self):
        self.targets = [CloudStatusTarget(7, "Example", "/games/example", "480")]
        self.context = SimpleNamespace(
            generation=3,
            network_allowed=True,
            backend_active=True,
            authentication_configured=True,
        )
        self.service = _StatusService(self.context)
        self.coordinator = SimpleNamespace(
            generation=3,
            accepts=lambda generation: generation == 3,
        )
        self.mark_offline = Mock()
        self.mark_auth = Mock()
        self.on_status = Mock()
        self.on_changed = Mock()
        self.on_batch = Mock()
        self.controller = CloudStatusController(
            request_manager=object(),
            status_service=self.service,
            coordinator=self.coordinator,
            games_provider=lambda: [
                (7, "Example", "/games/example", "", "", "", "480")
            ],
            request_allowed=lambda: True,
            network_allowed=lambda: True,
            mark_offline=self.mark_offline,
            mark_auth_required=self.mark_auth,
            on_status_calculated=self.on_status,
            on_poll_changed=self.on_changed,
            on_batch_finished=self.on_batch,
        )
        self.controller.targets_snapshot = lambda: tuple(self.targets)
        self.controller.request_statuses = Mock()

    def test_full_scan_preserves_startup_cache_policy_and_batch_completion(self):
        self.controller.request_recheck(reason="startup", auto_sync=True)

        args, options = self.controller.request_statuses.call_args
        self.assertEqual(args[0], [(7, "Example", "/games/example", "480")])
        self.assertEqual(args[2], " (startup)")
        self.assertEqual(options["generation"], 3)
        self.assertFalse(options["force"])
        self.assertIsNotNone(options["on_batch_complete"])

    def test_explicit_recheck_forces_fresh_status(self):
        self.controller.request_recheck([7], reason="explicit")

        options = self.controller.request_statuses.call_args.kwargs
        self.assertTrue(options["force"])
        callback = self.controller.request_statuses.call_args.args[1]
        callback(7, "status", "local", "cloud")
        self.on_status.assert_called_once_with(
            7, "status", "local", "cloud", auto_sync=False
        )

    def test_offline_and_missing_auth_are_handled_without_requests(self):
        self.context.network_allowed = False
        self.controller.request_recheck()
        self.mark_offline.assert_called_once_with(None)
        self.controller.request_statuses.assert_not_called()

        self.context.network_allowed = True
        self.context.authentication_configured = False
        self.controller.request_recheck([7])
        self.mark_auth.assert_called_once_with([7])
        self.controller.request_statuses.assert_not_called()

    def test_empty_game_list_requests_listing_diff(self):
        changed = [CloudStatusTarget(7, "Example", "/games/example", "480")]
        self.controller.request_recheck([])

        targets, generation, force = self.service.diff_request
        self.assertEqual(targets, tuple(self.targets))
        self.assertEqual(generation, 3)
        self.assertTrue(force)
        self.service.diff_callback(changed)
        self.on_changed.assert_called_once_with([(7, "Example", "/games/example", "480")])


if __name__ == "__main__":
    unittest.main()
