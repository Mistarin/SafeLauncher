import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFormLayout,
    QFileDialog, QMessageBox, QDialogButtonBox, QListWidget, QListWidgetItem, QFrame,
    QProgressBar, QPlainTextEdit
)
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont

from core.proton_manager import (
    fetch_online_ge_proton_releases, list_installed_ge_proton, get_default_install_dir, GEProtonDownloader
)
from ui.threads import GitHubReleasesFetcherThread, UmuBootstrapWorker


class ProtonSetupWizard(QDialog):
    """Recovery wizard for UMU failures caused by a missing PROTONPATH."""
    def __init__(self, current_path: str = "", parent=None):
        super().__init__(parent)
        self.retry_with_network = False
        self.setWindowTitle("Proton Setup Wizard")
        self.setMinimumWidth(560)
        self.setStyleSheet("QDialog { background: #141414; color: #fff; } QLabel { color: #d4d4d8; } QLineEdit { background: #09090b; color: #fff; border: 1px solid #333; padding: 8px; border-radius: 5px; } QPushButton { background: #52565e; color: #fff; border: none; border-radius: 5px; padding: 8px 14px; font-weight: bold; }")
        layout = QVBoxLayout(self)
        title = QLabel("Proton runtime needs setup")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        layout.addWidget(title)
        message = QLabel("UMU could not find a Proton runtime. Normal launches use --net=none, so UMU cannot download Proton automatically. Select an existing Proton tool folder, or allow a one-time network-enabled retry.")
        message.setWordWrap(True)
        layout.addWidget(message)
        form = QFormLayout()
        row = QHBoxLayout()
        self.path_input = QLineEdit(current_path)
        self.path_input.setPlaceholderText("Example: ~/.local/share/umu/compatibilitytools/UMU-Proton-10.0-4")
        row.addWidget(self.path_input)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        form.addRow("Proton tool folder:", row)
        layout.addLayout(form)
        tips = QLabel("Tips: Steam compatibility tools are often in ~/.local/share/Steam/compatibilitytools.d/\nUMU tools are often in ~/.local/share/umu/compatibilitytools/")
        tips.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        layout.addWidget(tips)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        use_path = buttons.addButton("Save Path", QDialogButtonBox.ButtonRole.AcceptRole)
        use_network = buttons.addButton("Retry with Network Once", QDialogButtonBox.ButtonRole.ActionRole)
        use_path.clicked.connect(self._accept_path)
        use_network.clicked.connect(self._accept_network)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Select Proton tool directory", os.path.expanduser("~/.local/share"))
        if path:
            self.path_input.setText(path)

    def _accept_path(self):
        path = os.path.realpath(os.path.expanduser(self.path_input.text().strip()))
        if not os.path.isdir(path):
            QMessageBox.warning(self, "Invalid Proton path", "Select an existing Proton tool directory.")
            return
        self.path_input.setText(path)
        self.accept()

    def _accept_network(self):
        self.retry_with_network = True
        self.accept()

    def get_path(self) -> str:
        return self.path_input.text().strip()


class ProtonManagerDialog(QDialog):
    """Sleek UI Manager for GE-Proton releases and local installations."""
    proton_selected = pyqtSignal(str)
    apply_to_game_requested = pyqtSignal(str)

    def __init__(self, current_proton_path: str = "", selected_game_name: str = "", parent=None):
        super().__init__(parent)
        self.current_proton_path = current_proton_path
        self.selected_game_name = selected_game_name
        self.downloader_thread = None
        self.fetcher_thread = None

        self.setWindowTitle("GE-Proton Manager - GitHub Auto-Downloader")
        self.setFixedSize(720, 560)
        self.setStyleSheet("""
            QDialog { background-color: #121215; color: #ffffff; }
            QLabel { color: #d4d4d8; font-size: 12px; }
            QPushButton {
                background: #52565e; color: #ffffff; border: none;
                border-radius: 6px; padding: 6px 14px; font-weight: bold; font-size: 11px;
            }
            QPushButton:hover { background: #6b707a; }
            QPushButton:disabled { background: #27272a; color: #71717a; }
            QListWidget {
                background: #18181b; color: #ffffff; border: 1px solid #27272a;
                border-radius: 8px; padding: 4px;
            }
            QProgressBar {
                background: #09090b; border: 1px solid #27272a; border-radius: 6px;
                text-align: center; color: #ffffff; font-weight: bold; font-size: 11px;
            }
            QProgressBar::chunk { background-color: #22c55e; border-radius: 5px; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        header = QLabel("📦 GE-Proton Manager (GitHub Releases)")
        header.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        header.setStyleSheet("color: #ffffff;")
        layout.addWidget(header)

        sub_text = "Download GE-Proton builds directly from GitHub into ~/.local/share/umu/ with 1-click."
        if self.selected_game_name:
            sub_text += f"\nTarget Game: '{self.selected_game_name}'"
        sub = QLabel(sub_text)
        sub.setStyleSheet("color: #a1a1aa; font-size: 12px;")
        layout.addWidget(sub)

        # Tab / List Layout
        self.releases_list = QListWidget()
        layout.addWidget(self.releases_list)

        # Download Manager Active Card Panel
        self.download_card = QFrame()
        self.download_card.setStyleSheet("""
            QFrame {
                background-color: #18181b;
                border: 1px solid #27272a;
                border-radius: 8px;
                padding: 8px;
            }
        """)
        card_layout = QVBoxLayout(self.download_card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(6)

        dl_row = QHBoxLayout()
        self.download_title_lbl = QLabel("Active Download")
        self.download_title_lbl.setStyleSheet("font-weight: bold; color: #c4c7cc; font-size: 12px;")
        dl_row.addWidget(self.download_title_lbl)

        dl_row.addStretch(1)

        self.btn_cancel_dl = QPushButton("❌ Cancel Download")
        self.btn_cancel_dl.setStyleSheet("QPushButton { background: #7f1d1d; color: #fca5a5; border: 1px solid #991b1b; } QPushButton:hover { background: #991b1b; }")
        self.btn_cancel_dl.clicked.connect(self._cancel_download)
        dl_row.addWidget(self.btn_cancel_dl)
        card_layout.addLayout(dl_row)

        self.progress_bar = QProgressBar()
        card_layout.addWidget(self.progress_bar)

        self.download_stats_lbl = QLabel("0.0 MB / 0.0 MB (0%)")
        self.download_stats_lbl.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        card_layout.addWidget(self.download_stats_lbl)

        self.download_card.setVisible(False)
        layout.addWidget(self.download_card)

        # Status Label
        self.status_label = QLabel("Querying GitHub API for GE-Proton releases...")
        self.status_label.setStyleSheet("color: #c4c7cc; font-size: 11px; font-weight: bold;")
        layout.addWidget(self.status_label)

        # Bottom Buttons
        btn_layout = QHBoxLayout()

        btn_refresh = QPushButton("🔄 Refresh Releases")
        btn_refresh.setStyleSheet("QPushButton { background: #27272a; border: 1px solid #3f3f46; } QPushButton:hover { background: #3f3f46; }")
        btn_refresh.clicked.connect(self._fetch_releases)
        btn_layout.addWidget(btn_refresh)

        btn_system = QPushButton("⚙️ Use System Auto Proton (Default)")
        btn_system.setStyleSheet("QPushButton { background: #1e293b; color: #94a3b8; border: 1px solid #334155; } QPushButton:hover { background: #334155; }")
        btn_system.clicked.connect(lambda: self._select_proton(""))
        btn_layout.addWidget(btn_system)

        btn_layout.addStretch(1)

        btn_close = QPushButton("Close")
        btn_close.setStyleSheet("QPushButton { background: #27272a; border: 1px solid #3f3f46; } QPushButton:hover { background: #3f3f46; }")
        btn_close.clicked.connect(self.reject)
        btn_layout.addWidget(btn_close)

        layout.addLayout(btn_layout)

        self._fetch_releases()

    def _fetch_releases(self):
        self.status_label.setText("Querying GitHub API for GE-Proton releases...")
        self.releases_list.clear()

        fetcher = GitHubReleasesFetcherThread(parent=self)
        fetcher.releases_fetched.connect(self._on_releases_fetched)
        fetcher.fetch_failed.connect(self._on_fetch_failed)
        fetcher.start()
        self.fetcher_thread = fetcher

    def _on_releases_fetched(self, releases: list):
        self.releases_list.clear()
        installed_builds = {b["name"].lower(): b["path"] for b in list_installed_ge_proton()}

        if not releases:
            item = QListWidgetItem("No GE-Proton releases found on GitHub.")
            self.releases_list.addItem(item)
            self.status_label.setText("Done.")
            return

        current_path_norm = os.path.realpath(os.path.expanduser(self.current_proton_path)).lower() if self.current_proton_path else ""

        for rel in releases:
            tag = rel["tag"]
            size_mb = rel["size_mb"]
            published = rel["published"]

            widget = QWidget()
            w_layout = QHBoxLayout(widget)
            w_layout.setContentsMargins(10, 8, 10, 8)

            info_lbl = QLabel(f"<b>{tag}</b> ({size_mb} MB) — Published: {published}")
            info_lbl.setStyleSheet("font-size: 12px; color: #f4f4f5;")
            w_layout.addWidget(info_lbl)

            w_layout.addStretch(1)

            target_path = installed_builds.get(tag.lower(), os.path.join(get_default_install_dir(), tag))
            target_path_norm = os.path.realpath(os.path.expanduser(target_path)).lower()
            is_installed = tag.lower() in installed_builds or any(tag.lower() in k for k in installed_builds.keys())
            is_active = is_installed and current_path_norm and (current_path_norm in target_path_norm or target_path_norm in current_path_norm)

            if is_active:
                active_lbl = QLabel("✓ Currently Active")
                active_lbl.setStyleSheet("color: #34d399; font-weight: bold; font-size: 11px; padding: 4px 8px; background: #064e3b; border-radius: 4px;")
                w_layout.addWidget(active_lbl)
            elif is_installed:
                if self.selected_game_name:
                    btn_apply_game = QPushButton("⚡ Apply to Selected Game")
                    btn_apply_game.setStyleSheet("QPushButton { background: #166534; color: #86efac; border: 1px solid #22c55e; } QPushButton:hover { background: #15803d; }")
                    btn_apply_game.clicked.connect(lambda _, p=target_path: self._apply_to_game(p))
                    w_layout.addWidget(btn_apply_game)

                btn_global = QPushButton("🌍 Set Global Default")
                btn_global.setStyleSheet("QPushButton { background: #1e293b; color: #94a3b8; border: 1px solid #334155; } QPushButton:hover { background: #334155; }")
                btn_global.clicked.connect(lambda _, p=target_path: self._select_proton(p))
                w_layout.addWidget(btn_global)
            else:
                dl_btn = QPushButton("📥 Download & Install")
                dl_btn.clicked.connect(lambda _, r=rel: self._start_download(r))
                w_layout.addWidget(dl_btn)

            item = QListWidgetItem(self.releases_list)
            item.setSizeHint(widget.sizeHint())
            self.releases_list.addItem(item)
            self.releases_list.setItemWidget(item, widget)

        self.status_label.setText(f"Found {len(releases)} GE-Proton releases from GitHub.")

    def _on_fetch_failed(self, error_msg: str):
        self.status_label.setText(f"Failed to fetch GitHub releases: {error_msg}")

    def _start_download(self, release: dict):
        tag = release["tag"]
        url = release["url"]

        if self.downloader_thread and self.downloader_thread.isRunning():
            QMessageBox.warning(self, "Download in Progress", "A download is already in progress.")
            return

        self.download_title_lbl.setText(f"Downloading {tag}…")
        self.download_stats_lbl.setText(f"0.0 MB / {release['size_mb']} MB (0%)")
        self.progress_bar.setValue(0)
        self.download_card.setVisible(True)
        self.status_label.setText(f"Active Download: {tag}")

        downloader = GEProtonDownloader(url, tag, parent=self)
        downloader.progress_details.connect(self._on_progress_details)
        downloader.status_text.connect(self.download_title_lbl.setText)
        downloader.download_complete.connect(self._on_download_complete)
        downloader.download_failed.connect(self._on_download_failed)
        downloader.start()
        self.downloader_thread = downloader

    def _on_progress_details(self, tag: str, downloaded_mb: float, total_mb: float, percentage: int):
        self.progress_bar.setValue(percentage)
        self.download_stats_lbl.setText(f"{downloaded_mb} MB / {total_mb} MB ({percentage}%)")

    def _cancel_download(self):
        if self.downloader_thread and self.downloader_thread.isRunning():
            self.downloader_thread.requestInterruption()
            self.downloader_thread.wait(3000)
        self.download_card.setVisible(False)
        self.status_label.setText("Download cancelled.")

    def _on_download_complete(self, tag: str, installed_path: str):
        self.download_card.setVisible(False)
        self.status_label.setText(f"✓ {tag} installed successfully to {installed_path}!")
        QMessageBox.information(self, "GE-Proton Installed", f"✓ GE-Proton build '{tag}' was downloaded and extracted successfully!\n\nLocation:\n{installed_path}")
        self.proton_selected.emit(installed_path)
        self._fetch_releases()

    def _on_download_failed(self, error_msg: str):
        self.download_card.setVisible(False)
        self.status_label.setText(f"❌ Download failed: {error_msg}")
        if "cancelled" not in error_msg.lower():
            QMessageBox.critical(self, "Download Error", f"Failed to download GE-Proton: {error_msg}")

    def _apply_to_game(self, path: str):
        self.apply_to_game_requested.emit(path)
        self.current_proton_path = path
        self._fetch_releases()

    def _select_proton(self, path: str):
        self.proton_selected.emit(path)
        self.current_proton_path = path
        self._fetch_releases()

    def closeEvent(self, event):
        if self.downloader_thread and self.downloader_thread.isRunning():
            self.downloader_thread.requestInterruption()
            self.downloader_thread.wait(3000)
        if self.fetcher_thread and self.fetcher_thread.isRunning():
            self.fetcher_thread.requestInterruption()
            self.fetcher_thread.wait(3000)
        super().closeEvent(event)


class UmuRuntimeManagerDialog(QDialog):
    """Package-manager-style UI for explicitly provisioning UMU runtimes."""
    proton_path_selected = pyqtSignal(str)

    def __init__(self, proton_path: str = "", parent=None):
        super().__init__(parent)
        self.worker = None
        self.setWindowTitle("UMU Runtime Manager")
        self.setMinimumSize(700, 500)
        self.setStyleSheet("""
            QDialog { background: #141414; color: #fff; }
            QLabel { color: #d4d4d8; }
            QLineEdit { background: #09090b; color: #fff; border: 1px solid #333; padding: 8px; border-radius: 5px; }
            QPushButton { background: #52565e; color: #fff; border: none; border-radius: 5px; padding: 8px 14px; font-weight: bold; }
            QPushButton:hover { background: #6b707a; }
            QPlainTextEdit { background: #09090b; color: #34d399; border: 1px solid #27272a; border-radius: 8px; font-family: monospace; font-size: 11px; }
        """)
        layout = QVBoxLayout(self)
        title = QLabel("UMU Runtime Packages")
        title.setFont(QFont("Arial", 17, QFont.Weight.Bold))
        layout.addWidget(title)
        layout.addWidget(QLabel("Download or repair Proton and the Steam Runtime before launching games offline."))

        self.namespace_status = QLabel(self._namespace_tip())
        self.namespace_status.setWordWrap(True)
        self.namespace_status.setStyleSheet("background: #1c1917; color: #fed7aa; border: 1px solid #7c2d12; border-radius: 6px; padding: 9px; font-size: 11px;")
        layout.addWidget(self.namespace_status)

        row = QHBoxLayout()
        self.proton_input = QLineEdit(proton_path)
        self.proton_input.setPlaceholderText("GE-Proton (automatic) or a local Proton tool path")
        row.addWidget(self.proton_input)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        self.status = QLabel("Ready. This step intentionally uses network access.")
        self.status.setStyleSheet("color: #a1a1aa;")
        layout.addWidget(self.status)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output)

        buttons = QHBoxLayout()
        self.install_button = QPushButton("Download / Repair Runtime")
        self.install_button.clicked.connect(self._start)
        buttons.addWidget(self.install_button)
        buttons.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    @staticmethod
    def _namespace_tip() -> str:
        """Explain the user-namespace requirement without changing system policy."""
        try:
            with open("/proc/sys/kernel/unprivileged_userns_clone", "r", encoding="utf-8") as file:
                enabled = file.read().strip() == "1"
        except (OSError, ValueError):
            return (
                "UMU/Proton may need unprivileged user namespaces for bubblewrap. "
                "This system does not expose the Debian-style status switch. "
                "The launcher will not change kernel security settings automatically."
            )

        state = "enabled" if enabled else "disabled"
        return (
            f"Compatibility: unprivileged user namespaces are currently {state}. "
            "UMU/Proton may fail with ‘bwrap: No permissions to create a new namespace’ when disabled. "
            "If you trust the software and use a fully updated personal desktop, enable it temporarily with "
            "`sudo sysctl -w kernel.unprivileged_userns_clone=1`. Revert with "
            "`sudo sysctl -w kernel.unprivileged_userns_clone=0`. "
            "Enabling it expands kernel attack surface; SafeLauncher never changes this setting automatically."
        )

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Select Proton tool directory", os.path.expanduser("~/.local/share"))
        if path:
            self.proton_input.setText(path)

    def _start(self):
        if self.worker and self.worker.isRunning():
            return
        path = self.proton_input.text().strip()
        if path and os.path.sep in path:
            path = os.path.realpath(os.path.expanduser(path))
            if not os.path.isdir(path):
                QMessageBox.warning(self, "Invalid Proton path", "Choose an existing Proton tool directory, or leave the field as GE-Proton.")
                return
            self.proton_input.setText(path)
            self.proton_path_selected.emit(path)
        self.output.clear()
        self.status.setText("Downloading and validating UMU runtime…")
        self.install_button.setEnabled(False)
        self.worker = UmuBootstrapWorker(path, self)
        self.worker.output_line.connect(self.output.appendPlainText)
        self.worker.completed.connect(self._finished)
        self.worker.start()

    def _finished(self, success: bool, return_code: int):
        self.install_button.setEnabled(True)
        if success:
            self.status.setText("Runtime ready. Normal game launches can remain offline.")
            self.status.setStyleSheet("color: #34d399; font-weight: bold;")
        else:
            self.status.setText(f"Runtime setup failed (exit code {return_code}). Check the log above.")
            self.status.setStyleSheet("color: #fca5a5; font-weight: bold;")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.stop()
            self.worker.quit()
            self.worker.wait(5000)
        super().closeEvent(event)
