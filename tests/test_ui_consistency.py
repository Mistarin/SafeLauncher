import unittest

from PyQt6.QtWidgets import (
    QApplication, QFormLayout, QLineEdit, QLabel, QToolButton, QFrame,
    QPushButton, QMainWindow,
)
from PyQt6.QtCore import QSettings
from unittest.mock import Mock, patch

from core.launch_diagnostics import LaunchDiagnostics
from ui.components.popup_shell import PopupDialog
from ui.dialogs.game_dialogs import AddGameDialog, SafeLaunchDialog
from ui.dialogs.settings_dialog import UserSettingsDialog
from ui.components.sidebar import CustomTitleBar
from ui.main_window import MainWindow


class PopupPropertyConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_property_form_aligns_labels_and_expands_fields(self):
        dialog = PopupDialog("Consistency")
        form = QFormLayout()
        value = QLineEdit()
        form.addRow("A long property name:", value)

        dialog.polish_property_form(form)
        label = form.itemAt(0, QFormLayout.ItemRole.LabelRole).widget()

        self.assertEqual(label.objectName(), "propertyLabel")
        self.assertEqual(label.alignment().value, 0x82)  # right + vertical center
        self.assertGreaterEqual(label.minimumWidth(), 160)
        self.assertEqual(value.sizePolicy().horizontalPolicy().name, "Expanding")
        dialog.close()

    def test_info_hint_is_muted_and_accessible(self):
        hint = PopupDialog.info_hint(
            "Credentials stay in the local secret store.",
            tooltip="Credentials are never included in request keys, logs, or caches.",
        )
        icon = hint.findChild(QToolButton, "propertyInfo")
        text = hint.findChild(QLabel, "propertyHintText")

        self.assertIsNotNone(icon)
        self.assertEqual(icon.toolTip(), "Credentials are never included in request keys, logs, or caches.")
        self.assertIsNotNone(text)
        self.assertEqual(text.accessibleDescription(), "Credentials are never included in request keys, logs, or caches.")
        hint.deleteLater()

    def test_settings_uses_shared_section_surfaces_and_contrasting_fields(self):
        dialog = UserSettingsDialog("Player", parent=None)
        try:
            dialog.show()
            self.app.processEvents()
            self.assertGreaterEqual(len(dialog.findChildren(QFrame, "settingsSection")), 5)
            self.assertGreaterEqual(len(dialog.findChildren(QFrame, "settingsDivider")), 5)
            stylesheet = dialog.styleSheet()
            self.assertIn("#1B1E24", stylesheet)
            self.assertIn("#20242C", stylesheet)
            self.assertIn("QLabel#propertyLabel", stylesheet)
            self.assertIn("#1B1E24", dialog.findChildren(QFrame, "settingsSection")[0].styleSheet())
            self.assertIn("#20242C", dialog.name_input.styleSheet())
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_cloud_settings_routes_account_work_to_cloud_center(self):
        dialog = UserSettingsDialog("Player", parent=None)
        try:
            self.assertEqual(dialog.btn_open_cloud_center.text(), "Open Cloud Center…")
            self.assertFalse(dialog.btn_review_conflicts.isVisible())
            self.assertFalse(dialog.btn_refresh_quota.isHidden())
            self.assertTrue(any(
                "Use Cloud Center" in label.text()
                for label in dialog.findChildren(QLabel)
            ))
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_cloud_backend_selector_does_not_persist_until_save(self):
        settings = QSettings("SafeLauncher", "SafeLauncher")
        original = settings.value("cloud_mode", "local", type=str)
        settings.setValue("cloud_mode", "local")
        dialog = UserSettingsDialog("Player", parent=None)
        try:
            dialog.combo_cloud_mode.setCurrentIndex(1)
            self.assertEqual(settings.value("cloud_mode", "local", type=str), "local")
            dialog.reject()
            self.assertEqual(settings.value("cloud_mode", "local", type=str), "local")
        finally:
            settings.setValue("cloud_mode", original)
            dialog.deleteLater()
            self.app.processEvents()

    def test_add_game_keeps_invalid_form_open(self):
        dialog = AddGameDialog(parent=None)
        try:
            dialog.name_input.setText("Example")
            dialog.exe_combo.setEditText("game.exe")
            dialog.add_btn.click()
            self.assertEqual(dialog.result(), 0)
            self.assertIn("existing game directory", dialog.status_label.text())
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_header_view_menu_exposes_only_library_profile_and_friends(self):
        class _HeaderWindow(QMainWindow):
            def _toggle_maximize(self):
                pass

        window = _HeaderWindow()
        title_bar = CustomTitleBar(window)
        try:
            labels = [action.text() for action in title_bar.view_menu.actions()]
            self.assertEqual(labels, ["Library", "Profile", "Friends"])
            self.assertFalse(title_bar.btn_friends.isVisible())
        finally:
            title_bar.deleteLater()
            window.deleteLater()
            self.app.processEvents()

    def test_cloud_action_is_deferred_until_menu_event_finishes(self):
        fake = Mock()
        fake._game_by_id.return_value = (7, "Test", "/tmp/test", "", "", "", "")

        with patch("ui.main_window.QTimer.singleShot") as single_shot:
            MainWindow._on_game_cloud_action(fake, 7, "upload")

        fake._perform_game_cloud_action.assert_not_called()
        single_shot.assert_called_once()
        self.assertEqual(single_shot.call_args.args[0], 0)

    def test_launch_failure_actions_reflow_without_gaps(self):
        dialog = SafeLaunchDialog("Test Game", process=None)
        try:
            dialog.diagnostics = LaunchDiagnostics(
                game_name="Test Game",
                return_code=143,
                output=["The runtime exited with code 143."],
            )
            dialog._configure_recovery_actions()
            dialog.stack.setCurrentWidget(dialog.page_error)
            dialog.show()
            self.app.processEvents()

            self.assertTrue(dialog.recovery_actions["safe_retry"].isVisible())
            self.assertTrue(dialog.recovery_actions["prefix"].isVisible())
            self.assertFalse(dialog.recovery_actions["edit_game"].isVisible())
            first = dialog._recovery_layout.itemAtPosition(0, 0).widget()
            second = dialog._recovery_layout.itemAtPosition(0, 1).widget()
            self.assertEqual(first.text(), "Retry safe")
            self.assertEqual(second.text(), "Prefix maintenance")
            self.assertEqual(dialog.close_log_button.objectName(), "launchCloseButton")
            self.assertEqual(len(dialog.findChildren(QPushButton, "launchCloseButton")), 1)
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
