from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import QApplication
from core.request_manager import RequestManager
from core.request_contracts import RequestKey
from core.operation_registry import OperationRegistry
from ui.dialogs.settings_dialog import UserSettingsDialog
from ui.settings_dialog_services import SettingsDialogServices
from ui.settings_account_controller import SettingsAccountController


class SettingsLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def drain(self, condition):
        deadline = time.monotonic() + 2
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition())

    def test_one_worker_account_probe_never_waits_inside_worker(self):
        manager = RequestManager(max_workers=1)
        self.addCleanup(manager.shutdown)
        service, status = Mock(), Mock()
        service.request_manager = manager
        service.request_snapshot.side_effect = lambda **kwargs: manager.request(
            RequestKey('fixture', 'account'), lambda token: {
                'overview': {'quotaBytes': 5 * 1024**3, 'bytesUsed': 1024},
                'listing': {'games': []}})
        controller = SettingsAccountController(service, manager, allowed=lambda: True,
            show_status=status, start_task=Mock())
        self.addCleanup(controller.dispose)
        controller.refresh()
        self.drain(lambda: status.called)
        self.assertIn('5.00 GB', status.call_args.args[0])
        controller.start_task.assert_not_called()

    def make_dialog(self, root, manager, stack):
        settings = QSettings(str(Path(root) / 'settings.ini'), QSettings.Format.IniFormat)
        settings.setValue('offline_mode', True)
        for module in ('ui.dialogs.settings_dialog', 'ui.dialogs.settings_pages.general', 'ui.dialogs.settings_pages.cloud'):
            stack.enter_context(patch(module+'.QSettings', return_value=settings))
        for name in ('get_secret', 'set_secret', 'delete_secret'):
            stack.enter_context(patch('ui.dialogs.settings_dialog.'+name, return_value=''))
        stack.enter_context(patch('ui.dialogs.settings_pages.storage.ensure_sandbox_dir', return_value=root))
        stack.enter_context(patch.object(UserSettingsDialog, '_dialog_network_allowed', return_value=False))
        account = Mock()
        account.mode.return_value = 'local'
        dialog = UserSettingsDialog('Player', services=SettingsDialogServices(
            request_manager=manager, operation_registry=OperationRegistry(), cloud_account=account))
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        return dialog, settings

    def test_cancel_drains_loader_and_suppresses_its_callback(self):
        with TemporaryDirectory() as root, ExitStack() as stack:
            manager = RequestManager(max_workers=1)
            dialog, settings = self.make_dialog(root, manager, stack)
            release, entered = Event(), Event()
            callback, finished = Mock(), Mock()
            dialog.finished.connect(finished)
            def work():
                entered.set()
                release.wait(2)
                return 'late'
            try:
                dialog._start_managed_task('blocked-fixture', work, callback, allow_offline=True)
                self.assertTrue(entered.wait(1))
                dialog.reject()
                self.assertTrue(dialog._closing)
                finished.assert_not_called()
                release.set()
                self.drain(lambda: finished.called)
                callback.assert_not_called()
                self.assertFalse(dialog._managed_tasks.has_pending_work())
            finally:
                release.set()
                manager.shutdown()
                dialog.deleteLater()
                self.app.processEvents()

    def test_failed_startup_change_does_not_partially_save_cloud_preferences(self):
        with TemporaryDirectory() as root, ExitStack() as stack:
            manager = RequestManager(max_workers=1)
            dialog, settings = self.make_dialog(root, manager, stack)
            try:
                dialog.edit_device_name.setText('Unsaved fixture')
                with patch('ui.dialogs.settings_dialog.set_startup_enabled', return_value=(False, 'fixture failure')), \
                     patch('ui.dialogs.settings_dialog.QMessageBox.warning'):
                    dialog._save()
                self.assertEqual(settings.value('cloud_device_name', ''), '')
                self.assertFalse(dialog._cloud_settings_committed)
                dialog.reject()
                self.drain(lambda: not dialog._managed_tasks.has_pending_work())
                self.app.processEvents()
            finally:
                manager.shutdown()
                dialog.deleteLater()
                self.app.processEvents()

    def test_download_offline_race_reports_error_without_unpacking_dict(self):
        with TemporaryDirectory() as root, ExitStack() as stack:
            manager = RequestManager(max_workers=1, offline_check=lambda: True)
            dialog, _settings = self.make_dialog(root, manager, stack)
            failed = Mock()
            dialog.appDownloadFailed.connect(failed)
            try:
                with patch.object(dialog, '_dialog_network_allowed', return_value=True), \
                     patch('ui.dialogs.settings_dialog.QMessageBox.critical'), \
                     patch('ui.dialogs.settings_dialog.download_and_apply_appimage_update') as download:
                    dialog._start_appimage_download('https://example.invalid/update')
                    self.drain(lambda: failed.called)
                    download.assert_not_called()
                    self.assertIn('Download failed', dialog.lbl_update_status.text())
                dialog.reject()
                self.drain(lambda: not dialog._managed_tasks.has_pending_work())
            finally:
                manager.shutdown()
                dialog.deleteLater()
                self.app.processEvents()

    def test_storage_scan_failure_replaces_calculating_placeholder(self):
        with TemporaryDirectory() as root, ExitStack() as stack:
            manager = RequestManager(max_workers=1, offline_check=lambda: True)
            dialog, _settings = self.make_dialog(root, manager, stack)
            try:
                self.assertIn('Calculating', dialog.lbl_sandbox_size.text())
                dialog._start_managed_task(
                    'storage-size-fixture', lambda: 1,
                    dialog._on_sandbox_size_ready,
                    on_error=dialog._on_sandbox_size_error,
                )
                self.drain(lambda: 'Could not calculate' in dialog.lbl_sandbox_size.text())
                self.assertIn('readable', dialog.lbl_sandbox_size.toolTip())
                dialog.reject()
                self.drain(lambda: not dialog._managed_tasks.has_pending_work())
            finally:
                manager.shutdown()
                dialog.deleteLater()
                self.app.processEvents()
