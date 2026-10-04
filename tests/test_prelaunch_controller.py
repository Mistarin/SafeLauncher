from concurrent.futures import Future
from types import SimpleNamespace
from threading import Thread
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from core.request_contracts import ResourceStatus
from ui.prelaunch_controller import PrelaunchController


class _Settings:
    def value(self, _key, default=None, type=None):
        return default

    def setValue(self, *_args):
        pass


class _CloudOperations:
    def __init__(self):
        self.request_count = 0
        self.cancelled = []
        self.future = Future()
        self.restore_future = Future()
        self.restore_calls = []

    def request_prelaunch_resolution(self, *_args, **_kwargs):
        self.request_count += 1
        return SimpleNamespace(request_id="prelaunch-1", future=self.future)

    def cancel(self, request_id):
        self.cancelled.append(request_id)
        return True

    def operation(self, _request_id):
        return None

    def request_restore(self, target, **kwargs):
        self.restore_calls.append((target, kwargs))
        return SimpleNamespace(request_id="restore-1", future=self.restore_future)


class PrelaunchControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_controller(self, *, network_allowed=True):
        cloud = _CloudOperations()
        continue_launch = Mock()
        show_toast = Mock()
        update_button = Mock()
        parent = self.app.activeWindow()
        controller = PrelaunchController(
            cloud,
            _Settings(),
            parent=parent,
            network_allowed=lambda: network_allowed,
            continue_launch=continue_launch,
            show_toast=show_toast,
            update_launch_button=update_button,
            open_cloud_center=Mock(),
            refresh_cloud_status=Mock(),
        )
        self.addCleanup(self._dispose_controller, controller)
        return controller, cloud, continue_launch, show_toast, update_button

    def _dispose_controller(self, controller):
        for game_id, state in list(controller._pending.items()):
            controller._finish({
                **state.get("ctx", {}),
                "game_id": game_id,
                "prelaunch_token": state.get("token"),
            })
        controller.deleteLater()
        self.app.processEvents()

    @staticmethod
    def context():
        return {
            "game_id": 7,
            "game_name": "Example",
            "path": "/game",
            "exe": "game.exe",
            "steam_id": "480",
            "sandbox": True,
            "env_vars": {},
            "selected_mode": "umu",
            "selected_proton": "",
        }

    def test_offline_launch_skips_cloud_and_continues_with_local_state(self):
        controller, cloud, continue_launch, toast, _button = self.make_controller(
            network_allowed=False
        )
        ctx = self.context()

        self.assertTrue(controller.start(ctx))

        self.assertEqual(cloud.request_count, 0)
        continue_launch.assert_called_once_with(ctx)
        toast.assert_called_once_with(
            "Offline mode — launching 'Example' with local saves."
        )
        self.assertFalse(controller.is_pending(7))

    def test_duplicate_launch_activation_shares_one_preflight(self):
        controller, cloud, continue_launch, toast, _button = self.make_controller()
        ctx = self.context()

        self.assertTrue(controller.start(ctx))
        self.assertFalse(controller.start(ctx))

        self.assertEqual(cloud.request_count, 1)
        self.assertTrue(controller.is_pending(7))
        continue_launch.assert_not_called()
        toast.assert_called_once_with(
            "Launch is already preparing this game's cloud save."
        )

    def test_cancel_keeps_lock_until_cloud_operation_finishes(self):
        controller, cloud, continue_launch, _toast, _button = self.make_controller()
        ctx = self.context()
        controller.start(ctx)
        token = controller._pending[7]["token"]

        controller.cancel(7, token)

        self.assertEqual(cloud.cancelled, ["prelaunch-1"])
        self.assertTrue(controller.is_pending(7))
        continue_launch.assert_not_called()

    def test_cancel_racing_with_successful_restore_aborts_launch(self):
        controller, _cloud, continue_launch, toast, button = self.make_controller()
        ctx = self.context()
        controller.start(ctx)
        token = controller._pending[7]["token"]
        controller._pending[7]["cancel_requested"] = True

        controller._on_operation_result({
            "ctx": {**ctx, "prelaunch_token": token},
            "ok": True,
            "cancelled": False,
            "toast": "Restored cloud save.",
        })

        self.assertFalse(controller.is_pending(7))
        continue_launch.assert_not_called()
        toast.assert_called_once_with(
            "Launch cancelled — cloud sync for 'Example' was stopped."
        )
        button.assert_called_with(7)

    def test_shutdown_cancels_preflight_and_never_starts_the_game(self):
        controller, cloud, continue_launch, toast, _button = self.make_controller()
        controller._show_progress = Mock()
        ctx = self.context()
        controller.start(ctx)
        launched_context = controller._pending[7]["ctx"]
        controller.is_closing = lambda: True

        controller.cancel_pending_for_shutdown()
        self.assertTrue(controller.is_pending(7))
        self.assertEqual(cloud.cancelled, ["prelaunch-1"])

        controller._on_preflight_result({
            "ctx": launched_context,
            "local_ready": True,
        })

        self.assertFalse(controller.is_pending(7))
        continue_launch.assert_not_called()
        toast.assert_not_called()

    def test_cancelled_preflight_retires_lock_without_launching(self):
        controller, _cloud, continue_launch, toast, _button = self.make_controller()
        ctx = self.context()
        controller.start(ctx)
        token = controller._pending[7]["token"]

        controller._on_preflight_result({
            "ctx": {**ctx, "prelaunch_token": token},
            "cancelled": True,
        })

        self.assertFalse(controller.is_pending(7))
        continue_launch.assert_not_called()
        toast.assert_called_once_with(
            "Launch cancelled — cloud sync for 'Example' was stopped."
        )

    def test_ready_local_preflight_hands_off_once_and_clears_progress(self):
        controller, cloud, continue_launch, toast, button = self.make_controller()
        controller._show_progress = Mock()
        ctx = self.context()
        controller.start(ctx)
        launched_context = controller._pending[7]["ctx"]
        cloud.future.set_result(SimpleNamespace(
            status=ResourceStatus.READY,
            value={"local_ready": True},
            error=None,
        ))

        self.app.processEvents()
        controller._on_preflight_result({
            "ctx": launched_context,
            "local_stats": None,
            "cloud_stats": None,
            "toast": "duplicate event",
        })

        self.assertFalse(controller.is_pending(7))
        continue_launch.assert_called_once_with(launched_context)
        toast.assert_called_once_with("Local saves ready for 'Example'.", is_error=False)
        button.assert_called_with(7)

    def test_conflict_restore_runs_async_then_continues(self):
        controller, cloud, continue_launch, toast, _button = self.make_controller()
        controller._show_progress = Mock()
        controller.refresh_cloud_status = Mock()
        ctx = self.context()
        controller.start(ctx)
        launched_context = controller._pending[7]["ctx"]
        cloud_stats = SimpleNamespace(cloud_version=12)

        class _ConflictDialog:
            always_newer = False
            choice = "cloud"

            def __init__(self, *_args, **_kwargs):
                pass

            def exec(self):
                return QDialog.DialogCode.Accepted

        with patch("ui.prelaunch_controller.SaveConflictDialog", _ConflictDialog):
            controller._on_preflight_result({
                "ctx": launched_context,
                "needs_conflict": True,
                "local_stats": object(),
                "cloud_stats": cloud_stats,
            })

        self.assertTrue(controller.is_pending(7))
        self.assertEqual(cloud.restore_calls[0][1]["target_version"], 12)
        cloud.restore_future.set_result(SimpleNamespace(
            status=ResourceStatus.READY,
            value=SimpleNamespace(success=True),
            error=None,
        ))
        self.app.processEvents()

        self.assertFalse(controller.is_pending(7))
        continue_launch.assert_called_once_with(launched_context)
        toast.assert_called_once()
        controller.refresh_cloud_status.assert_called_once_with(7)

    def test_stale_result_cannot_release_newer_launch_lock(self):
        controller, _cloud, continue_launch, toast, _button = self.make_controller()
        ctx = self.context()
        controller.start(ctx)
        old_token = controller._pending[7]["token"]
        controller._finish({**ctx, "prelaunch_token": old_token})
        controller.start(ctx)
        new_token = controller._pending[7]["token"]

        controller._on_preflight_result({
            "ctx": {**ctx, "prelaunch_token": old_token},
            "toast": "Stale result",
        })

        self.assertNotEqual(old_token, new_token)
        self.assertTrue(controller.is_pending(7))
        continue_launch.assert_not_called()
        toast.assert_not_called()

    def test_worker_payloads_preserve_failure_and_quota_guidance(self):
        ctx = self.context()
        future = Future()
        future.set_result(SimpleNamespace(
            status=ResourceStatus.READY,
            value={
                "cloud_result": SimpleNamespace(
                    success=False,
                    error="quota exceeded",
                    guidance="Free space or launch locally.",
                    category="quota",
                    payload={"quota_bytes": 100},
                ),
            },
        ))

        payload = PrelaunchController._build_preflight_payload(ctx, future)

        self.assertTrue(payload["quota_blocked"])
        self.assertEqual(payload["quota_details"], {"quota_bytes": 100})
        self.assertEqual(payload["guidance"], "Free space or launch locally.")

    def test_real_progress_close_after_queued_success_does_not_cancel_launch(self):
        controller, cloud, launch, _toast, _button = self.make_controller()
        controller.start(self.context())
        progress = controller._pending[7]["progress"]
        worker = Thread(target=lambda: cloud.future.set_result(SimpleNamespace(
            status=ResourceStatus.READY, value={"local_ready": True}, error=None,
        )))
        worker.start()
        worker.join()
        launch.assert_not_called()
        self.app.processEvents()
        self.assertFalse(progress.isVisible())
        self.assertEqual(cloud.cancelled, [])
        launch.assert_called_once()
        self.assertFalse(controller.is_pending(7))

    def test_replacing_real_progress_does_not_cancel_either_operation(self):
        controller, cloud, launch, _toast, _button = self.make_controller()
        controller.start(self.context())
        ctx = controller._pending[7]["ctx"]
        previous = controller._pending[7]["progress"]
        controller._queue_cloud_operation(ctx, "restore", "Restored", "Failed")
        self.assertFalse(previous.isVisible())
        self.assertEqual(cloud.cancelled, [])
        self.assertFalse(controller._pending[7]["cancel_requested"])
        self.assertEqual(controller._pending[7]["handle"].request_id, "restore-1")
        cloud.restore_future.set_result(SimpleNamespace(
            status=ResourceStatus.READY, value=SimpleNamespace(success=True), error=None,
        ))
        self.app.processEvents()
        self.assertEqual(cloud.cancelled, [])
        launch.assert_called_once()

    def test_user_closes_real_progress_keeps_lock_until_terminal_result(self):
        controller, cloud, launch, _toast, _button = self.make_controller()
        controller.start(self.context())
        controller._pending[7]["progress"].close()
        self.assertEqual(cloud.cancelled, ["prelaunch-1"])
        self.assertTrue(controller.is_pending(7))
        cloud.future.set_result(SimpleNamespace(
            status=ResourceStatus.CANCELLED, value=None, error=None,
        ))
        self.app.processEvents()
        self.assertFalse(controller.is_pending(7))
        launch.assert_not_called()

    def test_real_quota_modal_cannot_launch_after_shutdown_reentry(self):
        controller, cloud, launch, _toast, _button = self.make_controller()
        controller.start(self.context())
        ctx = controller._pending[7]["ctx"]
        closing = [False]
        controller.is_closing = lambda: closing[0]
        original_exec = QMessageBox.exec

        def exec_with_shutdown(dialog):
            def shut_down_then_choose_launch():
                closing[0] = True
                controller.cancel_pending_for_shutdown()
                next(button for button in dialog.buttons()
                     if button.text() == "Launch with local saves").click()
            QTimer.singleShot(0, shut_down_then_choose_launch)
            return original_exec(dialog)

        with patch.object(QMessageBox, "exec", exec_with_shutdown):
            controller._on_preflight_result({"ctx": ctx, "quota_blocked": True})
        launch.assert_not_called()
        self.assertFalse(controller.is_pending(7))
        self.assertEqual(cloud.cancelled, ["prelaunch-1"])

    def test_cancel_during_restore_confirmation_cannot_queue_followup_work(self):
        controller, cloud, launch, _toast, _button = self.make_controller()
        controller.start(self.context())
        ctx = controller._pending[7]["ctx"]

        def confirm_then_cancel(*_args, **_kwargs):
            controller.cancel(7, ctx["prelaunch_token"])
            return True

        with patch("ui.prelaunch_controller.confirm_restore", confirm_then_cancel):
            controller._on_preflight_result({
                "ctx": ctx, "needs_cloud_only_prompt": True,
                "cloud_stats": SimpleNamespace(cloud_version=12),
            })
        self.assertEqual(cloud.restore_calls, [])
        launch.assert_not_called()
        self.assertFalse(controller.is_pending(7))


if __name__ == "__main__":
    unittest.main()
