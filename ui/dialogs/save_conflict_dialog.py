"""
Save Conflict Resolution Dialog for SafeLauncher.
Allows users to compare local vs. cloud save timestamps and choose which save to preserve.
"""

from datetime import datetime
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QFrame, QCheckBox
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QIcon

from ui.icons import get_icon, get_app_icon
from ui.components.sidebar import DialogTitleBar
from ui.components.popup_shell import PopupDialog
from ui.components.check_field import CheckField as QCheckBox
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


class SaveConflictDialog(PopupDialog):
    """Modal prompt to resolve save timestamp discrepancies between local and cloud saves."""

    def __init__(self, game_name: str, local_stats: SaveStats, cloud_stats: SaveStats, parent=None):
        super().__init__("Cloud Save Conflict Detected", parent)
        self.game_name = game_name
        self.local_stats = local_stats
        self.cloud_stats = cloud_stats
        self.local_is_newer = local_stats.last_modified >= cloud_stats.last_modified
        self.choice: str = "local" if self.local_is_newer else "cloud"
        # PopupDialog uses WA_DeleteOnClose.  Keep the user preference as a
        # plain Python value before accept() schedules the dialog for
        # destruction; callers must not dereference child widgets after exec().
        self.always_newer = False

        self.setWindowTitle(f"Save Conflict - {game_name}")
        self.setFixedSize(540, 420)
        body_layout = self.popup_layout()
        body_layout.setContentsMargins(20, 16, 20, 16)
        body_layout.setSpacing(14)

        # Explanation
        if self.local_is_newer:
            info_msg = (
                f"Your local save file on this PC is newer than the Cloud copy for <b>{game_name}</b>.<br>"
                "Either choice is safe: the version you don't pick is preserved as a backup "
                "generation and can be restored from Game Properties → Cloud Save Synchronization."
            )
        else:
            info_msg = (
                f"A newer save file was found in your Cloud storage for <b>{game_name}</b> — "
                "uploaded from another device.<br>"
                "Either choice is safe: the version you don't pick is preserved as a backup "
                "generation and can be restored from Game Properties → Cloud Save Synchronization."
            )
        lbl_info = QLabel(info_msg)
        # Qt stylesheets do not support CSS line-height; QLabel word wrapping
        # already provides the required layout behavior here.
        lbl_info.setStyleSheet("color: #F5F7FA; font-size: 12px;")
        lbl_info.setWordWrap(True)
        body_layout.addWidget(lbl_info)

        # Comparison Cards Row
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)

        highlight_style = """
            QFrame {
                background: #141E28;
                border: 2px solid #3B9FE8;
                border-radius: 8px;
                padding: 12px;
            }
        """
        normal_style = """
            QFrame {
                background: #14171D;
                border: 1px solid #252A33;
                border-radius: 8px;
                padding: 12px;
            }
        """

        # Cloud Save Card
        cloud_card = QFrame()
        cloud_card.setStyleSheet(normal_style if self.local_is_newer else highlight_style)
        cc_layout = QVBoxLayout(cloud_card)
        cc_layout.setSpacing(6)

        cloud_tag_text = "CLOUD SAVE (OLDER)" if self.local_is_newer else "CLOUD SAVE (NEWER)"
        cloud_tag_color = "#A7ADB8" if self.local_is_newer else "#3B9FE8"
        tag_cloud = QLabel(cloud_tag_text)
        tag_cloud.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        tag_cloud.setStyleSheet(f"color: {cloud_tag_color};")
        cc_layout.addWidget(tag_cloud)

        cloud_date = datetime.fromtimestamp(cloud_stats.last_modified).strftime("%Y-%m-%d %H:%M:%S") if cloud_stats.last_modified > 0 else "Unknown"
        cloud_date_color = "#A7ADB8" if self.local_is_newer else "#35C98A"
        lbl_cd = QLabel(f"<b>Edited:</b><br><font color='{cloud_date_color}'>{cloud_date}</font>")
        lbl_cd.setStyleSheet("font-size: 11px; color: #F5F7FA;")
        cc_layout.addWidget(lbl_cd)

        lbl_cs = QLabel(f"Size: {format_bytes(cloud_stats.size_bytes)}<br>Generations kept: {cloud_stats.file_count}")
        lbl_cs.setStyleSheet("font-size: 11px; color: #A7ADB8;")
        lbl_cs.setToolTip("Older cloud generations are retained and can be recovered.")
        cc_layout.addWidget(lbl_cs)

        cards_row.addWidget(cloud_card, 1)

        # Local Save Card
        local_card = QFrame()
        local_card.setStyleSheet(highlight_style if self.local_is_newer else normal_style)
        lc_layout = QVBoxLayout(local_card)
        lc_layout.setSpacing(6)

        local_tag_text = "LOCAL SAVE (NEWER)" if self.local_is_newer else "LOCAL SAVE (OLDER)"
        local_tag_color = "#3B9FE8" if self.local_is_newer else "#A7ADB8"
        tag_local = QLabel(local_tag_text)
        tag_local.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        tag_local.setStyleSheet(f"color: {local_tag_color};")
        lc_layout.addWidget(tag_local)

        local_date = datetime.fromtimestamp(local_stats.last_modified).strftime("%Y-%m-%d %H:%M:%S") if local_stats.last_modified > 0 else "Unknown"
        local_date_color = "#35C98A" if self.local_is_newer else "#A7ADB8"
        lbl_ld = QLabel(f"<b>Edited:</b><br><font color='{local_date_color}'>{local_date}</font>")
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

        recommended_btn_style = """
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
        """
        secondary_btn_style = """
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
        """

        if self.local_is_newer:
            self.btn_use_cloud = QPushButton("Download Cloud Save (Local Copy → Backup)")
            self.btn_use_cloud.setFixedHeight(38)
            self.btn_use_cloud.setStyleSheet(secondary_btn_style)
            self.btn_use_cloud.clicked.connect(self._select_cloud)
            btn_layout.addWidget(self.btn_use_cloud)

            btn_layout.addStretch()

            self.btn_keep_local = QPushButton("Keep Local Save (Recommended)")
            self.btn_keep_local.setIcon(get_app_icon("export"))
            self.btn_keep_local.setFixedHeight(38)
            self.btn_keep_local.setStyleSheet(recommended_btn_style)
            self.btn_keep_local.clicked.connect(self._select_local)
            btn_layout.addWidget(self.btn_keep_local)
        else:
            self.btn_keep_local = QPushButton("Keep Local Save (Cloud Copy → Backup)")
            self.btn_keep_local.setFixedHeight(38)
            self.btn_keep_local.setStyleSheet(secondary_btn_style)
            self.btn_keep_local.clicked.connect(self._select_local)
            btn_layout.addWidget(self.btn_keep_local)

            btn_layout.addStretch()

            self.btn_use_cloud = QPushButton("Download & Use Cloud Save (Recommended)")
            self.btn_use_cloud.setIcon(get_app_icon("import"))
            self.btn_use_cloud.setFixedHeight(38)
            self.btn_use_cloud.setStyleSheet(recommended_btn_style)
            self.btn_use_cloud.clicked.connect(self._select_cloud)
            btn_layout.addWidget(self.btn_use_cloud)

        body_layout.addLayout(btn_layout)

        self.setStyleSheet("""
            QDialog {
                background-color: #0D0F14;
                border: 1px solid #252A33;
                border-radius: 10px;
            }
        """)

    def _select_cloud(self):
        self.choice = "cloud"
        self.always_newer = self.cb_always_newer.isChecked()
        self.accept()

    def _select_local(self):
        self.choice = "local"
        self.always_newer = self.cb_always_newer.isChecked()
        self.accept()
