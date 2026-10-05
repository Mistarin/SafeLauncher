"""Targeted regressions for achievement badge state and async icon refresh."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication

from ui.dialogs.achievements_dialog import AppleAchievementCard


class AchievementRenderingRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_locked_card_never_uses_the_unlocked_icon_as_its_badge(self):
        with tempfile.TemporaryDirectory() as directory:
            unlocked_path = Path(directory) / "unlocked.png"
            locked_path = Path(directory) / "locked.png"
            unlocked = QPixmap(8, 8)
            unlocked.fill(QColor("#ff0000"))
            locked = QPixmap(8, 8)
            locked.fill(QColor("#0000ff"))
            self.assertTrue(unlocked.save(str(unlocked_path)))
            self.assertTrue(locked.save(str(locked_path)))

            card = AppleAchievementCard({
                "api_name": "ACH_TEST",
                "display_name": "Test",
                "unlocked": False,
                "icon_path": str(unlocked_path),
                "icongray_path": str(locked_path),
            })
            try:
                displayed = card.icon_lbl.pixmap().toImage().pixelColor(26, 26)
                self.assertEqual(displayed.blue(), 255)
                self.assertEqual(displayed.red(), 0)

                card.ach["icongray_path"] = ""
                card._refresh_badge_icon()
                fallback = card.icon_lbl.pixmap().toImage().pixelColor(26, 26)
                self.assertNotEqual(fallback.red(), 255)
            finally:
                card.close()
                card.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
