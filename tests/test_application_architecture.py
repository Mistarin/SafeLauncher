"""Behavioral checks for runtime ownership, task delivery, and safe shutdown."""

from concurrent.futures import Future
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread, get_ident
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from database import GameDatabase
from core.operation_registry import OperationRegistry
from core.request_contracts import ResourceStatus
from ui.application_runtime import ApplicationRuntime
from ui.managed_task_controller import ManagedTaskController
from ui.shutdown_controller import ShutdownController, ShutdownState
from ui.main_window import MainWindow


class ApplicationArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_runtime_services_share_one_manager_and_projection(self):
        with TemporaryDirectory() as directory:
            db = GameDatabase(str(Path(directory) / "library.db"))
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            runtime = ApplicationRuntime(db, Mock(), Mock(), settings=settings)
            try:
                self.assertIs(runtime.library_service.state_store, runtime.library_state)
                self.assertIs(runtime.cloud_operation_service.request_manager, runtime.request_manager)
                self.assertIs(runtime.achievement_resource_service.request_manager, runtime.request_manager)
                self.assertIs(runtime.steam_resource_service.request_manager, runtime.request_manager)
                self.assertIs(runtime.artwork_resource_service.request_manager, runtime.request_manager)
                self.assertIs(runtime.worker_supervisor.parent(), runtime)
                runtime.set_network_gate(lambda: True)
                self.assertTrue(runtime.network_unavailable())
            finally:
                runtime.close_resources()
                runtime.close_resources()
                # Caller-owned databases remain usable after runtime disposal.
                self.assertEqual(db.get_all_games(), [])
                db.close()

    def test_partial_runtime_construction_closes_created_clients(self):
        artwork = Mock()
        with patch("ui.application_runtime.ArtworkClient", return_value=artwork), patch(
            "ui.application_runtime.SteamClient", side_effect=RuntimeError("construction failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "construction failed"):
                ApplicationRuntime(Mock(), Mock(), Mock())
        artwork.close.assert_called_once()

    def task_controller(self):
        manager = Mock()
        future = Future()
        handle = SimpleNamespace(request_id="request-1", future=future, cancel=Mock())
        manager.request.return_value = handle
        registry = OperationRegistry()
        controller = ManagedTaskController(manager, registry)
        self.addCleanup(controller.dispose)
        return controller, manager, registry, handle

    def test_task_completion_is_delivered_on_gui_thread(self):
        controller, manager, registry, handle = self.task_controller()
        deliveries = []
        owner_thread = get_ident()
        controller.start("test", lambda: 42, lambda result: deliveries.append((result, get_ident())))
        thread = Thread(target=lambda: handle.future.set_result(
            SimpleNamespace(status=ResourceStatus.READY, value=42)
        ))
        thread.start()
        thread.join()
        self.assertEqual(deliveries, [])
        self.app.processEvents()
        self.assertEqual(deliveries, [(42, owner_thread)])
        self.assertEqual(registry.all()[0].state, "completed")

    def test_disposed_task_cancels_and_suppresses_late_callback(self):
        controller, manager, registry, handle = self.task_controller()
        callback = Mock()
        controller.start("test", lambda: 42, callback)
        controller.dispose()
        handle.future.set_result(SimpleNamespace(status=ResourceStatus.READY, value=42))
        self.app.processEvents()
        callback.assert_not_called()
        handle.cancel.assert_called_once()
        self.assertEqual(registry.all()[0].state, "cancelled")
        self.assertIsNone(controller.start("late", lambda: None))
        self.assertEqual(manager.request.call_count, 1)

    def test_failed_task_submission_is_observable(self):
        controller, manager, registry, _handle = self.task_controller()
        manager.request.side_effect = RuntimeError("manager closed")
        self.assertIsNone(controller.start("test", lambda: 42))
        self.assertEqual(registry.all()[0].state, "failed")
        self.assertEqual(registry.all()[0].error, "manager closed")

    def shutdown_controller(self):
        hooks = SimpleNamespace(begin=Mock(), cancel=Mock(), pending=Mock(return_value=[]),
                                finish=Mock(), resume=Mock(), close=Mock(), show=Mock())
        now = [0.0]
        deferred = []
        controller = ShutdownController(
            begin=hooks.begin, cancel_work=hooks.cancel, pending=hooks.pending,
            finish=hooks.finish, resume=hooks.resume, request_close=hooks.close,
            show_waiting=hooks.show, clock=lambda: now[0],
            defer=lambda delay, callback: deferred.append(callback),
        )
        return controller, hooks, now, deferred

    def test_shutdown_drains_before_disposal_and_disposes_once(self):
        controller, hooks, _now, deferred = self.shutdown_controller()
        hooks.pending.return_value = ["save rollback"]
        self.assertFalse(controller.request())
        hooks.finish.assert_not_called()
        self.assertEqual(controller.state, ShutdownState.DRAINING)
        deferred[0]()
        hooks.close.assert_called_once()
        hooks.pending.return_value = []
        self.assertTrue(controller.request())
        self.assertTrue(controller.request())
        hooks.begin.assert_called_once()
        hooks.finish.assert_called_once()

    def test_shutdown_timeout_resumes_and_invalidates_queued_close(self):
        controller, hooks, now, deferred = self.shutdown_controller()
        hooks.pending.return_value = ["slow transfer"]
        self.assertFalse(controller.request())
        old_retry = deferred[0]
        now[0] = 13.0
        self.assertFalse(controller.request())
        self.assertEqual(controller.state, ShutdownState.CANCELLED)
        hooks.resume.assert_called_once()
        hooks.finish.assert_not_called()
        # A stale retry cannot close a resumed window or a later shutdown.
        controller.request()
        old_retry()
        hooks.close.assert_not_called()

    def test_user_abort_preserves_runtime(self):
        controller, hooks, _now, deferred = self.shutdown_controller()
        hooks.pending.return_value = ["worker"]
        controller.request()
        controller.abort()
        controller.abort()
        hooks.resume.assert_called_once()
        hooks.finish.assert_not_called()
        deferred[0]()
        hooks.close.assert_not_called()

    def test_real_window_close_waits_for_loader_and_can_resume(self):
        with TemporaryDirectory() as directory:
            db = GameDatabase(str(Path(directory) / "library.db"))
            entered, release = Event(), Event()
            with patch.object(MainWindow, "_on_sync_sandbox"), patch.object(MainWindow, "_setup_tray_icon"):
                window = MainWindow(db, Mock(), Mock())
            try:
                def work():
                    entered.set()
                    release.wait(3)
                    return "finished"
                handle = window.start_background_task("shutdown-integration", work, allow_offline=True)
                self.assertTrue(entered.wait(1))
                window.close()
                self.assertEqual(window.shutdown_controller.state, ShutdownState.DRAINING)
                self.assertFalse(window.runtime._closed)
                window._abort_shutdown()
                self.assertEqual(window.shutdown_controller.state, ShutdownState.CANCELLED)
                self.assertFalse(window._closing)
                self.assertFalse(window.request_manager._closed)
                release.set()
                handle.future.result(timeout=2)
                # Drain queued completions; the loader's cancellation cannot
                # accidentally close a window the user chose to keep open.
                self.app.processEvents()
                self.assertFalse(window.runtime._closed)
            finally:
                release.set()
                window.request_manager.cancel_matching(lambda _spec: True)
                window.runtime.close_resources()
                window.close()
                window.deleteLater()
                self.app.processEvents()
                db.close()


if __name__ == "__main__":
    unittest.main()
