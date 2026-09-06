"""
Save Manager Dialog for SafeLauncher.
Visual save inspector powered by LudusaviDetector and ZipBackupManager.
"""

import os
import time
import threading
from datetime import datetime
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QFileDialog, QFrame, QScrollArea, QMessageBox, QCheckBox, QProgressBar,
    QTabWidget, QListWidget, QListWidgetItem
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QIcon

from ui.icons import get_icon, get_app_icon
from ui.components.sidebar import DialogTitleBar
from core.ludusavi_detector import LudusaviDetector, SaveLocation
from core.zip_backup import ZipBackupManager
from core.logger import get_logger

logger = get_logger("SaveManagerDialog")


def format_bytes(size_bytes: int) -> str:
    """Format bytes to human readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


class SaveManagerDialog(QDialog):
    """Interactive save snapshot dialog displaying detected locations and metadata."""

    _restore_done = pyqtSignal(bool, str)

    def __init__(self, game_id: int, game_name: str, game_path: str, steam_id: str = "", parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.game_name = game_name
        self.game_path = game_path
        self.steam_id = steam_id
        self.backup_mgr = ZipBackupManager()
        self.save_locations: list[SaveLocation] = []
        self.checkboxes: list[tuple[QCheckBox, SaveLocation]] = []
        self._restore_done.connect(self._on_restore_done)

        self.setWindowTitle(f"Save Manager - {game_name}")
        self.setFixedSize(640, 550)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Title bar
        self.title_bar = DialogTitleBar(self, f"Save Manager: {game_name}")
        root_layout.addWidget(self.title_bar)

        # Content container
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 16, 20, 16)
        body_layout.setSpacing(12)

        # Header Info Banner
        header_frame = QFrame()
        header_frame.setStyleSheet("""
            QFrame {
                background: #14171D;
                border: 1px solid #252A33;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        h_layout = QVBoxLayout(header_frame)
        h_layout.setContentsMargins(4, 4, 4, 4)
        h_layout.setSpacing(4)

        info_title = QLabel(f"<b>{game_name}</b>")
        info_title.setFont(QFont("Arial", 12))
        info_title.setStyleSheet("color: #F5F7FA;")
        h_layout.addWidget(info_title)

        source_note = "Ludusavi CLI Engine" if LudusaviDetector.is_cli_available() else "Heuristic Wine/UMU Prefix Detector"
        self.lbl_status = QLabel(f"<font color='#6F7682'>Discovery Engine:</font> <font color='#3B9FE8'>{source_note}</font>")
        self.lbl_status.setStyleSheet("font-size: 11px;")
        h_layout.addWidget(self.lbl_status)

        body_layout.addWidget(header_frame)

        # Tabs container
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #252A33;
                border-radius: 8px;
                background: #0D0F14;
            }
            QTabBar::tab {
                background: #14171D;
                color: #A7ADB8;
                border: 1px solid #252A33;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 8px 16px;
                margin-right: 4px;
                font-size: 11px;
                font-weight: 500;
            }
            QTabBar::tab:selected {
                background: #1E293B;
                color: #F5F7FA;
                border-color: #3B9FE8;
            }
        """)

        # ── Tab 1: Live Save Files ──
        self.tab_files = QWidget()
        tab_files_layout = QVBoxLayout(self.tab_files)
        tab_files_layout.setContentsMargins(12, 12, 12, 12)
        tab_files_layout.setSpacing(10)

        list_header = QHBoxLayout()
        list_lbl = QLabel("Detected Save Locations")
        list_lbl.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        list_lbl.setStyleSheet("color: #F5F7FA;")
        list_header.addWidget(list_lbl)
        list_header.addStretch()

        btn_rescan = QPushButton("Rescan")
        btn_rescan.setIcon(get_icon("ph.arrows-clockwise-bold", color="#A7ADB8"))
        btn_rescan.setIconSize(QSize(12, 12))
        btn_rescan.setFixedHeight(24)
        btn_rescan.setStyleSheet("""
            QPushButton {
                background: #1A1E26;
                color: #A7ADB8;
                border: 1px solid #252A33;
                border-radius: 4px;
                padding: 0 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                color: #F5F7FA;
                border-color: #3B9FE8;
            }
        """)
        btn_rescan.clicked.connect(self._scan_saves)
        list_header.addWidget(btn_rescan)
        tab_files_layout.addLayout(list_header)

        # Scroll Area for Save Locations
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background: #0D0F14;
                border: 1px solid #252A33;
                border-radius: 8px;
            }
        """)

        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(10, 10, 10, 10)
        self.scroll_layout.setSpacing(8)
        self.scroll_layout.addStretch()
        self.scroll_area.setWidget(self.scroll_content)
        tab_files_layout.addWidget(self.scroll_area)

        # Action Buttons Footer for Tab 1
        footer_layout = QHBoxLayout()
        footer_layout.setSpacing(10)

        btn_import = QPushButton("Import Snapshot (.zip)")
        btn_import.setIcon(get_app_icon("import"))
        btn_import.setFixedHeight(36)
        btn_import.setStyleSheet("""
            QPushButton {
                background: #1A1E26;
                color: #F5F7FA;
                border: 1px solid #252A33;
                border-radius: 6px;
                padding: 0 16px;
                font-weight: 500;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #252A33;
                border-color: #3B9FE8;
            }
        """)
        btn_import.clicked.connect(self._import_snapshot)
        footer_layout.addWidget(btn_import)

        btn_cloud = QPushButton("Restore from Cloud")
        btn_cloud.setIcon(get_icon("ph.cloud-arrow-down-bold", "#3B9FE8"))
        btn_cloud.setFixedHeight(36)
        btn_cloud.setStyleSheet("""
            QPushButton {
                background: #1A1E26;
                color: #3B9FE8;
                border: 1px solid #2563EB;
                border-radius: 6px;
                padding: 0 16px;
                font-weight: 500;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #1E293B;
                border-color: #60A5FA;
            }
        """)
        btn_cloud.clicked.connect(self._restore_from_cloud)
        footer_layout.addWidget(btn_cloud)

        footer_layout.addStretch()

        self.btn_export = QPushButton("Export Selected Saves")
        self.btn_export.setIcon(get_app_icon("export"))
        self.btn_export.setFixedHeight(36)
        self.btn_export.setStyleSheet("""
            QPushButton {
                background: #238636;
                color: #FFFFFF;
                border: 1px solid #2ea043;
                border-radius: 6px;
                padding: 0 18px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #2ea043;
            }
            QPushButton:disabled {
                background: #21262d;
                color: #6F7682;
                border-color: #30363d;
            }
        """)
        self.btn_export.clicked.connect(self._export_selected)
        footer_layout.addWidget(self.btn_export)
        tab_files_layout.addLayout(footer_layout)

        self.tabs.addTab(self.tab_files, "Live Save Files")

        # ── Tab 2: Save History & Cloud ──
        self.tab_history = QWidget()
        tab_history_layout = QVBoxLayout(self.tab_history)
        tab_history_layout.setContentsMargins(12, 12, 12, 12)
        tab_history_layout.setSpacing(10)

        history_header = QHBoxLayout()
        lbl_hist = QLabel("Retained Generations & Backups")
        lbl_hist.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        lbl_hist.setStyleSheet("color: #F5F7FA;")
        history_header.addWidget(lbl_hist)
        history_header.addStretch()

        btn_refresh_hist = QPushButton("Refresh")
        btn_refresh_hist.setIcon(get_icon("ph.arrows-clockwise-bold", color="#A7ADB8"))
        btn_refresh_hist.setIconSize(QSize(12, 12))
        btn_refresh_hist.setFixedHeight(24)
        btn_refresh_hist.setStyleSheet("""
            QPushButton {
                background: #1A1E26;
                color: #A7ADB8;
                border: 1px solid #252A33;
                border-radius: 4px;
                padding: 0 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                color: #F5F7FA;
                border-color: #3B9FE8;
            }
        """)
        btn_refresh_hist.clicked.connect(self._load_history)
        history_header.addWidget(btn_refresh_hist)
        tab_history_layout.addLayout(history_header)

        self.lst_history = QListWidget()
        self.lst_history.setStyleSheet("""
            QListWidget {
                background: #0D0F14;
                border: 1px solid #252A33;
                border-radius: 8px;
                color: #F5F7FA;
                padding: 6px;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #1A1E26;
                border-radius: 6px;
                color: #E5E7EB;
            }
            QListWidget::item:selected {
                background: #1E293B;
                color: #FFFFFF;
                border: 1px solid #3B9FE8;
            }
        """)
        self.lst_history.itemSelectionChanged.connect(self._on_history_selection_changed)
        tab_history_layout.addWidget(self.lst_history)

        history_footer = QHBoxLayout()
        history_footer.setSpacing(10)

        lbl_hint = QLabel("Select any saved version above to restore it to your local game.")
        lbl_hint.setStyleSheet("font-size: 11px; color: #6F7682;")
        history_footer.addWidget(lbl_hint)
        history_footer.addStretch()

        self.btn_restore_history = QPushButton("Restore Selected Save")
        self.btn_restore_history.setIcon(get_icon("ph.clock-counter-clockwise-bold"))
        self.btn_restore_history.setFixedHeight(36)
        self.btn_restore_history.setStyleSheet("""
            QPushButton {
                background: #3B9FE8;
                color: #FFFFFF;
                border: 1px solid #2563EB;
                border-radius: 6px;
                padding: 0 18px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #2563EB;
            }
            QPushButton:disabled {
                background: #21262d;
                color: #6F7682;
                border-color: #30363d;
            }
        """)
        self.btn_restore_history.setEnabled(False)
        self.btn_restore_history.clicked.connect(self._restore_selected_history_save)
        history_footer.addWidget(self.btn_restore_history)

        tab_history_layout.addLayout(history_footer)
        self.tabs.addTab(self.tab_history, "Save History & Cloud")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        body_layout.addWidget(self.tabs)
        root_layout.addWidget(body)

        self.setStyleSheet("""
            QDialog {
                background-color: #0D0F14;
                border: 1px solid #252A33;
                border-radius: 10px;
            }
        """)

        # Run initial scan
        self._scan_saves()

    def _scan_saves(self):
        """Scan for save locations and populate scroll view."""
        # Clear existing items
        while self.scroll_layout.count() > 1:
            item = self.scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.checkboxes.clear()
        self.save_locations = LudusaviDetector.detect_saves(self.game_name, self.game_path, self.steam_id)

        if not self.save_locations:
            empty_lbl = QLabel("No save files found yet for this title.\nThey will appear once the game is launched and creates its initial save.")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setStyleSheet("color: #6F7682; padding: 40px; font-size: 12px;")
            self.scroll_layout.insertWidget(0, empty_lbl)
            self.btn_export.setEnabled(False)
            return

        self.btn_export.setEnabled(True)

        for loc in self.save_locations:
            card = QFrame()
            card.setStyleSheet("""
                QFrame {
                    background: #14171D;
                    border: 1px solid #252A33;
                    border-radius: 6px;
                    padding: 8px;
                }
                QFrame:hover {
                    border-color: #353C4A;
                }
            """)
            c_layout = QHBoxLayout(card)
            c_layout.setContentsMargins(6, 4, 6, 4)
            c_layout.setSpacing(10)

            cb = QCheckBox()
            cb.setChecked(True)
            cb.setStyleSheet("""
                QCheckBox::indicator {
                    width: 18px;
                    height: 18px;
                    border-radius: 4px;
                    border: 1px solid #353C4A;
                    background: #1A1E26;
                }
                QCheckBox::indicator:checked {
                    background: #3B9FE8;
                    border-color: #3B9FE8;
                }
            """)
            c_layout.addWidget(cb)
            self.checkboxes.append((cb, loc))

            info_vbox = QVBoxLayout()
            info_vbox.setSpacing(2)

            name_row = QHBoxLayout()
            lbl_name = QLabel(f"<b>{loc.display_name}</b>")
            lbl_name.setStyleSheet("color: #F5F7FA; font-size: 12px;")
            name_row.addWidget(lbl_name)

            size_str = format_bytes(loc.total_size_bytes)
            lbl_size = QLabel(f"{loc.file_count} file(s) · {size_str}")
            lbl_size.setStyleSheet("color: #35C98A; font-size: 11px; font-weight: 500;")
            name_row.addWidget(lbl_size)
            name_row.addStretch()
            info_vbox.addLayout(name_row)

            # Path & Date
            date_str = datetime.fromtimestamp(loc.last_modified).strftime("%Y-%m-%d %H:%M") if loc.last_modified > 0 else "Unknown"
            lbl_path = QLabel(f"<font color='#6F7682'>{loc.path}</font> <font color='#555'>· Modified: {date_str}</font>")
            lbl_path.setStyleSheet("font-size: 10px;")
            lbl_path.setWordWrap(True)
            info_vbox.addWidget(lbl_path)

            c_layout.addLayout(info_vbox, 1)

            self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, card)

    def _export_selected(self):
        selected_locations = [loc for cb, loc in self.checkboxes if cb.isChecked()]
        if not selected_locations:
            QMessageBox.warning(self, "Warning", "Please select at least one save location to export.")
            return

        date_suffix = datetime.now().strftime("%Y%m%d_%H%M")
        safe_name = "".join(c for c in self.game_name if c.isalnum() or c in "-_")
        default_filename = f"{safe_name}_save_{date_suffix}.zip"

        export_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Save Snapshot",
            default_filename,
            "ZIP Files (*.zip)"
        )

        if export_path:
            success = self.backup_mgr.export_save_locations(
                selected_locations,
                export_path,
                game_name=self.game_name,
                game_path=self.game_path
            )
            if success:
                QMessageBox.information(self, "Export Successful", f"Save snapshot saved to:\n{export_path}")
            else:
                QMessageBox.critical(self, "Export Error", "Failed to package save snapshot.")

    def _import_snapshot(self):
        import_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Save Snapshot",
            "",
            "ZIP Files (*.zip)"
        )

        if import_path:
            # Target prefix root or game root
            target_dest = os.path.join(self.game_path, "prefix")
            if not os.path.isdir(target_dest):
                target_dest = self.game_path

            success = self.backup_mgr.import_save(import_path, target_dest, game_path=self.game_path)
            if success:
                QMessageBox.information(self, "Import Successful", "Game save snapshot restored successfully.")
                self._scan_saves()
                self._notify_parent_changed()
            else:
                QMessageBox.critical(self, "Import Error", "Failed to extract save snapshot.")

    def _notify_parent_changed(self):
        """Notify parent window or dialog that saves changed so stats refresh immediately."""
        p = self.parent()
        if p is not None:
            if hasattr(p, "refresh_cloud_status_for_game"):
                p.refresh_cloud_status_for_game(self.game_id)
            elif hasattr(p, "request_cloud_recheck"):
                p.request_cloud_recheck([self.game_id], "save_restored")
            if hasattr(p, "_load_save_stats_async"):
                p._load_save_stats_async()
            if hasattr(p, "_notify_parent_cloud_changed"):
                p._notify_parent_cloud_changed()

    def _on_tab_changed(self, index: int):
        if index == 1:
            self._load_history()

    def _on_history_selection_changed(self):
        item = self.lst_history.currentItem()
        self.btn_restore_history.setEnabled(bool(item and item.data(Qt.ItemDataRole.UserRole)))

    def _load_history(self):
        """Fetch and populate all available cloud generations and local forks."""
        from core.cloud_save_sync import CloudSaveSyncEngine
        self.lst_history.clear()
        versions = CloudSaveSyncEngine.get_available_versions(self.game_name, self.game_path, self.steam_id)
        if not versions:
            empty_item = QListWidgetItem("No saved generations or backup forks found yet.")
            empty_item.setFlags(empty_item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.lst_history.addItem(empty_item)
            self.btn_restore_history.setEnabled(False)
            return

        for v in versions:
            v_num = v.get("version")
            is_active = v.get("is_active", False)
            date_str = datetime.fromtimestamp(v.get("mtime", 0)).strftime("%Y-%m-%d %H:%M:%S") if v.get("mtime") else "Unknown date"
            sz_str = format_bytes(int(v.get("size_bytes", 0)))
            active_badge = " · [Active on this PC]" if is_active else ""

            title = v.get("display_name", f"Save {v_num}")
            item = QListWidgetItem(f"{title}\n{date_str} · {sz_str}{active_badge}")
            item.setData(Qt.ItemDataRole.UserRole, v)
            self.lst_history.addItem(item)

        if self.lst_history.count() > 0:
            self.lst_history.setCurrentRow(0)
            self.btn_restore_history.setEnabled(True)

    def _restore_selected_history_save(self):
        curr = self.lst_history.currentItem()
        if not curr:
            QMessageBox.information(self, "Nothing Selected", "Please select a save from the list first.")
            return
        entry = curr.data(Qt.ItemDataRole.UserRole)
        if not entry:
            return

        title = entry.get("display_name", "Save")
        confirm = QMessageBox.question(
            self, "Restore Selected Save",
            f"Restore '{title}' to '{self.game_name}'?\n\n"
            f"Target Directory: {self.game_path}\n\n"
            "Your existing local save will be preserved in your local backups before overwriting.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.btn_restore_history.setEnabled(False)
        self.btn_export.setEnabled(False)

        def _worker():
            success = False
            from core.cloud_save_sync import CloudSaveSyncEngine, set_active_save_version
            try:
                if entry.get("source") == "cloud":
                    v_num = entry.get("version")
                    success = CloudSaveSyncEngine.sync_cloud_to_local(
                        self.game_name, self.game_path, steam_id=self.steam_id,
                        preserve_local_fork=True, target_version=int(v_num) if v_num else None
                    )
                elif entry.get("source") == "fork" and entry.get("path"):
                    target_dest = os.path.join(self.game_path, "prefix")
                    if not os.path.isdir(target_dest):
                        target_dest = self.game_path
                    success = self.backup_mgr.import_save(entry["path"], target_dest, game_path=self.game_path)
                    if success:
                        set_active_save_version(self.game_name, None)
            except Exception as e:
                logger.error(f"Worker restore failed for '{self.game_name}': {e}")
                success = False
            self._restore_done.emit(bool(success), title)

        threading.Thread(target=_worker, daemon=True, name=f"SafeLauncher-HistRestore-{self.game_id}").start()

    def _on_restore_done(self, success: bool, title: str):
        self.btn_restore_history.setEnabled(True)
        self.btn_export.setEnabled(True)
        if success:
            QMessageBox.information(
                self, "Restore Successful",
                f"Successfully restored '{title}'."
            )
            self._scan_saves()
            self._load_history()
            self._notify_parent_changed()
        else:
            QMessageBox.critical(
                self, "Restore Error",
                f"Failed to restore '{title}'. Check logs for details."
            )

    def _restore_from_cloud(self):
        from core.cloud_save_sync import CloudSaveSyncEngine
        versions = CloudSaveSyncEngine.get_available_versions(self.game_name, self.game_path, self.steam_id)
        cloud_versions = [v for v in versions if v.get("source") == "cloud"]

        if len(cloud_versions) > 1:
            # Switch to history tab where all versions are listed for manual selection
            self.tabs.setCurrentIndex(1)
            return

        status, local_stats, cloud_stats = CloudSaveSyncEngine.check_sync_status(
            self.game_name, self.game_path, self.steam_id
        )
        if not cloud_stats.exists:
            QMessageBox.information(
                self, "No Cloud Saves",
                f"No cloud save archive found for '{self.game_name}'."
            )
            return

        confirm = QMessageBox.question(
            self, "Restore Cloud Save",
            f"Restore cloud save for '{self.game_name}'?\n\n"
            f"Target Directory: {self.game_path}\n"
            f"Cloud Save Details: {cloud_stats.display_path}\n\n"
            "Your existing local save will be preserved in your local backups before overwriting.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.btn_restore_history.setEnabled(False)
        self.btn_export.setEnabled(False)

        def _worker():
            success = False
            try:
                success = CloudSaveSyncEngine.sync_cloud_to_local(
                    self.game_name, self.game_path, steam_id=self.steam_id, preserve_local_fork=True
                )
            except Exception as e:
                logger.error(f"Cloud restore failed for '{self.game_name}': {e}")
                success = False
            self._restore_done.emit(bool(success), cloud_stats.display_path)

        threading.Thread(target=_worker, daemon=True, name=f"SafeLauncher-CloudRestore-{self.game_id}").start()
