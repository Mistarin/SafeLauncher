"""Tests for the composed UI achievement synchronization controller."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PyQt6.QtWidgets import QApplication

from core.achievement_resource_service import AchievementTarget
from core.library_achievement_coordinator import LibraryAchievementCoordinator
from core.request_contracts import RequestKey, RequestPriority, ResourceStatus
from core.request_manager import RequestManager
from ui.achievement_sync_controller import AchievementSyncController


class _ResourceService:
    def __init__(self):
        self.requests = []
        self.invalidated = []

    @staticmethod
    def key_for(target):
        return RequestKey("achievement-status", f"{target.app_id}:{target.game_id}", "v1")

    def request_many(self, targets, **kwargs):
        self.requests.append((targets, kwargs))

    def invalidate(self, target):
        self.invalidated.append(target)

    def request_status(self, target, **kwargs):
        self.requests.append(([target], kwargs))


class _StateStore:
    def __init__(self):
        self.pending_unlocks = {}
        self.statuses = {}

    def get_status(self, _game_id):
        return None

    def is_stale(self, _game_id, _seconds, now=None):
        return False

    def pending_for(self, game_id):
        return self.pending_unlocks.setdefault(game_id, {})

    def pop_pending(self, game_id):
        return self.pending_unlocks.pop(game_id, {})

    def set_status(self, game_id, status):
        self.statuses[game_id] = status

    def set_resolution(self, _game_id, _resolution):
        pass


def _game(game_id=7, app_id="480"):
    row = [None] * 13
    row[0], row[1], row[2], row[6], row[12] = game_id, "Example", "/games/example", app_id, "/prefix"
    return tuple(row)


class AchievementSyncControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.service = _ResourceService()
        self.coordinator = LibraryAchievementCoordinator(self.service)
        self.manager = object()
        self.status = Mock()
        self.batch = Mock()
        self.persistence = Mock()
        self.settings = Mock()
        self.state_store = _StateStore()
        self.save_cache = Mock()
        self.sync_metadata = Mock()
        self.profile_changed = Mock()
        self.controller = AchievementSyncController(
            request_manager=self.manager,
            resource_service=self.service,
            coordinator=self.coordinator,
            state_store=self.state_store,
            persistence_service=self.persistence,
            settings=self.settings,
            games_provider=lambda: [_game()],
            selected_game_provider=lambda: None,
            db_path_provider=lambda: "library.db",
            online_allowed=lambda: True,
            workers_provider=list,
            track_worker=Mock(),
            save_cache=self.save_cache,
            sync_launcher_metadata=self.sync_metadata,
            mark_profile_changed=self.profile_changed,
            refresh_inspector=Mock(),
            refresh_compact_page=Mock(),
            on_batch_finished=self.batch,
        )
        # Binding behavior is covered by ResourceBinding tests; scheduling
        # tests only need to observe the coordinator request contract.
        self.controller._ensure_binding = Mock()
        self.addCleanup(self.controller.dispose)

    def test_local_watcher_starts_offline_and_is_replaced_once(self):
        self.controller._online_allowed = lambda: False
        first, second = Mock(), Mock()
        with patch("core.achievement_watcher.AchievementWatcher", side_effect=[first, second]):
            self.controller.start_watching(7, "480", "/prefix", "/game")
            self.controller.start_watching(7, "480", "/prefix", "/game")
        first.start.assert_called_once()
        first.stop.assert_called_once()
        first.deleteLater.assert_called_once()
        self.assertIs(self.controller.watchers[7], second)

    def test_delayed_exit_read_finishes_local_observation_offline(self):
        self.controller._online_allowed = lambda: False
        watcher = Mock()
        with patch("core.achievement_watcher.AchievementWatcher", return_value=watcher):
            self.controller.start_watching(7, "480", "/prefix", "/game")
        self.controller.finish_session(7)
        watcher.check_updates.assert_called_once()
        self.controller._exit_timers[7].timeout.emit()
        self.assertEqual(watcher.check_updates.call_count, 2)
        self.assertNotIn(7, self.controller.watchers)
        watcher.stop.assert_called_once()

    def test_new_session_cancels_old_exit_timer(self):
        first, second = Mock(), Mock()
        with patch("core.achievement_watcher.AchievementWatcher", side_effect=[first, second]):
            self.controller.start_watching(7, "480", "/prefix", "/game")
            self.controller.finish_session(7)
            timer = self.controller._exit_timers[7]
            self.controller.start_watching(7, "480", "/prefix", "/game")
        self.assertFalse(timer.isActive())
        timer.timeout.emit()
        self.assertIs(self.controller.watchers[7], second)
        second.stop.assert_not_called()

    def test_full_library_request_is_deduplicated_while_in_flight(self):
        self.controller.request_recheck(tag="running")
        self.controller.request_recheck(tag="running")

        self.assertEqual(len(self.service.requests), 1)
        targets, options = self.service.requests[0]
        self.assertEqual(targets, [AchievementTarget(7, "480", "/games/example", "/prefix")])
        self.assertEqual(options["db_path"], "library.db")
        self.assertEqual(options["priority"], RequestPriority.CRITICAL)
        self.assertTrue(self.coordinator.batch_in_flight)

    def test_targeted_refresh_invalidates_then_requests_critical_priority(self):
        self.controller.request_recheck([7], tag="selection")

        self.assertEqual(len(self.service.invalidated), 1)
        self.assertEqual(self.service.requests[0][0], self.service.invalidated)
        self.assertEqual(self.service.requests[0][1]["priority"], RequestPriority.CRITICAL)

    def test_offline_policy_keeps_managed_local_achievement_resolution(self):
        self.controller._online_allowed = lambda: False

        self.controller.request_recheck()

        self.assertEqual(len(self.service.requests), 1)
        self.assertTrue(self.coordinator.batch_in_flight)

    def test_offline_policy_does_not_start_legacy_remote_worker(self):
        self.controller._online_allowed = lambda: False
        self.controller._request_manager = None
        self.controller.request_recheck()
        self.assertEqual(self.service.requests, [])

    def test_suspended_controller_does_not_schedule_work(self):
        self.controller.suspend()
        self.controller.request_recheck()
        self.assertEqual(self.service.requests, [])

    def test_managed_batch_completion_releases_lock_and_reports_totals(self):
        self.coordinator.set_batch_in_flight(True)
        result = SimpleNamespace(
            status=ResourceStatus.READY,
            value=(object(), 3, 5, 60.0, []),
        )

        self.controller.handle_managed_batch_done([result])

        self.assertFalse(self.coordinator.batch_in_flight)
        self.batch.assert_called_once_with(1, 3)

    def test_live_unlock_waits_for_schema_then_requests_targeted_resolution(self):
        self.persistence.schema.return_value = []

        self.controller.handle_unlock(7, "480", {"api_name": "WIN", "unlock_time": 12})

        self.assertEqual(self.state_store.pending_unlocks, {7: {"WIN": 12.0}})
        self.assertEqual(len(self.service.requests), 1)
        self.persistence.unlock.assert_not_called()

    def test_duplicate_live_unlock_does_not_repeat_side_effects(self):
        self.persistence.schema.return_value = [{"api_name": "WIN"}]
        self.persistence.unlock.return_value = SimpleNamespace(claimed=False)

        self.controller.handle_unlock(7, "480", {"api_name": "WIN", "unlock_time": 12})

        self.persistence.unlock.assert_called_once()
        self.save_cache.assert_not_called()
        self.sync_metadata.assert_not_called()
        self.profile_changed.assert_not_called()

    def test_real_binding_delivers_resolution_and_status(self):
        manager = RequestManager(max_workers=1)
        self.addCleanup(manager.shutdown)
        self.controller._request_manager = manager
        # Exercise the actual subscription wiring, not a direct handler call.
        del self.controller._ensure_binding
        target = AchievementTarget(7, '480', '/games/example', '/prefix')
        plan = self.coordinator.prepare(target)
        self.controller.ensure_binding(plan.key)
        resolution = SimpleNamespace(schema=[])
        handle = manager.request(plan.key, lambda token: (resolution, 1, 2, 50.0, []))
        handle.future.result(timeout=1)
        for _ in range(4):
            self.app.processEvents()
        self.persistence.persist_resolution.assert_called_once_with(7, '480', resolution)
        self.assertEqual(self.state_store.statuses[7], (1, 2, 50.0, []))

    def test_shutdown_suppresses_late_watcher_and_batch_side_effects(self):
        self.controller.suspend()
        self.controller.handle_unlock(7, '480', {'api_name': 'WIN'})
        self.controller.handle_state_refreshed(7, '480', {'WIN': 12})
        self.controller.handle_resolution_ready(7, '480', SimpleNamespace(schema=[]))
        self.controller.handle_status_calculated(7, 1, 2, 50.0, [])
        self.controller.handle_managed_batch_done([])
        self.persistence.unlock.assert_not_called()
        self.persistence.record_state.assert_not_called()
        self.persistence.persist_resolution.assert_not_called()
        self.batch.assert_not_called()
        self.save_cache.assert_not_called()
        self.assertEqual(self.state_store.statuses, {})


if __name__ == "__main__":
    unittest.main()
