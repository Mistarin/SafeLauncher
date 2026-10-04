from concurrent.futures import Future
from types import SimpleNamespace
from threading import Thread
import unittest
from unittest.mock import Mock

from PyQt6.QtWidgets import QApplication
from core.cloud_operation_service import CloudOperationTarget
from core.request_contracts import ResourceStatus
from ui.manual_restore_controller import ManualRestoreController


class ManualRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.generation = 1
        self.preflight, self.restore = Future(), Future()
        self.first = SimpleNamespace(future=self.preflight, cancel=Mock())
        self.second = SimpleNamespace(future=self.restore, cancel=Mock())
        self.service = Mock()
        self.service.request_restore_preflight.return_value = self.first
        self.service.request_restore.return_value = self.second
        self.confirm, self.notify, self.changed, self.recheck = Mock(return_value=True), Mock(), Mock(), Mock()
        self.allowed = True
        self.controller = ManualRestoreController(
            service=self.service, generation=lambda: self.generation,
            accepts_work=lambda: True, can_restore=lambda target: self.allowed,
            progress=Mock(), close_progress=Mock(), confirm=self.confirm,
            notify=self.notify, no_save=Mock(), state_changed=self.changed, recheck=self.recheck)
        self.target = CloudOperationTarget(7, 'Game', '/game', '480')
        self.addCleanup(self.controller.dispose)

    def complete(self, future, value=None, status=ResourceStatus.READY, error=None):
        future.set_result(SimpleNamespace(status=status, value=value, error=error))
        self.app.processEvents()

    def ready(self):
        self.complete(self.preflight, {'kind': 'ready', 'history_entry': {'version': 3}, 'restore_plan': 'plan'})

    def test_duplicate_click_has_one_preflight(self):
        self.assertTrue(self.controller.start(self.target))
        self.assertFalse(self.controller.start(self.target))
        self.service.request_restore_preflight.assert_called_once()

    def test_confirmed_version_and_plan_survive_selection_changes(self):
        self.controller.start(self.target)
        self.ready()
        self.assertTrue(self.controller.is_pending(7))
        args, kwargs = self.service.request_restore.call_args
        self.assertEqual(args, (self.target,))
        self.assertEqual((kwargs['target_version'], kwargs['restore_plan']), (3, 'plan'))
        self.complete(self.restore, SimpleNamespace(success=True))
        self.recheck.assert_called_once_with([7], 'manual_restore')
        self.assertFalse(self.controller.is_pending(7))

    def test_failure_does_not_reference_undefined_payload(self):
        self.controller.start(self.target)
        self.complete(self.preflight, status=ResourceStatus.ERROR, error='Offline')
        self.assertIn('Offline', self.notify.call_args.args[0])
        self.assertFalse(self.controller.is_pending(7))
        self.service.request_restore.assert_not_called()

    def test_cancel_keeps_lock_until_rollback_finishes(self):
        self.controller.start(self.target)
        self.ready()
        self.controller.cancel()
        self.second.cancel.assert_called_once()
        self.assertTrue(self.controller.is_pending(7))
        self.complete(self.restore, status=ResourceStatus.CANCELLED)
        self.assertFalse(self.controller.is_pending(7))
        self.recheck.assert_not_called()

    def test_account_change_discards_old_preflight(self):
        self.controller.start(self.target)
        self.generation += 1
        self.ready()
        self.confirm.assert_not_called()
        self.service.request_restore.assert_not_called()

    def test_modal_confirm_revalidates_account_and_running_game(self):
        self.controller.start(self.target)
        self.confirm.side_effect = lambda *args: setattr(self, 'allowed', False) or True
        self.ready()
        self.service.request_restore.assert_not_called()
        self.assertFalse(self.controller.is_pending(7))

    def test_rejected_confirm_and_missing_cloud_release_lock(self):
        self.controller.start(self.target)
        self.confirm.return_value = False
        self.ready()
        self.assertFalse(self.controller.is_pending(7))
        self.service.request_restore.assert_not_called()

    def test_submission_failure_releases_lock(self):
        self.service.request_restore_preflight.side_effect = RuntimeError('Shutdown')
        self.controller.start(self.target)
        self.assertFalse(self.controller.is_pending(7))
        self.notify.assert_called_once()

    def test_worker_result_is_delivered_on_gui_thread(self):
        self.controller.start(self.target)
        worker = Thread(target=lambda: self.preflight.set_result(SimpleNamespace(
            status=ResourceStatus.READY, value={'kind': 'ready'}, error=None)))
        worker.start(); worker.join()
        self.confirm.assert_not_called()
        self.app.processEvents()
        self.confirm.assert_called_once()

    def test_disposed_controller_never_confirms_or_restores(self):
        self.controller.start(self.target)
        self.controller.dispose()
        self.ready()
        self.confirm.assert_not_called()
        self.service.request_restore.assert_not_called()
