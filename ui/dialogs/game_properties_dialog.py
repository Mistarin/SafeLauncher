"""Unified Game Properties dialog for managing runtime, prefix maintenance, and save imports/exports."""

import os
import subprocess
from typing import Optional
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QFileDialog, QFrame, QScrollArea, QMessageBox, QGridLayout
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QIcon

from ui.icons import get_icon, get_app_icon
from ui.dialogs.settings_dialog import DialogTitleBar
from ui.maintenance_dialogs import PrefixMaintenanceDialog
from core.host_process import host_process_env
from core.logger import get_logger

logger = get_logger("GamePropertiesDialog")


class GamePropertiesDialog(QDialog):
    """Clean, consolidated Game Properties dialog replacing scattered inspector buttons."""

    def __init__(self, game: tuple, parent=None):
        super().__init__(parent)
        self.game = game
        self.parent_window = parent

        # Extract game record fields
        self.game_id = game[0]
        self.game_name = game[1]
        self.game_path = game[2]
        self.game_exe = game[3] if len(game) > 3 else ""
        self.game_mode = game[4] if len(game) > 4 else ""
        self.custom_proton_path = game[12] if len(game) > 12 and game[12] else ""

        self.setWindowTitle(f"Properties - {self.game_name}")
        self.setMinimumSize(560, 480)
        self.resize(580, 520)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Title bar
        self.title_bar = DialogTitleBar(self, f"Game Properties: {self.game_name}")
        root_layout.addWidget(self.title_bar)

        # Body Scroll Area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: #121214; border: none; }")

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 16, 20, 16)
        body_layout.setSpacing(14)

        # ── Section 1: Game Summary ─────────────────────────────────────────
        sec_summary = QLabel("Game Overview")
        sec_summary.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        sec_summary.setStyleSheet("color: #ffffff; border-bottom: 1px solid #27272a; padding-bottom: 4px;")
        body_layout.addWidget(sec_summary)

        summary_card = QFrame()
        summary_card.setStyleSheet("QFrame { background: #18181b; border: 1px solid #27272a; border-radius: 6px; padding: 10px; }")
        sum_layout = QGridLayout(summary_card)
        sum_layout.setContentsMargins(8, 8, 8, 8)
        sum_layout.setHorizontalSpacing(12)
        sum_layout.setVerticalSpacing(6)

        sum_layout.addWidget(QLabel("<font color='#71717a'>Name:</font>"), 0, 0)
        lbl_n = QLabel(f"<b>{self.game_name}</b>")
        lbl_n.setStyleSheet("color: #ffffff;")
        sum_layout.addWidget(lbl_n, 0, 1)

        sum_layout.addWidget(QLabel("<font color='#71717a'>Directory:</font>"), 1, 0)
        lbl_p = QLabel(self.game_path)
        lbl_p.setStyleSheet("color: #a1a1aa; font-family: monospace; font-size: 11px;")
        lbl_p.setWordWrap(True)
        sum_layout.addWidget(lbl_p, 1, 1)

        sum_layout.addWidget(QLabel("<font color='#71717a'>Executable:</font>"), 2, 0)
        lbl_e = QLabel(self.game_exe or "Auto-detect")
        lbl_e.setStyleSheet("color: #a1a1aa; font-family: monospace; font-size: 11px;")
        sum_layout.addWidget(lbl_e, 2, 1)

        body_layout.addWidget(summary_card)

        # ── Section 2: Proton / Wine Runtime ────────────────────────────────
        sec_runtime = QLabel("Proton / Wine Runtime")
        sec_runtime.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        sec_runtime.setStyleSheet("color: #ffffff; border-bottom: 1px solid #27272a; padding-bottom: 4px; margin-top: 6px;")
        body_layout.addWidget(sec_runtime)

        runtime_card = QFrame()
        runtime_card.setStyleSheet("QFrame { background: #18181b; border: 1px solid #27272a; border-radius: 6px; padding: 10px; }")
        rc_layout = QVBoxLayout(runtime_card)
        rc_layout.setContentsMargins(8, 8, 8, 8)
        rc_layout.setSpacing(8)

        self.lbl_current_runtime = QLabel()
        self._update_runtime_label()
        rc_layout.addWidget(self.lbl_current_runtime)

        btn_row_rt = QHBoxLayout()
        btn_set_rt = QPushButton("Change Proton Runtime...")
        btn_set_rt.setIcon(get_icon("ph.folder-open-bold"))
        btn_set_rt.setStyleSheet("QPushButton { background: #27272a; color: #ffffff; border: 1px solid #3f3f46; border-radius: 4px; padding: 6px 12px; font-weight: 600; font-size: 12px; } QPushButton:hover { background: #3f3f46; }")
        btn_set_rt.clicked.connect(self._select_proton_runtime)
        btn_row_rt.addWidget(btn_set_rt)

        if self.custom_proton_path:
            btn_reset_rt = QPushButton("Reset to Default")
            btn_reset_rt.setStyleSheet("QPushButton { background: #2a1818; color: #f87171; border: 1px solid #7f1d1d; border-radius: 4px; padding: 6px 12px; font-weight: 600; font-size: 12px; } QPushButton:hover { background: #7f1d1d; color: #ffffff; }")
            btn_reset_rt.clicked.connect(self._reset_proton_runtime)
            btn_row_rt.addWidget(btn_reset_rt)

        btn_row_rt.addStretch()
        rc_layout.addLayout(btn_row_rt)
        body_layout.addWidget(runtime_card)

        # ── Section 3: Wine Prefix & Maintenance ───────────────────────────
        sec_prefix = QLabel("Wine Prefix & Sandbox Maintenance")
        sec_prefix.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        sec_prefix.setStyleSheet("color: #ffffff; border-bottom: 1px solid #27272a; padding-bottom: 4px; margin-top: 6px;")
        body_layout.addWidget(sec_prefix)

        prefix_card = QFrame()
        prefix_card.setStyleSheet("QFrame { background: #18181b; border: 1px solid #27272a; border-radius: 6px; padding: 10px; }")
        pc_layout = QHBoxLayout(prefix_card)
        pc_layout.setContentsMargins(8, 8, 8, 8)
        pc_layout.setSpacing(10)

        btn_open_maint = QPushButton(" Prefix Maintenance Tools")
        btn_open_maint.setIcon(get_icon("ph.wrench-bold"))
        btn_open_maint.setStyleSheet("QPushButton { background: #27272a; color: #ffffff; border: 1px solid #3f3f46; border-radius: 4px; padding: 8px 14px; font-weight: 600; font-size: 12px; } QPushButton:hover { background: #3f3f46; }")
        btn_open_maint.clicked.connect(self._open_prefix_maintenance)
        pc_layout.addWidget(btn_open_maint)

        btn_open_dir = QPushButton(" Open Game Directory")
        btn_open_dir.setIcon(get_icon("ph.folder-bold"))
        btn_open_dir.setStyleSheet("QPushButton { background: #27272a; color: #ffffff; border: 1px solid #3f3f46; border-radius: 4px; padding: 8px 14px; font-weight: 600; font-size: 12px; } QPushButton:hover { background: #3f3f46; }")
        btn_open_dir.clicked.connect(self._open_game_directory)
        pc_layout.addWidget(btn_open_dir)

        pc_layout.addStretch()
        body_layout.addWidget(prefix_card)

        # ── Section 4: Save Backups & Export/Import ────────────────────────
        sec_save = QLabel("Save Game Backups (.zip)")
        sec_save.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        sec_save.setStyleSheet("color: #ffffff; border-bottom: 1px solid #27272a; padding-bottom: 4px; margin-top: 6px;")
        body_layout.addWidget(sec_save)

        save_card = QFrame()
        save_card.setStyleSheet("QFrame { background: #18181b; border: 1px solid #27272a; border-radius: 6px; padding: 10px; }")
        sc_layout = QHBoxLayout(save_card)
        sc_layout.setContentsMargins(8, 8, 8, 8)
        sc_layout.setSpacing(10)

        btn_export = QPushButton(" Export Save (.zip)")
        btn_export.setIcon(get_app_icon("export"))
        btn_export.setStyleSheet("QPushButton { background: #27272a; color: #ffffff; border: 1px solid #3f3f46; border-radius: 4px; padding: 8px 14px; font-weight: 600; font-size: 12px; } QPushButton:hover { background: #3f3f46; }")
        btn_export.clicked.connect(self._export_save)
        sc_layout.addWidget(btn_export)

        btn_import = QPushButton(" Import Save (.zip)")
        btn_import.setIcon(get_app_icon("import"))
        btn_import.setStyleSheet("QPushButton { background: #27272a; color: #ffffff; border: 1px solid #3f3f46; border-radius: 4px; padding: 8px 14px; font-weight: 600; font-size: 12px; } QPushButton:hover { background: #3f3f46; }")
        btn_import.clicked.connect(self._import_save)
        sc_layout.addWidget(btn_import)

        sc_layout.addStretch()
        body_layout.addWidget(save_card)

        body_layout.addStretch()
        scroll.setWidget(body)
        root_layout.addWidget(scroll)

        # Bottom Bar
        bottom_bar = QWidget()
        bottom_bar.setStyleSheet("background: #18181b; border-top: 1px solid #27272a;")
        bb_layout = QHBoxLayout(bottom_bar)
        bb_layout.setContentsMargins(16, 10, 16, 10)
        bb_layout.addStretch()

        btn_close = QPushButton("Close")
        btn_close.setMinimumWidth(90)
        btn_close.setStyleSheet("QPushButton { background: #27272a; color: #ffffff; border: 1px solid #3f3f46; border-radius: 4px; padding: 7px 16px; font-weight: 600; } QPushButton:hover { background: #3f3f46; }")
        btn_close.clicked.connect(self.accept)
        bb_layout.addWidget(btn_close)

        root_layout.addWidget(bottom_bar)
        self.setStyleSheet("QDialog { background-color: #121214; color: #ffffff; }")

    def _update_runtime_label(self):
        if self.custom_proton_path:
            self.lbl_current_runtime.setText(
                f"<font color='#71717a'>Custom Runtime:</font> <font color='#38bdf8'><b>{self.custom_proton_path}</b></font>"
            )
        else:
            self.lbl_current_runtime.setText(
                "<font color='#71717a'>Runtime:</font> <font color='#e4e4e7'>Global Default (from SafeLauncher Settings)</font>"
            )

    def _select_proton_runtime(self):
        initial = self.custom_proton_path or os.path.expanduser("~/.local/share/umu")
        path = QFileDialog.getExistingDirectory(self, f"Select Proton runtime for {self.game_name}", initial)
        if path:
            resolved = os.path.realpath(path)
            if self.parent_window and hasattr(self.parent_window, "db"):
                self.parent_window.db.update_game_proton_path(self.game_id, resolved)
                self.custom_proton_path = resolved
                self._update_runtime_label()
                self.parent_window._refresh_library()
                self.parent_window._select_game_by_id(self.game_id)
                QMessageBox.information(self, "Runtime Updated", f"Per-game Proton runtime set to:\n{resolved}")

    def _reset_proton_runtime(self):
        if self.parent_window and hasattr(self.parent_window, "db"):
            self.parent_window.db.update_game_proton_path(self.game_id, "")
            self.custom_proton_path = ""
            self._update_runtime_label()
            self.parent_window._refresh_library()
            self.parent_window._select_game_by_id(self.game_id)
            QMessageBox.information(self, "Runtime Reset", "Reset to global default Proton runtime.")

    def _open_prefix_maintenance(self):
        PrefixMaintenanceDialog(self.game_path, self).exec()

    def _open_game_directory(self):
        if self.game_path and os.path.exists(self.game_path):
            try:
                subprocess.Popen(["xdg-open", self.game_path], env=host_process_env())
            except Exception as e:
                logger.warning(f"Failed to open game directory: {e}")

    def _export_save(self):
        if self.parent_window and hasattr(self.parent_window, "_on_export"):
            self.parent_window._on_export()

    def _import_save(self):
        if self.parent_window and hasattr(self.parent_window, "_on_import"):
            self.parent_window._on_import()
