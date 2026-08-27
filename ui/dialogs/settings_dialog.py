import os
import re
import shutil
import subprocess
from datetime import datetime
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFormLayout,
    QFileDialog, QWidget, QScrollArea, QGridLayout, QFrame, QStackedWidget,
    QProgressBar, QSizeGrip, QCheckBox, QComboBox, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QSettings
from PyQt6.QtGui import QFont, QIcon, QPixmap, QKeySequence
from PyQt6.QtWidgets import QKeySequenceEdit

from core.disk_utils import get_dir_size, get_disk_usage, format_size, store_dir_size
from core.host_process import host_process_env
from database import _APP_DATA_DIR
from core.desktop_integration import install_safelauncher_desktop_entry, is_desktop_entry_installed
from core.security_diagnostics import inspect_security_health, run_live_sandbox_verification
from core.launch_diagnostics import diagnostics_directory
from core.screenshot_capture import capture_desktop_screenshot, get_available_screens
from core.plugins.gpu_screen_recorder import (
    GpuRecorderService, GpuRecorderConfig,
    WlScreenrecService, WlScreenrecConfig,
    DEFAULT_RECORDINGS_DIR
)
from ui.icons import get_icon, get_app_icon
from typing import Optional
from ui.icons import LOGO_PATH
from ui.components.sidebar import DialogTitleBar
from ui.maintenance_dialogs import RuntimeInventoryDialog
from ui.dialogs.game_dialogs import ensure_sandbox_dir
from ui.dialogs.save_conflict_dialog import format_bytes


class UserSettingsDialog(QDialog):
    """Clean, resizable settings, plugins and security diagnostics center."""
    runtime_manager_requested = pyqtSignal()
    proton_manager_requested = pyqtSignal()
    _sandbox_size_ready = pyqtSignal(int)  # emitted from worker thread
    accountStatusReady = pyqtSignal(str)   # cloud account status from workers

    def __init__(self, user_name: str, proton_path: str = "", show_welcome_wizard: bool = False, gpu_config: Optional[GpuRecorderConfig] = None, screenshot_screen: str = "current", screenshot_hotkey: str = "F12", cloud_saves_dir: str = "", parent=None):
        super().__init__(parent)
        self.user_name = user_name
        self.proton_path = proton_path
        self.show_welcome_wizard = show_welcome_wizard
        self.gpu_config = gpu_config or GpuRecorderConfig()
        self.screenshot_screen = screenshot_screen or "current"
        self.screenshot_hotkey = screenshot_hotkey or "F12"
        from core.cloud_save_sync import CloudSaveSyncEngine
        self.cloud_saves_dir = cloud_saves_dir or CloudSaveSyncEngine.get_cloud_root()

        self.setWindowTitle("Settings")
        self.setWindowIcon(QIcon(LOGO_PATH) if os.path.exists(LOGO_PATH) else QIcon())
        self.setMinimumSize(700, 540)
        self.setSizeGripEnabled(True)

        self.setStyleSheet("""
            QDialog {
                background: #121214;
                color: #ffffff;
            }
            QLabel {
                color: #e4e4e7;
            }
            QLineEdit, QComboBox {
                background: #1c1c20;
                color: #ffffff;
                border: 1px solid #333338;
                border-radius: 4px;
                padding: 7px 10px;
                font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #52525b;
                background: #222227;
            }
            QPushButton {
                background: #27272a;
                color: #ffffff;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 7px 14px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #3f3f46;
            }
        """)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Title bar
        self.title_bar = DialogTitleBar(self, "Settings")
        root_layout.addWidget(self.title_bar)

        # Body container
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(18, 14, 18, 14)
        body_layout.setSpacing(12)

        # Navigation Bar
        nav_bar = QHBoxLayout()
        nav_bar.setSpacing(6)

        self.tab_buttons = []
        tabs = [
            ("General & Profile", 0),
            ("Container Security", 1),
            ("Storage & Logs", 2),
            ("Cloud", 3),
            ("Plugins & Addons", 4),
        ]

        for title, idx in tabs:
            btn = QPushButton(title)
            btn.setCheckable(True)
            btn.setMinimumHeight(32)
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    color: #a1a1aa;
                    border: 1px solid transparent;
                    border-bottom: 2px solid transparent;
                    border-radius: 0px;
                    padding: 6px 14px;
                    font-size: 13px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    color: #ffffff;
                }
                QPushButton:checked {
                    background: transparent;
                    color: #ffffff;
                    border-bottom: 2px solid #3b82f6;
                }
            """)
            btn.clicked.connect(lambda _, i=idx: self._switch_tab(i))
            nav_bar.addWidget(btn)
            self.tab_buttons.append(btn)

        nav_bar.addStretch()
        self.tab_buttons[0].setChecked(True)
        body_layout.addLayout(nav_bar)

        # Stacked Pages
        self.stack = QStackedWidget()
        self.page_general = self._create_general_page()
        self.page_security = self._create_security_page()
        self.page_storage = self._create_storage_page()
        self.page_cloud = self._create_cloud_page()
        self.page_plugins = self._create_plugins_page()

        self.stack.addWidget(self.page_general)
        self.stack.addWidget(self.page_security)
        self.stack.addWidget(self.page_storage)
        self.stack.addWidget(self.page_cloud)
        self.stack.addWidget(self.page_plugins)

        body_layout.addWidget(self.stack, 1)

        # Bottom Bar
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(8)
        bottom_bar.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        bottom_bar.addWidget(btn_cancel)

        btn_save = QPushButton("Save")
        btn_save.setStyleSheet("""
            QPushButton {
                background: #2563eb; color: #ffffff; border: none;
                border-radius: 4px; padding: 7px 18px; font-weight: bold;
            }
            QPushButton:hover { background: #1d4ed8; }
        """)
        btn_save.clicked.connect(self._save)
        bottom_bar.addWidget(btn_save)

        # Size grip for window resizing
        size_grip = QSizeGrip(self)
        bottom_bar.addWidget(size_grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        body_layout.addLayout(bottom_bar)
        root_layout.addWidget(body)

    def _switch_tab(self, index: int):
        for i, btn in enumerate(self.tab_buttons):
            btn.setChecked(i == index)
        self.stack.setCurrentIndex(index)

    # -------------------------------------------------------------
    # TAB 1: General & Profile
    # -------------------------------------------------------------
    def _create_general_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        sec_profile = QLabel("Profile & Paths")
        sec_profile.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_profile.setStyleSheet("color: #ffffff; border-bottom: 1px solid #27272a; padding-bottom: 4px;")
        layout.addWidget(sec_profile)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.name_input = QLineEdit(self.user_name)
        self.name_input.setPlaceholderText("Enter display name")
        form.addRow("Display Name:", self.name_input)

        proton_row = QHBoxLayout()
        self.proton_input = QLineEdit(self.proton_path)
        self.proton_input.setPlaceholderText("Leave blank for automatic detection (~/.local/share/umu/...)")
        proton_row.addWidget(self.proton_input)

        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_proton)
        proton_row.addWidget(browse_btn)

        form.addRow("Proton Path:", proton_row)
        layout.addLayout(form)

        sec_desktop = QLabel("Desktop & Menu Integration")
        sec_desktop.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_desktop.setStyleSheet("color: #ffffff; border-bottom: 1px solid #27272a; padding-bottom: 4px; margin-top: 8px;")
        layout.addWidget(sec_desktop)

        is_installed = is_desktop_entry_installed()
        btn_start_screen = QPushButton(
            "Installed in Start Screen & App Menu" if is_installed else "Add to Start Screen & App Menu"
        )
        btn_start_screen.setEnabled(not is_installed)
        if is_installed:
            btn_start_screen.setStyleSheet("""
                QPushButton {
                    background: #14532d; color: #86efac; border: 1px solid #166534;
                    border-radius: 4px; padding: 8px 12px; font-weight: bold;
                }
            """)
        else:
            btn_start_screen.setStyleSheet("""
                QPushButton {
                    background: #1e293b; color: #38bdf8; border: 1px solid #0284c7;
                    border-radius: 4px; padding: 8px 12px; font-weight: bold;
                }
                QPushButton:hover { background: #0369a1; color: #ffffff; }
            """)
        btn_start_screen.clicked.connect(lambda: self._add_to_start_screen(btn_start_screen))
        layout.addWidget(btn_start_screen)

        sec_startup = QLabel("Startup Preferences")
        sec_startup.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_startup.setStyleSheet("color: #ffffff; border-bottom: 1px solid #27272a; padding-bottom: 4px; margin-top: 8px;")
        layout.addWidget(sec_startup)

        self.chk_welcome = QCheckBox("Show introduction wizard on startup")
        self.chk_welcome.setChecked(self.show_welcome_wizard)
        layout.addWidget(self.chk_welcome)

        layout.addStretch()

        scroll.setWidget(page)
        return scroll

    # -------------------------------------------------------------
    # TAB 2: Container Security (Clean flat header & diagnostics)
    # -------------------------------------------------------------
    def _create_security_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)

        report = inspect_security_health()

        # Clean, flat status banner with white text and solid colored background (no borders/corners)
        if report.overall_status == "healthy":
            bg_color = "#166534"  # Solid dark green
            status_text = "Container Isolation Active: System and personal files are protected inside sandbox."
        elif report.overall_status == "warning":
            bg_color = "#9a3412"  # Solid dark orange
            status_text = f"Container Warning: {report.summary}"
        else:
            bg_color = "#991b1b"  # Solid dark red
            status_text = "Container Critical: Firejail sandbox is missing. Games will run unconfined."

        header_banner = QLabel(status_text)
        header_banner.setWordWrap(True)
        header_banner.setStyleSheet(f"""
            QLabel {{
                background-color: {bg_color};
                color: #ffffff;
                padding: 12px 14px;
                font-size: 12px;
                font-weight: 600;
            }}
        """)
        layout.addWidget(header_banner)

        # Status Table
        sec_subsys = QLabel("Subsystem Inspection")
        sec_subsys.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_subsys.setStyleSheet("color: #ffffff; border-bottom: 1px solid #27272a; padding-bottom: 4px; margin-top: 4px;")
        layout.addWidget(sec_subsys)

        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setContentsMargins(0, 4, 0, 4)

        fj_status = f"Active ({report.firejail_version})" if report.firejail_installed else "Missing"
        grid.addWidget(QLabel("Firejail Sandbox:"), 0, 0)
        grid.addWidget(QLabel(fj_status), 0, 1)

        grid.addWidget(QLabel("Kernel Namespaces:"), 1, 0)
        grid.addWidget(QLabel(report.userns_detail), 1, 1)

        bw_status = f"Available ({report.bwrap_version})" if report.bwrap_installed else "Missing"
        grid.addWidget(QLabel("Bubblewrap:"), 2, 0)
        grid.addWidget(QLabel(bw_status), 2, 1)

        grid.addWidget(QLabel("Prefix Isolation:"), 3, 0)
        grid.addWidget(QLabel("Active (Z: host drive removed, user folders isolated)"), 3, 1)

        layout.addLayout(grid)

        # GPU Caches
        sec_gpu = QLabel("GPU Shader Cache Whitelists")
        sec_gpu.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_gpu.setStyleSheet("color: #ffffff; border-bottom: 1px solid #27272a; padding-bottom: 4px; margin-top: 6px;")
        layout.addWidget(sec_gpu)

        gpu_grid = QGridLayout()
        gpu_grid.setSpacing(6)
        gpu_grid.setContentsMargins(0, 4, 0, 4)

        for i, cache in enumerate(report.gpu_caches):
            gpu_grid.addWidget(QLabel(f"{cache['name']} ({cache['path']}):"), i, 0)
            status_str = f"Available ({cache['size_formatted']})" if cache['exists'] else "Not created yet"
            gpu_grid.addWidget(QLabel(status_str), i, 1)

        layout.addLayout(gpu_grid)

        # Live probe test
        sec_probe = QLabel("Sandbox Verification")
        sec_probe.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_probe.setStyleSheet("color: #ffffff; border-bottom: 1px solid #27272a; padding-bottom: 4px; margin-top: 6px;")
        layout.addWidget(sec_probe)

        btn_run_test = QPushButton("Run Sandbox Isolation Test")
        btn_run_test.setFixedWidth(220)
        btn_run_test.clicked.connect(lambda: self._execute_live_probe(btn_run_test))
        layout.addWidget(btn_run_test)

        self.probe_output_lbl = QLabel("")
        self.probe_output_lbl.setWordWrap(True)
        self.probe_output_lbl.setStyleSheet("font-size: 11px; padding: 4px 0px;")
        layout.addWidget(self.probe_output_lbl)

        layout.addStretch()
        scroll.setWidget(page)
        return scroll

    def _execute_live_probe(self, button: QPushButton):
        button.setEnabled(False)
        button.setText("Testing isolation…")
        res = run_live_sandbox_verification()
        button.setEnabled(True)
        button.setText("Run Sandbox Isolation Test")

        if res["success"]:
            self.probe_output_lbl.setStyleSheet("color: #4ade80; font-weight: bold;")
            self.probe_output_lbl.setText(f"Pass: {res['message']}")
        else:
            self.probe_output_lbl.setStyleSheet("color: #f87171; font-weight: bold;")
            self.probe_output_lbl.setText(f"Fail: {res['message']}")

    # -------------------------------------------------------------
    # TAB 3: Storage & Logs
    # -------------------------------------------------------------
    def _create_storage_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        sec_disk = QLabel("Storage Usage")
        sec_disk.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_disk.setStyleSheet("color: #ffffff; border-bottom: 1px solid #27272a; padding-bottom: 4px;")
        layout.addWidget(sec_disk)

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
        import threading
        self._sandbox_size_ready.connect(self._on_sandbox_size_ready)
        def _calc_sandbox():
            try:
                self._sandbox_size_ready.emit(get_dir_size(sandbox_dir))
            except Exception:
                pass
        threading.Thread(target=_calc_sandbox, daemon=True, name="SafeLauncher-StorageCalc").start()

        self.combo_screenshot_screen = QComboBox()
        screens = get_available_screens()
        selected_ss_idx = 0
        for i, (s_val, s_lbl) in enumerate(screens):
            self.combo_screenshot_screen.addItem(s_lbl, s_val)
            if s_val == self.screenshot_screen:
                selected_ss_idx = i
        self.combo_screenshot_screen.setCurrentIndex(selected_ss_idx)
        form_disk.addRow("Screenshot Monitor / Display:", self.combo_screenshot_screen)

        # Cloud saves directory moved to the dedicated Cloud tab (index 3);
        # Storage keeps only local disk widgets.
        layout.addLayout(form_disk)

        sec_logs = QLabel("Logs & Diagnostics")
        sec_logs.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_logs.setStyleSheet("color: #ffffff; border-bottom: 1px solid #27272a; padding-bottom: 4px; margin-top: 8px;")
        layout.addWidget(sec_logs)

        diag_dir = diagnostics_directory()
        diag_count = len(os.listdir(diag_dir)) if os.path.exists(diag_dir) else 0

        self.lbl_diag_info = QLabel(f"{diag_count} saved launch reports")
        layout.addWidget(self.lbl_diag_info)

        log_btns = QHBoxLayout()
        log_btns.setSpacing(8)

        btn_open_diag = QPushButton("Open Logs Directory")
        btn_open_diag.clicked.connect(lambda: self._open_folder(diag_dir))
        log_btns.addWidget(btn_open_diag)

        btn_clear_diag = QPushButton("Clear Saved Reports")
        btn_clear_diag.clicked.connect(self._clear_diagnostics)
        log_btns.addWidget(btn_clear_diag)

        btn_open_shots = QPushButton("Open Screenshots")
        shots_dir = os.path.join(_APP_DATA_DIR, "screenshots")
        btn_open_shots.clicked.connect(lambda: self._open_folder(shots_dir))
        log_btns.addWidget(btn_open_shots)

        layout.addLayout(log_btns)
        layout.addStretch()

        scroll.setWidget(page)
        return scroll

    # -------------------------------------------------------------
    # TAB 4: Cloud (account, backend mode, endpoints)
    # -------------------------------------------------------------
    def _create_cloud_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        sec_account = QLabel("Cloud Account")
        sec_account.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_account.setStyleSheet("color: #ffffff; border-bottom: 1px solid #27272a; padding-bottom: 4px;")
        layout.addWidget(sec_account)

        from core.cloud_save_sync import cloud_mode as current_cloud_mode

        form_mode = QFormLayout()
        form_mode.setSpacing(10)

        self.combo_cloud_mode = QComboBox()
        self.combo_cloud_mode.addItem("Local folder sync", "local")
        self.combo_cloud_mode.addItem("Convex account (sign-in required)", "convex")
        self.combo_cloud_mode.setCurrentIndex(
            1 if current_cloud_mode() == "convex" else 0
        )
        self.combo_cloud_mode.currentIndexChanged.connect(self._on_cloud_mode_changed)
        form_mode.addRow("Cloud Backend:", self.combo_cloud_mode)

        # Local fallback directory used when the backend is 'local' or offline.
        cloud_row = QHBoxLayout()
        self.edit_cloud_saves_dir = QLineEdit(self.cloud_saves_dir)
        cloud_row.addWidget(self.edit_cloud_saves_dir, 1)
        btn_browse_cloud = QPushButton("Browse...")
        btn_browse_cloud.clicked.connect(self._browse_cloud_dir)
        cloud_row.addWidget(btn_browse_cloud)
        form_mode.addRow("Local Sync Folder:", cloud_row)

        self.lbl_account_status = QLabel("Not signed in.")
        self.lbl_account_status.setStyleSheet("color: #9ca3af;")
        form_mode.addRow("Status:", self.lbl_account_status)

        # Visual quota meter (server-enforced budget).
        self.bar_quota_settings = QProgressBar()
        self.bar_quota_settings.setFixedHeight(14)
        self.bar_quota_settings.setTextVisible(False)
        self.bar_quota_settings.setRange(0, 1000)
        self.bar_quota_settings.setValue(0)
        self.bar_quota_settings.setStyleSheet("""
            QProgressBar { background: #18181B; border: 1px solid #27272A; border-radius: 7px; }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #3B9FE8, stop:1 #7C5CFF);
                border-radius: 6px;
            }
        """)
        form_mode.addRow("Quota:", self.bar_quota_settings)
        layout.addLayout(form_mode)

        acct_btns = QHBoxLayout()
        acct_btns.setSpacing(8)
        self.btn_sign_in = QPushButton("Sign In…")
        self.btn_sign_in.clicked.connect(self._cloud_sign_in)
        acct_btns.addWidget(self.btn_sign_in)
        btn_account_mgr = QPushButton("Open Account Manager…")
        btn_account_mgr.clicked.connect(self._open_account_manager)
        acct_btns.addWidget(btn_account_mgr)
        self.btn_refresh_quota = QPushButton("Refresh Quota")
        self.btn_refresh_quota.clicked.connect(self._refresh_account_status)
        acct_btns.addWidget(self.btn_refresh_quota)
        self.btn_logout = QPushButton("Sign Out")
        self.btn_logout.clicked.connect(self._cloud_sign_out)
        acct_btns.addWidget(self.btn_logout)
        acct_btns.addStretch()
        layout.addLayout(acct_btns)

        sec_endpoint = QLabel("Endpoints (pre-filled defaults; edit only for another deployment)")
        sec_endpoint.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_endpoint.setStyleSheet("color: #ffffff; border-bottom: 1px solid #27272a; padding-bottom: 4px; margin-top: 8px;")
        layout.addWidget(sec_endpoint)

        endpoint_form = QFormLayout()
        endpoint_form.setSpacing(10)
        self.edit_clerk_domain = QLineEdit()
        self.edit_clerk_domain.setPlaceholderText("https://your-instance.clerk.accounts.dev")
        self.edit_clerk_domain.setText(QSettings().value("clerk_domain", "", type=str))
        self.edit_clerk_domain.editingFinished.connect(self._persist_clerk_settings)
        endpoint_form.addRow("Clerk Frontend API Domain:", self.edit_clerk_domain)
        self.edit_clerk_client_id = QLineEdit()
        self.edit_clerk_client_id.setPlaceholderText("OAuth application client id")
        self.edit_clerk_client_id.setText(QSettings().value("clerk_client_id", "", type=str))
        self.edit_clerk_client_id.editingFinished.connect(self._persist_clerk_settings)
        endpoint_form.addRow("Clerk OAuth Client ID:", self.edit_clerk_client_id)
        self.edit_convex_site_url = QLineEdit()
        self.edit_convex_site_url.setPlaceholderText("https://<deployment>.convex.site")
        self.edit_convex_site_url.setText(QSettings().value("convex_site_url", "", type=str))
        self.edit_convex_site_url.editingFinished.connect(self._persist_clerk_settings)
        endpoint_form.addRow("Convex Site URL:", self.edit_convex_site_url)
        layout.addLayout(endpoint_form)

        layout.addStretch()

        self.accountStatusReady.connect(self._apply_account_status)
        self._refresh_account_status()

        scroll.setWidget(page)
        return scroll

    def _apply_account_status(self, message: str):
        try:
            self.lbl_account_status.setText(message)
            signed_out = message.startswith(("Not signed in", "Sign-in failed"))
            self.btn_sign_in.setEnabled(True)
            self.btn_logout.setEnabled(not signed_out)
            if not signed_out and hasattr(self, "bar_quota_settings"):
                import re as _re
                match = _re.search(r"([\d.]+ [KMG]?B) / ([\d.]+ [KMG]?B)", message)
                if match:
                    def _to_bytes(text: str) -> float:
                        value, unit = text.split()
                        factor = {"B": 1, "KB": 1024, "MB": 1024**2,
                                  "GB": 1024**3}.get(unit, 1)
                        return float(value) * factor
                    used = _to_bytes(match.group(1))
                    total = _to_bytes(match.group(2)) or 1
                    pct = min(1.0, used / total)
                    bar = self.bar_quota_settings
                    color = "#3B9FE8" if pct < 0.75 else ("#EAB308" if pct < 0.92 else "#EF4444")
                    style = (
                        "QProgressBar { background: #18181B; border: 1px solid #27272A;"
                        " border-radius: 7px; }\n"
                        f"QProgressBar::chunk {{ background: {color}; border-radius: 6px; }}"
                    )
                    bar.setValue(int(pct * 1000))
                    bar.setStyleSheet(style)
        except RuntimeError:
            pass  # dialog already destroyed

    # -------------------------------------------------------------
    # TAB 5: Plugins & Addons (wl-screenrec)
    # -------------------------------------------------------------
    def _create_plugins_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        sec_title = QLabel("Hardware Accelerated Video Recording")
        sec_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_title.setStyleSheet("color: #ffffff; border-bottom: 1px solid #27272a; padding-bottom: 4px;")
        layout.addWidget(sec_title)

        desc = QLabel(
            "GPU Screen Recorder is a high-performance, shadowplay-like hardware screen recorder for Linux (NVIDIA NVENC, AMD VAAPI, Intel QuickSync) with zero gameplay FPS loss and instant replay buffer clipping."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        layout.addWidget(desc)

        # Main Plugin Toggle (Default: False)
        self.chk_plugin_enabled = QCheckBox("Enable GPU Screen Recorder Addon")
        self.chk_plugin_enabled.setChecked(self.gpu_config.enabled)
        self.chk_plugin_enabled.setStyleSheet("font-weight: bold; font-size: 13px; color: #ffffff;")
        layout.addWidget(self.chk_plugin_enabled)

        # Status Banner
        backend = WlScreenrecService.get_backend_type()
        if backend == "gpu-screen-recorder":
            bg_col = "#166534"
            status_text = f"Installed & Ready: GPU Screen Recorder ({WlScreenrecService.get_executable_path()})"
        elif backend == "wl-screenrec":
            bg_col = "#166534"
            status_text = f"Installed & Ready: wl-screenrec ({WlScreenrecService.get_executable_path()})"
        elif backend == "ffmpeg":
            bg_col = "#1e3a8a"
            status_text = f"Active: Built-in Universal ffmpeg fallback ({WlScreenrecService.get_executable_path()})"
        else:
            bg_col = "#9a3412"
            status_text = "No recorder backend found. Install gpu-screen-recorder via paru to enable hardware NVENC recording."

        banner = QLabel(status_text)
        banner.setWordWrap(True)
        banner.setStyleSheet(f"background-color: {bg_col}; color: #ffffff; padding: 10px 12px; font-weight: 600; font-size: 11px;")
        layout.addWidget(banner)

        # Installation Helper (if gpu-screen-recorder not installed)
        if backend != "gpu-screen-recorder":
            install_row = QHBoxLayout()
            self.install_cmd_box = QLineEdit("paru -S gpu-screen-recorder")
            self.install_cmd_box.setReadOnly(True)
            install_row.addWidget(self.install_cmd_box)

            btn_copy_cmd = QPushButton("Copy Command")
            btn_copy_cmd.clicked.connect(self._copy_install_command)
            install_row.addWidget(btn_copy_cmd)

            btn_install_now = QPushButton("Install via Helper")
            btn_install_now.setStyleSheet("QPushButton { background: #2563eb; color: #ffffff; border: none; } QPushButton:hover { background: #1d4ed8; }")
            btn_install_now.clicked.connect(self._open_install_notice)
            install_row.addWidget(btn_install_now)

            layout.addLayout(install_row)

        # Configuration Form
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.combo_mode = QComboBox()
        self.combo_mode.addItem("Manual (Record on Hotkey / Button)", "manual")
        self.combo_mode.addItem("Automatic (Record While Playing)", "auto_game")
        self.combo_mode.addItem("Instant Replay Buffer (Shadowplay)", "replay_buffer")
        idx_m = self.combo_mode.findData(self.gpu_config.mode)
        if idx_m >= 0:
            self.combo_mode.setCurrentIndex(idx_m)
        form.addRow("Recording Mode:", self.combo_mode)

        self.combo_recording_monitor = QComboBox()
        monitors = GpuRecorderService.get_available_monitors()
        selected_rm_idx = 0
        cur_target_screen = getattr(self.gpu_config, "target_screen", "screen") or "screen"
        for i, (m_val, m_lbl) in enumerate(monitors):
            self.combo_recording_monitor.addItem(m_lbl, m_val)
            if m_val == cur_target_screen:
                selected_rm_idx = i
        self.combo_recording_monitor.setCurrentIndex(selected_rm_idx)
        form.addRow("Recording Screen / Display:", self.combo_recording_monitor)

        self.combo_replay = QComboBox()
        self.combo_replay.addItem("30 Seconds", 30)
        self.combo_replay.addItem("60 Seconds (Default)", 60)
        self.combo_replay.addItem("120 Seconds (2 min)", 120)
        self.combo_replay.addItem("300 Seconds (5 min)", 300)
        idx_r = self.combo_replay.findData(self.gpu_config.history_seconds)
        if idx_r >= 0:
            self.combo_replay.setCurrentIndex(idx_r)
        form.addRow("Replay Buffer Size:", self.combo_replay)

        self.combo_bitrate = QComboBox()
        self.combo_bitrate.addItem("8 Mbps (Compact 1080p)", "8M")
        self.combo_bitrate.addItem("12 Mbps (Standard 1080p 60fps)", "12M")
        self.combo_bitrate.addItem("20 Mbps (High Quality 1440p)", "20M")
        self.combo_bitrate.addItem("30 Mbps (Ultra 4K)", "30M")
        idx_b = self.combo_bitrate.findData(self.gpu_config.bitrate)
        if idx_b >= 0:
            self.combo_bitrate.setCurrentIndex(idx_b)
        form.addRow("Video Bitrate:", self.combo_bitrate)

        self.combo_codec = QComboBox()
        self.combo_codec.addItem("Auto (Hardware Detect)", "auto")
        self.combo_codec.addItem("H.264 / AVC (Broadest Compatibility)", "avc")
        self.combo_codec.addItem("HEVC / H.265 (Efficient)", "hevc")
        self.combo_codec.addItem("AV1 (Next-Gen GPU)", "av1")
        idx_c = self.combo_codec.findData(self.gpu_config.codec)
        if idx_c >= 0:
            self.combo_codec.setCurrentIndex(idx_c)
        form.addRow("Video Codec:", self.combo_codec)

        # Audio Configuration & Dual Devices (Output + Input)
        self.chk_audio = QCheckBox("Record Audio")
        self.chk_audio.setChecked(self.gpu_config.audio)
        form.addRow("Audio Master:", self.chk_audio)

        self.combo_audio_output = QComboBox()
        out_devices = GpuRecorderService.get_audio_output_devices()
        selected_out_idx = 0
        current_out = getattr(self.gpu_config, "audio_device", "default") or "default"
        for i, (dev_name, dev_label) in enumerate(out_devices):
            self.combo_audio_output.addItem(dev_label, dev_name)
            if dev_name == current_out:
                selected_out_idx = i
        self.combo_audio_output.setCurrentIndex(selected_out_idx)
        form.addRow("Audio Output (Game / Desktop):", self.combo_audio_output)

        self.combo_audio_input = QComboBox()
        in_devices = GpuRecorderService.get_audio_input_devices()
        selected_in_idx = 0
        current_in = getattr(self.gpu_config, "microphone_device", "") or ""
        for i, (dev_name, dev_label) in enumerate(in_devices):
            self.combo_audio_input.addItem(dev_label, dev_name)
            if dev_name == current_in:
                selected_in_idx = i
        self.combo_audio_input.setCurrentIndex(selected_in_idx)
        form.addRow("Audio Input (Microphone):", self.combo_audio_input)

        # ── Hotkeys (custom key sequence) ──────────────────────────────────
        capture_hint = QLabel("Click a field and press the key combination you want to bind")
        capture_hint.setStyleSheet("color: #71717a; font-size: 10px; margin-bottom: 2px;")
        form.addRow("", capture_hint)

        self.edit_hotkey = QKeySequenceEdit()
        self.edit_hotkey.setKeySequence(QKeySequence(self.gpu_config.capture_hotkey or "F9"))
        self.edit_hotkey.setStyleSheet(
            "QKeySequenceEdit { background: #1c1c20; color: #ffffff; border: 1px solid #333338;"
            " border-radius: 4px; padding: 7px 10px; font-size: 12px; }"
        )
        capture_mode_hint = QLabel()
        if self.gpu_config.mode == "replay_buffer":
            capture_mode_hint.setText("Saves an instant replay clip of the last buffered minutes")
        else:
            capture_mode_hint.setText("Starts / stops manual video recording")
        capture_mode_hint.setStyleSheet("color: #71717a; font-size: 10px;")
        capture_vbox = QVBoxLayout()
        capture_vbox.setSpacing(3)
        capture_vbox.addWidget(self.edit_hotkey)
        capture_vbox.addWidget(capture_mode_hint)
        form.addRow("Record / Clip Hotkey:", capture_vbox)

        self.edit_screenshot_hotkey = QKeySequenceEdit()
        self.edit_screenshot_hotkey.setKeySequence(QKeySequence(self.screenshot_hotkey or "F12"))
        self.edit_screenshot_hotkey.setStyleSheet(
            "QKeySequenceEdit { background: #1c1c20; color: #ffffff; border: 1px solid #333338;"
            " border-radius: 4px; padding: 7px 10px; font-size: 12px; }"
        )
        form.addRow("Screenshot Hotkey:", self.edit_screenshot_hotkey)

        self.chk_overlay = QCheckBox("Show In-Game Notification Overlay (Floating HUD)")
        self.chk_overlay.setChecked(getattr(self.gpu_config, "in_game_overlay", True))
        form.addRow("In-Game Overlay:", self.chk_overlay)

        out_row = QHBoxLayout()
        self.output_dir_input = QLineEdit(self.gpu_config.output_dir or DEFAULT_RECORDINGS_DIR)
        out_row.addWidget(self.output_dir_input)
        browse_out = QPushButton("Browse")
        browse_out.clicked.connect(self._browse_recordings_dir)
        out_row.addWidget(browse_out)
        form.addRow("Recordings Folder:", out_row)

        layout.addLayout(form)
        layout.addStretch()

        scroll.setWidget(page)
        return scroll

    def _open_install_notice(self):
        dlg = PluginInstallNoticeDialog(self)
        dlg.exec()

    def _copy_install_command(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText("paru -S gpu-screen-recorder")

    def _browse_recordings_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Recordings Directory", os.path.expanduser("~/Videos"))
        if path:
            self.output_dir_input.setText(path)

    def _open_folder(self, path: str):
        if path and os.path.exists(path):
            try:
                subprocess.Popen(["xdg-open", path], env=host_process_env())
            except Exception:
                pass

    def _clear_diagnostics(self):
        diag_dir = diagnostics_directory()
        if os.path.exists(diag_dir):
            for f in os.listdir(diag_dir):
                fp = os.path.join(diag_dir, f)
                try:
                    if os.path.isfile(fp):
                        os.remove(fp)
                except Exception:
                    pass
            self.lbl_diag_info.setText("0 saved launch reports (0 B)")

    def _add_to_start_screen(self, button: QPushButton):
        success, msg = install_safelauncher_desktop_entry()
        if success:
            button.setText("Installed in Start Screen & App Menu")
            button.setEnabled(False)
            button.setStyleSheet("""
                QPushButton {
                    background: #14532d; color: #86efac; border: 1px solid #166534;
                    border-radius: 4px; padding: 8px 12px; font-weight: bold;
                }
            """)
        else:
            button.setText(f"Error: {msg}")

    def _save(self):
        if self.name_input.text().strip():
            self.accept()

    def _browse_proton(self):
        path = QFileDialog.getExistingDirectory(self, "Select Proton tool directory", os.path.expanduser("~/.local/share"))
        if path:
            self.proton_input.setText(path)

    def _browse_cloud_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Cloud Saves Root Directory", self.edit_cloud_saves_dir.text() or os.path.expanduser("~"))
        if path:
            self.edit_cloud_saves_dir.setText(path)

    # ---- Cloud account handlers --------------------------------------------

    def _on_cloud_mode_changed(self, index: int):
        from core.cloud_save_sync import set_cloud_mode
        set_cloud_mode(self.combo_cloud_mode.itemData(index) or "local")

    def _persist_clerk_settings(self):
        qs = QSettings("SafeLauncher", "SafeLauncher")
        qs.setValue("clerk_domain", self.edit_clerk_domain.text().strip())
        qs.setValue("clerk_client_id", self.edit_clerk_client_id.text().strip())
        qs.setValue("convex_site_url", self.edit_convex_site_url.text().strip())

    def _open_account_manager(self):
        """Launch the full profile/quota/version manager dialog."""
        try:
            from ui.dialogs.account_dialog import AccountDialog
            dialog = AccountDialog(self)
            dialog.exec()
            self._refresh_account_status()  # picker may have changed session/state
        except Exception as e:
            QMessageBox.warning(self, "Account Manager", f"Could not open: {e}")

    def _cloud_sign_in(self):
        """Run the browser PKCE flow on a worker thread; report via signal."""
        import threading

        def _work():
            try:
                from core import clerk_auth
                tokens = clerk_auth.login()
                email = tokens.get("email") or "signed in"
                self.accountStatusReady.emit(f"Signed in: {email}")
            except Exception as e:
                self.accountStatusReady.emit(f"Sign-in failed: {e}")

        self.btn_sign_in.setEnabled(False)
        self.lbl_account_status.setText("Waiting for browser sign-in…")
        threading.Thread(target=_work, daemon=True, name="SafeLauncher-Login").start()

    def _cloud_sign_out(self):
        from core import clerk_auth
        clerk_auth.clear_stored_session()
        self._refresh_account_status()

    def _refresh_account_status(self):
        import threading

        def _probe():
            try:
                from core import clerk_auth
                status = clerk_auth.get_status()
                if not status.get("signed_in"):
                    self.accountStatusReady.emit("Not signed in.")
                    return
                email = status.get("email") or "account"
                try:
                    from core.cloud_backend import ConvexSaveBackend
                    overview = ConvexSaveBackend().account()
                    used = overview.get("bytesUsed", 0)
                    quota = overview.get("quotaBytes", 1)
                    games = len(overview.get("games", []))
                    msg = f"{email} — {format_bytes(used)} / {format_bytes(quota)} used · {games} game(s)"
                except Exception as e:
                    msg = f"{email} (cloud unreachable: {e})"
                self.accountStatusReady.emit(msg)
            except Exception as e:
                self.accountStatusReady.emit(f"Status error: {e}")

        threading.Thread(target=_probe, daemon=True, name="SafeLauncher-AccountProbe").start()

    def get_cloud_saves_dir(self) -> str:
        return self.edit_cloud_saves_dir.text().strip()

    def _open_runtime_manager(self):
        self.runtime_manager_requested.emit()
        self.reject()

    def _open_runtime_inventory(self):
        RuntimeInventoryDialog(self).exec()

    def _open_proton_manager(self):
        self.proton_manager_requested.emit()
        self.reject()

    def get_user_name(self) -> str:
        return self.name_input.text().strip()

    def get_proton_path(self) -> str:
        return self.proton_input.text().strip()

    def get_show_welcome_wizard(self) -> bool:
        return self.chk_welcome.isChecked()

    @staticmethod
    def _normalise_hotkey(ks: QKeySequence) -> str:
        """Convert a QKeySequence to a plain string the global hotkey listener can parse.
        E.g. QKeySequence(Qt.Key.Key_F9) -> 'F9', QKeySequence('Ctrl+F9') -> 'Ctrl+F9'."""
        txt = ks.toString(QKeySequence.SequenceFormat.PortableText).strip()
        return txt if txt else "None"

    def get_screenshot_hotkey(self) -> str:
        return self._normalise_hotkey(self.edit_screenshot_hotkey.keySequence()) or "F12"

    def get_gpu_recorder_config(self) -> GpuRecorderConfig:
        cap_hk = self._normalise_hotkey(self.edit_hotkey.keySequence())
        return GpuRecorderConfig(
            enabled=self.chk_plugin_enabled.isChecked(),
            mode=self.combo_mode.currentData() or "manual",
            codec=self.combo_codec.currentData() or "auto",
            bitrate=self.combo_bitrate.currentData() or "12M",
            target_screen=self.combo_recording_monitor.currentData() or "screen",
            audio=self.chk_audio.isChecked(),
            audio_device=self.combo_audio_output.currentData() or "default_output",
            microphone_device=self.combo_audio_input.currentData() or "",
            history_seconds=int(self.combo_replay.currentData() or 60),
            output_dir=self.output_dir_input.text().strip() or DEFAULT_RECORDINGS_DIR,
            capture_hotkey=cap_hk or "F9",
            replay_hotkey=cap_hk or "F9",
            in_game_overlay=self.chk_overlay.isChecked(),
        )

    get_wl_screenrec_config = get_gpu_recorder_config

    def _on_sandbox_size_ready(self, size_bytes: int):
        """Receive sandbox size computed on the worker thread (GUI-thread slot)."""
        try:
            if hasattr(self, "lbl_sandbox_size"):
                self.lbl_sandbox_size.setText(f"{format_size(size_bytes)} ({ensure_sandbox_dir()})")
        except RuntimeError:
            pass  # dialog already destroyed

    def get_screenshot_target_screen(self) -> str:
        return self.combo_screenshot_screen.currentData() or "current"


class PluginInstallNoticeDialog(QDialog):
    """Notice dialog explaining why root/sudo privileges are required for AUR package installation."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Install GPU Screen Recorder")
        self.setMinimumSize(540, 320)
        self.setSizeGripEnabled(True)
        self.setStyleSheet("""
            QDialog { background: #121214; color: #ffffff; }
            QLabel { color: #d4d4d8; font-size: 12px; }
            QLineEdit { background: #1c1c20; color: #38bdf8; border: 1px solid #333338; border-radius: 4px; padding: 6px; }
            QPushButton {
                background: #27272a; color: #ffffff; border: 1px solid #3f3f46;
                border-radius: 4px; padding: 8px 16px; font-weight: bold; font-size: 12px;
            }
            QPushButton:hover { background: #3f3f46; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("Install Hardware Recorder (gpu-screen-recorder)")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        title.setStyleSheet("color: #ffffff;")
        layout.addWidget(title)

        notice_text = (
            "To enable zero-overhead hardware NVENC / VAAPI recording and shadowplay instant replay "
            "on KDE Plasma Wayland, SafeLauncher uses the open-source 'gpu-screen-recorder' package.\n\n"
            "Why privileges are required:\n"
            "Building and installing places the compiled binary and capture capabilities into /usr/bin/gpu-screen-recorder, "
            "which requires administrator (sudo) privileges on your system.\n\n"
            "You can choose to install automatically via your terminal AUR helper (paru), "
            "or copy the command and install it manually."
        )
        msg = QLabel(notice_text)
        msg.setWordWrap(True)
        msg.setStyleSheet("color: #a1a1aa;")
        layout.addWidget(msg)

        cmd_box = QLineEdit("paru -S gpu-screen-recorder")
        cmd_box.setReadOnly(True)
        layout.addWidget(cmd_box)

        layout.addStretch()

        btn_box = QHBoxLayout()
        btn_box.setSpacing(8)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_copy = QPushButton("Copy Command & Close")
        btn_copy.clicked.connect(self._copy_and_close)
        btn_box.addWidget(btn_copy)

        btn_install = QPushButton("Install via Terminal (paru)")
        btn_install.setStyleSheet("QPushButton { background: #2563eb; color: #ffffff; border: none; } QPushButton:hover { background: #1d4ed8; }")
        btn_install.clicked.connect(self._launch_install)
        btn_box.addWidget(btn_install)

        layout.addLayout(btn_box)

    def _copy_and_close(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText("paru -S gpu-screen-recorder")
        self.accept()

    def _launch_install(self):
        WlScreenrecService.launch_terminal_installer(self)
        self.accept()


class ScreenshotLightboxDialog(QDialog):
    """Clean, high-resolution 16:9 lightbox modal with navigation and folder opening."""

    def __init__(self, filepaths: list, current_index: int = 0, parent=None):
        super().__init__(parent)
        self.filepaths = [f for f in filepaths if os.path.exists(f)]
        self.current_index = max(0, min(current_index, len(self.filepaths) - 1)) if self.filepaths else 0
        self.gallery_parent = parent

        self.setWindowTitle("Screenshot Preview")
        self.setMinimumSize(850, 580)
        self.resize(1000, 680)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header bar
        header = QWidget()
        header.setStyleSheet("background: #18181b; border-bottom: 1px solid #27272a;")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 10, 16, 10)
        h_layout.setSpacing(12)

        self.lbl_info = QLabel("")
        self.lbl_info.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.lbl_info.setStyleSheet("color: #ffffff;")
        h_layout.addWidget(self.lbl_info)
        h_layout.addStretch()

        btn_open_folder = QPushButton(" Open Folder")
        btn_open_folder.setIcon(get_icon("ph.folder-open-bold"))
        btn_open_folder.setStyleSheet("QPushButton { background: #27272a; color: #ffffff; border: 1px solid #3f3f46; border-radius: 4px; padding: 6px 12px; font-weight: 600; } QPushButton:hover { background: #3f3f46; }")
        btn_open_folder.clicked.connect(self._open_current_folder)
        h_layout.addWidget(btn_open_folder)

        btn_delete = QPushButton(" Delete")
        btn_delete.setIcon(get_icon("ph.trash-bold", color="#ef4444"))
        btn_delete.setStyleSheet("QPushButton { background: #2a1212; color: #ef4444; border: 1px solid #7f1d1d; border-radius: 4px; padding: 6px 12px; font-weight: 600; } QPushButton:hover { background: #7f1d1d; color: #ffffff; }")
        btn_delete.clicked.connect(self._delete_current)
        h_layout.addWidget(btn_delete)

        btn_close = QPushButton("✕ Close")
        btn_close.setStyleSheet("QPushButton { background: #27272a; color: #ffffff; border: 1px solid #3f3f46; border-radius: 4px; padding: 6px 12px; font-weight: 600; } QPushButton:hover { background: #3f3f46; }")
        btn_close.clicked.connect(self.accept)
        h_layout.addWidget(btn_close)

        root.addWidget(header)

        # Center Preview Area with 16:9 canvas
        body = QWidget()
        body.setStyleSheet("background: #0d0d0f;")
        b_layout = QHBoxLayout(body)
        b_layout.setContentsMargins(14, 14, 14, 14)
        b_layout.setSpacing(10)

        self.btn_prev = QPushButton("◀")
        self.btn_prev.setFixedSize(44, 80)
        self.btn_prev.setStyleSheet("QPushButton { background: rgba(39, 39, 42, 0.6); color: #ffffff; border: 1px solid #3f3f46; border-radius: 6px; font-size: 16px; font-weight: bold; } QPushButton:hover { background: rgba(63, 63, 70, 0.9); }")
        self.btn_prev.clicked.connect(self._prev_image)
        b_layout.addWidget(self.btn_prev)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet("background: transparent;")
        b_layout.addWidget(self.image_label, 1)

        self.btn_next = QPushButton("▶")
        self.btn_next.setFixedSize(44, 80)
        self.btn_next.setStyleSheet("QPushButton { background: rgba(39, 39, 42, 0.6); color: #ffffff; border: 1px solid #3f3f46; border-radius: 6px; font-size: 16px; font-weight: bold; } QPushButton:hover { background: rgba(63, 63, 70, 0.9); }")
        self.btn_next.clicked.connect(self._next_image)
        b_layout.addWidget(self.btn_next)

        root.addWidget(body, 1)
        self.setStyleSheet("QDialog { background: #0d0d0f; color: #ffffff; }")
        self._update_display()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_display()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Left:
            self._prev_image()
        elif event.key() == Qt.Key.Key_Right:
            self._next_image()
        elif event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return):
            self.accept()
        else:
            super().keyPressEvent(event)

    def _prev_image(self):
        if self.filepaths and self.current_index > 0:
            self.current_index -= 1
            self._update_display()

    def _next_image(self):
        if self.filepaths and self.current_index < len(self.filepaths) - 1:
            self.current_index += 1
            self._update_display()

    def _update_display(self):
        if not self.filepaths:
            self.lbl_info.setText("No screenshots available")
            self.image_label.setText("No screenshot selected")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            return

        cur_path = self.filepaths[self.current_index]
        filename = os.path.basename(cur_path)
        total = len(self.filepaths)
        self.lbl_info.setText(f"{filename}  ·  ({self.current_index + 1} of {total})")

        pix = QPixmap(cur_path)
        if not pix.isNull():
            lbl_w = max(200, self.image_label.width() - 10)
            lbl_h = max(150, self.image_label.height() - 10)
            scaled = pix.scaled(lbl_w, lbl_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.image_label.setPixmap(scaled)
        else:
            self.image_label.setText("Failed to load image")

        self.btn_prev.setEnabled(self.current_index > 0)
        self.btn_next.setEnabled(self.current_index < len(self.filepaths) - 1)

    def _open_current_folder(self):
        if self.filepaths:
            cur_path = self.filepaths[self.current_index]
            folder = os.path.dirname(cur_path)
            try:
                subprocess.Popen(["xdg-open", folder], env=host_process_env())
            except Exception:
                pass

    def _delete_current(self):
        if not self.filepaths:
            return
        cur_path = self.filepaths[self.current_index]
        try:
            if os.path.exists(cur_path):
                os.remove(cur_path)
            self.filepaths.pop(self.current_index)
            if self.current_index >= len(self.filepaths) and self.filepaths:
                self.current_index = len(self.filepaths) - 1
            self._update_display()
            if self.gallery_parent and hasattr(self.gallery_parent, "load_screenshots"):
                self.gallery_parent.load_screenshots()
            if not self.filepaths:
                self.accept()
        except Exception as e:
            from core.logger import get_logger
            get_logger("Settings").warning(f"Failed to delete screenshot: {e}")


class ScreenshotGalleryDialog(QDialog):
    """Custom dark modal dialog for browsing in-game screenshots with 16:9 ratio and Lightbox."""
    def __init__(self, game_id: int, game_name: str, parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.game_name = game_name

        self.setWindowTitle(f"Screenshots - {game_name}")
        self.setMinimumSize(780, 520)
        self.resize(840, 560)
        self.setSizeGripEnabled(True)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Title bar
        self.title_bar = DialogTitleBar(self, f"Screenshots - {game_name}")
        root_layout.addWidget(self.title_bar)

        # Body container
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(18, 14, 18, 14)
        body_layout.setSpacing(12)

        # Grid scroll area for screenshots
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { background: #121214; border: 1px solid #27272a; }")

        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setContentsMargins(14, 14, 14, 14)
        self.grid_layout.setSpacing(14)

        scroll_area.setWidget(self.grid_widget)
        body_layout.addWidget(scroll_area)

        # Bottom Action Bar
        action_layout = QHBoxLayout()
        
        btn_capture = QPushButton("Capture Screen")
        btn_capture.setIcon(get_icon("ph.camera-bold"))
        btn_capture.setStyleSheet("QPushButton { background: #27272a; color: #ffffff; border: 1px solid #3f3f46; border-radius: 4px; padding: 7px 14px; font-weight: 600; } QPushButton:hover { background: #3f3f46; }")
        btn_capture.clicked.connect(self._capture_screen)
        action_layout.addWidget(btn_capture)

        btn_open_folder = QPushButton("Open Directory")
        btn_open_folder.setIcon(get_icon("ph.folder-open-bold"))
        btn_open_folder.setStyleSheet("QPushButton { background: #27272a; color: #ffffff; border: 1px solid #3f3f46; border-radius: 4px; padding: 7px 14px; font-weight: 600; } QPushButton:hover { background: #3f3f46; }")
        btn_open_folder.clicked.connect(self._open_folder)
        action_layout.addWidget(btn_open_folder)

        action_layout.addStretch()

        btn_close = QPushButton("Close")
        btn_close.setMinimumWidth(80)
        btn_close.setStyleSheet("QPushButton { background: #27272a; color: #ffffff; border: 1px solid #3f3f46; border-radius: 4px; padding: 7px 16px; font-weight: 600; } QPushButton:hover { background: #3f3f46; }")
        btn_close.clicked.connect(self.accept)
        action_layout.addWidget(btn_close)

        size_grip = QSizeGrip(self)
        action_layout.addWidget(size_grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        body_layout.addLayout(action_layout)
        root_layout.addWidget(body)

        self.setStyleSheet("QDialog { background-color: #121214; color: #ffffff; }")

        self.screenshots_dir = os.path.join(_APP_DATA_DIR, "screenshots", str(game_id))
        os.makedirs(self.screenshots_dir, exist_ok=True)
        self.files = []
        self.load_screenshots()

    def load_screenshots(self):
        while self.grid_layout.count() > 0:
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.files = sorted(
            [os.path.join(self.screenshots_dir, f) for f in os.listdir(self.screenshots_dir) if f.lower().endswith((".png", ".jpg", ".jpeg"))],
            reverse=True
        )

        if not self.files:
            empty_label = QLabel("No screenshots captured yet.\nPress your screenshot hotkey in-game to capture.")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_label.setStyleSheet("color: #71717a; font-size: 13px; padding: 40px;")
            self.grid_layout.addWidget(empty_label, 0, 0)
            return

        cols = 3
        card_w, card_h = 224, 126  # Exact 16:9 aspect ratio

        for idx, filepath in enumerate(self.files):
            card = QFrame()
            card.setStyleSheet("""
                QFrame {
                    background: #18181b;
                    border: 1px solid #27272a;
                    border-radius: 6px;
                }
                QFrame:hover {
                    border: 1px solid #52525b;
                }
            """)
            c_layout = QVBoxLayout(card)
            c_layout.setContentsMargins(6, 6, 6, 6)
            c_layout.setSpacing(6)

            thumb_label = QLabel()
            thumb_label.setFixedSize(card_w, card_h)
            thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb_label.setCursor(Qt.CursorShape.PointingHandCursor)
            thumb_label.setToolTip("Click to view in full Lightbox")

            pixmap = QPixmap(filepath)
            if not pixmap.isNull():
                thumb_label.setPixmap(pixmap.scaled(card_w, card_h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))

            # Make thumbnail clickable to open Lightbox
            thumb_label.mousePressEvent = lambda event, i=idx: self._open_lightbox(i)
            c_layout.addWidget(thumb_label)

            # Card info row
            fn_label = QLabel(os.path.basename(filepath))
            fn_label.setStyleSheet("color: #a1a1aa; font-size: 10px; background: transparent;")
            fn_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            c_layout.addWidget(fn_label)

            btn_del = QPushButton("Delete")
            btn_del.setStyleSheet("QPushButton { background: #2a1212; color: #ef4444; border: 1px solid #7f1d1d; border-radius: 4px; font-size: 11px; padding: 4px; } QPushButton:hover { background: #7f1d1d; color: white; }")
            btn_del.clicked.connect(lambda _, p=filepath: self._delete_screenshot(p))
            c_layout.addWidget(btn_del)

            row, col = divmod(idx, cols)
            self.grid_layout.addWidget(card, row, col)

    def _open_lightbox(self, index: int):
        dlg = ScreenshotLightboxDialog(self.files, index, parent=self)
        dlg.exec()

    def _delete_screenshot(self, filepath: str):
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                self.load_screenshots()
        except Exception:
            pass

    def _capture_screen(self):
        try:
            capture_desktop_screenshot(self.game_id)
            self.load_screenshots()
        except Exception:
            pass

    def _open_folder(self):
        try:
            subprocess.Popen(["xdg-open", self.screenshots_dir], env=host_process_env())
        except Exception:
            pass


class VideoGalleryDialog(QDialog):
    """Browse recordings and replay clips belonging to one game."""

    VIDEO_EXTENSIONS = (".mp4", ".mkv", ".webm", ".mov", ".avi")

    def __init__(self, game_id: int, game_name: str, output_dir: str = DEFAULT_RECORDINGS_DIR, parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.game_name = game_name
        self.video_dir = os.path.abspath(os.path.expanduser(output_dir))
        self.game_prefix = re.sub(r"[^a-z0-9]+", "_", game_name.strip().lower()).strip("_") or "gameplay"

        self.setWindowTitle(f"Videos - {game_name}")
        self.setMinimumSize(700, 480)
        self.resize(820, 560)
        self.setSizeGripEnabled(True)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(DialogTitleBar(self, f"Videos - {game_name}"))

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(18, 14, 18, 14)
        body_layout.setSpacing(12)

        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(10, 10, 10, 10)
        self.list_layout.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: #121214; border: 1px solid #27272a; }")
        scroll.setWidget(self.list_widget)
        body_layout.addWidget(scroll)

        actions = QHBoxLayout()
        open_folder = QPushButton("Open Directory")
        open_folder.setIcon(get_icon("ph.folder-open-bold"))
        open_folder.clicked.connect(self._open_folder)
        actions.addWidget(open_folder)
        actions.addStretch()
        close = QPushButton("Close")
        close.setMinimumWidth(80)
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        actions.addWidget(QSizeGrip(self), 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        body_layout.addLayout(actions)
        root_layout.addWidget(body)
        self.setStyleSheet("QDialog { background-color: #121214; color: #ffffff; }")
        self.load_videos()

    def _files(self):
        if not os.path.isdir(self.video_dir):
            return []
        return sorted(
            [os.path.join(self.video_dir, name) for name in os.listdir(self.video_dir)
             if name.lower().endswith(self.VIDEO_EXTENSIONS)
             and name.lower().startswith(self.game_prefix + "_")
             and os.path.isfile(os.path.join(self.video_dir, name))],
            key=os.path.getmtime,
            reverse=True,
        )

    def load_videos(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        files = self._files()
        if not files:
            empty = QLabel("No recordings or replay clips found for this game.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #71717a; font-size: 13px; padding: 40px;")
            self.list_layout.addWidget(empty)
            return

        for filepath in files:
            row = QFrame()
            row.setStyleSheet("QFrame { background: #18181b; border: 1px solid #27272a; border-radius: 6px; }")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(12, 10, 12, 10)
            icon = QLabel()
            icon.setPixmap(get_icon("ph.video-camera-bold").pixmap(28, 28))
            layout.addWidget(icon)
            info = QVBoxLayout()
            name = QLabel(os.path.basename(filepath))
            name.setStyleSheet("color: #f4f4f5; font-weight: 600; background: transparent;")
            info.addWidget(name)
            size_mb = os.path.getsize(filepath) / (1024 * 1024)
            modified = os.path.getmtime(filepath)
            detail = QLabel(f"{size_mb:.1f} MB  •  {datetime.fromtimestamp(modified):%Y-%m-%d %H:%M}")
            detail.setStyleSheet("color: #a1a1aa; font-size: 11px; background: transparent;")
            info.addWidget(detail)
            layout.addLayout(info, 1)
            play = QPushButton("Play")
            play.clicked.connect(lambda _, p=filepath: self._play(p))
            layout.addWidget(play)
            reveal = QPushButton("Show")
            reveal.clicked.connect(lambda _, p=filepath: self._show_file(p))
            layout.addWidget(reveal)
            delete = QPushButton("Delete")
            delete.clicked.connect(lambda _, p=filepath: self._delete(p))
            layout.addWidget(delete)
            self.list_layout.addWidget(row)
        self.list_layout.addStretch()

    def _play(self, filepath):
        subprocess.Popen(["xdg-open", filepath], env=host_process_env())

    def _show_file(self, filepath):
        subprocess.Popen(["xdg-open", os.path.dirname(filepath)], env=host_process_env())

    def _delete(self, filepath):
        answer = QMessageBox.question(self, "Delete video", f"Delete {os.path.basename(filepath)}?")
        if answer == QMessageBox.StandardButton.Yes:
            try:
                os.remove(filepath)
                self.load_videos()
            except OSError as error:
                QMessageBox.warning(self, "Delete failed", str(error))

    def _open_folder(self):
        os.makedirs(self.video_dir, exist_ok=True)
        subprocess.Popen(["xdg-open", self.video_dir], env=host_process_env())


class DiskManagerDialog(QDialog):
    """Clean dark dialog for analyzing sandbox disk space consumption.

    All directory walks happen on a worker thread; results arrive via the
    queued _sizes_ready signal so opening the dialog never blocks the GUI.
    """
    _sizes_ready = pyqtSignal(list)  # [(name, path, bytes)] sorted desc

    def __init__(self, games: list, parent=None):
        super().__init__(parent)
        self.games = games

        self.setWindowTitle("Disk Space Manager")
        self.setMinimumSize(660, 480)
        self.setSizeGripEnabled(True)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Title bar
        self.title_bar = DialogTitleBar(self, "Sandbox Disk Space Manager")
        root_layout.addWidget(self.title_bar)

        # Body container
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(18, 14, 18, 14)
        body_layout.setSpacing(12)

        sandbox_dir = ensure_sandbox_dir()
        total_drive, used_drive, free_drive = get_disk_usage(sandbox_dir)

        form_top = QFormLayout()
        self.lbl_total_sandbox = QLabel("Calculating…")
        form_top.addRow("Total Sandbox Storage:", self.lbl_total_sandbox)
        form_top.addRow("Drive Available Space:", QLabel(f"{format_size(free_drive)} free out of {format_size(total_drive)}"))
        body_layout.addLayout(form_top)

        lbl_rank = QLabel("Installed Games by Size:")
        lbl_rank.setStyleSheet("color: #ffffff; font-weight: bold; border-bottom: 1px solid #27272a; padding-bottom: 4px; margin-top: 4px;")
        body_layout.addWidget(lbl_rank)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: #121214; border: 1px solid #27272a; }")

        list_widget = QWidget()
        self._game_rows_layout = QVBoxLayout(list_widget)
        self._game_rows_layout.setContentsMargins(8, 8, 8, 8)
        self._game_rows_layout.setSpacing(6)

        lbl_wait = QLabel("Calculating game sizes…")
        lbl_wait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_wait.setStyleSheet("color: #777; font-size: 12px; padding: 16px;")
        self._game_rows_layout.addWidget(lbl_wait)
        self._placeholder_row = lbl_wait

        # One worker computes the sandbox total and every game size, then hands
        # the ranked results back through a queued signal (thread-safe emit).
        self._sizes_ready.connect(self._on_sizes_ready)
        import threading
        def _compute_sizes():
            results = []
            for g in games:
                if hasattr(g, 'id'):
                    game_id, name, path = g.id, g.name, g.path
                else:
                    game_id, name, path = g[0], g[1], g[2]
                try:
                    sz = get_dir_size(path) if path and os.path.exists(path) else 0
                except Exception:
                    sz = 0
                store_dir_size(path, sz)  # feed shared cache for list view/sorting
                results.append((name, path, sz))
            results.sort(key=lambda x: x[2], reverse=True)
            self._sizes_ready.emit(results)
        threading.Thread(target=_compute_sizes, daemon=True, name="SafeLauncher-DiskSizes").start()

        scroll.setWidget(list_widget)
        body_layout.addWidget(scroll)

        # Close button
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        size_grip = QSizeGrip(self)
        btn_row.addWidget(size_grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        body_layout.addLayout(btn_row)

        root_layout.addWidget(body)
        self.setStyleSheet("QDialog { background-color: #121214; color: #ffffff; }")

    def _on_sizes_ready(self, results: list):
        """Populate the ranked size list once the worker finishes (GUI-thread slot)."""
        try:
            if hasattr(self, "lbl_total_sandbox"):
                self.lbl_total_sandbox.setText(format_size(sum(sz for _, _, sz in results)))
            if not hasattr(self, "_game_rows_layout"):
                return
            if self._placeholder_row is not None:
                self._placeholder_row.setParent(None)
                self._placeholder_row.deleteLater()
                self._placeholder_row = None
            for name, path, sz in results:
                self._game_rows_layout.addWidget(self._build_size_row(name, path, sz))
        except RuntimeError:
            pass  # dialog already destroyed

    def _build_size_row(self, name: str, path: str, sz: int) -> QFrame:
        row_frame = QFrame()
        row_frame.setStyleSheet("QFrame { background: #18181b; border: 1px solid #27272a; }")
        r_layout = QHBoxLayout(row_frame)
        r_layout.setContentsMargins(10, 8, 10, 8)

        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("color: #ffffff; font-weight: bold; font-size: 12px;")
        r_layout.addWidget(name_lbl)

        r_layout.addStretch()

        size_badge = QLabel(format_size(sz))
        size_badge.setStyleSheet("color: #a1a1aa; font-size: 11px; padding: 2px 6px;")
        r_layout.addWidget(size_badge)

        btn_folder = QPushButton("Open Directory")
        btn_folder.clicked.connect(lambda _, p=path: self._open_path(p))
        r_layout.addWidget(btn_folder)
        return row_frame

    def _open_path(self, path: str):
        try:
            if path and os.path.exists(path):
                subprocess.Popen(["xdg-open", path], env=host_process_env())
        except Exception:
            pass
