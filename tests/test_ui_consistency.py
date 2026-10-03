import unittest
from types import SimpleNamespace

from PyQt6.QtWidgets import (
    QApplication, QFormLayout, QLineEdit, QLabel, QToolButton, QFrame, QComboBox,
    QPushButton, QMainWindow, QWidget, QCheckBox,
)
from PyQt6.QtCore import QEvent, QSettings, Qt
from PyQt6.QtGui import QKeyEvent
from unittest.mock import Mock, patch

from core.launch_diagnostics import LaunchDiagnostics
from ui.components.popup_shell import PopupDialog
from ui.dialogs.game_dialogs import AddGameDialog, SafeLaunchDialog
from ui.dialogs.settings_dialog import UserSettingsDialog
from ui.components.sidebar import CustomTitleBar
from ui.main_window import MainWindow
from ui.components.banner_card import GameBannerWidget
from ui.components.virtual_grid import VirtualizedGameGridView
from ui.components.library_view_host import LibraryViewHost
from ui.components.game_detail_page import GameDetailPageWidget
from core.cloud_models import SyncStatus
from core.cloud_models import SaveStats


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
            self.assertIn("#18181B", stylesheet)
            self.assertIn("#1C1C1F", stylesheet)
            self.assertIn("QLabel#propertyLabel", stylesheet)
            self.assertIn("#18181B", dialog.findChildren(QFrame, "settingsSection")[0].styleSheet())
            self.assertIn("#1C1C1F", dialog.name_input.styleSheet())
            self.assertGreaterEqual(dialog.name_input.minimumHeight(), 36)
            self.assertIn("border: none", dialog.findChildren(QFrame, "settingsSection")[0].styleSheet())
            self.assertTrue(hasattr(dialog, "chk_launch_startup"))
            self.assertLess(
                dialog.findChildren(QCheckBox)[0].geometry().top(),
                dialog.chk_welcome.geometry().top(),
            )
            self.assertEqual(dialog.btn_save.minimumHeight(), dialog.btn_cancel.minimumHeight())
            self.assertIn("#3B9FE8", dialog.btn_desktop_entry.styleSheet())
            self.assertTrue(all(
                widget.findChild(QToolButton, "propertyInfo") is not None
                for widget in dialog.findChildren(QWidget, "propertyHint")
            ))
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_repeated_launch_activation_is_ignored_while_prelaunch_is_pending(self):
        fake = SimpleNamespace(
            _prelaunch_in_flight={7: {"token": "active"}},
            _show_toast=Mock(),
        )

        MainWindow._launch_mode(fake, 7, "/game", "game.exe", "umu")

        fake._show_toast.assert_called_once_with(
            "Launch is already preparing this game's cloud save."
        )

    def test_already_running_launch_continuation_never_stops_game(self):
        coordinator = Mock(return_value=SimpleNamespace(already_running=True))
        fake = SimpleNamespace(
            launch_session_coordinator=SimpleNamespace(start=coordinator),
            _update_detail_launch_button=Mock(),
            _show_toast=Mock(),
            _stop_game=Mock(),
        )
        context = {
            "game_id": 7,
            "game_name": "Example",
            "path": "/game",
            "exe": "game.exe",
            "steam_id": "",
            "sandbox": True,
            "env_vars": {},
            "selected_mode": "umu",
            "selected_proton": "",
        }

        MainWindow._continue_launch(fake, context)

        fake._stop_game.assert_not_called()
        fake._update_detail_launch_button.assert_called_once_with(7)
        fake._show_toast.assert_called_once_with("'Example' is already running.")

    def test_prelaunch_cancel_requests_worker_stop_but_keeps_launch_lock_until_completion(self):
        progress = Mock()
        handle = SimpleNamespace(request_id="operation-1")
        cancel = Mock(return_value=True)
        fake = SimpleNamespace(
            _prelaunch_in_flight={7: {
                "token": "active",
                "handle": handle,
                "progress": progress,
                "cancel_requested": False,
            }},
            cloud_operation_service=SimpleNamespace(cancel=cancel),
        )

        MainWindow._cancel_prelaunch(fake, 7, "active")

        cancel.assert_called_once_with("operation-1")
        progress.setLabelText.assert_called_once_with("Cancelling cloud sync…")
        progress.setCancelButton.assert_called_once_with(None)
        self.assertTrue(fake._prelaunch_in_flight[7]["cancel_requested"])
        self.assertTrue(MainWindow._prelaunch_pending(fake, 7))

    def test_cancelled_prelaunch_result_aborts_launch_and_retires_pending_state(self):
        ctx = {"game_id": 7, "game_name": "Example", "prelaunch_token": "active"}
        fake = SimpleNamespace(
            _prelaunch_in_flight={7: {"token": "active", "cancel_requested": True}},
            _close_prelaunch_progress=Mock(),
            _finish_prelaunch=Mock(),
            _show_toast=Mock(),
        )

        MainWindow._finish_prelaunch_sync(fake, {"ctx": ctx, "cancelled": True})

        fake._close_prelaunch_progress.assert_called_once_with(ctx)
        fake._finish_prelaunch.assert_called_once_with(ctx)
        fake._show_toast.assert_called_once_with(
            "Launch cancelled — cloud sync for 'Example' was stopped."
        )

    def test_cancel_racing_with_completed_cloud_operation_still_aborts_launch(self):
        ctx = {"game_id": 7, "game_name": "Example", "prelaunch_token": "active"}
        fake = SimpleNamespace(
            _prelaunch_in_flight={7: {"token": "active", "cancel_requested": True}},
            _close_prelaunch_progress=Mock(),
            _finish_prelaunch=Mock(),
            _show_toast=Mock(),
        )

        MainWindow._on_prelaunch_restore_done(fake, {
            "ctx": ctx,
            "ok": True,
            "cancelled": False,
            "toast": "Restored cloud save.",
        })

        fake._finish_prelaunch.assert_called_once_with(ctx)
        fake._show_toast.assert_called_once_with(
            "Launch cancelled — cloud sync for 'Example' was stopped."
        )

    def test_cloud_settings_routes_account_work_to_cloud_center(self):
        dialog = UserSettingsDialog("Player", parent=None)
        try:
            self.assertEqual(dialog.btn_open_cloud_center.text(), "Open Cloud Center…")
            self.assertFalse(dialog.tab_buttons[3].isVisible())
            self.assertFalse(dialog.btn_review_conflicts.isVisible())
            self.assertFalse(dialog.btn_refresh_quota.isHidden())
            self.assertTrue(any(
                "Cloud Center is the single account-wide cloud surface" in label.text()
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

    def test_add_game_button_checked_payload_is_not_treated_as_collection(self):
        dialog = Mock()
        dialog.exec.return_value = 1  # QDialog.DialogCode.Accepted
        dialog.get_values.return_value = ("Example", "/tmp", "game.exe", "sandbox", "")
        dialog.get_steam_id.return_value = ""
        dialog.get_version_metadata.return_value = ("", "")
        dialog.get_build_id.return_value = ""
        dialog.get_build_date.return_value = ""

        service = Mock()
        service.upsert_game.return_value = 42
        fake_window = SimpleNamespace(
            sgdb_client=Mock(),
            request_manager=Mock(),
            library_service=service,
            _record_initial_steam_build=Mock(),
            _refresh_library=Mock(),
            _sync_launcher_metadata_async=Mock(),
            _show_toast=Mock(),
        )

        with patch("ui.main_window.AddGameDialog", return_value=dialog), \
                patch("ui.main_window.save_sandbox_config"):
            # QPushButton.clicked emits False/True even though the slot needs
            # no argument. This must not become the literal collection name.
            MainWindow._on_add(fake_window, True)

        service.update_collection_membership.assert_not_called()

    def test_header_view_menu_exposes_only_library_navigation(self):
        class _HeaderWindow(QMainWindow):
            def _toggle_maximize(self):
                pass

        window = _HeaderWindow()
        title_bar = CustomTitleBar(window)
        try:
            labels = [action.text() for action in title_bar.view_menu.actions()]
            self.assertEqual(labels, ["Library"])
            self.assertFalse(title_bar.btn_friends.isVisible())
        finally:
            title_bar.deleteLater()
            window.deleteLater()
            self.app.processEvents()

    def test_identity_menu_owns_profile_and_social_navigation(self):
        class _HeaderWindow(QMainWindow):
            def _toggle_maximize(self):
                pass

        window = _HeaderWindow()
        title_bar = CustomTitleBar(window)
        try:
            labels = [action.text() for action in title_bar.profile_menu.actions() if not action.isSeparator()]
            self.assertEqual(labels, ["Profile", "Friends"])
        finally:
            title_bar.deleteLater()
            window.deleteLater()
            self.app.processEvents()

    def test_cloud_badge_is_an_actionable_control(self):
        card = GameBannerWidget(42, "Test Game")
        actions = []
        card.cloudActionRequested.connect(actions.append)
        try:
            card.set_cloud_status(SyncStatus.LOCAL_NEWER)
            card.cloud_badge.click()
            self.assertEqual(actions, [42])
            self.assertIn("Cloud save actions", card.cloud_badge.accessibleName())
        finally:
            card.deleteLater()
            self.app.processEvents()

    def test_virtual_grid_ignores_unmapped_ctrl_u_shortcut(self):
        grid = VirtualizedGameGridView()
        actions = []
        grid.cloud_action_requested.connect(lambda game_id, action: actions.append((game_id, action)))
        game = (1, "Test Game", "", "game", "umu", "", "", 0, 0, "", "", "", "", "", "", "", "", 0, "")
        try:
            grid.set_games(
                [game],
                selected_ids={1},
                cloud_status_map={1: (SyncStatus.LOCAL_NEWER, None, None)},
            )
            grid.setCurrentIndex(grid.model.index(0, 0))
            grid.keyPressEvent(
                QKeyEvent(
                    QEvent.Type.KeyPress,
                    Qt.Key.Key_U,
                    Qt.KeyboardModifier.ControlModifier,
                )
            )
            self.assertEqual(actions, [])
        finally:
            grid.deleteLater()
            self.app.processEvents()

    def test_library_shortcuts_do_not_override_buttons_or_combos(self):
        button = QPushButton("Continue")
        combo = QComboBox()
        try:
            with patch("ui.main_window.QApplication.focusWidget", return_value=button):
                self.assertFalse(MainWindow._shortcut_allowed(object()))
            with patch("ui.main_window.QApplication.focusWidget", return_value=combo):
                self.assertFalse(MainWindow._shortcut_allowed(object()))
            with patch("ui.main_window.QApplication.focusWidget", return_value=None):
                self.assertTrue(MainWindow._shortcut_allowed(object()))
        finally:
            button.deleteLater()
            combo.deleteLater()

    def test_stopping_game_schedules_process_cleanup_off_gui_thread(self):
        tracker = SimpleNamespace(game_id=42, process=object(), sandbox_name="safe-42")
        scheduled = []
        fake = SimpleNamespace(
            _game_termination_in_flight=set(),
            _stopping_game_ids=set(),
            _closing=False,
            compact_container=None,
            selected_game=None,
            game_sessions=SimpleNamespace(mark_stopping=Mock()),
            launch_session_coordinator=SimpleNamespace(trackers=lambda: [tracker]),
            _game_termination_tasks=SimpleNamespace(
                start=lambda name, work: scheduled.append((name, work))
            ),
            _show_toast=Mock(),
            _update_detail_launch_button=Mock(),
        )
        with patch("ui.main_window.terminate_game_process") as terminate:
            MainWindow._stop_game(fake, 42)
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0][0], "StopGame-42")
        terminate.assert_not_called()

    def test_grid_empty_state_emits_add_game_action(self):
        host = LibraryViewHost()
        actions = []
        host.add_game_requested.connect(lambda: actions.append("add"))
        try:
            host.set_empty_grid_message("No games yet", show_add=True)
            self.assertEqual(host._empty_label.text(), "No games yet")
            self.assertFalse(host._empty_add_button.isHidden())
            host._empty_add_button.click()
            self.assertEqual(actions, ["add"])
            host.set_empty_grid_message("No matching games", show_add=False)
            self.assertTrue(host._empty_add_button.isHidden())
        finally:
            host.deleteLater()
            self.app.processEvents()

    def test_game_detail_shows_cloud_save_time_and_device(self):
        detail = GameDetailPageWidget()
        try:
            detail.set_cloud_status(
                SyncStatus.CLOUD_NEWER,
                SaveStats(exists=True, last_modified=1_700_000_000, device_name="Steam Deck"),
                SaveStats(exists=True, last_modified=1_700_000_100, device_name="Desktop"),
            )
            self.assertFalse(detail.lbl_cloud_metadata.isHidden())
            self.assertIn("Saved:", detail.lbl_cloud_metadata.text())
            self.assertIn("Device: Desktop", detail.lbl_cloud_metadata.text())
        finally:
            detail.deleteLater()
            self.app.processEvents()

    def test_background_cloud_restore_enters_syncing_and_targets_version(self):
        restore = Mock()
        restore.future = SimpleNamespace(add_done_callback=Mock())
        cloud = SaveStats(exists=True, cloud_version=12)
        fake = SimpleNamespace(
            running_game_ids=set(),
            _cloud_auto_restore_in_flight={},
            games_by_id={7: (7, "Example", "/saves/example", "game.exe", "umu", "", "480")},
            game_status_by_id={},
            cloud_operation_service=SimpleNamespace(
                active_operations=Mock(return_value=[]),
                request_restore=Mock(return_value=restore),
            ),
            cloud_sync_coordinator=SimpleNamespace(generation=4),
            _automatic_network_allowed=Mock(return_value=True),
            _render_cloud_status=Mock(),
            _update_detail_launch_button=Mock(),
        )
        fake._set_cloud_syncing = MainWindow._set_cloud_syncing.__get__(fake)
        MainWindow._maybe_auto_restore_cloud_save(
            fake,
            7,
            SyncStatus.CLOUD_NEWER,
            SaveStats(exists=True, last_modified=100),
            cloud,
        )

        self.assertEqual(fake.game_status_by_id[7].cloud_status, SyncStatus.SYNCING)
        fake.cloud_operation_service.request_restore.assert_called_once()
        target = fake.cloud_operation_service.request_restore.call_args.args[0]
        self.assertEqual((target.game_id, target.game_name, target.game_path), (7, "Example", "/saves/example"))
        self.assertEqual(fake.cloud_operation_service.request_restore.call_args.kwargs["target_version"], 12)
        self.assertEqual(fake.cloud_operation_service.request_restore.call_args.kwargs["tag"], "automatic-cloud-restore")

        # A second status callback cannot queue another restore for the same game.
        MainWindow._maybe_auto_restore_cloud_save(fake, 7, SyncStatus.CLOUD_NEWER, None, cloud)
        fake.cloud_operation_service.request_restore.assert_called_once()

    def test_background_cloud_restore_is_deferred_while_game_runs(self):
        fake = SimpleNamespace(
            running_game_ids={7},
            _cloud_auto_restore_in_flight={},
            games_by_id={7: (7, "Example", "/saves/example", "game.exe", "umu", "", "480")},
            cloud_operation_service=SimpleNamespace(
                active_operations=Mock(return_value=[]),
                request_restore=Mock(),
            ),
            _automatic_network_allowed=Mock(return_value=True),
        )

        MainWindow._maybe_auto_restore_cloud_save(
            fake,
            7,
            SyncStatus.CLOUD_NEWER,
            None,
            SaveStats(exists=True, cloud_version=12),
        )

        fake.cloud_operation_service.request_restore.assert_not_called()

    def test_background_cloud_upload_enters_syncing_and_is_automatic(self):
        upload = Mock()
        upload.future = SimpleNamespace(add_done_callback=Mock())
        fake = SimpleNamespace(
            running_game_ids=set(),
            _cloud_auto_restore_in_flight={},
            _cloud_auto_upload_in_flight={},
            games_by_id={7: (7, "Example", "/saves/example", "game.exe", "umu", "", "480")},
            game_status_by_id={},
            cloud_operation_service=SimpleNamespace(
                active_operations=Mock(return_value=[]),
                request_upload=Mock(return_value=upload),
            ),
            cloud_sync_coordinator=SimpleNamespace(generation=4),
            _automatic_network_allowed=Mock(return_value=True),
            _render_cloud_status=Mock(),
            _update_detail_launch_button=Mock(),
        )
        fake._set_cloud_syncing = MainWindow._set_cloud_syncing.__get__(fake)

        MainWindow._maybe_auto_upload_cloud_save(
            fake,
            7,
            SyncStatus.LOCAL_NEWER,
            SaveStats(exists=True, last_modified=100, device_name="Desktop"),
            SaveStats(exists=True, cloud_version=11),
        )

        self.assertEqual(fake.game_status_by_id[7].cloud_status, SyncStatus.SYNCING)
        fake.cloud_operation_service.request_upload.assert_called_once()
        self.assertEqual(
            fake.cloud_operation_service.request_upload.call_args.kwargs["tag"],
            "automatic-cloud-upload",
        )
        upload.future.add_done_callback.assert_called_once()

    def test_background_cloud_upload_success_rechecks(self):
        fake = SimpleNamespace(
            _cloud_auto_upload_in_flight={7: (4, "upload")},
            cloud_sync_coordinator=SimpleNamespace(accepts=Mock(return_value=True)),
            _update_detail_launch_button=Mock(),
            _show_toast=Mock(),
            request_cloud_recheck=Mock(),
        )
        MainWindow._on_cloud_auto_upload_done(fake, {
            "game_id": 7,
            "game_name": "Example",
            "generation": 4,
            "success": True,
        })
        self.assertNotIn(7, fake._cloud_auto_upload_in_flight)
        fake.request_cloud_recheck.assert_called_once_with(
            [7], "automatic-upload-complete", auto_sync=False
        )

    def test_background_cloud_restore_failure_rechecks_without_retry_loop(self):
        fake = SimpleNamespace(
            _cloud_auto_restore_in_flight={7: (4, 12)},
            cloud_sync_coordinator=SimpleNamespace(
                accepts=Mock(return_value=True),
            ),
            _update_detail_launch_button=Mock(),
            _show_toast=Mock(),
            request_cloud_recheck=Mock(),
        )

        MainWindow._on_cloud_auto_restore_done(
            fake,
            {
                "game_id": 7,
                "game_name": "Example",
                "generation": 4,
                "target_version": 12,
                "success": False,
                "error": "Backend unavailable",
                "guidance": "Try again later.",
            },
        )

        self.assertNotIn(7, fake._cloud_auto_restore_in_flight)
        fake.request_cloud_recheck.assert_called_once_with([7], "automatic-restore-failed")

    def test_background_cloud_restore_success_rechecks_and_allows_convergence(self):
        fake = SimpleNamespace(
            _cloud_auto_restore_in_flight={7: (4, 12)},
            cloud_sync_coordinator=SimpleNamespace(
                accepts=Mock(return_value=True),
            ),
            _update_detail_launch_button=Mock(),
            _show_toast=Mock(),
            request_cloud_recheck=Mock(),
        )

        MainWindow._on_cloud_auto_restore_done(
            fake,
            {
                "game_id": 7,
                "game_name": "Example",
                "generation": 4,
                "target_version": 12,
                "success": True,
            },
        )

        self.assertNotIn(7, fake._cloud_auto_restore_in_flight)
        fake.request_cloud_recheck.assert_called_once_with(
            [7],
            "automatic-restore-complete",
            auto_sync=False,
        )

    def test_background_cloud_restore_caps_retries_for_unchanged_snapshot(self):
        restore = Mock()
        restore.future = SimpleNamespace(add_done_callback=Mock())
        cloud = SaveStats(
            exists=True,
            cloud_version=12,
            last_modified=200,
            size_bytes=100,
            file_count=4,
            device_name="Desktop",
        )
        fake = SimpleNamespace(
            running_game_ids=set(),
            _cloud_auto_restore_in_flight={},
            _cloud_auto_restore_attempts={},
            games_by_id={7: (7, "Example", "/saves/example", "game.exe", "umu", "", "480")},
            cloud_operation_service=SimpleNamespace(
                active_operations=Mock(return_value=[]),
                request_restore=Mock(return_value=restore),
            ),
            cloud_sync_coordinator=SimpleNamespace(generation=4),
            game_status_by_id={},
            _automatic_network_allowed=Mock(return_value=True),
            _render_cloud_status=Mock(),
            _update_detail_launch_button=Mock(),
        )
        fake._set_cloud_syncing = MainWindow._set_cloud_syncing.__get__(fake)

        for _ in range(3):
            fake._cloud_auto_restore_in_flight.clear()
            MainWindow._maybe_auto_restore_cloud_save(
                fake, 7, SyncStatus.CLOUD_NEWER, None, cloud
            )

        self.assertEqual(fake.cloud_operation_service.request_restore.call_count, 2)

    def test_late_restore_completion_is_ignored_during_shutdown(self):
        fake = SimpleNamespace(
            _closing=True,
            _cloud_auto_restore_in_flight={7: (4, 12)},
            _show_toast=Mock(),
            request_cloud_recheck=Mock(),
        )
        MainWindow._on_cloud_auto_restore_done(fake, {
            "game_id": 7,
            "generation": 4,
            "target_version": 12,
            "success": True,
        })
        self.assertNotIn(7, fake._cloud_auto_restore_in_flight)
        fake.request_cloud_recheck.assert_not_called()

    def test_virtual_grid_exposes_cloud_tooltip_and_accessible_text(self):
        grid = VirtualizedGameGridView()
        game = (1, "Test Game", "", "game", "umu", "", "", 0, 0, "", "", "", "", "", "", "", "", 0, "")
        try:
            grid.set_games(
                [game],
                cloud_status_map={1: (SyncStatus.LOCAL_NEWER, None, None)},
            )
            item = grid.model.item(0, 0)
            tooltip = str(item.data(Qt.ItemDataRole.ToolTipRole))
            accessible = str(item.data(Qt.ItemDataRole.AccessibleTextRole))
            self.assertIn("automatically", tooltip.lower())
            self.assertNotIn("ctrl+u", tooltip.lower())
            self.assertIn("Test Game", accessible)
            self.assertIn("automatically", accessible.lower())
        finally:
            grid.deleteLater()
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
