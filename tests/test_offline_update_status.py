"""Regression coverage for cached Steam update facts in offline mode."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6.QtWidgets import QApplication

from core.achievement_state_store import AchievementStateStore
from core.cloud_models import SyncStatus
from core.cloud_operations import CloudStatusResult
from core.game_status import GameStatusState, update_indicator
from core.library_metadata_state import LibraryMetadataState
from core.request_contracts import RequestKey, ResourceResult, ResourceStatus
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

    def test_returning_online_forces_cloud_and_game_version_refreshes(self):
        class Settings:
            def value(self, key, default=None, type=None):
                if key == "offline_mode":
                    return False
                return default

        fake = SimpleNamespace(
            settings=Settings(),
            _automatic_network_allowed=Mock(return_value=True),
            _set_network_status=Mock(),
            _show_toast=Mock(),
            _refresh_library=Mock(),
            _start_cloud_poll_timer=Mock(),
            cloud_center_service=SimpleNamespace(invalidate_account_reads=Mock()),
            _refresh_cloud_center_indicator=Mock(),
            request_cloud_recheck=Mock(),
            _check_all_steam_updates=Mock(),
            _start_background_achievement_sync=Mock(),
            _sync_profile_metadata_async=Mock(),
            _check_backend_update_on_startup=Mock(),
            _update_check_timer=Mock(),
            _startup_backend_health=None,
            _startup_update_notice_shown=False,
        )

        with patch("ui.main_window.QTimer.singleShot") as single_shot:
            MainWindow._apply_network_policy_change(fake, True)

        fake.request_cloud_recheck.assert_called_once_with(None, "online-mode")
        fake.cloud_center_service.invalidate_account_reads.assert_called_once_with()
        fake._refresh_cloud_center_indicator.assert_called_once_with()
        self.assertTrue(any(
            len(call.args) == 2 and call.args[0] == 300
            and call.args[1] is fake._check_all_steam_updates
            for call in single_shot.call_args_list
        ))

    def test_detail_cloud_panel_has_explicit_checking_state(self):
        from PyQt6.QtWidgets import QLabel, QPushButton

        detail = QLabel()
        restore = QPushButton()
        restore.show()
        fake = SimpleNamespace(
            detail_cloud_status=detail,
            btn_detail_cloud_restore=restore,
        )

        MainWindow._set_detail_cloud_checking(fake)

        self.assertIn("Checking", detail.text())
        self.assertFalse(restore.isVisible())

    def test_managed_cloud_error_resolves_detail_to_unavailable(self):
        key = RequestKey("cloud-save-status", "context:4", "v1")
        received = []
        fake = SimpleNamespace(
            _cloud_status_callbacks={key: [(0, 4, lambda *args: received.append(args))]},
            _cloud_status_target_ids={key: 4},
            cloud_sync_coordinator=SimpleNamespace(accepts=lambda generation: generation == 0),
            _automatic_network_allowed=lambda: True,
        )
        fake._cloud_status_failure_status = MainWindow._cloud_status_failure_status.__get__(fake)
        fake._deliver_managed_cloud_status = MainWindow._deliver_managed_cloud_status.__get__(fake)

        MainWindow._on_managed_cloud_status_state(
            fake,
            key,
            ResourceResult(
                key=key,
                status=ResourceStatus.ERROR,
                error=TimeoutError("backend timed out"),
            ),
        )

        self.assertEqual(received, [(4, SyncStatus.CLOUD_UNAVAILABLE, None, None)])
        self.assertNotIn(key, fake._cloud_status_callbacks)

    def test_managed_cloud_status_fans_out_to_detail_and_library(self):
        key = RequestKey("cloud-save-status", "context:4", "v1")
        received = []
        callback = lambda name: lambda *args: received.append((name, args))
        fake = SimpleNamespace(
            _cloud_status_callbacks={key: [
                (0, 4, callback("library")),
                (0, 4, callback("detail")),
            ]},
            _cloud_status_target_ids={key: 4},
            cloud_sync_coordinator=SimpleNamespace(accepts=lambda generation: generation == 0),
            _automatic_network_allowed=lambda: True,
        )
        fake._cloud_status_failure_status = MainWindow._cloud_status_failure_status.__get__(fake)
        fake._deliver_managed_cloud_status = MainWindow._deliver_managed_cloud_status.__get__(fake)
        expected = CloudStatusResult("Example", SyncStatus.IN_SYNC)

        MainWindow._on_managed_cloud_status_state(
            fake,
            key,
            ResourceResult(key=key, status=ResourceStatus.READY, value=expected),
        )

        self.assertEqual([item[0] for item in received], ["library", "detail"])
        self.assertTrue(all(item[1][1] == SyncStatus.IN_SYNC for item in received))


if __name__ == "__main__":
    unittest.main()
