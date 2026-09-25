"""Offscreen workflow checks for the shared cloud-dialog UX contract."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QToolButton

from core.request_contracts import ResourceStatus
from core.ludusavi_detector import SaveLocation
from core.save_validation import SaveLocationValidation

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

    def test_successful_probe_refreshes_overview_and_notifies_shell(self):
        with patch.object(CloudCenterDialog, "_request_overview") as request_overview:
            dialog = CloudCenterDialog(
                cloud_center_service=_CloudCenterService(),
            )
            request_overview.assert_called_once_with(force=True)
            restored = []
            dialog.connection_restored.connect(lambda: restored.append(True))
            try:
                request_overview.reset_mock()
                dialog._apply_probe_result(SimpleNamespace(
                    status=ResourceStatus.READY,
                    value={"healthy": True, "version": "1.0.0", "latency_ms": 8},
                ))
                self.assertEqual(restored, [True])
                request_overview.assert_called_once_with(force=True)
                self.assertIn("Backend reachable", dialog.lbl_probe.text())
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
            self.assertFalse(hasattr(dialog, "btn_cloud"))
            self.assertFalse(any(
                "Restore latest cloud save" in button.text()
                for button in dialog.findChildren(type(dialog.btn_upload))
            ))
            self.assertEqual(dialog.cloud_status_panel.state, "loading")
            self._assert_focus_contract(dialog)
            self.assertFalse(dialog.tabs.isTabVisible(dialog.tabs.indexOf(dialog.tab_history)))
            self.assertTrue(any(
                button.text() == "Import local save archive (.zip)"
                for button in dialog.findChildren(type(dialog.btn_upload))
            ))
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_save_manager_shows_local_save_time_and_device(self):
        with patch.object(SaveManagerDialog, "_scan_saves"), \
             patch("ui.dialogs.save_manager_dialog.get_device_identity", return_value=("id", "Desktop", "Linux")):
            dialog = SaveManagerDialog(1, "Example", "/tmp/example")
            location = SaveLocation(
                "Profile saves", "/tmp/example/saves", True,
                file_count=2, total_size_bytes=42, last_modified=1_700_000_000,
            )
            dialog._render_scan_results((
                [location],
                [SaveLocationValidation(
                    location, True, file_count=2, total_size_bytes=42,
                    last_modified=1_700_000_000,
                )],
            ))
        try:
            labels = [label.text() for label in dialog.findChildren(type(dialog.lbl_status))]
            self.assertTrue(any(text.startswith("Saved: ") and "Device: Desktop" in text for text in labels))
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

    def test_game_properties_latest_restore_uses_newest_content_version(self):
        game = (2, "Example", "/tmp/example", "example.exe", "", "", "") + ("",) * 14

        class _RestoreService:
            def __init__(self):
                self.calls = []

            def request_restore(self, target, **kwargs):
                self.calls.append((target, kwargs))
                return object()

        service = _RestoreService()
        with patch.object(GamePropertiesDialog, "_load_save_stats_async"), \
             patch("ui.dialogs.game_properties_dialog.confirm_restore", return_value=True), \
             patch("ui.dialogs.game_properties_dialog.cloud_progress", return_value=SimpleNamespace(
                 close=lambda: None,
                 deleteLater=lambda: None,
             )), \
             patch.object(GamePropertiesDialog, "_bind_cloud_operation"):
            dialog = GamePropertiesDialog(game)
            try:
                dialog.cloud_center_service = service
                dialog.history_timeline.set_entries([
                    {
                        "source": "cloud",
                        "version": 8,
                        "sourceMaxMtime": 100,
                        "uploadedAt": 300,
                        "sizeBytes": 80,
                        "deviceName": "Desktop",
                    },
                    {
                        "source": "cloud",
                        "version": 7,
                        "sourceMaxMtime": 200,
                        "uploadedAt": 200,
                        "sizeBytes": 70,
                        "deviceName": "Steam Deck",
                    },
                ])
                dialog._sync_down_now()

                self.assertEqual(len(service.calls), 1)
                kwargs = service.calls[0][1]
                self.assertEqual(kwargs["target_version"], 7)
                self.assertEqual(kwargs["restore_plan"].source_version, 7)
            finally:
                dialog.close()
                dialog.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
