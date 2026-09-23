import os
import json
import subprocess
from html import escape as html_escape
from typing import Optional, Dict
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QFileDialog, QFrame, QScrollArea, QMessageBox, QGridLayout, QToolButton,
    QTabWidget, QCheckBox, QSlider, QComboBox, QSpinBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QApplication
)
from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QIcon

from ui.icons import get_icon, get_app_icon
from ui.components.sidebar import DialogTitleBar
from ui.components.popup_shell import PopupDialog
from ui.components.check_field import CheckField as QCheckBox
from ui.maintenance_dialogs import PrefixMaintenanceDialog
from ui.dialogs.save_manager_dialog import SaveManagerDialog
from core.host_process import host_process_env
from core.logger import get_logger
from core.date_formatting import format_datetime_timestamp, format_timestamp
from core.steam_build_tracker import has_resolved_build_reference
from core.safe_thread import TaskSupervisor
from core.cloud_operation_service import CloudOperationTarget
from core.cloud_status_service import CloudStatusTarget
from core.request_contracts import RequestPriority, ResourceStatus
from core.zip_backup import ZipBackupManager
from ui.resource_binding import ResourceBinding, bind_request
from ui.components.save_history_timeline import SaveHistoryTimeline
from ui.components.cloud_ui import confirm_restore, cloud_progress, set_accessible_status
from core.performance_env import (
    ENABLE_GAMEMODE,
    GAMEMODE_MODE,
    DXVK_MAX_DEVICE_MEMORY_MB,
    MANAGED_ENV_KEYS,
    MAX_VRAM_MB,
    MIN_VRAM_MB,
    gamemode_library,
    gamemode_wrapper,
    parse_vram_mb,
)

logger = get_logger("GamePropertiesDialog")


class GamePropertiesDialog(PopupDialog):
    """Clean, consolidated Game Properties dialog with Performance Presets and Save Manager."""

    _save_stats_ready = pyqtSignal(object)
    _gen_restore_done = pyqtSignal(object, int)
    _manual_sync_up_done = pyqtSignal(object)
    _manual_sync_down_done = pyqtSignal(object)

    def _cloud_unavailable_result(self, operation: str):
        from core.save_models import SaveOperationResult

        return SaveOperationResult(
            False,
            operation,
            self.game_name,
            error="Cloud service is not available in this view.",
            category="unavailable",
            guidance="Open this workflow from the main SafeLauncher window and check Cloud Center.",
            retry_safe=False,
        )

    def __init__(self, game: tuple, parent=None):
        super().__init__(f"Game Properties: {game[1]}", parent)
        self.game = game
        self.parent_window = parent
        self._task_supervisor = TaskSupervisor(self, logger)
        self.cloud_center_service = getattr(parent, "cloud_center_service", None)
        self.cloud_operation_service = getattr(parent, "cloud_operation_service", None)
        self.cloud_status_service = getattr(parent, "cloud_status_service", None)
        self.request_manager = getattr(parent, "request_manager", None)
        self._resource_bindings: dict[str, ResourceBinding] = {}
        self._save_stats_generation = 0
        self.backup_mgr = ZipBackupManager()

        # Extract game record fields
        self.game_id = game[0]
        self.game_name = game[1]
        self.game_path = game[2]
        self.game_exe = game[3] if len(game) > 3 else ""
        self.game_mode = game[4] if len(game) > 4 else ""
        self.steam_id = game[6] if len(game) > 6 else ""
        self.custom_proton_path = game[12] if len(game) > 12 and game[12] else ""
        self.current_build_id = game[11] if len(game) > 11 and game[11] else ""
        self.current_build_date = game[20] if len(game) > 20 and game[20] else 0
        self.latest_build_id = ""
        self.latest_build_date = 0
        if self.parent_window:
            local_build = getattr(self.parent_window, "local_version_by_game_id", {}).get(self.game_id)
            if local_build:
                self.current_build_id = local_build[0] or self.current_build_id
                self.current_build_date = local_build[1] or self.current_build_date
            latest_result = getattr(self.parent_window, "steam_check_results", {}).get(self.game_id)
            if latest_result:
                self.latest_build_id = str(latest_result[0] or "")
                self.latest_build_date = int(latest_result[1] or 0)

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


        self.setMinimumSize(640, 560)
        self.resize(660, 600)
        root_layout = self._popup_root

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
        self.polish_property_grid(sum_layout)

        # Imported-game build references and the latest public Steam match.
        sec_builds = QLabel("Build Matching")
        sec_builds.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        sec_builds.setStyleSheet("color: #F5F7FA; background: transparent; border: none; padding-bottom: 4px; margin-top: 6px;")
        body_layout.addWidget(sec_builds)

        builds_card = QFrame()
        builds_card.setStyleSheet("QFrame { background: #18181B; border: none; border-radius: 10px; padding: 8px; }")
        builds_layout = QGridLayout(builds_card)
        builds_layout.setHorizontalSpacing(12)
        builds_layout.setVerticalSpacing(6)
        build_rows = (
            ("Current Build ID:", self.current_build_id or "Not recorded"),
            ("Current Build Date:", format_timestamp(self.current_build_date, fallback="Not recorded")),
            ("Latest Build ID:", self.latest_build_id or "Not checked"),
            ("Latest Build Date:", format_timestamp(self.latest_build_date, fallback="Not checked")),
        )
        for row, (label, value) in enumerate(build_rows):
            builds_layout.addWidget(QLabel(f"<font color='#6F7682'>{label}</font>"), row, 0)
            value_label = QLabel(str(value))
            if row == 0 and has_resolved_build_reference(self.current_build_id, self.current_build_date):
                value_label.setText(
                    f"{html_escape(str(value))} <font color='#35C98A'>(found)</font>"
                )
            value_label.setStyleSheet("color: #E4E4E7; font-family: monospace; font-size: 11px;")
            builds_layout.addWidget(value_label, row, 1)
        builds_layout.setColumnStretch(1, 1)
        body_layout.addWidget(builds_card)
        self.polish_property_grid(builds_layout)

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
        gamemode_mode_row = QHBoxLayout()
        gamemode_mode_row.setContentsMargins(28, 0, 0, 0)
        gamemode_mode_row.addWidget(QLabel("GameMode method:"))
        self.combo_gamemode_mode = QComboBox()
        self.combo_gamemode_mode.addItems([
            "Feral direct injection (LD_PRELOAD)",
            "Standard wrapper (gamemoderun / Steam style)",
        ])
        self.combo_gamemode_mode.setCurrentIndex(
            1 if str(self.env_vars.get(GAMEMODE_MODE, "feral")).strip().lower() == "steam" else 0
        )
        self.combo_gamemode_mode.setEnabled(self.cb_gamemode.isChecked())
        self.combo_gamemode_mode.setStyleSheet(
            "QComboBox { background: #161A22; color: #F5F7FA; border: none; "
            "border-radius: 6px; padding: 4px 10px; min-width: 230px; }"
        )
        self.cb_gamemode.toggled.connect(self.combo_gamemode_mode.setEnabled)
        gamemode_mode_row.addWidget(self.combo_gamemode_mode)
        gamemode_mode_row.addStretch()
        tc_layout.addLayout(gamemode_mode_row)
        gamemode_available = bool(gamemode_library() or gamemode_wrapper())
        gamemode_note = QLabel(
            "GameMode support detected — the selected method will be used when this game starts."
            if gamemode_available
            else "GameMode support not detected — the game will start normally if this option is enabled."
        )
        gamemode_note.setWordWrap(True)
        gamemode_note.setStyleSheet(
            "color: #8F96A3; font-size: 11px; padding-left: 28px;"
            if gamemode_available
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
        set_accessible_status(
            self.lbl_cloud_status,
            "Cloud save status",
            "Current synchronization state for this game's local and cloud saves.",
        )
        syc_layout.addWidget(self.lbl_cloud_status)

        from core.cloud_storage import get_cloud_root
        cloud_root = get_cloud_root()
        details_toggle = QToolButton()
        details_toggle.setText("Technical details")
        details_toggle.setCheckable(True)
        details_toggle.setChecked(False)
        details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        details_toggle.setStyleSheet(
            "QToolButton { color: #A7ADB8; background: transparent; border: none; "
            "padding: 2px 0; font-size: 11px; font-weight: 600; text-align: left; }"
            "QToolButton:hover { color: #F5F7FA; }"
        )
        details_toggle.setAccessibleName("Show cloud technical details")
        syc_layout.addWidget(details_toggle)

        self.lbl_cloud_dir = QLabel(
            f"<font color='#6F7682'>Local cloud storage:</font> "
            f"<font color='#A7ADB8' face='monospace'>{html_escape(str(cloud_root))}</font>"
        )
        self.lbl_cloud_dir.setStyleSheet("font-size: 10px;")
        self.lbl_cloud_dir.setWordWrap(True)
        self.lbl_cloud_dir.setVisible(False)
        self.lbl_cloud_dir.setAccessibleName("Local cloud storage location")
        syc_layout.addWidget(self.lbl_cloud_dir)

        def _toggle_cloud_details(checked: bool) -> None:
            details_toggle.setArrowType(
                Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
            )
            self.lbl_cloud_dir.setVisible(bool(checked))

        details_toggle.toggled.connect(_toggle_cloud_details)

        sync_btn_row = QHBoxLayout()
        self.btn_sync_up = QPushButton(" Upload local save")
        self.btn_sync_up.setIcon(get_app_icon("export"))
        self.btn_sync_up.setStyleSheet("QPushButton { background: #161A22; color: #3B9FE8; border: none; border-radius: 6px; padding: 6px 12px; font-weight: 600; font-size: 11px; } QPushButton:hover { background: #202633; }")
        self.btn_sync_up.clicked.connect(self._sync_up_now)
        self.btn_sync_up.setAccessibleName("Upload local save to cloud")
        sync_btn_row.addWidget(self.btn_sync_up)

        self.btn_sync_down = QPushButton(" Restore latest cloud save")
        self.btn_sync_down.setIcon(get_app_icon("import"))
        self.btn_sync_down.setStyleSheet("QPushButton { background: #161A22; color: #35C98A; border: none; border-radius: 6px; padding: 6px 12px; font-weight: 600; font-size: 11px; } QPushButton:hover { background: #202633; }")
        self.btn_sync_down.clicked.connect(self._sync_down_now)
        self.btn_sync_down.setAccessibleName("Restore latest cloud save")
        self.btn_sync_down.hide()
        sync_btn_row.addWidget(self.btn_sync_down)

        sync_btn_row.addStretch()
        syc_layout.addLayout(sync_btn_row)

        # Retained cloud versions (active + history) with manual selection & restore
        self.ver_selector_layout = QVBoxLayout()
        self.ver_selector_layout.setSpacing(6)

        lbl_ver_title = QLabel("Cloud save versions & history")
        lbl_ver_title.setStyleSheet("font-size: 11px; font-weight: bold; color: #F5F7FA;")
        self.ver_selector_layout.addWidget(lbl_ver_title)

        timeline_row = QVBoxLayout()
        timeline_row.setSpacing(8)
        self.history_timeline = SaveHistoryTimeline()
        # Read-only compatibility alias for callers that used to locate the
        # version selector by its old combo-box name.
        self.combo_cloud_versions = self.history_timeline
        self.history_timeline.setMinimumHeight(150)
        self.history_timeline.entry_selected.connect(self._on_history_entry_selected)
        timeline_row.addWidget(self.history_timeline)

        self.btn_restore_selected = QPushButton(" Restore selected version")
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
        self.btn_restore_selected.setAccessibleName("Restore selected cloud save version")
        self.btn_restore_backup = self.btn_restore_selected  # Backwards compatibility
        timeline_row.addWidget(self.btn_restore_selected)

        self.ver_selector_layout.addLayout(timeline_row)

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
        btn_open_mgr = QPushButton(" Open save inspector & backup manager")
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
        registry = getattr(self.parent_window, "operation_registry", None)
        operation = None
        if registry is not None:
            operation = registry.start(
                name.replace("SafeLauncher-", "").replace("-", " ").strip(),
                category="Game Properties",
                game_id=self.game_id,
                game_name=self.game_name,
            )

        def _complete(result):
            if operation is not None:
                registry.finish_result(operation.operation_id, result)
            on_complete(result)

        worker = self._task_supervisor.start(name, work, _complete)
        if operation is not None:
            operation.cancel = getattr(worker, "request_cancel", worker.requestInterruption)
            operation.retry = lambda: self._start_managed_task(name, work, on_complete)
            worker.error_occurred.connect(
                lambda error, op_id=operation.operation_id: registry.fail(op_id, error)
            )
        return worker

    def closeEvent(self, event):
        for binding in tuple(self._resource_bindings.values()):
            binding.close()
        self._resource_bindings.clear()
        self._task_supervisor.cancel_all(100)
        if self._task_supervisor.has_running_tasks():
            QTimer.singleShot(100, self.close)
            event.ignore()
            return
        super().closeEvent(event)

    def _load_save_stats_async(self):
        """Load save detection and history through managed resources."""
        self._save_stats_generation += 1
        load_generation = self._save_stats_generation

        # The normal desktop path uses the shared resource and operation
        # services.  Keeping status and history as two managed resources lets
        # the request manager deduplicate each independently and prevents a
        # dialog-local worker from becoming a second cloud scheduler.
        if (
            self.request_manager is not None
            and self.cloud_status_service is not None
            and self.cloud_operation_service is not None
        ):
            target = CloudStatusTarget(
                self.game_id,
                self.game_name,
                self.game_path,
                str(self.steam_id or ""),
            )
            operation_target = CloudOperationTarget(
                self.game_id,
                self.game_name,
                self.game_path,
                str(self.steam_id or ""),
            )
            state = {
                "status": None,
                "local": None,
                "cloud": None,
                "operation_result": None,
                "resource_status": None,
                "versions": None,
                "status_done": False,
                "history_done": False,
            }

            def finish_if_ready() -> None:
                if load_generation != self._save_stats_generation:
                    return
                if not state["status_done"] or not state["history_done"]:
                    return
                # The signal intentionally carries one tuple so the
                # compatibility payload shape is identical for managed and
                # standalone dialogs. Emitting the tuple as separate Qt
                # arguments would violate the one-object signal contract.
                self._save_stats_ready.emit((
                    state["status"],
                    state["local"],
                    state["cloud"],
                    state["versions"],
                    state["operation_result"],
                    state["resource_status"],
                ))

            def bind_read(handle, callback) -> None:
                request_id = handle.request_id

                def deliver(resource):
                    if load_generation != self._save_stats_generation:
                        return
                    if resource.status in {ResourceStatus.IDLE, ResourceStatus.LOADING}:
                        return
                    binding = self._resource_bindings.pop(request_id, None)
                    if binding is not None:
                        binding.close()
                    callback(resource)
                    finish_if_ready()

                self._resource_bindings[request_id] = bind_request(
                    self.request_manager,
                    handle,
                    deliver,
                    self,
                    cancel_on_close=True,
                )

            status_handle = self.cloud_status_service.request_status(
                target,
                priority=RequestPriority.NORMAL,
                tag="game_properties_status",
            )

            def apply_status(resource) -> None:
                state["status_done"] = True
                state["resource_status"] = resource.status
                if resource.status not in {ResourceStatus.READY, ResourceStatus.STALE}:
                    state["operation_result"] = getattr(resource, "error", None)
                    return
                result = resource.value
                state["operation_result"] = getattr(result, "error", None)
                if getattr(result, "success", False):
                    state["status"] = result.status
                    state["local"] = result.local_stats
                    state["cloud"] = result.cloud_stats
                    self.cloud_status_service.record_status(
                        self.game_id,
                        result.status,
                        result.local_stats,
                        result.cloud_stats,
                        generation=self.cloud_status_service.current_context().generation,
                    )

            bind_read(status_handle, apply_status)

            if self.cloud_center_service is not None:
                history_handle = self.cloud_center_service.request_save_history(
                    self.game_id,
                    game_name=self.game_name,
                    game_path=self.game_path,
                    steam_id=str(self.steam_id or ""),
                    priority=RequestPriority.NORMAL,
                )
            else:
                history_handle = self.cloud_operation_service.request_history(
                    operation_target,
                    priority=RequestPriority.NORMAL,
                    tag="game_properties_history",
                )

            def apply_history(resource) -> None:
                state["history_done"] = True
                if resource.status not in {ResourceStatus.READY, ResourceStatus.STALE}:
                    return
                value = resource.value
                if isinstance(value, tuple) and len(value) >= 2:
                    state["versions"] = value[0]
                    if value[1] is not None and state["operation_result"] is None:
                        state["operation_result"] = value[1]
                elif isinstance(value, list):
                    state["versions"] = value

            bind_read(history_handle, apply_history)
            return

        # Standalone dialogs remain constructible for smoke tests, but cloud
        # state cannot be fetched without the application services. Do not
        # create a dialog-local coordinator or call the save engine directly.
        from core.save_models import SaveOperationResult
        self._save_stats_ready.emit((
            None,
            None,
            None,
            [],
            SaveOperationResult(
                False,
                "Cloud status",
                self.game_name,
                error="Cloud service is not available in this view.",
                category="unavailable",
                guidance="Open this workflow from the main SafeLauncher window and check Cloud Center.",
                retry_safe=False,
            ),
        ))

    def _on_save_stats_ready(self, payload):
        """GUI-thread handler to populate Save tab metadata without blocking dialog opening."""
        if len(payload) == 4:
            # Compatibility with older task payloads from an already-open dialog.
            status, local_stats, cloud_stats, versions = payload
            operation_result = None
            resource_state = None
        elif len(payload) == 5:
            status, local_stats, cloud_stats, versions, operation_result = payload
            resource_state = None
        else:
            status, local_stats, cloud_stats, versions, operation_result, resource_state = payload
        if status is None or local_stats is None:
            message = getattr(operation_result, "error", "Save status check failed.")
            guidance = getattr(operation_result, "guidance", "")
            detail = f"{message} {guidance}".strip()
            self.lbl_cloud_status.setText(
                f"<font color='#F05D6C'>{html_escape(detail)}</font>"
            )
            self.lbl_generations.hide()
            self.btn_restore_backup.hide()
            return

        from ui.dialogs.save_conflict_dialog import format_bytes
        from core.game_status import cloud_indicator

        if local_stats.exists:
            date_str = format_datetime_timestamp(local_stats.last_modified, "%H:%M:%S")
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

        meta = cloud_indicator(status)
        status_prefix = "Using cached data · " if resource_state == ResourceStatus.STALE else ""
        self.lbl_cloud_status.setText(
            f"<font color='{meta.color}'><b>{html_escape(status_prefix + meta.label)}</b></font>"
        )
        self.lbl_cloud_status.setToolTip(
            f"{meta.tooltip} Cached data will refresh when the cloud is reachable."
            if resource_state == ResourceStatus.STALE else meta.tooltip
        )

        if cloud_stats and cloud_stats.exists:
            self.btn_sync_down.show()
        else:
            self.btn_sync_down.hide()

        self._render_generations(versions, local_exists=local_stats.exists)

    def _render_generations(self, versions, local_exists: bool = True):
        """Show retained cloud versions and local safety backups in one timeline."""
        self._cloud_versions = list(versions or [])
        if not self._cloud_versions:
            self._backup_version = None
            self.history_timeline.set_message("No saved versions or local safety backups found.")
            self.ver_selector_widget.hide()
            self.btn_restore_selected.setEnabled(False)
            return

        active_ver = None
        if self.cloud_operation_service is not None:
            active_ver = self.cloud_operation_service.active_save_version(self.game_name)
        for v in self._cloud_versions:
            v_num = v.get("version")
            if local_exists and active_ver is not None and v_num == active_ver:
                v["is_active"] = True
        self.history_timeline.set_entries(self._cloud_versions)

        normalized = self.history_timeline.entries()
        if len(normalized) >= 2:
            self._backup_version = normalized[1].raw.get("version")
        else:
            self._backup_version = None

        count_str = f"{len(normalized)} cloud version(s) and local safety backup(s)."
        self.lbl_generations.setText(f"<font color='#6F7682'>History:</font> {count_str}")
        self.lbl_generations.show()
        self.btn_restore_selected.setEnabled(self.history_timeline.selected_entry() is not None)
        self.ver_selector_widget.show()

    def _on_history_entry_selected(self, entry) -> None:
        self.btn_restore_selected.setEnabled(entry is not None)

    def _restore_selected_version_now(self):
        selected = self.history_timeline.selected_entry()
        if selected is None:
            return
        entry = selected.raw
        version = entry.get("version")
        source_label = "cloud save version" if selected.source == "cloud" else "local safety backup"
        title = selected.title

        answer = confirm_restore(
            self,
            game_name=self.game_name,
            entry=selected,
            target_path=self.game_path,
            title=f"Restore {source_label}",
        )
        if not answer:
            return

        self.btn_restore_selected.setEnabled(False)

        if selected.source != "cloud":
            fork_path = str(entry.get("path") or "")
            target_dest = os.path.join(self.game_path, "prefix")
            if not os.path.isdir(target_dest):
                target_dest = self.game_path

            def _restore_fork():
                return self.backup_mgr.import_save(fork_path, target_dest, game_path=self.game_path)

            self._start_managed_task(
                f"SafeLauncher-ForkRestore-{self.game_id}",
                _restore_fork,
                lambda success: self._on_local_history_restore_done(bool(success), title),
            )
            return

        if self.cloud_center_service is not None or self.cloud_operation_service is not None:
            target = CloudOperationTarget(
                self.game_id, self.game_name, self.game_path, str(self.steam_id or "")
            )
            operation_service = self.cloud_center_service or self.cloud_operation_service
            handle = operation_service.request_restore(
                target,
                priority=RequestPriority.CRITICAL,
                tag="properties_generation_restore",
                target_version=int(version),
            )

            def _deliver(resource):
                result = resource.value if resource.status == ResourceStatus.READY else None
                error = str(resource.error or "") if result is None else ""
                if result is None:
                    from core.save_models import SaveOperationResult
                    result = SaveOperationResult(
                        False,
                        "Cloud save restore",
                        self.game_name,
                        error=error or "Cloud restore failed.",
                        category="backend_unavailable",
                        guidance="Check the cloud connection and try again.",
                    )
                self._gen_restore_done.emit(result, int(version or 0))

            self._bind_cloud_operation(handle, _deliver)
            return

        self._gen_restore_done.emit(
            self._cloud_unavailable_result("Cloud save restore"), int(version or 0)
        )

    def _on_local_history_restore_done(self, success: bool, title: str) -> None:
        self.btn_restore_selected.setEnabled(True)
        if success:
            QMessageBox.information(self, "Save History", f"'{title}' was restored successfully.")
            self._notify_parent_cloud_changed()
        else:
            QMessageBox.critical(self, "Save History", f"Could not restore '{title}'.")
        self._load_save_stats_async()

    def _restore_backup_now(self):
        """Backwards-compatible wrapper for restoring a selected version."""
        self._restore_selected_version_now()

    def _on_gen_restore_done(self, result, version: int):
        self.btn_restore_selected.setEnabled(True)
        if getattr(result, "success", bool(result)):
            QMessageBox.information(self, "Cloud Sync",
                                    f"Cloud save version {version} restored successfully.")
            self._notify_parent_cloud_changed()
        else:
            message = getattr(result, "error", "") or f"Failed to restore cloud save version {version}."
            guidance = getattr(result, "guidance", "")
            QMessageBox.critical(self, "Cloud Sync",
                                 f"{message}\n\n{guidance}".strip())
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

    def _operation_result_from_resource(self, future, operation: str):
        """Adapt a managed resource completion to the dialog result model."""
        try:
            resource = future.result() if hasattr(future, "result") else future
            if resource.status == ResourceStatus.READY:
                return resource.value
            error = str(resource.error or f"{operation} failed.")
        except Exception as exc:
            error = str(exc)
        from core.save_models import SaveOperationResult
        return SaveOperationResult(
            False,
            operation,
            self.game_name,
            error=error,
            category="backend_unavailable",
            guidance="Check the cloud connection and try again.",
        )

    def _bind_cloud_operation(self, handle, callback) -> ResourceBinding | None:
        """Deliver one cloud operation on the dialog's Qt thread."""
        if self.request_manager is None:
            return None
        request_id = handle.request_id

        def _deliver(resource):
            if resource.status in {ResourceStatus.IDLE, ResourceStatus.LOADING}:
                return
            binding = self._resource_bindings.pop(request_id, None)
            if binding is not None:
                binding.close()
            callback(resource)

        binding = bind_request(
            self.request_manager,
            handle,
            _deliver,
            self,
            cancel_on_close=True,
        )
        self._resource_bindings[request_id] = binding
        return binding

    def _sync_up_now(self):
        self.btn_sync_up.setEnabled(False)
        self.btn_sync_down.setEnabled(False)
        if hasattr(self, "btn_restore_selected"):
            self.btn_restore_selected.setEnabled(False)

        prog = cloud_progress(
            self, f"Uploading local save for '{self.game_name}'…"
        )
        self._active_manual_sync_progress = prog

        if self.cloud_center_service is not None or self.cloud_operation_service is not None:
            target = CloudOperationTarget(
                self.game_id, self.game_name, self.game_path, str(self.steam_id or "")
            )
            operation_service = self.cloud_center_service or self.cloud_operation_service
            handle = operation_service.request_upload(
                target,
                priority=RequestPriority.NORMAL,
                tag="properties_upload",
            )
            self._bind_cloud_operation(
                handle,
                lambda resource: self._manual_sync_up_done.emit(
                    self._operation_result_from_resource(resource, "Cloud upload")
                ),
            )
            return

        self._manual_sync_up_done.emit(self._cloud_unavailable_result("Cloud upload"))

    def _on_manual_sync_up_done(self, result):
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

        if getattr(result, "success", bool(result)):
            QMessageBox.information(self, "Cloud Sync", "Local save uploaded successfully.")
            self._load_save_stats_async()
            self._notify_parent_cloud_changed()
        else:
            message = getattr(result, "error", "No local save files found to upload.")
            guidance = getattr(result, "guidance", "")
            QMessageBox.warning(self, "Cloud Sync", f"{message}\n\n{guidance}".strip())

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
                "Close the game first, then restore the cloud save.")
            return

        self.btn_sync_up.setEnabled(False)
        self.btn_sync_down.setEnabled(False)
        if hasattr(self, "btn_restore_selected"):
            self.btn_restore_selected.setEnabled(False)

        prog = cloud_progress(
            self, f"Restoring latest cloud save for '{self.game_name}'…"
        )
        self._active_manual_sync_progress = prog

        if self.cloud_center_service is not None or self.cloud_operation_service is not None:
            target = CloudOperationTarget(
                self.game_id, self.game_name, self.game_path, str(self.steam_id or "")
            )
            operation_service = self.cloud_center_service or self.cloud_operation_service
            handle = operation_service.request_restore(
                target,
                priority=RequestPriority.CRITICAL,
                tag="properties_restore",
            )
            self._bind_cloud_operation(
                handle,
                lambda resource: self._manual_sync_down_done.emit(
                    self._operation_result_from_resource(resource, "Cloud restore")
                ),
            )
            return

        self._manual_sync_down_done.emit(self._cloud_unavailable_result("Cloud restore"))

    def _on_manual_sync_down_done(self, result):
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

        if getattr(result, "success", bool(result)):
            QMessageBox.information(self, "Cloud Sync", "Cloud save successfully restored to game prefix.")
            self._load_save_stats_async()
            self._notify_parent_cloud_changed()
        else:
            message = getattr(result, "error", "Failed to restore cloud save.")
            guidance = getattr(result, "guidance", "")
            QMessageBox.critical(self, "Cloud Sync", f"{message}\n\n{guidance}".strip())


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
            updated_env[GAMEMODE_MODE] = "steam" if self.combo_gamemode_mode.currentIndex() == 1 else "feral"

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
