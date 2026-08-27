"""Unified Game Properties dialog for managing runtime, presets, prefix maintenance, and save snapshots."""

import os
import json
import subprocess
from typing import Optional, Dict
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QFileDialog, QFrame, QScrollArea, QMessageBox, QGridLayout,
    QTabWidget, QCheckBox, QSlider, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QIcon

from ui.icons import get_icon, get_app_icon
from ui.components.sidebar import DialogTitleBar
from ui.maintenance_dialogs import PrefixMaintenanceDialog
from ui.dialogs.save_manager_dialog import SaveManagerDialog
from core.host_process import host_process_env
from core.logger import get_logger

logger = get_logger("GamePropertiesDialog")


class GamePropertiesDialog(QDialog):
    """Clean, consolidated Game Properties dialog with Performance Presets and Save Manager."""

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
        self.steam_id = game[6] if len(game) > 6 else ""
        self.custom_proton_path = game[12] if len(game) > 12 and game[12] else ""

        # Load environment variables from database
        self.env_vars: Dict[str, str] = {}
        if self.parent_window and hasattr(self.parent_window, "db"):
            self.env_vars = self.parent_window.db.get_game_env_vars(self.game_id)

        self.setWindowTitle(f"Properties - {self.game_name}")
        self.setMinimumSize(640, 560)
        self.resize(660, 600)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Title bar
        self.title_bar = DialogTitleBar(self, f"Game Properties: {self.game_name}")
        root_layout.addWidget(self.title_bar)

        # Tab Widget
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #252A33;
                background: #0D0F14;
                border-radius: 0 0 8px 8px;
                top: -1px;
            }
            QTabBar::tab {
                background: #14171D;
                color: #A7ADB8;
                border: 1px solid #252A33;
                border-bottom: none;
                padding: 8px 18px;
                margin-right: 2px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 600;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background: #0D0F14;
                color: #3B9FE8;
                border-color: #252A33;
                border-bottom: 1px solid #0D0F14;
            }
            QTabBar::tab:hover:!selected {
                background: #1A1E26;
                color: #F5F7FA;
            }
        """)

        # Tab 1: General & Runtime
        self.tab_general = self._create_general_tab()
        self.tabs.addTab(self.tab_general, "General & Runtime")

        # Tab 2: Performance & Presets
        self.tab_presets = self._create_presets_tab()
        self.tabs.addTab(self.tab_presets, "Performance & Presets")

        # Tab 3: Saves & Snapshots
        self.tab_saves = self._create_saves_tab()
        self.tabs.addTab(self.tab_saves, "Saves & Snapshots")

        root_layout.addWidget(self.tabs)

        # Bottom Bar
        bottom_bar = QWidget()
        bottom_bar.setStyleSheet("background: #14171D; border-top: 1px solid #252A33;")
        bb_layout = QHBoxLayout(bottom_bar)
        bb_layout.setContentsMargins(16, 10, 16, 10)
        bb_layout.addStretch()

        btn_save = QPushButton("Save & Close")
        btn_save.setMinimumWidth(110)
        btn_save.setFixedHeight(34)
        btn_save.setStyleSheet("""
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
        btn_save.clicked.connect(self._save_and_close)
        bb_layout.addWidget(btn_save)

        root_layout.addWidget(bottom_bar)
        self.setStyleSheet("QDialog { background-color: #0D0F14; border: 1px solid #252A33; border-radius: 8px; }")

    def _create_general_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(18, 16, 18, 16)
        body_layout.setSpacing(14)

        # Summary
        sec_summary = QLabel("Game Overview")
        sec_summary.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        sec_summary.setStyleSheet("color: #F5F7FA; border-bottom: 1px solid #252A33; padding-bottom: 4px;")
        body_layout.addWidget(sec_summary)

        summary_card = QFrame()
        summary_card.setStyleSheet("QFrame { background: #14171D; border: 1px solid #252A33; border-radius: 6px; padding: 8px; }")
        sum_layout = QGridLayout(summary_card)
        sum_layout.setHorizontalSpacing(12)
        sum_layout.setVerticalSpacing(6)

        sum_layout.addWidget(QLabel("<font color='#6F7682'>Name:</font>"), 0, 0)
        lbl_n = QLabel(f"<b>{self.game_name}</b>")
        lbl_n.setStyleSheet("color: #F5F7FA;")
        sum_layout.addWidget(lbl_n, 0, 1)

        sum_layout.addWidget(QLabel("<font color='#6F7682'>Directory:</font>"), 1, 0)
        lbl_p = QLabel(self.game_path)
        lbl_p.setStyleSheet("color: #A7ADB8; font-family: monospace; font-size: 11px;")
        lbl_p.setWordWrap(True)
        sum_layout.addWidget(lbl_p, 1, 1)

        sum_layout.addWidget(QLabel("<font color='#6F7682'>Executable:</font>"), 2, 0)
        lbl_e = QLabel(self.game_exe or "Auto-detect")
        lbl_e.setStyleSheet("color: #A7ADB8; font-family: monospace; font-size: 11px;")
        sum_layout.addWidget(lbl_e, 2, 1)

        body_layout.addWidget(summary_card)

        # Proton / Wine Runtime
        sec_runtime = QLabel("Proton / Wine Runtime")
        sec_runtime.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        sec_runtime.setStyleSheet("color: #F5F7FA; border-bottom: 1px solid #252A33; padding-bottom: 4px; margin-top: 6px;")
        body_layout.addWidget(sec_runtime)

        runtime_card = QFrame()
        runtime_card.setStyleSheet("QFrame { background: #14171D; border: 1px solid #252A33; border-radius: 6px; padding: 10px; }")
        rc_layout = QVBoxLayout(runtime_card)
        rc_layout.setSpacing(8)

        self.lbl_current_runtime = QLabel()
        self._update_runtime_label()
        rc_layout.addWidget(self.lbl_current_runtime)

        btn_row_rt = QHBoxLayout()
        btn_set_rt = QPushButton("Change Proton Runtime...")
        btn_set_rt.setIcon(get_icon("ph.folder-open-bold"))
        btn_set_rt.setStyleSheet("QPushButton { background: #1A1E26; color: #F5F7FA; border: 1px solid #252A33; border-radius: 4px; padding: 6px 12px; font-weight: 600; font-size: 12px; } QPushButton:hover { background: #252A33; border-color: #3B9FE8; }")
        btn_set_rt.clicked.connect(self._select_proton_runtime)
        btn_row_rt.addWidget(btn_set_rt)

        if self.custom_proton_path:
            btn_reset_rt = QPushButton("Reset to Default")
            btn_reset_rt.setStyleSheet("QPushButton { background: rgba(240, 93, 108, 0.15); color: #F05D6C; border: 1px solid #F05D6C; border-radius: 4px; padding: 6px 12px; font-weight: 600; font-size: 12px; } QPushButton:hover { background: rgba(240, 93, 108, 0.3); }")
            btn_reset_rt.clicked.connect(self._reset_proton_runtime)
            btn_row_rt.addWidget(btn_reset_rt)

        btn_row_rt.addStretch()
        rc_layout.addLayout(btn_row_rt)
        body_layout.addWidget(runtime_card)

        # Wine Prefix & Maintenance
        sec_prefix = QLabel("Wine Prefix & Sandbox Maintenance")
        sec_prefix.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        sec_prefix.setStyleSheet("color: #F5F7FA; border-bottom: 1px solid #252A33; padding-bottom: 4px; margin-top: 6px;")
        body_layout.addWidget(sec_prefix)

        prefix_card = QFrame()
        prefix_card.setStyleSheet("QFrame { background: #14171D; border: 1px solid #252A33; border-radius: 6px; padding: 10px; }")
        pc_layout = QHBoxLayout(prefix_card)
        pc_layout.setSpacing(10)

        btn_open_maint = QPushButton(" Prefix Maintenance Tools")
        btn_open_maint.setIcon(get_icon("ph.wrench-bold"))
        btn_open_maint.setStyleSheet("QPushButton { background: #1A1E26; color: #F5F7FA; border: 1px solid #252A33; border-radius: 4px; padding: 8px 14px; font-weight: 600; font-size: 12px; } QPushButton:hover { background: #252A33; border-color: #3B9FE8; }")
        btn_open_maint.clicked.connect(self._open_prefix_maintenance)
        pc_layout.addWidget(btn_open_maint)

        btn_open_dir = QPushButton(" Open Game Directory")
        btn_open_dir.setIcon(get_icon("ph.folder-bold"))
        btn_open_dir.setStyleSheet("QPushButton { background: #1A1E26; color: #F5F7FA; border: 1px solid #252A33; border-radius: 4px; padding: 8px 14px; font-weight: 600; font-size: 12px; } QPushButton:hover { background: #252A33; border-color: #3B9FE8; }")
        btn_open_dir.clicked.connect(self._open_game_directory)
        pc_layout.addWidget(btn_open_dir)

        pc_layout.addStretch()
        body_layout.addWidget(prefix_card)

        body_layout.addStretch()
        scroll.setWidget(body)
        return scroll

    def _create_presets_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(18, 16, 18, 16)
        body_layout.setSpacing(14)

        # Quick Performance Toggles Card
        sec_toggles = QLabel("Performance & Optimization Presets")
        sec_toggles.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        sec_toggles.setStyleSheet("color: #F5F7FA; border-bottom: 1px solid #252A33; padding-bottom: 4px;")
        body_layout.addWidget(sec_toggles)

        toggles_card = QFrame()
        toggles_card.setStyleSheet("QFrame { background: #14171D; border: 1px solid #252A33; border-radius: 8px; padding: 12px; }")
        tc_layout = QVBoxLayout(toggles_card)
        tc_layout.setSpacing(10)

        # FSR Checkbox + Slider
        self.cb_fsr = QCheckBox("Enable AMD FSR Upscaling (WINE_FULLSCREEN_FSR=1)")
        self.cb_fsr.setChecked(self.env_vars.get("WINE_FULLSCREEN_FSR", "0") == "1")
        self.cb_fsr.setStyleSheet("QCheckBox { color: #F5F7FA; font-weight: 600; }")
        tc_layout.addWidget(self.cb_fsr)

        fsr_slider_row = QHBoxLayout()
        fsr_slider_row.setContentsMargins(24, 0, 0, 4)
        fsr_slider_row.addWidget(QLabel("<font color='#6F7682'>FSR Sharpness:</font>"))
        self.slider_fsr = QSlider(Qt.Orientation.Horizontal)
        self.slider_fsr.setRange(0, 5)
        self.slider_fsr.setValue(int(self.env_vars.get("WINE_FULLSCREEN_FSR_STRENGTH", "2")))
        self.lbl_fsr_val = QLabel(str(self.slider_fsr.value()))
        self.lbl_fsr_val.setStyleSheet("color: #3B9FE8; font-weight: bold; min-width: 20px;")
        self.slider_fsr.valueChanged.connect(lambda v: self.lbl_fsr_val.setText(str(v)))
        fsr_slider_row.addWidget(self.slider_fsr)
        fsr_slider_row.addWidget(self.lbl_fsr_val)
        tc_layout.addLayout(fsr_slider_row)

        # DXVK Async Checkbox
        self.cb_dxvk_async = QCheckBox("DXVK Asynchronous Pipeline (DXVK_ASYNC=1)")
        self.cb_dxvk_async.setChecked(self.env_vars.get("DXVK_ASYNC", "0") == "1")
        self.cb_dxvk_async.setStyleSheet("QCheckBox { color: #F5F7FA; font-weight: 600; }")
        tc_layout.addWidget(self.cb_dxvk_async)

        # Mesa GPL Shader Checkbox
        self.cb_radv_gpl = QCheckBox("Mesa Graphics Pipeline Libraries (RADV_PERFTEST=gpl)")
        self.cb_radv_gpl.setChecked("gpl" in self.env_vars.get("RADV_PERFTEST", ""))
        self.cb_radv_gpl.setStyleSheet("QCheckBox { color: #F5F7FA; font-weight: 600; }")
        tc_layout.addWidget(self.cb_radv_gpl)

        # Dedicated GPU Priority Checkbox
        self.cb_discrete_gpu = QCheckBox("Force Dedicated GPU (DRI_PRIME=1 / MESA_VK_DEVICE_SELECT)")
        self.cb_discrete_gpu.setChecked(self.env_vars.get("DRI_PRIME", "0") == "1")
        self.cb_discrete_gpu.setStyleSheet("QCheckBox { color: #F5F7FA; font-weight: 600; }")
        tc_layout.addWidget(self.cb_discrete_gpu)

        # Frame Limiter Row
        fps_row = QHBoxLayout()
        fps_row.addWidget(QLabel("<font color='#F5F7FA'><b>Frame Rate Cap (DXVK_FRAME_RATE):</b></font>"))
        self.combo_fps = QComboBox()
        self.combo_fps.addItems(["Unlimited (0)", "30 FPS", "60 FPS", "90 FPS", "120 FPS", "144 FPS"])
        self.combo_fps.setStyleSheet("QComboBox { background: #1A1E26; color: #F5F7FA; border: 1px solid #252A33; border-radius: 4px; padding: 4px 10px; }")
        cur_fps = str(self.env_vars.get("DXVK_FRAME_RATE", "0"))
        fps_map = {"0": 0, "30": 1, "60": 2, "90": 3, "120": 4, "144": 5}
        self.combo_fps.setCurrentIndex(fps_map.get(cur_fps, 0))
        fps_row.addWidget(self.combo_fps)
        fps_row.addStretch()
        tc_layout.addLayout(fps_row)

        body_layout.addWidget(toggles_card)

        # Custom Key-Value Environment Variables Table
        sec_custom = QLabel("Custom Environment Variables")
        sec_custom.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        sec_custom.setStyleSheet("color: #F5F7FA; border-bottom: 1px solid #252A33; padding-bottom: 4px; margin-top: 6px;")
        body_layout.addWidget(sec_custom)

        custom_card = QFrame()
        custom_card.setStyleSheet("QFrame { background: #14171D; border: 1px solid #252A33; border-radius: 8px; padding: 10px; }")
        cc_layout = QVBoxLayout(custom_card)
        cc_layout.setSpacing(8)

        self.table_vars = QTableWidget()
        self.table_vars.setColumnCount(2)
        self.table_vars.setHorizontalHeaderLabels(["Variable Name", "Value"])
        self.table_vars.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_vars.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table_vars.setStyleSheet("""
            QTableWidget {
                background: #0D0F14;
                color: #F5F7FA;
                border: 1px solid #252A33;
                border-radius: 6px;
                gridline-color: #1A1E26;
            }
            QHeaderView::section {
                background: #14171D;
                color: #A7ADB8;
                border: 1px solid #252A33;
                padding: 4px 8px;
                font-weight: bold;
            }
        """)

        # Populate custom vars (excluding the managed preset toggles)
        managed_keys = {"WINE_FULLSCREEN_FSR", "WINE_FULLSCREEN_FSR_STRENGTH", "DXVK_ASYNC", "RADV_PERFTEST", "DRI_PRIME", "DXVK_FRAME_RATE"}
        custom_items = [(k, v) for k, v in self.env_vars.items() if k not in managed_keys]
        self.table_vars.setRowCount(len(custom_items))
        for row_idx, (k, v) in enumerate(custom_items):
            self.table_vars.setItem(row_idx, 0, QTableWidgetItem(str(k)))
            self.table_vars.setItem(row_idx, 1, QTableWidgetItem(str(v)))

        cc_layout.addWidget(self.table_vars)

        btn_row_table = QHBoxLayout()
        btn_add_var = QPushButton("+ Add Variable")
        btn_add_var.setStyleSheet("QPushButton { background: #1A1E26; color: #3B9FE8; border: 1px solid #252A33; border-radius: 4px; padding: 4px 10px; font-weight: 600; font-size: 11px; } QPushButton:hover { background: #252A33; }")
        btn_add_var.clicked.connect(self._add_variable_row)
        btn_row_table.addWidget(btn_add_var)

        btn_del_var = QPushButton("- Remove Selected")
        btn_del_var.setStyleSheet("QPushButton { background: #1A1E26; color: #F05D6C; border: 1px solid #252A33; border-radius: 4px; padding: 4px 10px; font-weight: 600; font-size: 11px; } QPushButton:hover { background: #252A33; }")
        btn_del_var.clicked.connect(self._remove_variable_row)
        btn_row_table.addWidget(btn_del_var)
        btn_row_table.addStretch()
        cc_layout.addLayout(btn_row_table)

        body_layout.addWidget(custom_card)

        body_layout.addStretch()
        scroll.setWidget(body)
        return scroll

    def _create_saves_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(18, 16, 18, 16)
        body_layout.setSpacing(14)

        # ── 1. Detected Save Folder Card ──
        sec_detected = QLabel("Detected Save Location")
        sec_detected.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        sec_detected.setStyleSheet("color: #F5F7FA; border-bottom: 1px solid #252A33; padding-bottom: 4px;")
        body_layout.addWidget(sec_detected)

        from core.cloud_save_sync import CloudSaveSyncEngine, SyncStatus
        status, local_stats, cloud_stats = CloudSaveSyncEngine.check_sync_status(self.game_name, self.game_path, self.steam_id)

        save_card = QFrame()
        save_card.setStyleSheet("QFrame { background: #14171D; border: 1px solid #252A33; border-radius: 8px; padding: 12px; }")
        sc_layout = QVBoxLayout(save_card)
        sc_layout.setSpacing(8)

        if local_stats.exists:
            from ui.dialogs.save_conflict_dialog import format_bytes
            from datetime import datetime
            date_str = datetime.fromtimestamp(local_stats.last_modified).strftime("%Y-%m-%d %H:%M:%S")

            row_path = QHBoxLayout()
            lbl_folder_path = QLabel(f"<b>Path:</b> <font color='#3B9FE8' face='monospace'>{local_stats.display_path}</font>")
            lbl_folder_path.setWordWrap(True)
            row_path.addWidget(lbl_folder_path, 1)

            btn_open_save_folder = QPushButton(" Open Folder")
            btn_open_save_folder.setIcon(get_icon("ph.folder-open-bold"))
            btn_open_save_folder.setStyleSheet("QPushButton { background: #1A1E26; color: #F5F7FA; border: 1px solid #252A33; border-radius: 4px; padding: 4px 10px; font-size: 11px; } QPushButton:hover { background: #252A33; border-color: #3B9FE8; }")
            btn_open_save_folder.clicked.connect(lambda: subprocess.Popen(["xdg-open", os.path.dirname(local_stats.display_path) if os.path.isfile(local_stats.display_path) else local_stats.display_path], env=host_process_env()))
            row_path.addWidget(btn_open_save_folder)
            sc_layout.addLayout(row_path)

            lbl_details = QLabel(f"<font color='#6F7682'>Files:</font> {local_stats.file_count} &nbsp;|&nbsp; <font color='#6F7682'>Total Size:</font> {format_bytes(local_stats.size_bytes)} &nbsp;|&nbsp; <font color='#6F7682'>Last Modified:</font> <font color='#35C98A'>{date_str}</font>")
            lbl_details.setStyleSheet("font-size: 11px; color: #F5F7FA;")
            sc_layout.addWidget(lbl_details)
        else:
            lbl_none = QLabel("<font color='#6F7682'>No save folder discovered yet. Save directory will be auto-detected after first launch.</font>")
            lbl_none.setStyleSheet("font-size: 11px;")
            sc_layout.addWidget(lbl_none)

        body_layout.addWidget(save_card)

        # ── 2. Cloud Save Synchronization Card ──
        sec_sync = QLabel("Cloud Save Synchronization")
        sec_sync.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        sec_sync.setStyleSheet("color: #F5F7FA; border-bottom: 1px solid #252A33; padding-bottom: 4px; margin-top: 6px;")
        body_layout.addWidget(sec_sync)

        sync_card = QFrame()
        sync_card.setStyleSheet("QFrame { background: #14171D; border: 1px solid #252A33; border-radius: 8px; padding: 12px; }")
        syc_layout = QVBoxLayout(sync_card)
        syc_layout.setSpacing(10)

        # Status badge
        status_text_map = {
            SyncStatus.IN_SYNC: "<font color='#35C98A'><b>Synced with Cloud</b></font> (Local & Cloud versions match)",
            SyncStatus.LOCAL_NEWER: "<font color='#3B9FE8'><b>Local Save is Newer</b></font> (Ready to upload)",
            SyncStatus.CLOUD_NEWER: "<font color='#E5A93D'><b>Cloud Save is Newer</b></font> (Cloud contains newer save)",
            SyncStatus.CLOUD_ONLY: "<font color='#3B9FE8'><b>Cloud Save Available</b></font> (No local save found)",
            SyncStatus.NO_SAVES: "<font color='#6F7682'>No local or cloud save files found</font>"
        }
        self.lbl_cloud_status = QLabel(status_text_map.get(status, "Unknown"))
        self.lbl_cloud_status.setStyleSheet("font-size: 12px;")
        syc_layout.addWidget(self.lbl_cloud_status)

        cloud_root = CloudSaveSyncEngine.get_cloud_root()
        lbl_cloud_dir = QLabel(f"<font color='#6F7682'>Cloud Root:</font> <font color='#A7ADB8' face='monospace'>{cloud_root}</font>")
        lbl_cloud_dir.setStyleSheet("font-size: 10px;")
        lbl_cloud_dir.setWordWrap(True)
        syc_layout.addWidget(lbl_cloud_dir)

        sync_btn_row = QHBoxLayout()
        btn_sync_up = QPushButton(" Upload to Cloud Now")
        btn_sync_up.setIcon(get_app_icon("export"))
        btn_sync_up.setStyleSheet("QPushButton { background: #1A1E26; color: #3B9FE8; border: 1px solid #252A33; border-radius: 4px; padding: 6px 12px; font-weight: 600; font-size: 11px; } QPushButton:hover { background: #252A33; border-color: #3B9FE8; }")
        btn_sync_up.clicked.connect(self._sync_up_now)
        sync_btn_row.addWidget(btn_sync_up)

        if cloud_stats.exists:
            btn_sync_down = QPushButton(" Download from Cloud")
            btn_sync_down.setIcon(get_app_icon("import"))
            btn_sync_down.setStyleSheet("QPushButton { background: #1A1E26; color: #35C98A; border: 1px solid #252A33; border-radius: 4px; padding: 6px 12px; font-weight: 600; font-size: 11px; } QPushButton:hover { background: #252A33; border-color: #35C98A; }")
            btn_sync_down.clicked.connect(self._sync_down_now)
            sync_btn_row.addWidget(btn_sync_down)

        sync_btn_row.addStretch()
        syc_layout.addLayout(sync_btn_row)
        body_layout.addWidget(sync_card)

        # ── 3. Interactive Save Manager Button ──
        btn_open_mgr = QPushButton(" Open Full Save Inspector & Backup Manager")
        btn_open_mgr.setIcon(get_icon("ph.archive-bold"))
        btn_open_mgr.setFixedHeight(38)
        btn_open_mgr.setStyleSheet("""
            QPushButton {
                background: #1A1E26;
                color: #F5F7FA;
                border: 1px solid #252A33;
                border-radius: 6px;
                padding: 0 16px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #252A33;
                border-color: #3B9FE8;
            }
        """)
        btn_open_mgr.clicked.connect(self._open_save_manager)
        body_layout.addWidget(btn_open_mgr)

        body_layout.addStretch()
        scroll.setWidget(body)
        return scroll

    def _sync_up_now(self):
        from core.cloud_save_sync import CloudSaveSyncEngine
        if CloudSaveSyncEngine.sync_local_to_cloud(self.game_name, self.game_path, self.steam_id):
            QMessageBox.information(self, "Cloud Sync", "Local save successfully uploaded to Cloud save repository.")
        else:
            QMessageBox.warning(self, "Cloud Sync", "No local save files found to upload.")

    def _sync_down_now(self):
        from core.cloud_save_sync import CloudSaveSyncEngine
        if CloudSaveSyncEngine.sync_cloud_to_local(self.game_name, self.game_path):
            QMessageBox.information(self, "Cloud Sync", "Cloud save successfully restored to game prefix.")
        else:
            QMessageBox.critical(self, "Cloud Sync", "Failed to restore cloud save.")

    def _add_variable_row(self):
        row = self.table_vars.rowCount()
        self.table_vars.insertRow(row)
        self.table_vars.setItem(row, 0, QTableWidgetItem("VARIABLE_NAME"))
        self.table_vars.setItem(row, 1, QTableWidgetItem("1"))

    def _remove_variable_row(self):
        row = self.table_vars.currentRow()
        if row >= 0:
            self.table_vars.removeRow(row)

    def _save_and_close(self):
        """Collect all presets and custom variables, update database, and close."""
        updated_env: Dict[str, str] = {}

        # 1. Preset Toggles
        if self.cb_fsr.isChecked():
            updated_env["WINE_FULLSCREEN_FSR"] = "1"
            updated_env["WINE_FULLSCREEN_FSR_STRENGTH"] = str(self.slider_fsr.value())

        if self.cb_dxvk_async.isChecked():
            updated_env["DXVK_ASYNC"] = "1"

        if self.cb_radv_gpl.isChecked():
            updated_env["RADV_PERFTEST"] = "gpl"

        if self.cb_discrete_gpu.isChecked():
            updated_env["DRI_PRIME"] = "1"
            updated_env["MESA_VK_DEVICE_SELECT"] = "1"

        # Frame Rate
        fps_options = ["0", "30", "60", "90", "120", "144"]
        selected_fps = fps_options[self.combo_fps.currentIndex()]
        if selected_fps != "0":
            updated_env["DXVK_FRAME_RATE"] = selected_fps

        # 2. Custom Variables Table
        for row in range(self.table_vars.rowCount()):
            item_k = self.table_vars.item(row, 0)
            item_v = self.table_vars.item(row, 1)
            if item_k and item_v:
                k_txt = item_k.text().strip()
                v_txt = item_v.text().strip()
                if k_txt:
                    updated_env[k_txt] = v_txt

        # Save to database
        if self.parent_window and hasattr(self.parent_window, "db"):
            self.parent_window.db.update_game_env_vars(self.game_id, updated_env)
            logger.info(f"Saved {len(updated_env)} environment variables for game {self.game_id}")

        self.accept()

    def _update_runtime_label(self):
        if self.custom_proton_path:
            self.lbl_current_runtime.setText(
                f"<font color='#6F7682'>Custom Runtime:</font> <font color='#3B9FE8'><b>{self.custom_proton_path}</b></font>"
            )
        else:
            self.lbl_current_runtime.setText(
                "<font color='#6F7682'>Runtime:</font> <font color='#F5F7FA'>Global Default (from SafeLauncher Settings)</font>"
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

    def _open_save_manager(self):
        SaveManagerDialog(self.game_id, self.game_name, self.game_path, self.steam_id, self).exec()
