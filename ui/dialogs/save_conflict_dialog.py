"""
Save Conflict Resolution Dialog for SafeLauncher.
Allows users to compare local vs. cloud save timestamps and choose which save to preserve.
"""

from datetime import datetime
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QFrame, QCheckBox
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QIcon

from ui.icons import get_icon, get_app_icon
from ui.components.sidebar import DialogTitleBar
from core.cloud_save_sync import SaveStats


def format_bytes(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


class SaveConflictDialog(QDialog):
    """Modal prompt to resolve save timestamp discrepancies between local and cloud saves."""

    def __init__(self, game_name: str, local_stats: SaveStats, cloud_stats: SaveStats, parent=None):
        super().__init__(parent)
        self.game_name = game_name
        self.local_stats = local_stats
        self.cloud_stats = cloud_stats
        self.choice: str = "cloud"  # "cloud" or "local"

        self.setWindowTitle(f"Save Conflict - {game_name}")
        self.setFixedSize(540, 420)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Title bar
        self.title_bar = DialogTitleBar(self, "Cloud Save Conflict Detected")
        root_layout.addWidget(self.title_bar)

        # Body container
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 16, 20, 16)
        body_layout.setSpacing(14)

        # Explanation
        lbl_info = QLabel(
            f"A newer save file was found in your Cloud storage for <b>{game_name}</b>.<br>"
            "Which save version would you like to keep?"
        )
        lbl_info.setStyleSheet("color: #F5F7FA; font-size: 12px; line-height: 1.4;")
        lbl_info.setWordWrap(True)
        body_layout.addWidget(lbl_info)

        # Comparison Cards Row
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)

        # Cloud Save Card (Newer)
        cloud_card = QFrame()
        cloud_card.setStyleSheet("""
            QFrame {
                background: #141E28;
                border: 2px solid #3B9FE8;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        cc_layout = QVBoxLayout(cloud_card)
        cc_layout.setSpacing(6)

        tag_cloud = QLabel("CLOUD SAVE (NEWER)")
        tag_cloud.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        tag_cloud.setStyleSheet("color: #3B9FE8;")
        cc_layout.addWidget(tag_cloud)

        cloud_date = datetime.fromtimestamp(cloud_stats.last_modified).strftime("%Y-%m-%d %H:%M:%S") if cloud_stats.last_modified > 0 else "Unknown"
        lbl_cd = QLabel(f"<b>Edited:</b><br><font color='#35C98A'>{cloud_date}</font>")
        lbl_cd.setStyleSheet("font-size: 11px; color: #F5F7FA;")
        cc_layout.addWidget(lbl_cd)

        lbl_cs = QLabel(f"Size: {format_bytes(cloud_stats.size_bytes)}<br>Generations kept: {cloud_stats.file_count}")
        lbl_cs.setStyleSheet("font-size: 11px; color: #A7ADB8;")
        lbl_cs.setToolTip("Older cloud generations are retained and can be recovered.")
        cc_layout.addWidget(lbl_cs)

        cards_row.addWidget(cloud_card, 1)

        # Local Save Card
        local_card = QFrame()
        local_card.setStyleSheet("""
            QFrame {
                background: #14171D;
                border: 1px solid #252A33;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        lc_layout = QVBoxLayout(local_card)
        lc_layout.setSpacing(6)

        tag_local = QLabel("LOCAL SAVE (OLDER)")
        tag_local.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        tag_local.setStyleSheet("color: #A7ADB8;")
        lc_layout.addWidget(tag_local)

        local_date = datetime.fromtimestamp(local_stats.last_modified).strftime("%Y-%m-%d %H:%M:%S") if local_stats.last_modified > 0 else "Unknown"
        lbl_ld = QLabel(f"<b>Edited:</b><br><font color='#A7ADB8'>{local_date}</font>")
        lbl_ld.setStyleSheet("font-size: 11px; color: #F5F7FA;")
        lc_layout.addWidget(lbl_ld)

        lbl_ls = QLabel(f"Size: {format_bytes(local_stats.size_bytes)}<br>Files: {local_stats.file_count}")
        lbl_ls.setStyleSheet("font-size: 11px; color: #6F7682;")
        lc_layout.addWidget(lbl_ls)

        cards_row.addWidget(local_card, 1)
        body_layout.addLayout(cards_row)

        # Always keep newer checkbox
        self.cb_always_newer = QCheckBox("Always automatically choose the newer save without prompting")
        self.cb_always_newer.setStyleSheet("QCheckBox { color: #A7ADB8; font-size: 11px; }")
        body_layout.addWidget(self.cb_always_newer)

        # Buttons Footer
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        btn_keep_local = QPushButton("Keep Local Save (Overwrite Cloud)")
        btn_keep_local.setFixedHeight(38)
        btn_keep_local.setStyleSheet("""
            QPushButton {
                background: #1A1E26;
                color: #A7ADB8;
                border: 1px solid #252A33;
                border-radius: 6px;
                padding: 0 14px;
                font-weight: 500;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #252A33;
                color: #F5F7FA;
            }
        """)
        btn_keep_local.clicked.connect(self._select_local)
        btn_layout.addWidget(btn_keep_local)

        btn_layout.addStretch()

        btn_use_cloud = QPushButton("Download & Use Cloud Save (Recommended)")
        btn_use_cloud.setIcon(get_app_icon("import"))
        btn_use_cloud.setFixedHeight(38)
        btn_use_cloud.setStyleSheet("""
            QPushButton {
                background: #238636;
                color: #FFFFFF;
                border: 1px solid #2ea043;
                border-radius: 6px;
                padding: 0 16px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #2ea043;
            }
        """)
        btn_use_cloud.clicked.connect(self._select_cloud)
        btn_layout.addWidget(btn_use_cloud)

        body_layout.addLayout(btn_layout)
        root_layout.addWidget(body)

        self.setStyleSheet("""
            QDialog {
                background-color: #0D0F14;
                border: 1px solid #252A33;
                border-radius: 10px;
            }
        """)

    def _select_cloud(self):
        self.choice = "cloud"
        self.accept()

    def _select_local(self):
        self.choice = "local"
        self.accept()
