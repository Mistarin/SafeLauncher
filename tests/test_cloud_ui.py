import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

from core.save_history import normalize_history_entries
from ui.components.cloud_ui import (
    CloudStatusPanel,
    confirm_delete,
    confirm_restore,
    cloud_version_details,
    set_cloud_focus_order,
    set_cloud_initial_focus,
)
from ui.components.save_history_timeline import SaveHistoryTimeline


class _FakeRestoreMessage:
    class Icon:
        Warning = object()

    class ButtonRole:
        AcceptRole = object()
        RejectRole = object()

    def __init__(self, parent=None):
        self.text_value = ""
        self.informative_value = ""
        self.details_value = ""
        self.default_button = None
        self.escape_button = None
        self._buttons = []
        self._clicked = None

    def setIcon(self, _icon):
        pass

    def setWindowTitle(self, _title):
        pass

    def setText(self, value):
        self.text_value = value

    def setInformativeText(self, value):
        self.informative_value = value

    def setDetailedText(self, value):
        self.details_value = value

    def addButton(self, text, role):
        button = (text, role)
        self._buttons.append(button)
        return button

    def setDefaultButton(self, button):
        self.default_button = button

    def setEscapeButton(self, button):
        self.escape_button = button

    def exec(self):
        # Simulate the safe keyboard/default path: cancel is selected.
        self._clicked = self._buttons[-1]

    def clickedButton(self):
        return self._clicked


class _FakeDeleteMessage:
    class StandardButton:
        Yes = 1
        No = 2

    answer = StandardButton.No
    call = None

    @classmethod
    def question(cls, *args):
        cls.call = args
        return cls.answer


class CloudUiPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_history_uses_plain_cloud_version_vocabulary(self):
        entry = normalize_history_entries([
            {
                "source": "cloud",
                "version": 4,
                "createdAt": 1_700_000_000,
                "sizeBytes": 2048,
                "deviceName": "Desktop",
            }
        ])[0]
        self.assertEqual(entry.version_label, "Cloud save version 4")
        self.assertIn("Cloud save version 4", cloud_version_details(entry))
        self.assertNotIn("generation", cloud_version_details(entry).casefold())

    def test_restore_confirmation_defaults_to_cancel_and_hides_path_in_primary_copy(self):
        instances = []

        class CapturingMessage(_FakeRestoreMessage):
            def __init__(self, parent=None):
                super().__init__(parent)
                instances.append(self)

        with patch("ui.components.cloud_ui.QMessageBox", CapturingMessage):
            result = confirm_restore(
                None,
                game_name="Example Game",
                entry=None,
                target_path="/home/user/.local/share/example",
                title="Restore cloud save version",
            )

        message = instances[0]
        self.assertFalse(result)
        self.assertIs(message.default_button, message.escape_button)
        self.assertNotIn("/home/user/.local/share/example", message.text_value)
        self.assertNotIn("/home/user/.local/share/example", message.informative_value)
        self.assertIn("/home/user/.local/share/example", message.details_value)

    def test_restore_message_places_target_path_in_details(self):
        instances = []

        class CapturingMessage(_FakeRestoreMessage):
            def __init__(self, parent=None):
                super().__init__(parent)
                instances.append(self)

        with patch("ui.components.cloud_ui.QMessageBox", CapturingMessage):
            confirm_restore(
                None,
                game_name="Example Game",
                target_path="/tmp/game-save",
            )

        message = instances[0]
        self.assertNotIn("/tmp/game-save", message.text_value)
        self.assertNotIn("/tmp/game-save", message.informative_value)
        self.assertIn("/tmp/game-save", message.details_value)
        self.assertIs(message.default_button, message.escape_button)

    def test_delete_confirmation_defaults_to_no(self):
        _FakeDeleteMessage.answer = _FakeDeleteMessage.StandardButton.No
        with patch("ui.components.cloud_ui.QMessageBox", _FakeDeleteMessage):
            result = confirm_delete(None, game_name="Example Game", version=3)

        self.assertFalse(result)
        self.assertEqual(_FakeDeleteMessage.call[-1], _FakeDeleteMessage.StandardButton.No)
        self.assertIn("Example Game", _FakeDeleteMessage.call[2])
        self.assertNotIn("name_key", _FakeDeleteMessage.call[2])

    def test_history_entries_have_accessible_selection_metadata(self):
        timeline = SaveHistoryTimeline()
        timeline.set_entries([
            {
                "source": "cloud",
                "version": 2,
                "createdAt": 1_700_000_000,
                "deviceName": "Desktop",
            }
        ])
        button = next(iter(timeline._buttons.values()))
        self.assertEqual(button.accessibleName(), "Cloud save version 2")
        self.assertIn("Created and uploaded on Desktop", button.accessibleDescription())
        timeline.deleteLater()

    def test_history_is_grouped_by_upload_device(self):
        timeline = SaveHistoryTimeline()
        timeline.set_entries([
            {
                "source": "cloud",
                "version": 2,
                "createdAt": 1_700_000_100,
                "uploadedDeviceName": "Steam Deck",
            },
            {
                "source": "cloud",
                "version": 1,
                "createdAt": 1_700_000_000,
                "uploadedDeviceName": "Desktop",
            },
        ])
        section_labels = []
        for index in range(timeline.content_layout.count()):
            widget = timeline.content_layout.itemAt(index).widget()
            if widget is None:
                continue
            section_labels.extend(
                child.text()
                for child in widget.findChildren(QLabel)
                if child.text().startswith("Device: ")
            )
        self.assertEqual(section_labels, ["Device: Steam Deck", "Device: Desktop"])
        timeline.deleteLater()

    def test_history_shows_registered_devices_without_versions(self):
        timeline = SaveHistoryTimeline()
        timeline.set_devices([
            {"deviceName": "Desktop"},
            {"name": "Steam Deck"},
        ])
        timeline.set_entries([
            {
                "source": "cloud",
                "version": 1,
                "createdAt": 1_700_000_000,
                "uploadedDeviceName": "Desktop",
            }
        ])
        section_labels = []
        for index in range(timeline.content_layout.count()):
            widget = timeline.content_layout.itemAt(index).widget()
            if widget is None:
                continue
            section_labels.extend(
                child.text()
                for child in widget.findChildren(QLabel)
                if child.text().startswith("Device: ")
            )
        self.assertEqual(section_labels, ["Device: Desktop", "Device: Steam Deck"])
        self.assertTrue(
            any(
                child.text() == "No cloud versions from this device."
                for child in timeline.content.findChildren(QLabel)
            )
        )
        timeline.set_entries([])
        empty_sections = []
        for index in range(timeline.content_layout.count()):
            widget = timeline.content_layout.itemAt(index).widget()
            if widget is None:
                continue
            empty_sections.extend(
                child.text()
                for child in widget.findChildren(QLabel)
                if child.text().startswith("Device: ")
            )
        self.assertEqual(empty_sections, ["Device: Desktop", "Device: Steam Deck"])
        timeline.deleteLater()

    def test_shared_cloud_status_panel_covers_all_states_with_text(self):
        panel = CloudStatusPanel(show_action=True)
        states = (
            ("loading", panel.set_loading, ""),
            ("ready", lambda: panel.set_ready("Current"), "Refresh"),
            ("stale", lambda: panel.set_ready("Cached", stale=True), "Refresh"),
            ("offline", panel.set_offline, "Retry"),
            ("empty", panel.set_empty, ""),
            ("error", lambda: panel.set_error("Try again"), "Retry"),
        )
        for expected, setter, action in states:
            setter()
            self.assertEqual(panel.state, expected)
            self.assertTrue(panel.lbl_status.text())
            self.assertTrue(panel.lbl_message.text())
            self.assertEqual(panel.btn_action.text(), action)
            self.assertEqual(panel.lbl_status.accessibleName(), "Cloud status")
            self.assertTrue(panel.lbl_status.accessibleDescription())
        panel.deleteLater()

    def test_cloud_focus_order_is_explicit_and_recorded(self):
        dialog = QDialog()
        first = QPushButton("Refresh", dialog)
        second = QPushButton("Restore", dialog)
        third = QPushButton("Close", dialog)
        order = set_cloud_focus_order(dialog, first, second, third)
        self.assertEqual(order, (first, second, third))
        self.assertEqual(dialog._cloud_focus_order, order)
        dialog.deleteLater()

    def test_cloud_initial_focus_uses_a_safe_primary_control(self):
        dialog = QDialog()
        refresh = QPushButton("Refresh", dialog)
        restore = QPushButton("Restore", dialog)
        set_cloud_initial_focus(dialog, refresh)
        self.assertIs(dialog._cloud_initial_focus, refresh)
        self.assertIsNot(dialog._cloud_initial_focus, restore)
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
