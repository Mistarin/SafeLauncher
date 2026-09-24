"""Offscreen regression tests for achievement text layout and settings surfaces."""

import unittest

from PyQt6.QtWidgets import QApplication, QLabel

from ui.components.compact_game_page import (
    CompactActivityTimelineCard,
    CompactAchievementsShowcaseWidget,
)
from ui.components.achievement_toast import AchievementToast
from ui.dialogs.achievements_dialog import AppleAchievementCard
from ui.dialogs.settings_dialog import UserSettingsDialog


class AchievementTextLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_full_achievement_card_wraps_long_text_and_keeps_tooltips(self):
        name = "A very long achievement title that must remain readable instead of being cut off"
        description = (
            "A deliberately long description explains the achievement in detail and should wrap "
            "inside the card at narrow widths without disappearing behind the status badge."
        )
        card = AppleAchievementCard({
            "display_name": name,
            "description": description,
            "api_name": "LONG_ACHIEVEMENT",
            "unlocked": False,
        })
        try:
            self.assertTrue(card.title_lbl.wordWrap())
            self.assertTrue(card.desc_lbl.wordWrap())
            self.assertEqual(card.title_lbl.toolTip(), name)
            self.assertEqual(card.desc_lbl.toolTip(), description)
            card.resize(360, 160)
            card.show()
            self.app.processEvents()
            self.assertGreater(card.height(), 80)
        finally:
            card.close()
            card.deleteLater()
            self.app.processEvents()

    def test_compact_achievement_surfaces_wrap_and_expose_full_text(self):
        name = "A long recent achievement title that should wrap in the compact activity feed"
        description = "A long recent achievement description that remains available from the compact card tooltip."

        activity = CompactActivityTimelineCard()
        showcase = CompactAchievementsShowcaseWidget()
        try:
            activity.set_recent_achievements([
                {"display_name": name, "description": description, "unlock_time": 0}
            ])
            activity_labels = [label for label in activity.findChildren(QLabel) if label.text() in {name, description}]
            self.assertEqual(len(activity_labels), 2)
            self.assertTrue(all(label.wordWrap() for label in activity_labels))
            self.assertEqual(next(label for label in activity_labels if label.text() == name).toolTip(), name)
            self.assertEqual(next(label for label in activity_labels if label.text() == description).toolTip(), description)

            showcase.set_achievements_data(1, 10, 10, [{
                "display_name": name,
                "description": description,
            }], [])
            self.assertTrue(showcase.recent_title.wordWrap())
            self.assertTrue(showcase.recent_desc.wordWrap())
            self.assertEqual(showcase.recent_title.toolTip(), name)
            self.assertEqual(showcase.recent_desc.toolTip(), description)
        finally:
            activity.deleteLater()
            showcase.deleteLater()
            self.app.processEvents()

    def test_unlock_toast_wraps_long_title_and_description(self):
        name = "A very long unlock notification title that must not be clipped"
        description = "The unlock notification description should remain readable and available in a tooltip."
        toast = AchievementToast(name, description)
        try:
            labels = [label for label in toast.findChildren(QLabel) if label.text() in {name, description}]
            self.assertEqual(len(labels), 2)
            title = next(label for label in labels if label.text() == name)
            desc = next(label for label in labels if label.text() == description)
            self.assertTrue(title.wordWrap())
            self.assertTrue(desc.wordWrap())
            self.assertEqual(title.toolTip(), name)
            self.assertEqual(desc.toolTip(), description)
        finally:
            toast.close()
            toast.deleteLater()
            self.app.processEvents()


class SettingsButtonSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_secondary_buttons_use_dark_surface_without_overriding_roles(self):
        dialog = UserSettingsDialog("Player")
        try:
            dialog.show()
            self.app.processEvents()

            self.assertIn("#171A20", dialog.btn_profile_resync.styleSheet())
            self.assertIn("#202633", dialog.btn_profile_resync.styleSheet())
            self.assertIn("#3B9FE8", dialog.btn_save.styleSheet())
            self.assertIn("#3A171B", dialog.btn_logout.styleSheet())
            self.assertIn("transparent", dialog.tab_buttons[0].styleSheet())

            dialog.btn_profile_resync.setEnabled(False)
            self.assertIn("#12151A", dialog.btn_profile_resync.styleSheet())
            self.assertIn("#777C86", dialog.btn_profile_resync.styleSheet())
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
