"""
Save Manager Dialog for SafeLauncher.
Visual save inspector powered by LudusaviDetector and ZipBackupManager.
"""

import os
import time
from datetime import datetime
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QFileDialog, QFrame, QScrollArea, QMessageBox, QCheckBox, QProgressBar
)
from PyQt6.QtCore import Qt, QSize
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

    def __init__(self, game_id: int, game_name: str, game_path: str, steam_id: str = "", parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.game_name = game_name
        self.game_path = game_path
        self.steam_id = steam_id
        self.backup_mgr = ZipBackupManager()
        self.save_locations: list[SaveLocation] = []
        self.checkboxes: list[tuple[QCheckBox, SaveLocation]] = []

        self.setWindowTitle(f"Save Manager - {game_name}")
        self.setFixedSize(620, 520)
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

        # Locations List Header
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
        body_layout.addLayout(list_header)

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

        body_layout.addWidget(self.scroll_area)

        # Action Buttons Footer
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

        body_layout.addLayout(footer_layout)
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

            success = self.backup_mgr.import_save(import_path, target_dest)
            if success:
                QMessageBox.information(self, "Import Successful", "Game save snapshot restored successfully.")
                self._scan_saves()
            else:
                QMessageBox.critical(self, "Import Error", "Failed to extract save snapshot.")
