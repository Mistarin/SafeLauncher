"""Own the application-release check and AppImage update banner lifecycle."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QMessageBox, QWidget


class AppUpdateController(QObject):
    """Coordinate release workers and the update-banner interaction state."""

    _check_result = pyqtSignal(object)
    _download_progress = pyqtSignal(int, int)
    _download_ready = pyqtSignal(str)
    _download_failed = pyqtSignal(str)

    def __init__(
        self,
        *,
        parent: QWidget,
        action_button,
        message_label,
        banner,
        register_worker,
        network_allowed,
        on_check_finished,
        running_game_ids,
        games_provider,
    ) -> None:
        super().__init__(parent)
        self.dialog_parent = parent
        self._closed = False
        self.action_button = action_button
        self.message_label = message_label
        self.banner = banner
        self.register_worker = register_worker
        self.network_allowed = network_allowed
        self.on_check_finished = on_check_finished
        self.running_game_ids = running_game_ids
        self.games_provider = games_provider
        self.latest_update_info: dict = {}
        self._check_worker = None
        self._download_worker = None
        self._check_result.connect(self._apply_check_result)
        self._download_progress.connect(self._apply_download_progress)
        self._download_ready.connect(self._apply_download_ready)
        self._download_failed.connect(self._apply_download_failure)

    def check_for_updates(self) -> None:
        if self._closed or not self.network_allowed():
            return
        import os
        if os.environ.get("SAFELAUNCHER_DISABLE_UPDATE_CHECK") == "1":
            return
        try:
            from core.updater import UpdateCheckWorker

            worker = UpdateCheckWorker(parent=self)
            worker.check_finished.connect(self._check_result.emit)
            self._check_worker = worker
            self.register_worker(worker)
            worker.start()
        except Exception:
            self.latest_update_info = {}
            self.on_check_finished({})

    @pyqtSlot(object)
    def _apply_check_result(self, info) -> None:
        if self._closed:
            return
        info = info if isinstance(info, dict) else {}
        self.latest_update_info = info
        self.on_check_finished(info)
        if self._closed or not info.get("update_available"):
            return

        latest = str(info.get("latest_version") or "")
        self._disconnect_action()
        asset = info.get("appimage_asset")
        if info.get("is_appimage") and asset:
            self.message_label.setText(f"SafeLauncher {latest} is available!")
            self.action_button.setText("Download & Apply")
            self.action_button.clicked.connect(self.download_update)
        else:
            self.message_label.setText(
                f"SafeLauncher {latest} is available on GitHub (source checkout)."
            )
            self.action_button.setText("View Release ↗")
            release_url = info.get("release_url") or "https://github.com/Mistarin/SafeLauncher/releases"
            self.action_button.clicked.connect(
                lambda _checked=False, url=release_url:
                    None if self._closed else QDesktopServices.openUrl(QUrl(str(url)))
            )
        self.banner.setVisible(True)

    def download_update(self) -> None:
        if self._closed:
            return
        asset = self.latest_update_info.get("appimage_asset")
        if not asset or not asset.get("download_url"):
            return
        self.action_button.setEnabled(False)
        self.action_button.setText("Downloading…")
        from core.updater import UpdateDownloadWorker

        worker = UpdateDownloadWorker(str(asset["download_url"]), parent=self)
        worker.progress.connect(self._download_progress.emit)
        worker.finished.connect(self._download_ready.emit)
        worker.failed.connect(self._download_failed.emit)
        self._download_worker = worker
        self.register_worker(worker)
        worker.start()

    @pyqtSlot(int, int)
    def _apply_download_progress(self, downloaded: int, total: int) -> None:
        if self._closed:
            return
        if total > 0:
            percentage = int(downloaded * 100 / total)
            self.message_label.setText(
                f"Downloading SafeLauncher update: {percentage}%…"
            )

    @pyqtSlot(str)
    def _apply_download_ready(self, _target_path: str) -> None:
        if self._closed:
            return
        self.message_label.setText("Update verified and ready to apply!")
        self.action_button.setText("Restart Now")
        self.action_button.setEnabled(True)
        self._disconnect_action()
        self.action_button.clicked.connect(self.restart_application)
        answer = QMessageBox.question(
            self.dialog_parent,
            "Update Ready",
            "SafeLauncher has been successfully updated.\n\nRestart now to apply?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.restart_application()

    @pyqtSlot(str)
    def _apply_download_failure(self, error: str) -> None:
        if self._closed:
            return
        self.message_label.setText(f"Update failed: {error}")
        self.action_button.setText("Retry")
        self.action_button.setEnabled(True)
        self._disconnect_action()
        self.action_button.clicked.connect(self.download_update)

    def restart_application(self) -> None:
        if self._closed:
            return
        game_ids = sorted(self.running_game_ids())
        if game_ids:
            games_by_id = {
                int(game[0]): str(game[1])
                for game in self.games_provider()
                if len(game) > 1
            }
            names = ", ".join(games_by_id.get(game_id, f"Game {game_id}") for game_id in game_ids)
            QMessageBox.warning(
                self.dialog_parent,
                "Games Still Running",
                "Restarting SafeLauncher now would lose playtime tracking and "
                f"the exit save-upload for: {names}.\n\nClose the game(s) first, "
                "then restart. The update is already applied and survives the restart.",
            )
            return
        from core.updater import restart_application

        restart_application()

    def shutdown(self) -> None:
        """Suppress queued delivery and cancel workers; the supervisor drains them."""
        if self._closed:
            return
        self._closed = True
        self._disconnect_action()
        worker = self._check_worker
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                pass
        worker = self._download_worker
        if worker is not None:
            try:
                worker.requestInterruption()
            except RuntimeError:
                pass

    def _disconnect_action(self) -> None:
        try:
            self.action_button.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass


__all__ = ["AppUpdateController"]
