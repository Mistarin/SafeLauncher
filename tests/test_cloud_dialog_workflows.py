"""Offscreen workflow checks for the shared cloud-dialog UX contract."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QToolButton

from core.request_contracts import ResourceStatus

try:
    from ui.dialogs.account_dialog import AccountDialog
    from ui.dialogs.cloud_center_dialog import CloudCenterDialog
    from ui.dialogs.game_properties_dialog import GamePropertiesDialog
    from ui.dialogs.save_manager_dialog import SaveManagerDialog
    _DIALOG_IMPORT_ERROR = None
except ModuleNotFoundError as error:  # pragma: no cover - dependency-gated CI
    AccountDialog = CloudCenterDialog = GamePropertiesDialog = SaveManagerDialog = None
    _DIALOG_IMPORT_ERROR = error


class _AccountService:
    def mode(self):
        return "local"


class _CloudCenterService:
    request_manager = None


class _OfflineCloudCenterService(_CloudCenterService):
    def current_context(self):
        return SimpleNamespace(network_allowed=False)


@unittest.skipIf(_DIALOG_IMPORT_ERROR is not None, f"cloud dialog dependencies unavailable: {_DIALOG_IMPORT_ERROR}")
class CloudDialogWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _assert_focus_contract(self, dialog):
        self.assertTrue(dialog._cloud_focus_order)
        self.assertIs(dialog._cloud_initial_focus, dialog._cloud_focus_order[0])
        self.assertNotIn("restore", dialog._cloud_initial_focus.accessibleName().casefold())

    def test_cloud_center_uses_shared_status_and_safe_focus(self):
        with patch.object(CloudCenterDialog, "_request_overview"):
            dialog = CloudCenterDialog(
                cloud_center_service=_CloudCenterService(),
            )
        try:
            self.assertEqual(dialog.cloud_status.state, "loading")
            self.assertEqual(dialog.btn_sync.text(), "Sync now")
            self._assert_focus_contract(dialog)
            technical = [
                button for button in dialog.findChildren(QToolButton)
                if button.text() == "Technical details"
            ]
            self.assertEqual(len(technical), 1)
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_late_cloud_transport_error_is_presented_as_offline(self):
        with patch.object(CloudCenterDialog, "_request_overview"):
            dialog = CloudCenterDialog(
                cloud_center_service=_OfflineCloudCenterService(),
            )
        try:
            dialog._show_resource_error(SimpleNamespace(
                status=ResourceStatus.ERROR,
                error=RuntimeError("DNS lookup failed"),
            ))
            self.assertEqual(dialog.cloud_status.state, "offline")
            self.assertEqual(dialog.cloud_status.lbl_status.text(), "Offline")
            self.assertNotIn("DNS lookup failed", dialog.cloud_status.lbl_message.text())
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_account_dialog_uses_shared_status_and_safe_focus(self):
        with patch.object(AccountDialog, "reload"):
            dialog = AccountDialog(cloud_account_service=_AccountService())
        try:
            self.assertEqual(dialog.cloud_status_panel.state, "loading")
            self._assert_focus_contract(dialog)
            self.assertFalse(dialog.lbl_endpoint_details.isVisible())
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_save_manager_standardizes_actions_and_hides_paths(self):
        with patch.object(SaveManagerDialog, "_scan_saves"):
            dialog = SaveManagerDialog(1, "Example", "/tmp/example")
        try:
            self.assertEqual(dialog.btn_upload.text(), "Upload local save")
            self.assertEqual(dialog.btn_export.text(), "Export local save archive")
            self.assertEqual(dialog.cloud_status_panel.state, "loading")
            self._assert_focus_contract(dialog)
            self.assertTrue(any(
                button.text() == "Import local save archive (.zip)"
                for button in dialog.findChildren(type(dialog.btn_upload))
            ))
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_game_properties_hides_paths_and_uses_shared_status(self):
        game = (1, "Example", "/tmp/example", "example.exe", "", "", "") + ("",) * 14
        with patch.object(GamePropertiesDialog, "_load_save_stats_async"):
            dialog = GamePropertiesDialog(game)
        try:
            self.assertFalse(dialog.lbl_folder_path.isVisible())
            self.assertEqual(dialog.cloud_status_panel.state, "loading")
            self._assert_focus_contract(dialog)
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
