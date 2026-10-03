"""Offscreen regression tests for achievement text layout and settings surfaces."""

import unittest

from PyQt6.QtWidgets import QApplication, QLabel

from ui.components.compact_game_page import (
    CompactActivityTimelineCard,
    CompactAchievementsShowcaseWidget,
)
from ui.components.achievement_toast import AchievementToast
from ui.dialogs.achievements_dialog import AppleAchievementCard
from ui.dialogs.achievements_dialog import AchievementsDialog
from ui.dialogs.settings_dialog import UserSettingsDialog
from core.achievement_providers import AchievementAvailability, AchievementResolution


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

    def test_empty_local_state_is_distinguished_from_missing_state(self):
        status = QLabel()
        dialog = type("DialogStatusHarness", (), {"status_tag": status})()
        resolution = AchievementResolution(
            schema=[{"api_name": "ACH_FIRST", "display_name": "First"}],
            state={},
            state_path="/prefix/Steam/RUNE/2725260/achievements.ini",
            schema_source="local",
            state_source="local-state",
            availability=AchievementAvailability.AVAILABLE,
            state_available=True,
            state_format="ini",
            state_adapter="codex-rune-steamachievements-ini",
            state_selection_reason="Selected the newest known candidate containing parseable unlock records.",
            state_contributing_paths=["/prefix/Steam/RUNE/2725260/achievements.ini"],
        )

        AchievementsDialog._set_resolution_status(dialog, resolution)

        self.assertIn("0 unlocked", status.text())
        self.assertIn("achievements.ini", status.toolTip())
        self.assertIn("contains no unlocked", status.toolTip())
        self.assertIn("codex-rune-steamachievements-ini", status.toolTip())
        self.assertIn("Selected the newest known candidate", status.toolTip())


class SettingsButtonSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_secondary_buttons_use_dark_surface_without_overriding_roles(self):
        dialog = UserSettingsDialog("Player")
        try:
            dialog.show()
            self.app.processEvents()

            self.assertIn("#18181B", dialog.btn_profile_resync.styleSheet())
            self.assertIn("#202024", dialog.btn_profile_resync.styleSheet())
            self.assertIn("#3B9FE8", dialog.btn_save.styleSheet())
            self.assertIn("#3A171B", dialog.btn_logout.styleSheet())
            self.assertIn("transparent", dialog.tab_buttons[0].styleSheet())

            dialog.btn_profile_resync.setEnabled(False)
            self.assertIn("#121214", dialog.btn_profile_resync.styleSheet())
            self.assertIn("#71717A", dialog.btn_profile_resync.styleSheet())
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
