"""Positioning and responsive-layout checks for floating notifications."""

import unittest

from PyQt6.QtWidgets import QApplication, QWidget

from ui.components.achievement_toast import AchievementToast
from ui.components import overlay_hud
from ui.dialogs.game_dialogs import ToastNotification


class NotificationLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_launcher_toasts_reflow_and_stack_after_parent_resize(self):
        parent = QWidget()
        parent.resize(520, 380)
        parent.show()
        first = ToastNotification(parent, "A long notification that should wrap within the window " * 3)
        second = ToastNotification(parent, "Second notification")
        try:
            first.show_toast(parent)
            second.show_toast(parent)
            self.app.processEvents()
            self.assertTrue(first.text_label.wordWrap())
            self.assertLessEqual(first.width(), 420)
            self.assertLess(first.y(), second.y())

            parent.resize(330, 250)
            self.app.processEvents()
            self.assertLessEqual(first.x() + first.width(), parent.width() - 17)
            self.assertLessEqual(second.x() + second.width(), parent.width() - 17)
            self.assertLess(first.y(), second.y())
        finally:
            first.close()
            second.close()
            parent.close()
            self.app.processEvents()

    def test_achievement_toasts_wrap_stack_and_follow_parent_resize(self):
        parent = QWidget()
        parent.resize(600, 440)
        parent.show()
        toasts = [
            AchievementToast(
                f"Achievement {index}: a title long enough to wrap at small window widths",
                "Description text that should reflow when the launcher window becomes narrower." * 2,
                parent=parent,
                duration_ms=10000,
            )
            for index in range(2)
        ]
        try:
            for toast in toasts:
                toast.show_animated(parent)
            self.app.processEvents()
            self.assertLess(toasts[0].y(), toasts[1].y())
            parent.resize(360, 300)
            self.app.processEvents()
            self.assertLessEqual(toasts[0].x() + toasts[0].width(), parent.frameGeometry().right() + 1)
            self.assertLess(toasts[0].y(), toasts[1].y())
            self.assertTrue(toasts[0].title_label.wordWrap())
        finally:
            for toast in toasts:
                toast.close()
            parent.close()
            self.app.processEvents()

    def test_game_hud_wraps_and_stacks_within_available_screen(self):
        screen = self.app.primaryScreen()
        self.assertIsNotNone(screen)
        area = screen.availableGeometry()
        widgets = [
            overlay_hud.GameOverlayNotificationWidget(
                f"Recording notification {index} with a long title",
                "A very long output filename that should wrap instead of exceeding the card width. " * 3,
                target_screen=screen.name(),
                duration_ms=10000,
            )
            for index in range(2)
        ]
        try:
            overlay_hud._ACTIVE_OVERLAYS.extend(widgets)
            for widget in widgets:
                widget.show_animated()
            self.app.processEvents()
            self.assertTrue(widgets[0].title_label.wordWrap())
            self.assertLessEqual(widgets[0].width(), widgets[0].W)
            self.assertLess(widgets[0].y(), widgets[1].y())
            for widget in widgets:
                self.assertGreaterEqual(widget.x(), area.left())
                self.assertGreaterEqual(widget.y(), area.top())
                self.assertLessEqual(widget.x() + widget.width(), area.right() + 1)
                self.assertLessEqual(widget.y() + widget.height(), area.bottom() + 1)
        finally:
            for widget in widgets:
                widget.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
