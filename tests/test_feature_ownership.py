"""Controller contracts: local observation, provenance, and isolated effects."""

from concurrent.futures import Future
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtWidgets import QApplication

from core.library_steam_metadata_coordinator import LibrarySteamMetadataCoordinator
from core.request_contracts import RequestKey, RequestPriority, ResourceStatus
from core.request_manager import RequestManager
from core.steam_resource_service import SteamResourceService
from ui.profile_controller import ProfileController
from ui.session_feature_controller import SessionFeatureController
from ui.steam_metadata_controller import SteamMetadataController


class FeatureOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def steam_controller(self):
        service = SteamResourceService(Mock(), client=Mock())
        coordinator = LibrarySteamMetadataCoordinator(service)
        build, failed, description = Mock(), Mock(), Mock()
        controller = SteamMetadataController(
            manager=service.request_manager, service=service, coordinator=coordinator,
            library_service=Mock(), on_build=build, on_failure=failed, on_offline=Mock(),
            on_tags=Mock(), on_network_status=Mock(), refresh_library=Mock(),
            on_description=description, accepts_work=lambda: True,
        )
        self.addCleanup(controller.dispose)
        return controller, coordinator, build, failed, description

    def test_shared_appid_compares_against_each_installed_build(self):
        controller, coordinator, build, failed, _description = self.steam_controller()
        first = coordinator.prepare_build(1, "480", "100", 10, priority=RequestPriority.NORMAL)
        coordinator.prepare_build(2, "480", "200", 20, priority=RequestPriority.NORMAL)
        controller.handle_build_state(first.key, SimpleNamespace(
            status=ResourceStatus.READY, value=("200", 20), from_cache=False, updated_at=123.0,
        ))
        self.assertEqual(build.call_count, 2)
        self.assertEqual(build.call_args_list[0].args, (1, "200", 20, True))
        self.assertEqual(build.call_args_list[1].args, (2, "200", 20, False))
        failed.assert_not_called()

    def test_cached_build_keeps_timestamp_and_provenance(self):
        controller, coordinator, build, _failed, _description = self.steam_controller()
        plan = coordinator.prepare_build(1, "480", "100", 10, priority=RequestPriority.NORMAL)
        controller.handle_build_state(plan.key, SimpleNamespace(
            status=ResourceStatus.STALE, value=("200", 20), from_cache=True, updated_at=123.0,
        ))
        self.assertEqual(build.call_args.kwargs, {"source": "cached", "checked_at": 123.0})

    def test_unknown_installed_build_is_not_silently_marked_current(self):
        controller, coordinator, build, failed, _description = self.steam_controller()
        plan = coordinator.prepare_build(1, "480", "", 0, priority=RequestPriority.NORMAL)
        controller.handle_build_state(plan.key, SimpleNamespace(
            status=ResourceStatus.READY, value=("200", 20), from_cache=False, updated_at=123.0,
        ))
        build.assert_not_called()
        self.assertIn("No installed Steam build reference", failed.call_args.args[1])

    def test_old_description_result_cannot_overwrite_new_selection(self):
        controller, _coordinator, _build, _failed, description = self.steam_controller()
        controller._description_generation = 2
        result = SimpleNamespace(status=ResourceStatus.READY, value={"short_description": "Old"})
        controller.handle_description(1, 1, result)
        description.assert_not_called()
        controller.handle_description(2, 2, result)
        description.assert_called_once_with(2, "Old", [])

    def test_disposed_steam_controller_suppresses_late_results(self):
        controller, coordinator, build, _failed, description = self.steam_controller()
        plan = coordinator.prepare_build(1, "480", "100", 10, priority=RequestPriority.NORMAL)
        controller.dispose()
        result = SimpleNamespace(status=ResourceStatus.READY, value=("200", 20))
        controller.handle_build_state(plan.key, result)
        controller.handle_description(1, 0, result)
        build.assert_not_called()
        description.assert_not_called()

    def test_closing_steam_subscriptions_is_reversible_after_offline_transition(self):
        controller, coordinator, _build, _failed, _description = self.steam_controller()
        controller.close_bindings()
        self.assertTrue(controller.request_build(1, '480', '100', 10))
        self.assertIsNotNone(controller.coordinator.binding('build', controller.service.build_key('480')))
        self.assertFalse(controller._closed)

    def test_linked_description_reuses_app_details_resource(self):
        manager = Mock()
        manager.cache = None
        client = Mock()
        service = SteamResourceService(manager, client=client)
        service.request_description("Example", "480")
        spec = manager.submit.call_args.args[0]
        self.assertEqual(spec.key, service.app_details_key("480"))
        client.search.assert_not_called()

    def profile_controller(self):
        page = Mock()
        page.isVisible.return_value = True
        page._mode = "owner"
        metadata = Mock()
        future = Future()
        metadata.request_profile.return_value = SimpleNamespace(future=future, cancel=Mock())
        refresh = Mock()
        controller = ProfileController(
            page=page, resources=Mock(), settings=Mock(), auth=Mock(), manager=Mock(),
            metadata_service=metadata, db_path="library.db", worker_registry=Mock(),
            show_profile=Mock(), open_owner=Mock(), refresh_library=refresh,
            refresh_identity=Mock(), network_allowed=lambda: True, accepts_work=lambda: True,
        )
        self.addCleanup(controller.dispose)
        return controller, page, metadata, future, refresh

    def test_private_profile_change_never_publishes_public_data(self):
        controller, page, metadata, _future, _refresh = self.profile_controller()
        controller.profile_changed(private_only=True)
        metadata.request_profile.assert_called_once()
        controller.resources.fetch_public.assert_not_called()
        page.mark_local_data_changed.assert_not_called()

    def test_public_result_is_generation_scoped(self):
        controller, page, _metadata, _future, _refresh = self.profile_controller()
        controller._public_generation = 3
        result = SimpleNamespace(status=ResourceStatus.READY, value={"handle": "example"})
        controller.handle_public_result(2, result)
        page.show_public.assert_not_called()
        controller.handle_public_result(3, result)
        page.show_public.assert_called_once_with(result.value)

    def test_private_sync_after_disposal_does_not_touch_views(self):
        controller, page, _metadata, future, refresh = self.profile_controller()
        controller.sync_private()
        controller.dispose()
        future.set_result(SimpleNamespace(status=ResourceStatus.READY, value=True))
        self.app.processEvents()
        refresh.assert_not_called()
        page.show_owner.assert_not_called()

    def session_features(self, *, online=True):
        rpc, recorder, achievements, exit_sync = Mock(), Mock(), Mock(), Mock()
        recorder.is_running.return_value = False
        config = SimpleNamespace(enabled=True, mode="auto_game")
        future = Future()
        exit_sync.request.return_value = SimpleNamespace(future=future)
        controller = SessionFeatureController(
            achievements=achievements, library_service=Mock(), metadata_service=Mock(),
            exit_sync_service=exit_sync,
            games_provider=lambda: {7: (7, "Example", "/game", "game.exe", "umu", "", "480")},
            db_path="library.db", running_games=lambda: set(), rpc_provider=lambda: rpc,
            recorder_config=lambda: config, recorder_provider=lambda: recorder, notify=Mock(),
            network_allowed=lambda: online, on_playtime_changed=Mock(), on_exit_result=Mock(),
        )
        return controller, rpc, recorder, achievements, exit_sync

    def test_presence_failure_does_not_skip_recorder_or_watcher_start(self):
        controller, rpc, recorder, achievements, _sync = self.session_features()
        rpc.set_activity.side_effect = RuntimeError("RPC unavailable")
        with self.assertLogs("SafeLauncher.SessionFeatures", level="ERROR"):
            controller.started({"game_id": 7, "game_name": "Example", "steam_id": "480", "path": "/game"})
        recorder.start_recording.assert_called_once()
        achievements.start_watching.assert_called_once_with(7, "480", "", "/game")

    def test_recorder_cleanup_failure_does_not_skip_exit_sync(self):
        controller, _rpc, recorder, achievements, sync = self.session_features()
        recorder.is_running.return_value = True
        recorder.stop_recording.side_effect = RuntimeError("recorder unavailable")
        with self.assertLogs("SafeLauncher.SessionFeatures", level="ERROR"):
            controller.finished(7)
        achievements.finish_session.assert_called_once_with(7)
        sync.request.assert_called_once()

    def test_offline_exit_reconciles_local_achievements_without_cloud_upload(self):
        controller, _rpc, _recorder, achievements, sync = self.session_features(online=False)
        controller.finished(7)
        achievements.finish_session.assert_called_once_with(7)
        sync.request.assert_not_called()

    def test_shutdown_barrier_includes_invalidated_loader(self):
        manager = RequestManager(max_workers=1)
        started, release = Event(), Event()
        def loader(token):
            started.set()
            release.wait(2)
            token.raise_if_cancelled()
        try:
            key = RequestKey("ownership-test", "slow-loader")
            handle = manager.request(key, loader)
            self.assertTrue(started.wait(1))
            manager.invalidate(key)
            self.assertGreater(manager.pending_work_count(), 0)
            release.set()
            handle.future.result(timeout=2)
        finally:
            release.set()
            manager.shutdown()
        self.assertEqual(manager.pending_work_count(), 0)


if __name__ == "__main__":
    unittest.main()
