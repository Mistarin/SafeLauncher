import os
from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFormLayout, QWidget, QScrollArea
from PyQt6.QtGui import QFont
from core.disk_utils import get_dir_size, get_disk_usage, format_size
from database import _APP_DATA_DIR
from core.launch_diagnostics import diagnostics_directory
from core.screenshot_capture import get_available_screens
from ui.components.sort_combo import SortComboBox
from ui.dialogs.game_dialogs import ensure_sandbox_dir
from core.logger import get_logger
from dataclasses import dataclass
from typing import Callable
logger = get_logger("SettingsPage")


@dataclass(frozen=True)
class StoragePageInputs:
    add_section_divider: Callable
    clear_diagnostics: Callable
    is_closing: Callable
    export_runtime_diagnostics: Callable
    on_sandbox_size_ready: Callable
    on_sandbox_size_error: Callable
    open_folder: Callable
    polish_settings_form: Callable
    sandbox_size_ready: Callable
    start_managed_task: Callable
    wrap_settings_sections: Callable
    screenshot_screen: str


class StorageSettingsPage(QScrollArea):
    """Build this form only; workflow callbacks are explicitly supplied."""
    def __init__(self, inputs: StoragePageInputs, parent=None):
        super().__init__(parent)
        scroll = self
        scroll.setWidgetResizable(True)
        scroll.setObjectName("settingsScroll")

        page = QWidget()
        page.setObjectName("settingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        sec_disk = QLabel("Storage Usage")
        sec_disk.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_disk.setStyleSheet("color: #ffffff; padding-bottom: 2px;")
        layout.addWidget(sec_disk)
        inputs.add_section_divider(layout)

        sandbox_dir = ensure_sandbox_dir()
        total_drive, used_drive, free_drive = get_disk_usage(sandbox_dir)

        form_disk = QFormLayout()
        form_disk.setSpacing(8)
        self.lbl_sandbox_size = QLabel(f"Calculating... ({sandbox_dir})")
        form_disk.addRow("Sandbox Games Directory:", self.lbl_sandbox_size)
        form_disk.addRow("Drive Available Space:", QLabel(f"{format_size(free_drive)} free out of {format_size(total_drive)}"))

        # Asynchronously calculate sandbox directory size without blocking dialog opening.
        # A queued signal marshals the result onto the GUI thread — QTimer must never
        # be started from a foreign thread.
        inputs.sandbox_size_ready.connect(inputs.on_sandbox_size_ready)
        size_worker = [None]
        def _calc_sandbox():
            try:
                return get_dir_size(
                    sandbox_dir,
                    cancel_callback=(
                        lambda: inputs.is_closing() or bool(
                            size_worker[0] is not None
                            and getattr(size_worker[0], "isInterruptionRequested", lambda: False)())
                    ),
                )
            except Exception:
                return 0
        size_worker[0] = inputs.start_managed_task(
            "SafeLauncher-StorageCalc", _calc_sandbox, inputs.on_sandbox_size_ready,
            allow_offline=True,
            on_error=inputs.on_sandbox_size_error,
        )

        self.combo_screenshot_screen = SortComboBox()
        screens = get_available_screens()
        selected_ss_idx = 0
        for i, (s_val, s_lbl) in enumerate(screens):
            self.combo_screenshot_screen.addItem(s_lbl, s_val)
            if s_val == inputs.screenshot_screen:
                selected_ss_idx = i
        self.combo_screenshot_screen.setCurrentIndex(selected_ss_idx)
        form_disk.addRow("Screenshot Monitor / Display:", self.combo_screenshot_screen)
        inputs.polish_settings_form(form_disk)

        # Cloud saves directory moved to the dedicated Cloud tab (index 3);
        # Storage keeps only local disk widgets.
        layout.addLayout(form_disk)

        sec_logs = QLabel("Logs & Diagnostics")
        sec_logs.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_logs.setStyleSheet("color: #ffffff; padding-bottom: 2px; margin-top: 8px;")
        layout.addWidget(sec_logs)
        inputs.add_section_divider(layout)

        diag_dir = diagnostics_directory()
        diag_count = len(os.listdir(diag_dir)) if os.path.exists(diag_dir) else 0

        self.lbl_diag_info = QLabel(f"{diag_count} saved launch reports")
        layout.addWidget(self.lbl_diag_info)

        log_btns = QHBoxLayout()
        log_btns.setSpacing(8)

        btn_open_diag = QPushButton("Open Logs Directory")
        btn_open_diag.clicked.connect(lambda: inputs.open_folder(diag_dir))
        log_btns.addWidget(btn_open_diag)

        btn_export_runtime = QPushButton("Export Runtime Diagnostics")
        btn_export_runtime.setToolTip(
            "Export version, platform, and request performance counters without secrets or game paths"
        )
        btn_export_runtime.clicked.connect(inputs.export_runtime_diagnostics)
        log_btns.addWidget(btn_export_runtime)

        btn_clear_diag = QPushButton("Clear Saved Reports")
        btn_clear_diag.setProperty("settingsButtonRole", "destructive")
        btn_clear_diag.clicked.connect(inputs.clear_diagnostics)
        log_btns.addWidget(btn_clear_diag)

        btn_open_shots = QPushButton("Open Screenshots")
        shots_dir = os.path.join(_APP_DATA_DIR, "screenshots")
        btn_open_shots.clicked.connect(lambda: inputs.open_folder(shots_dir))
        log_btns.addWidget(btn_open_shots)

        layout.addLayout(log_btns)
        layout.addStretch()
        inputs.wrap_settings_sections(layout)

        scroll.setWidget(page)
