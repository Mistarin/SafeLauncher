"""Modal exits must not destroy widgets while save work is still running."""

from concurrent.futures import Future
from contextlib import ExitStack
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog

from core.operation_registry import OperationRegistry
from core.request_contracts import ResourceStatus
from ui.save_dialog_services import SaveDialogServices
from ui.dialogs.save_manager_dialog import SaveManagerDialog
from ui.dialogs.game_properties_dialog import GamePropertiesDialog


class SaveDialogLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_all_modal_exits_drain_work_without_late_delivery(self):
        for dialog_type in (SaveManagerDialog, GamePropertiesDialog):
            for exit_method, result in (('accept', QDialog.DialogCode.Accepted),
                                        ('reject', QDialog.DialogCode.Rejected),
                                        ('close', QDialog.DialogCode.Rejected)):
                with self.subTest(dialog=dialog_type.__name__, exit=exit_method), ExitStack() as stack:
                    manager = Mock()
                    future = Future()
                    handle = SimpleNamespace(request_id='fixture', future=future, cancel=Mock())
                    manager.request.return_value = handle
                    services = SaveDialogServices(request_manager=manager, operation_registry=OperationRegistry())
                    if dialog_type is SaveManagerDialog:
                        stack.enter_context(patch.object(dialog_type, '_scan_saves'))
                        dialog = dialog_type(1, 'Fixture', '/missing', services=services)
                    else:
                        stack.enter_context(patch.object(dialog_type, '_load_save_stats_async'))
                        dialog = dialog_type((1, 'Fixture', '/missing', '', 'umu', '', ''), services=services)
                    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
                    finished, callback, binding = Mock(), Mock(), Mock()
                    dialog.finished.connect(finished)
                    dialog._resource_bindings['fixture'] = binding
                    dialog._start_managed_task('fixture', lambda: None, callback)
                    dialog.show()
                    getattr(dialog, exit_method)()
                    self.assertTrue(dialog._closing)
                    binding.close.assert_called_once()
                    handle.cancel.assert_called_once()
                    finished.assert_not_called()
                    future.set_result(SimpleNamespace(status=ResourceStatus.CANCELLED))
                    deadline = time.monotonic() + 1
                    while not finished.called and time.monotonic() < deadline:
                        self.app.processEvents()
                        time.sleep(.005)
                    finished.assert_called_once_with(result)
                    callback.assert_not_called()
                    dialog.deleteLater()
                    self.app.processEvents()
