"""Tests for the application update banner state machine."""

from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from ui.app_update_controller import AppUpdateController


_APP = QApplication.instance() or QApplication([])


class AppUpdateControllerTests(unittest.TestCase):
    def setUp(self):
        self.parent = QWidget()
        self.button = QPushButton("Update", self.parent)
        self.label = QLabel("", self.parent)
        self.banner = QWidget(self.parent)
        self.registered = []
        self.check_completed = Mock()
        self.running_ids = set()
        self.controller = AppUpdateController(
            parent=self.parent,
            action_button=self.button,
            message_label=self.label,
            banner=self.banner,
            register_worker=self.registered.append,
            network_allowed=lambda: True,
            on_check_finished=self.check_completed,
            running_game_ids=lambda: self.running_ids,
            games_provider=lambda: [(1, "Example")],
        )

    def tearDown(self):
        self.parent.close()
        self.parent.deleteLater()
        _APP.processEvents()

    def test_appimage_release_presents_download_action_and_forwards_result(self):
        self.controller._apply_check_result({
            "update_available": True,
            "latest_version": "9.9.9",
            "is_appimage": True,
            "appimage_asset": {"download_url": "https://example.invalid/app.AppImage"},
        })

        self.assertFalse(self.banner.isHidden())
        self.assertEqual(self.button.text(), "Download & Apply")
        self.assertEqual(self.label.text(), "SafeLauncher 9.9.9 is available!")
        self.check_completed.assert_called_once()

    def test_source_checkout_links_to_release_without_downloader(self):
        with patch("ui.app_update_controller.QDesktopServices.openUrl") as open_url:
            self.controller._apply_check_result({
                "update_available": True,
                "latest_version": "9.9.9",
                "is_appimage": False,
                "release_url": "https://example.invalid/release",
            })
            self.button.click()

        open_url.assert_called_once()
        self.assertEqual(self.button.text(), "View Release ↗")

    def test_restart_is_blocked_until_games_are_closed(self):
        self.running_ids.add(1)
        with patch("ui.app_update_controller.QMessageBox.warning") as warning, \
             patch("core.updater.restart_application") as restart:
            self.controller.restart_application()

        warning.assert_called_once()
        self.assertIn("Example", warning.call_args.args[2])
        restart.assert_not_called()

    def test_real_update_prompt_uses_widget_parent(self):
        def dismiss_prompt():
            prompt = QApplication.activeModalWidget()
            if prompt is not None:
                prompt.reject()
        QTimer.singleShot(0, dismiss_prompt)
        self.controller._apply_download_ready('/tmp/verified-update.AppImage')
        self.assertIsInstance(self.controller.dialog_parent, QWidget)

    def test_shutdown_suppresses_queued_update_ui(self):
        self.banner.hide()
        self.controller.shutdown()
        self.controller._apply_check_result({"update_available": True, "latest_version": "9"})
        self.controller._apply_download_ready('/tmp/verified-update.AppImage')
        self.controller._apply_download_failure('late failure')
        self.assertTrue(self.banner.isHidden())
        self.assertEqual(self.label.text(), '')


if __name__ == "__main__":
    unittest.main()
