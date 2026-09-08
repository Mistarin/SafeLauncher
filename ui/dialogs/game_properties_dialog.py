import os
import json
import subprocess
from typing import Optional, Dict
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QFileDialog, QFrame, QScrollArea, QMessageBox, QGridLayout,
    QTabWidget, QCheckBox, QSlider, QComboBox, QSpinBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QProgressDialog, QApplication
)
from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QIcon

from ui.icons import get_icon, get_app_icon
from ui.components.sidebar import DialogTitleBar
from ui.components.check_field import CheckField as QCheckBox
from ui.maintenance_dialogs import PrefixMaintenanceDialog
from ui.dialogs.save_manager_dialog import SaveManagerDialog
from core.host_process import host_process_env
from core.logger import get_logger
from core.safe_thread import TaskSupervisor
from core.performance_env import (
    ENABLE_GAMEMODE,
    DXVK_MAX_DEVICE_MEMORY_MB,
    MANAGED_ENV_KEYS,
    MAX_VRAM_MB,
    MIN_VRAM_MB,
    gamemode_library,
    parse_vram_mb,
)

logger = get_logger("GamePropertiesDialog")


class GamePropertiesDialog(QDialog):
    """Clean, consolidated Game Properties dialog with Performance Presets and Save Manager."""

    _save_stats_ready = pyqtSignal(object)
    _gen_restore_done = pyqtSignal(bool, int)
    _manual_sync_up_done = pyqtSignal(bool)
    _manual_sync_down_done = pyqtSignal(bool)

    def __init__(self, game: tuple, parent=None):
        super().__init__(parent)
        self.game = game
        self.parent_window = parent
        self._task_supervisor = TaskSupervisor(self, logger)

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

        self._save_stats_ready.connect(self._on_save_stats_ready)
        self._gen_restore_done.connect(self._on_gen_restore_done)
        self._manual_sync_up_done.connect(self._on_manual_sync_up_done)
        self._manual_sync_down_done.connect(self._on_manual_sync_down_done)
        self._cloud_versions = []
        self._backup_version = None
        self._active_manual_sync_progress = None


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
                border: none;
                background: #0D0F14;
                border-radius: 0 0 8px 8px;
                top: -1px;
            }
            QTabBar::tab {
                background: #18181B;
                color: #A7ADB8;
                border: none;
                border-bottom: none;
                padding: 8px 18px;
                margin-right: 2px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 600;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background: #18181B;
                color: #3B9FE8;
                border-color: transparent;
                border-bottom: 1px solid #0D0F14;
            }
            QTabBar::tab:hover:!selected {
                background: #202024;
                color: #F5F7FA;
            }
        """)

        # Tab 1: General & Runtime
        self.tab_general = self._create_general_tab()
        # Escape the ampersand so Qt does not treat it as a mnemonic marker.
        self.tabs.addTab(self.tab_general, "General && Runtime")

        # Tab 2: Performance & Presets
        self.tab_presets = self._create_presets_tab()
        self.tabs.addTab(self.tab_presets, "Performance && Presets")

        # Tab 3: Saves & Snapshots
        self.tab_saves = self._create_saves_tab()
        self.tabs.addTab(self.tab_saves, "Saves && Snapshots")

        root_layout.addWidget(self.tabs)

        # Bottom Bar
        bottom_bar = QWidget()
        bottom_bar.setStyleSheet("background: #18181B; border-top: none;")
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
        scroll.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        scroll.viewport().setStyleSheet("background: transparent; border: none;")

        body = QWidget()
        body.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        body.setStyleSheet("background: transparent;")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(18, 16, 18, 16)
        body_layout.setSpacing(14)

        # Summary
        sec_summary = QLabel("Game Overview")
        sec_summary.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        sec_summary.setStyleSheet("color: #F5F7FA; background: transparent; border: none; padding-bottom: 4px;")
        body_layout.addWidget(sec_summary)

        summary_card = QFrame()
        # Keep the overview card on the same dark surface as the dialog.  The
        # former lighter fill read as a grey overlay over every value cell.
        summary_card.setStyleSheet("QFrame { background: #18181B; border: none; border-radius: 10px; padding: 8px; }")
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
        sec_runtime.setStyleSheet("color: #F5F7FA; background: transparent; border: none; padding-bottom: 4px; margin-top: 6px;")
        body_layout.addWidget(sec_runtime)

        runtime_card = QFrame()
        runtime_card.setStyleSheet("QFrame { background: #18181B; border: none; border-radius: 10px; padding: 10px; }")
        rc_layout = QVBoxLayout(runtime_card)
        rc_layout.setSpacing(8)

        self.lbl_current_runtime = QLabel()
        self._update_runtime_label()
        rc_layout.addWidget(self.lbl_current_runtime)

        btn_row_rt = QHBoxLayout()
        btn_set_rt = QPushButton("Change Proton Runtime...")
        btn_set_rt.setIcon(get_icon("ph.folder-open-bold"))
        btn_set_rt.setStyleSheet("QPushButton { background: #161A22; color: #F5F7FA; border: 1px solid #2A303B; border-radius: 5px; padding: 6px 12px; font-weight: 600; font-size: 12px; } QPushButton:hover { background: #202633; border-color: #3B9FE8; }")
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
        sec_prefix.setStyleSheet("color: #F5F7FA; background: transparent; border: none; padding-bottom: 4px; margin-top: 6px;")
        body_layout.addWidget(sec_prefix)

        prefix_card = QFrame()
        prefix_card.setStyleSheet("QFrame { background: #18181B; border: none; border-radius: 10px; padding: 10px; }")
        pc_layout = QHBoxLayout(prefix_card)
        pc_layout.setSpacing(10)

        btn_open_maint = QPushButton(" Prefix Maintenance Tools")
        btn_open_maint.setIcon(get_icon("ph.wrench-bold"))
        btn_open_maint.setStyleSheet("QPushButton { background: #161A22; color: #F5F7FA; border: 1px solid #2A303B; border-radius: 5px; padding: 8px 14px; font-weight: 600; font-size: 12px; } QPushButton:hover { background: #202633; border-color: #3B9FE8; }")
        btn_open_maint.clicked.connect(self._open_prefix_maintenance)
        pc_layout.addWidget(btn_open_maint)

        btn_open_dir = QPushButton(" Open Game Directory")
        btn_open_dir.setIcon(get_icon("ph.folder-bold"))
        btn_open_dir.setStyleSheet("QPushButton { background: #161A22; color: #F5F7FA; border: 1px solid #2A303B; border-radius: 5px; padding: 8px 14px; font-weight: 600; font-size: 12px; } QPushButton:hover { background: #202633; border-color: #3B9FE8; }")
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
        sec_toggles = QLabel("Performance and Optimization Presets")
        sec_toggles.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        sec_toggles.setStyleSheet("color: #F5F7FA; background: transparent; border: none; padding-bottom: 4px;")
        body_layout.addWidget(sec_toggles)

        toggles_card = QFrame()
        toggles_card.setStyleSheet("QFrame { background: #18181B; border: none; border-radius: 10px; padding: 12px; }")
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

        # Optional Feral GameMode injection. This is resolved by the runner so
        # the setting remains portable between machines with and without the
        # GameMode library installed.
        self.cb_gamemode = QCheckBox("Inject Feral GameMode into this game (LD_PRELOAD)")
        self.cb_gamemode.setChecked(
            str(self.env_vars.get(ENABLE_GAMEMODE, "0")).strip().lower() in {"1", "true", "yes", "on"}
        )
        self.cb_gamemode.setStyleSheet("QCheckBox { color: #F5F7FA; font-weight: 600; }")
        tc_layout.addWidget(self.cb_gamemode)
        gamemode_note = QLabel(
            "GameMode library detected — it will be injected when this game starts."
            if gamemode_library()
            else "GameMode library not detected — the game will start normally if this option is enabled."
        )
        gamemode_note.setWordWrap(True)
        gamemode_note.setStyleSheet(
            "color: #8F96A3; font-size: 11px; padding-left: 28px;"
            if gamemode_library()
            else "color: #D9A441; font-size: 11px; padding-left: 28px;"
        )
        tc_layout.addWidget(gamemode_note)

        # DXVK changes the amount of device memory reported to the game. It
        # does not allocate physical VRAM and deliberately has a prominent
        # explanation beside the control.
        vram_row = QHBoxLayout()
        self.cb_vram_override = QCheckBox("Override reported DXVK VRAM")
        self.cb_vram_override.setChecked(parse_vram_mb(self.env_vars.get(DXVK_MAX_DEVICE_MEMORY_MB)) is not None)
        self.cb_vram_override.setStyleSheet("QCheckBox { color: #F5F7FA; font-weight: 600; }")
        vram_row.addWidget(self.cb_vram_override)
        self.spin_vram_override = QSpinBox()
        self.spin_vram_override.setRange(MIN_VRAM_MB, MAX_VRAM_MB)
        self.spin_vram_override.setSingleStep(256)
        self.spin_vram_override.setSuffix(" MiB")
        self.spin_vram_override.setValue(parse_vram_mb(self.env_vars.get(DXVK_MAX_DEVICE_MEMORY_MB)) or 8192)
        self.spin_vram_override.setEnabled(self.cb_vram_override.isChecked())
        self.spin_vram_override.setStyleSheet(
            "QSpinBox { background: #161A22; color: #F5F7FA; border: none; "
            "border-radius: 6px; padding: 4px 8px; min-width: 110px; }"
        )
        self.cb_vram_override.toggled.connect(self.spin_vram_override.setEnabled)
        vram_row.addWidget(self.spin_vram_override)
        vram_row.addStretch()
        tc_layout.addLayout(vram_row)

        vram_note = QLabel(
            "Reported VRAM only — this does not add physical memory or change BIOS/kernel limits. "
            "Values that are too high can cause allocation failures."
        )
        vram_note.setWordWrap(True)
        vram_note.setStyleSheet("color: #8F96A3; font-size: 11px; padding-left: 28px;")
        tc_layout.addWidget(vram_note)

        # Frame Limiter Row
        fps_row = QHBoxLayout()
        fps_row.addWidget(QLabel("<font color='#F5F7FA'><b>Frame Rate Cap (DXVK_FRAME_RATE):</b></font>"))
        self.combo_fps = QComboBox()
        self.combo_fps.addItems(["Unlimited (0)", "30 FPS", "60 FPS", "90 FPS", "120 FPS", "144 FPS"])
        self.combo_fps.setStyleSheet("QComboBox { background: #161A22; color: #F5F7FA; border: none; border-radius: 6px; padding: 4px 10px; }")
        cur_fps = str(self.env_vars.get("DXVK_FRAME_RATE", "0"))
        fps_map = {"0": 0, "30": 1, "60": 2, "90": 3, "120": 4, "144": 5}
        self.combo_fps.setCurrentIndex(fps_map.get(cur_fps, 0))
        fps_row.addWidget(self.combo_fps)
        fps_row.addStretch()
        tc_layout.addLayout(fps_row)

        body_layout.addWidget(toggles_card)

        # Custom Key-Value Environment Variables Table
        sec_custom = QLabel("Custom environment variables")
        sec_custom.setFont(QFont("Arial", 11, QFont.Weight.Medium))
        sec_custom.setStyleSheet("color: #D4D4D8; background: transparent; border: none; padding-bottom: 4px; margin-top: 6px;")
        body_layout.addWidget(sec_custom)

        custom_card = QFrame()
        custom_card.setStyleSheet("QFrame { background: #18181B; border: none; border-radius: 10px; padding: 10px; }")
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
        managed_keys = {
            "WINE_FULLSCREEN_FSR", "WINE_FULLSCREEN_FSR_STRENGTH", "DXVK_ASYNC",
            "RADV_PERFTEST", "DRI_PRIME", "MESA_VK_DEVICE_SELECT", "DXVK_FRAME_RATE",
            *MANAGED_ENV_KEYS,
        }
        custom_items = [(k, v) for k, v in self.env_vars.items() if k not in managed_keys]
        self.table_vars.setRowCount(len(custom_items))
        for row_idx, (k, v) in enumerate(custom_items):
            self.table_vars.setItem(row_idx, 0, QTableWidgetItem(str(k)))
            self.table_vars.setItem(row_idx, 1, QTableWidgetItem(str(v)))

        cc_layout.addWidget(self.table_vars)

        btn_row_table = QHBoxLayout()
        btn_add_var = QPushButton("+ Add Variable")
        btn_add_var.setStyleSheet("QPushButton { background: #161A22; color: #3B9FE8; border: none; border-radius: 6px; padding: 4px 10px; font-weight: 600; font-size: 11px; } QPushButton:hover { background: #202633; }")
        btn_add_var.clicked.connect(self._add_variable_row)
        btn_row_table.addWidget(btn_add_var)

        btn_del_var = QPushButton("- Remove Selected")
        btn_del_var.setStyleSheet("QPushButton { background: #161A22; color: #F05D6C; border: none; border-radius: 6px; padding: 4px 10px; font-weight: 600; font-size: 11px; } QPushButton:hover { background: #202633; }")
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
        sec_detected.setStyleSheet("color: #F5F7FA; background: transparent; border: none; padding-bottom: 4px;")
        body_layout.addWidget(sec_detected)

        save_card = QFrame()
        save_card.setStyleSheet("QFrame { background: #18181B; border: none; border-radius: 10px; padding: 12px; }")
        sc_layout = QVBoxLayout(save_card)
        sc_layout.setSpacing(8)

        row_path = QHBoxLayout()
        self.lbl_folder_path = QLabel("<b>Path:</b> <font color='#6F7682'>Scanning save directory…</font>")
        self.lbl_folder_path.setWordWrap(True)
        row_path.addWidget(self.lbl_folder_path, 1)

        self.btn_open_save_folder = QPushButton(" Open Folder")
        self.btn_open_save_folder.setIcon(get_icon("ph.folder-open-bold"))
        self.btn_open_save_folder.setEnabled(False)
        self.btn_open_save_folder.setStyleSheet("QPushButton { background: #161A22; color: #F5F7FA; border: none; border-radius: 6px; padding: 4px 10px; font-size: 11px; } QPushButton:hover { background: #202633; } QPushButton:disabled { color: #6F7682; }")
        row_path.addWidget(self.btn_open_save_folder)
        sc_layout.addLayout(row_path)

        self.lbl_save_details = QLabel("<font color='#6F7682'>Scanning save metadata…</font>")
        self.lbl_save_details.setStyleSheet("font-size: 11px; color: #F5F7FA;")
        sc_layout.addWidget(self.lbl_save_details)

        body_layout.addWidget(save_card)

        # ── 2. Cloud Save Synchronization Card ──
        sec_sync = QLabel("Cloud Save Synchronization")
        sec_sync.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        sec_sync.setStyleSheet("color: #F5F7FA; background: transparent; border: none; padding-bottom: 4px; margin-top: 6px;")
        body_layout.addWidget(sec_sync)

        sync_card = QFrame()
        sync_card.setStyleSheet("QFrame { background: #18181B; border: none; border-radius: 10px; padding: 12px; }")
        syc_layout = QVBoxLayout(sync_card)
        syc_layout.setSpacing(10)

        # Status badge
        self.lbl_cloud_status = QLabel("<font color='#6F7682'>Checking cloud status…</font>")
        self.lbl_cloud_status.setStyleSheet("font-size: 12px;")
        syc_layout.addWidget(self.lbl_cloud_status)

        from core.cloud_save_sync import CloudSaveSyncEngine
        cloud_root = CloudSaveSyncEngine.get_cloud_root()
        lbl_cloud_dir = QLabel(f"<font color='#6F7682'>Cloud Root:</font> <font color='#A7ADB8' face='monospace'>{cloud_root}</font>")
        lbl_cloud_dir.setStyleSheet("font-size: 10px;")
        lbl_cloud_dir.setWordWrap(True)
        syc_layout.addWidget(lbl_cloud_dir)

        sync_btn_row = QHBoxLayout()
        self.btn_sync_up = QPushButton(" Upload to Cloud Now")
        self.btn_sync_up.setIcon(get_app_icon("export"))
        self.btn_sync_up.setStyleSheet("QPushButton { background: #161A22; color: #3B9FE8; border: none; border-radius: 6px; padding: 6px 12px; font-weight: 600; font-size: 11px; } QPushButton:hover { background: #202633; }")
        self.btn_sync_up.clicked.connect(self._sync_up_now)
        sync_btn_row.addWidget(self.btn_sync_up)

        self.btn_sync_down = QPushButton(" Download from Cloud")
        self.btn_sync_down.setIcon(get_app_icon("import"))
        self.btn_sync_down.setStyleSheet("QPushButton { background: #161A22; color: #35C98A; border: none; border-radius: 6px; padding: 6px 12px; font-weight: 600; font-size: 11px; } QPushButton:hover { background: #202633; }")
        self.btn_sync_down.clicked.connect(self._sync_down_now)
        self.btn_sync_down.hide()
        sync_btn_row.addWidget(self.btn_sync_down)

        sync_btn_row.addStretch()
        syc_layout.addLayout(sync_btn_row)

        # Retained cloud generations (active + history) with manual selection & restore
        self.ver_selector_layout = QVBoxLayout()
        self.ver_selector_layout.setSpacing(6)

        lbl_ver_title = QLabel("Cloud Save Versions & History")
        lbl_ver_title.setStyleSheet("font-size: 11px; font-weight: bold; color: #F5F7FA;")
        self.ver_selector_layout.addWidget(lbl_ver_title)

        combo_row = QHBoxLayout()
        combo_row.setSpacing(8)

        self.combo_cloud_versions = QComboBox()
        self.combo_cloud_versions.setFixedHeight(32)
        self.combo_cloud_versions.setStyleSheet("""
            QComboBox {
                background: #161A22;
                color: #F5F7FA;
                border: none;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
            }
            QComboBox:hover {
                background: #202633;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background: #18181B;
                color: #F5F7FA;
                selection-background-color: #3B9FE8;
                border: none;
            }
        """)
        combo_row.addWidget(self.combo_cloud_versions, 1)

        self.btn_restore_selected = QPushButton(" Restore Selected")
        self.btn_restore_selected.setIcon(get_icon("ph.clock-counter-clockwise-bold"))
        self.btn_restore_selected.setFixedHeight(32)
        self.btn_restore_selected.setStyleSheet("""
            QPushButton {
                background: #161A22;
                color: #E5A93D;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
                font-size: 11px;
            }
            QPushButton:hover {
                background: #202633;
            }
            QPushButton:disabled {
                color: #6F7682;
                border: none;
            }
        """)
        self.btn_restore_selected.clicked.connect(self._restore_selected_version_now)
        self.btn_restore_backup = self.btn_restore_selected  # Backwards compatibility
        combo_row.addWidget(self.btn_restore_selected)

        self.ver_selector_layout.addLayout(combo_row)

        self.lbl_generations = QLabel("")
        self.lbl_generations.setStyleSheet("font-size: 11px; color: #A7ADB8;")
        self.lbl_generations.setWordWrap(True)
        self.lbl_generations.hide()
        self.ver_selector_layout.addWidget(self.lbl_generations)

        self.ver_selector_widget = QWidget()
        self.ver_selector_widget.setLayout(self.ver_selector_layout)
        self.ver_selector_widget.hide()
        syc_layout.addWidget(self.ver_selector_widget)

        body_layout.addWidget(sync_card)

        # ── 3. Interactive Save Manager Button ──
        btn_open_mgr = QPushButton(" Open Full Save Inspector & Backup Manager")
        btn_open_mgr.setIcon(get_icon("ph.archive-bold"))
        btn_open_mgr.setFixedHeight(38)
        btn_open_mgr.setStyleSheet("""
            QPushButton {
                background: #161A22;
                color: #F5F7FA;
                border: none;
                border-radius: 6px;
                padding: 0 16px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #202633;
            }
        """)
        btn_open_mgr.clicked.connect(self._open_save_manager)
        body_layout.addWidget(btn_open_mgr)

        body_layout.addStretch()
        scroll.setWidget(body)

        # Trigger async save status load
        self._load_save_stats_async()

        return scroll

    def _start_managed_task(self, name: str, work, on_complete):
        return self._task_supervisor.start(name, work, on_complete)

    def closeEvent(self, event):
        self._task_supervisor.cancel_all(100)
        if self._task_supervisor.has_running_tasks():
            QTimer.singleShot(100, self.close)
            event.ignore()
            return
        super().closeEvent(event)

    def _load_save_stats_async(self):
        """Asynchronously load save detection & cloud status on worker thread."""
        def _worker():
            try:
                from core.cloud_save_sync import CloudSaveSyncEngine, backend_active, resolve_name_key
                status, local_stats, cloud_stats = CloudSaveSyncEngine.check_sync_status(
                    self.game_name, self.game_path, self.steam_id
                )
                versions = None
                if backend_active():
                    try:
                        _stats, snapshot = CloudSaveSyncEngine._remote_stats(
                            resolve_name_key(self.game_name),
                            game_name=self.game_name
                        )
                        versions = (snapshot or {}).get("versions")
                    except Exception:
                        versions = None

                return status, local_stats, cloud_stats, versions
            except Exception as e:
                logger.warning(f"Async save stats check failed for '{self.game_name}': {e}")
                return None, None, None, None

        self._start_managed_task(
            f"SafeLauncher-PropSave-{self.game_id}", _worker, self._save_stats_ready.emit
        )

    def _on_save_stats_ready(self, payload):
        """GUI-thread handler to populate Save tab metadata without blocking dialog opening."""
        status, local_stats, cloud_stats, versions = payload
        if status is None or local_stats is None:
            self.lbl_cloud_status.setText("<font color='#F05D6C'>Save status check failed.</font>")
            self.lbl_generations.hide()
            self.btn_restore_backup.hide()
            return

        from ui.dialogs.save_conflict_dialog import format_bytes
        from datetime import datetime
        from core.cloud_save_sync import SyncStatus

        if local_stats.exists:
            date_str = datetime.fromtimestamp(local_stats.last_modified).strftime("%Y-%m-%d %H:%M:%S")
            self.lbl_folder_path.setText(f"<b>Path:</b> <font color='#3B9FE8' face='monospace'>{local_stats.display_path}</font>")
            self.lbl_save_details.setText(
                f"<font color='#6F7682'>Files:</font> {local_stats.file_count} &nbsp;|&nbsp; "
                f"<font color='#6F7682'>Total Size:</font> {format_bytes(local_stats.size_bytes)} &nbsp;|&nbsp; "
                f"<font color='#6F7682'>Last Modified:</font> <font color='#35C98A'>{date_str}</font>"
            )
            self.btn_open_save_folder.setEnabled(True)
            try:
                self.btn_open_save_folder.clicked.disconnect()
            except TypeError:
                pass
            p = local_stats.display_path
            self.btn_open_save_folder.clicked.connect(
                lambda: subprocess.Popen(["xdg-open", os.path.dirname(p) if os.path.isfile(p) else p], env=host_process_env())
            )
        else:
            self.lbl_folder_path.setText("<b>Path:</b> <font color='#6F7682'>Not found</font>")
            self.lbl_save_details.setText("<font color='#6F7682'>No save folder discovered yet. Save directory will be auto-detected after first launch.</font>")
            self.btn_open_save_folder.setEnabled(False)

        status_text_map = {
            SyncStatus.IN_SYNC: "<font color='#35C98A'><b>Synced with Cloud</b></font> (Local & Cloud versions match)",
            SyncStatus.LOCAL_NEWER: "<font color='#3B9FE8'><b>Local Save is Newer</b></font> (Ready to upload)",
            SyncStatus.CLOUD_NEWER: "<font color='#E5A93D'><b>Cloud Save is Newer</b></font> (Cloud contains newer save)",
            SyncStatus.CLOUD_ONLY: "<font color='#3B9FE8'><b>Cloud Save Available</b></font> (No local save found)",
            SyncStatus.NO_SAVES: "<font color='#6F7682'>No local or cloud save files found</font>",
            SyncStatus.CLOUD_OFFLINE: "<font color='#6F7682'><b>Cloud Not Connected</b></font> (Offline, or Secret Key not configured on this device)"
        }
        self.lbl_cloud_status.setText(status_text_map.get(status, "Unknown"))

        if cloud_stats and cloud_stats.exists:
            self.btn_sync_down.show()
        else:
            self.btn_sync_down.hide()

        self._render_generations(versions, local_exists=local_stats.exists)

    def _render_generations(self, versions, local_exists: bool = True):
        """Show retained cloud generations with multi-version selector & restore action."""
        from datetime import datetime
        from ui.dialogs.save_conflict_dialog import format_bytes
        from core.cloud_save_sync import get_active_save_version
        self._cloud_versions = list(versions or [])
        if not self._cloud_versions:
            self._backup_version = None
            self.ver_selector_widget.hide()
            self.btn_restore_selected.setEnabled(False)
            return

        active_ver = get_active_save_version(self.game_name)
        self.combo_cloud_versions.blockSignals(True)
        self.combo_cloud_versions.clear()

        selected_idx = 0
        for idx, v in enumerate(self._cloud_versions):
            v_num = v.get("version", 0)
            # Cloud generations and local safety forks use different wire
            # field names. Normalize both here so retained backups never show
            # an empty date or zero size.
            raw_mtime = v.get("mtime", v.get("sourceMaxMtime", 0)) or 0
            d = datetime.fromtimestamp(float(raw_mtime)).strftime("%Y-%m-%d %H:%M") if raw_mtime else "Unknown date"
            raw_size = v.get("size_bytes", v.get("sizeBytes", 0)) or 0
            sz = format_bytes(int(raw_size))
            is_active = local_exists and ((active_ver is not None and v_num == active_ver) or (active_ver is None and idx == 0))
            if is_active:
                selected_idx = idx
            source_label = "Cloud" if v.get("source") == "cloud" else "Local backup"
            tag = " [Active on this PC]" if is_active else (" [Latest Cloud]" if idx == 0 and source_label == "Cloud" else "")
            display_str = f"{source_label} · {d} · {sz}{tag}"
            self.combo_cloud_versions.addItem(display_str, v_num)

        self.combo_cloud_versions.setCurrentIndex(selected_idx)
        self.combo_cloud_versions.blockSignals(False)

        if len(self._cloud_versions) >= 2:
            self._backup_version = self._cloud_versions[1].get("version")
        else:
            self._backup_version = None

        count_str = f"{len(self._cloud_versions)} generation(s) safely retained in cloud history."
        self.lbl_generations.setText(f"<font color='#6F7682'>History:</font> {count_str}")
        self.lbl_generations.show()
        self.btn_restore_selected.setEnabled(True)
        self.ver_selector_widget.show()

    def _restore_selected_version_now(self):
        from core.cloud_save_sync import CloudSaveSyncEngine
        idx = self.combo_cloud_versions.currentIndex()
        if idx < 0 or idx >= len(self._cloud_versions):
            return
        version = self.combo_cloud_versions.currentData()
        if version is None:
            return

        answer = QMessageBox.question(
            self, "Restore Cloud Generation",
            f"Restore save generation v{version} for '{self.game_name}'?\n\n"
            f"Target Directory: {self.game_path}\n\n"
            "Your existing local save will be preserved in your local backups before overwriting.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.btn_restore_selected.setEnabled(False)

        def _work():
            ok = CloudSaveSyncEngine.restore_cloud_generation(
                self.game_name, self.game_path, self.steam_id, version=int(version)
            )
            return bool(ok), int(version or 0)

        self._start_managed_task(
            f"SafeLauncher-GenRestore-{self.game_id}",
            _work,
            lambda result: self._gen_restore_done.emit(*result),
        )

    def _restore_backup_now(self):
        """Backwards-compatible wrapper for restoring backup generation."""
        self._restore_selected_version_now()

    def _on_gen_restore_done(self, ok: bool, version: int):
        self.btn_restore_selected.setEnabled(True)
        if ok:
            QMessageBox.information(self, "Cloud Sync",
                                    f"Save generation v{version} restored successfully.")
            self._notify_parent_cloud_changed()
        else:
            QMessageBox.critical(self, "Cloud Sync",
                                 f"Failed to restore save generation v{version}.")
        self._load_save_stats_async()

    def _notify_parent_cloud_changed(self):
        """Sync actions change the cloud verdict — make the library badge
        follow immediately instead of waiting for the dialog to close."""
        p = self.parent_window
        if p is not None:
            if hasattr(p, "refresh_cloud_status_for_game"):
                p.refresh_cloud_status_for_game(self.game_id)
            elif hasattr(p, "request_cloud_recheck"):
                p.request_cloud_recheck([self.game_id], "properties_cloud_sync")

    def _sync_up_now(self):
        self.btn_sync_up.setEnabled(False)
        self.btn_sync_down.setEnabled(False)
        if hasattr(self, "btn_restore_selected"):
            self.btn_restore_selected.setEnabled(False)

        prog = QProgressDialog(f"Uploading local save for '{self.game_name}'...", None, 0, 0, self)
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setCancelButton(None)
        prog.setMinimumDuration(0)
        prog.show()
        self._active_manual_sync_progress = prog

        def _work():
            from core.cloud_save_sync import CloudSaveSyncEngine
            try:
                ok = CloudSaveSyncEngine.sync_local_to_cloud(self.game_name, self.game_path, self.steam_id)
            except Exception as e:
                logger.error(f"Manual cloud upload failed for '{self.game_name}': {e}")
                ok = False
            return bool(ok)

        self._start_managed_task(
            f"SafeLauncher-ManualSyncUp-{self.game_id}", _work, self._manual_sync_up_done.emit
        )

    def _on_manual_sync_up_done(self, ok: bool):
        if hasattr(self, "_active_manual_sync_progress") and self._active_manual_sync_progress:
            try:
                self._active_manual_sync_progress.close()
                self._active_manual_sync_progress.deleteLater()
            except Exception:
                pass
            self._active_manual_sync_progress = None

        self.btn_sync_up.setEnabled(True)
        self.btn_sync_down.setEnabled(True)
        if hasattr(self, "btn_restore_selected"):
            self.btn_restore_selected.setEnabled(True)

        if ok:
            QMessageBox.information(self, "Cloud Sync", "Local save successfully uploaded to Cloud save repository.")
            self._load_save_stats_async()
            self._notify_parent_cloud_changed()
        else:
            QMessageBox.warning(self, "Cloud Sync", "No local save files found to upload.")

    def _is_game_running(self) -> bool:
        """True only when a live process for this game is actually tracked.

        running_game_ids alone is not trustworthy: a finished session whose
        tracker got stuck (wrapper outliving the game window) would keep the
        id forever and block restores with a phantom 'game is running'.
        """
        p = self.parent_window
        if p is None:
            return False
        for tracker in getattr(p, "playtime_trackers", []):
            if getattr(tracker, "game_id", None) != self.game_id:
                continue
            proc = getattr(tracker, "process", None)
            if proc is not None and proc.poll() is None:
                return True
        return False

    def _sync_down_now(self):
        if self._is_game_running():
            QMessageBox.warning(
                self, "Game Is Running",
                f"'{self.game_name}' appears to be running. Its in-memory state overwrites the "
                "save files when it exits, so restoring the cloud save now would be undone.\n\n"
                "Close the game first, then download the cloud save.")
            return

        self.btn_sync_up.setEnabled(False)
        self.btn_sync_down.setEnabled(False)
        if hasattr(self, "btn_restore_selected"):
            self.btn_restore_selected.setEnabled(False)

        prog = QProgressDialog(f"Restoring cloud save for '{self.game_name}'...", None, 0, 0, self)
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setCancelButton(None)
        prog.setMinimumDuration(0)
        prog.show()
        self._active_manual_sync_progress = prog

        def _work():
            from core.cloud_save_sync import CloudSaveSyncEngine
            try:
                ok = CloudSaveSyncEngine.sync_cloud_to_local(self.game_name, self.game_path, steam_id=self.steam_id)
            except Exception as e:
                logger.error(f"Manual cloud restore failed for '{self.game_name}': {e}")
                ok = False
            return bool(ok)

        self._start_managed_task(
            f"SafeLauncher-ManualSyncDown-{self.game_id}", _work, self._manual_sync_down_done.emit
        )

    def _on_manual_sync_down_done(self, ok: bool):
        if hasattr(self, "_active_manual_sync_progress") and self._active_manual_sync_progress:
            try:
                self._active_manual_sync_progress.close()
                self._active_manual_sync_progress.deleteLater()
            except Exception:
                pass
            self._active_manual_sync_progress = None

        self.btn_sync_up.setEnabled(True)
        self.btn_sync_down.setEnabled(True)
        if hasattr(self, "btn_restore_selected"):
            self.btn_restore_selected.setEnabled(True)

        if ok:
            QMessageBox.information(self, "Cloud Sync", "Cloud save successfully restored to game prefix.")
            self._load_save_stats_async()
            self._notify_parent_cloud_changed()
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

        if self.cb_gamemode.isChecked():
            updated_env[ENABLE_GAMEMODE] = "1"

        if self.cb_vram_override.isChecked():
            updated_env[DXVK_MAX_DEVICE_MEMORY_MB] = str(self.spin_vram_override.value())

        # Frame Rate
        fps_options = ["0", "30", "60", "90", "120", "144"]
        selected_fps = fps_options[self.combo_fps.currentIndex()]
        if selected_fps != "0":
            updated_env["DXVK_FRAME_RATE"] = selected_fps

        # 2. Custom Variables Table. Managed performance variables are owned
        # by the named controls above and cannot be overridden accidentally.
        for row in range(self.table_vars.rowCount()):
            item_k = self.table_vars.item(row, 0)
            item_v = self.table_vars.item(row, 1)
            if item_k and item_v:
                k_txt = item_k.text().strip()
                v_txt = item_v.text().strip()
                if k_txt and k_txt not in MANAGED_ENV_KEYS:
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
        self._load_save_stats_async()
        self._notify_parent_cloud_changed()
