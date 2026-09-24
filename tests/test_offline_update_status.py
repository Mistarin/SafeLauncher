"""Regression coverage for cached Steam update facts in offline mode."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtWidgets import QApplication

from core.achievement_state_store import AchievementStateStore
from core.game_status import GameStatusState, update_indicator
from core.library_metadata_state import LibraryMetadataState
from ui.library_list import LibraryListItemWidget
from ui.main_window import MainWindow


class OfflineUpdateStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cached_update_is_explicitly_distinguished_from_live_update(self):
        live = update_indicator(True)
        cached = update_indicator(True, source="cached", checked_at=1_700_000_000)

        self.assertEqual(live.label, "Game Update: Available")
        self.assertIn("cached", cached.label)
        self.assertIn("Last checked online", cached.tooltip)
        self.assertIn("Reconnect", cached.tooltip)
        self.assertNotEqual(live.color, cached.color)

    def test_persisted_build_projection_is_loaded_as_cached_state(self):
        state = LibraryMetadataState()
        achievements = AchievementStateStore()

        self.assertTrue(state.load_payload({
            "steam_builds": {
                "4": {
                    "latest_build_id": "build-2",
                    "latest_build_date": 12,
                    "is_update": True,
                    "checked_at": 20,
                }
            }
        }, achievements))

        status = state.game_status_by_id[4]
        self.assertTrue(status.update_available)
        self.assertEqual(status.update_source, "cached")
        self.assertEqual(status.update_checked_at, 20.0)

    def test_offline_path_preserves_a_valid_cached_comparison(self):
        fake = SimpleNamespace(
            _updates_offline=False,
            nav_updates=None,
            selected_game=None,
            steam_check_results={4: ("build-2", 12, True, "")},
            metadata_attempted_builds={4},
            library_metadata_state=SimpleNamespace(steam_build_checked_at={4: 20.0}),
            _show_toast=Mock(),
            _set_network_status=Mock(),
            _on_steam_build_checked=Mock(),
        )

        MainWindow._on_update_check_offline(fake, 4)

        self.assertEqual(fake.steam_check_results[4], ("build-2", 12, True, ""))
        fake._on_steam_build_checked.assert_called_once_with(
            4,
            "build-2",
            12,
            True,
            source="cached",
            checked_at=20.0,
        )
        self.assertNotIn(4, fake.metadata_attempted_builds)

    def test_library_row_explains_that_an_update_is_cached(self):
        game = (
            4, "Cached Game", "", "", "linux", "", "480", 0, 0,
            0, "", "", "", "", 0, "", "", 1, "", "{}", 0,
        )
        row = LibraryListItemWidget(
            game,
            is_missing=True,
            is_update_available=True,
            update_source="cached",
            update_checked_at=1_700_000_000,
            cache_dir="/tmp/cache",
        )

        self.assertIn("cached", row.update_badge.text())
        self.assertIn("Last checked online", row.update_badge.toolTip())
        row.deleteLater()

    def test_footer_stays_visible_when_a_late_callback_reports_online(self):
        class Settings:
            def value(self, key, default=None, type=None):
                if key == "offline_mode":
                    return True
                return default

        label = Mock()
        fake = SimpleNamespace(settings=Settings(), lbl_network_status=label)

        # A late live-result callback passes offline=False, but the persisted
        # policy remains authoritative for the footer.
        MainWindow._set_network_status(fake, False)

        label.setVisible.assert_called_once_with(True)
        self.assertTrue(fake._network_offline_detected)


if __name__ == "__main__":
    unittest.main()
