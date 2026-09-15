import os
import re
import shutil
import subprocess
import html
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFormLayout,
    QFileDialog, QWidget, QScrollArea, QGridLayout, QFrame, QStackedWidget,
    QProgressBar, QSizeGrip, QCheckBox, QComboBox, QMessageBox, QSpinBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QSettings, QSize, QTimer
from PyQt6.QtGui import QFont, QIcon, QPixmap, QKeySequence
from PyQt6.QtWidgets import QKeySequenceEdit

from core.disk_utils import get_dir_size, get_disk_usage, format_size
from core.host_process import host_process_env
from database import _APP_DATA_DIR
from core.desktop_integration import install_safelauncher_desktop_entry, is_desktop_entry_installed
from core.security_diagnostics import inspect_security_health, run_live_sandbox_verification
from core.launch_diagnostics import diagnostics_directory
from core.runtime_diagnostics import build_runtime_diagnostics, export_runtime_diagnostics
from core.date_formatting import date_format_choices, format_datetime_timestamp, get_date_format_key
from core.screenshot_capture import capture_desktop_screenshot, get_available_screens
from core.plugins.gpu_screen_recorder import (
    GpuRecorderService, GpuRecorderConfig,
    WlScreenrecService, WlScreenrecConfig,
    DEFAULT_RECORDINGS_DIR
)
from ui.icons import get_icon, get_app_icon
from typing import Any, Optional
from ui.icons import LOGO_PATH
from ui.components.sidebar import DialogTitleBar
from ui.components.popup_shell import PopupDialog
from ui.components.check_field import CheckField as QCheckBox
from ui.maintenance_dialogs import RuntimeInventoryDialog
from ui.dialogs.game_dialogs import ensure_sandbox_dir
from ui.dialogs.save_conflict_dialog import format_bytes


from core.version import APP_VERSION, MIN_CONVEX_BACKEND_VERSION
from core.updater import check_for_updates, download_and_apply_appimage_update, restart_application, is_appimage
from core.cloud_account_service import CloudAccountService
from core.cloud_center_service import CloudOverview
from core.cloud_backend import normalize_site_url
from core.cloud_detector import detect_local_cloud_installation
from core.safe_thread import TaskSupervisor
from core.request_contracts import RequestKey, RequestPriority, ResourceStatus
from core.secret_store import get_secret, set_secret, delete_secret
from core.network_policy import (
    automatic_network_allowed,
    is_offline_mode,
    offline_status_text,
    set_offline_mode,
)
from ui.resource_binding import ResourceBinding, bind_request
from core.logger import get_logger
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl

logger = get_logger("SettingsDialog")


class UserSettingsDialog(PopupDialog):
    """Clean, resizable settings, plugins and security diagnostics center."""
    runtime_manager_requested = pyqtSignal()
    proton_manager_requested = pyqtSignal()
    _sandbox_size_ready = pyqtSignal(int)      # emitted from worker thread
    accountStatusReady = pyqtSignal(str)       # cloud account status from workers
    backendHealthReady = pyqtSignal(dict)      # live backend health probe result
    backendDeployReady = pyqtSignal(bool, str) # redeploy result from worker
    appUpdateReady = pyqtSignal(dict)          # manual app update check result
    appDownloadProgress = pyqtSignal(int, int) # (downloaded, total)
    appDownloadFinished = pyqtSignal(str)      # target path
    appDownloadFailed = pyqtSignal(str)        # error message
    profile_auth_requested = pyqtSignal()
    profile_publish_requested = pyqtSignal()
    profile_resync_requested = pyqtSignal()
    conflicts_requested = pyqtSignal()

    def __init__(self, user_name: str, proton_path: str = "", show_welcome_wizard: bool = False, gpu_config: Optional[GpuRecorderConfig] = None, screenshot_screen: str = "current", screenshot_hotkey: str = "F12", cloud_saves_dir: str = "", parent=None, date_format: str = "", request_manager=None, initial_tab: int | None = None):
        super().__init__("Settings", parent)
        self.user_name = user_name
        self.proton_path = proton_path
        self.show_welcome_wizard = show_welcome_wizard
        self.gpu_config = gpu_config or GpuRecorderConfig()
        self.screenshot_screen = screenshot_screen or "current"
        self.screenshot_hotkey = screenshot_hotkey or "F12"
        from core.cloud_storage import get_cloud_root
        self.cloud_saves_dir = cloud_saves_dir or get_cloud_root()
        self.date_format = date_format or get_date_format_key()
        self._task_supervisor = TaskSupervisor(self, logger)
        self.request_manager = request_manager
        self.cloud_account_service = getattr(parent, "cloud_account_service", None) or CloudAccountService(
            request_manager=request_manager,
        )
        self.cloud_center_service = getattr(parent, "cloud_center_service", None)
        self._resource_bindings: dict[str, ResourceBinding] = {}
        self._cloud_overview_binding: ResourceBinding | None = None
        self._account_probe_generation = 0
        self._health_probe_generation = 0
        self._profile_action_status_custom = False

        self.setWindowIcon(QIcon(LOGO_PATH) if os.path.exists(LOGO_PATH) else QIcon())
        self.setMinimumSize(820, 600)
        self.resize(1040, 720)
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMaximized)
        # PopupDialog is frameless and already has a controlled custom footer
        # grip. The native QDialog size grip conflicts with that surface on
        # some Wayland/X11 compositors and can crash while the popup is shown.

        self.setStyleSheet("""
            QDialog {
                background: #111113;
                color: #FFFFFF;
                border: 1px solid #2A2A2E;
                border-radius: 12px;
            }
            QWidget#settingsPage {
                background: #15171C;
            }
            QFrame#settingsSection {
                background: #1B1E24;
                border: 1px solid #292E37;
                border-top-color: #3A404B;
                border-bottom-color: #242931;
                border-radius: 10px;
            }
            QLabel#settingsSectionTitle {
                background: transparent;
                border: none;
                color: #F4F4F5;
                padding: 0;
                margin: 0;
            }
            QLabel {
                color: #E4E4E7;
            }
            QLabel#propertyLabel, QLabel#propertyValue {
                background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #232730, stop: 0.90 #20242C, stop: 1 rgba(32, 36, 44, 0));
                color: #D4D4D8;
                border: 1px solid #2A2E36;
                border-top-color: #3A3F49;
                border-bottom-color: #242830;
                border-radius: 7px;
                padding: 7px 10px;
                min-height: 18px;
            }
            QLabel#propertyValue {
                background: #20242C;
            }
            QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QKeySequenceEdit {
                background: #20242C;
                color: #FFFFFF;
                border: 1px solid #30353F;
                border-top-color: #414752;
                border-bottom-color: #252A32;
                border-radius: 7px;
                padding: 8px 10px;
                font-size: 12px;
            }
            QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
            QComboBox:focus, QSpinBox:focus, QKeySequenceEdit:focus {
                border-color: #4B9FFF;
                background: #20242C;
            }
            QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
            QComboBox:disabled, QSpinBox:disabled, QKeySequenceEdit:disabled {
                background: #15171B;
                color: #777C86;
                border-color: #252830;
            }
            QComboBox QAbstractItemView {
                background: #1B1B1F;
                color: #FFFFFF;
                border: 1px solid #303037;
                border-radius: 6px;
                padding: 4px;
            }
            QComboBox::drop-down {
                width: 26px;
                border: none;
                background: transparent;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0;
                height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #A1A1AA;
            }
            QComboBox::down-arrow:hover {
                border-top-color: #FFFFFF;
            }
            QPushButton {
                background: #222228;
                color: #FFFFFF;
                border: 1px solid #34343C;
                border-radius: 7px;
                padding: 8px 14px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: #2B2B32;
                border-color: #4B4B56;
            }
            QPushButton:pressed {
                background: #17171A;
            }
            QCheckBox {
                spacing: 9px;
                color: #D4D4D8;
                padding: 5px 0;
            }
            QCheckBox::indicator {
                width: 17px; height: 17px;
                border-radius: 5px;
                border: 1px solid #4A4A54;
                background: #1B1B1F;
            }
            QCheckBox::indicator:checked {
                background: #3B9FE8;
                border-color: #3B9FE8;
            }
            QFrame#settingsDivider {
                background: #2B2F38;
                border: none;
                min-height: 1px;
                max-height: 1px;
            }
            QWidget#propertyHint {
                background: transparent;
            }
            QLabel#propertyHintText {
                color: #858A95;
                background: transparent;
                font-size: 11px;
            }
            QToolButton#propertyInfo {
                background: transparent;
                color: #777C86;
                border: none;
                padding: 0;
                margin: 0;
                min-width: 18px;
                min-height: 18px;
                font-size: 13px;
                font-weight: 700;
            }
            QToolButton#propertyInfo:hover, QToolButton#propertyInfo:focus {
                color: #A1A1AA;
            }
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical { width: 7px; background: transparent; margin: 2px 0; }
            QScrollBar::handle:vertical { background: #3A3A43; border-radius: 3px; min-height: 32px; }
            QScrollBar::handle:vertical:hover { background: #5A5A66; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        body_layout = self.popup_layout(margins=(18, 14, 18, 14), spacing=12)

        # Navigation Bar
        nav_frame = QFrame()
        nav_frame.setObjectName("settingsNav")
        nav_frame.setStyleSheet("QFrame#settingsNav { background: #18181B; border: 1px solid #2A2A2E; border-radius: 9px; }")
        nav_bar = QHBoxLayout(nav_frame)
        nav_bar.setContentsMargins(5, 5, 5, 5)
        nav_bar.setSpacing(4)

        self.tab_buttons = []
        tabs = [
            ("Preferences", 0),
            ("Container Security", 1),
            ("Storage & Logs", 2),
            ("Cloud", 3),
            ("Plugins & Addons", 4),
        ]

        tab_icons = ["ph.sliders-horizontal-bold", "ph.shield-check-bold", "ph.hard-drives-bold", "ph.cloud-bold", "ph.puzzle-piece-bold"]
        for (title, idx), icon_name in zip(tabs, tab_icons):
            btn = QPushButton(title)
            btn.setIcon(get_icon(icon_name, color="#A1A1AA"))
            btn.setIconSize(QSize(15, 15))
            btn.setObjectName("settingsTab")
            btn.setCheckable(True)
            btn.setMinimumHeight(32)
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    color: #A1A1AA;
                    border: none;
                    border: 1px solid transparent;
                    border-radius: 7px;
                    padding: 8px 12px;
                    font-size: 12px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    color: #FFFFFF;
                }
                QPushButton:checked {
                    background: #2A2A31;
                    color: #FFFFFF;
                    border: 1px solid #42424D;
                }
            """)
            btn.clicked.connect(lambda _, i=idx: self._switch_tab(i))
            nav_bar.addWidget(btn)
            self.tab_buttons.append(btn)

        nav_bar.addStretch()
        self.tab_buttons[0].setChecked(True)
        body_layout.addWidget(nav_frame)

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

        if initial_tab is not None:
            self._switch_tab(max(0, min(int(initial_tab), len(self.tab_buttons) - 1)))

        body_layout.addWidget(self.stack, 1)

        # Bottom Bar
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(8)
        bottom_bar.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setObjectName("settingsActionButton")
        btn_cancel.clicked.connect(self.reject)
        bottom_bar.addWidget(btn_cancel)

        btn_save = QPushButton("Save")
        btn_save.setObjectName("settingsPrimaryButton")
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

    def _apply_settings_surfaces(self) -> None:
        """Re-apply Settings surfaces after PopupDialog normalization.

        Settings is composed from several scroll-area pages.  Qt treats a
        stylesheet installed on a scroll-area/viewport as a local styling
        boundary, which can prevent the dialog-level selectors from reaching
        the page contents.  Applying the small set of Settings tokens directly
        after the shared popup normalization keeps every page visually
        identical on all supported Qt styles.
        """
        page_style = "QWidget#settingsPage { background: #15171C; }"
        section_style = (
            "QFrame#settingsSection { background: #1B1E24; "
            "border: 1px solid #292E37; border-top-color: #3A404B; "
            "border-bottom-color: #242931; border-radius: 10px; }"
        )
        field_style = (
            "QLineEdit, QComboBox, QSpinBox, QKeySequenceEdit { "
            "background: #20242C; color: #FFFFFF; border: 1px solid #30353F; "
            "border-top-color: #414752; border-bottom-color: #252A32; "
            "border-radius: 7px; padding: 8px 10px; }"
        )
        property_style = (
            "QLabel#propertyLabel, QLabel#propertyValue { "
            "background: #20242C; color: #D4D4D8; border: 1px solid #2A2E36; "
            "border-top-color: #3A3F49; border-bottom-color: #242830; "
            "border-radius: 7px; padding: 7px 10px; }"
        )
        button_style = (
            "QPushButton { background: #20242C; color: #F4F4F5; "
            "border: 1px solid #30353F; border-top-color: #414752; "
            "border-bottom-color: #252A32; border-radius: 7px; "
            "padding: 8px 14px; font-size: 12px; font-weight: 600; } "
            "QPushButton:hover { background: #2A303A; border-color: #4B5563; } "
            "QPushButton:pressed { background: #171A20; }"
        )
        primary_button_style = (
            "QPushButton { background: #3B9FE8; color: #FFFFFF; border: none; "
            "border-radius: 7px; padding: 8px 18px; font-weight: 700; } "
            "QPushButton:hover { background: #55ACED; } "
            "QPushButton:pressed { background: #2789D0; }"
        )
        divider_style = (
            "QFrame#settingsDivider { background: #3A404B; border: none; "
            "min-height: 1px; max-height: 1px; }"
        )

        for page_scroll in (
            self.page_general, self.page_security, self.page_storage,
            self.page_cloud, self.page_plugins,
        ):
            page_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
            page = page_scroll.widget()
            if page is None:
                continue
            page.setStyleSheet(page_style)
            for section in page.findChildren(QFrame, "settingsSection"):
                section.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                section.setStyleSheet(section_style)
            for divider in page.findChildren(QFrame, "settingsDivider"):
                divider.setStyleSheet(divider_style)
            for title in page.findChildren(QLabel, "settingsSectionTitle"):
                title.setStyleSheet(
                    "QLabel#settingsSectionTitle { background: transparent; "
                    "color: #F4F4F5; padding: 0; margin: 0; "
                    "font-weight: 700; }"
                )
            for hint in page.findChildren(QWidget, "propertyHint"):
                hint.setStyleSheet("QWidget#propertyHint { background: transparent; }")
            for hint_text in page.findChildren(QLabel, "propertyHintText"):
                hint_text.setStyleSheet(
                    "QLabel#propertyHintText { background: transparent; "
                    "color: #858A95; font-size: 11px; }"
                )
            for widget_type in (QLineEdit, QComboBox, QSpinBox, QKeySequenceEdit):
                for field in page.findChildren(widget_type):
                    field.setStyleSheet(field_style)
            for label in page.findChildren(QLabel):
                if label.objectName() in {"propertyLabel", "propertyValue"}:
                    label.setStyleSheet(property_style)
                elif label.objectName() == "profileActionStatus":
                    label.setStyleSheet(
                        "QLabel#profileActionStatus { background: transparent; "
                        "color: #858A95; font-size: 12px; }"
                    )
            for button in page.findChildren(QPushButton):
                if button.objectName() != "settingsTab":
                    button.setStyleSheet(button_style)

        for button in self.findChildren(QPushButton):
            if button.objectName() in {"settingsActionButton", "settingsPrimaryButton"}:
                button.setStyleSheet(button_style)
            if button.objectName() == "settingsPrimaryButton":
                button.setStyleSheet(primary_button_style)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_settings_surfaces()

    def _switch_tab(self, index: int):
        for i, btn in enumerate(self.tab_buttons):
            btn.setChecked(i == index)
        self.stack.setCurrentIndex(index)

    def _polish_settings_form(self, form: QFormLayout) -> None:
        """Apply the shared popup property treatment to a Settings form."""
        self.polish_property_form(form)

    def _polish_settings_grid(self, grid: QGridLayout) -> None:
        """Align a Settings diagnostic grid with the shared property system."""
        self.polish_property_grid(grid)

    @staticmethod
    def _settings_divider() -> QFrame:
        """Return the explicit divider used between Settings sections."""
        divider = QFrame()
        divider.setObjectName("settingsDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Plain)
        divider.setFixedHeight(1)
        return divider

    def _add_section_divider(self, layout: QVBoxLayout) -> None:
        title_item = layout.itemAt(layout.count() - 1)
        title = title_item.widget() if title_item is not None else None
        if isinstance(title, QLabel):
            title.setObjectName("settingsSectionTitle")
        layout.addWidget(self._settings_divider())

    def _wrap_settings_sections(self, layout: QVBoxLayout) -> None:
        """Put every titled Settings group on the same raised surface.

        Settings pages historically consisted of a heading followed by loose
        widgets on the page background. That made the visual hierarchy vary
        by tab and made the property treatment look like it had no effect.
        Grouping the already-built layout here keeps each page's behavior
        unchanged while giving every section one consistent surface.
        """
        items = []
        while layout.count():
            items.append(layout.takeAt(0))

        rebuilt = []
        section_frame = None
        section_layout = None

        def finish_section() -> None:
            nonlocal section_frame, section_layout
            if section_frame is not None:
                rebuilt.append(section_frame)
            section_frame = None
            section_layout = None

        def append_item(target: QVBoxLayout, item) -> None:
            """Move a taken item by reusing its widget/layout safely.

            Keeping the original QWidgetItem alive while changing its parent
            layout can trigger a double-destruction in Qt during popup
            teardown. Rebuild the item around its actual widget or child
            layout instead.
            """
            widget = item.widget()
            if widget is not None:
                target.addWidget(widget)
                return
            child_layout = item.layout()
            if child_layout is not None:
                target.addLayout(child_layout)
                return
            target.addItem(item)

        for item in items:
            widget = item.widget()
            if isinstance(widget, QLabel) and widget.objectName() == "settingsSectionTitle":
                finish_section()
                section_frame = QFrame()
                section_frame.setObjectName("settingsSection")
                section_layout = QVBoxLayout(section_frame)
                section_layout.setContentsMargins(12, 11, 12, 12)
                section_layout.setSpacing(10)
                append_item(section_layout, item)
                continue

            # The stretch at the end of a page belongs outside the final card
            # so the cards retain their natural height.
            if section_layout is not None and item.spacerItem() is not None:
                finish_section()
                rebuilt.append(item)
            elif section_layout is not None:
                append_item(section_layout, item)
            else:
                rebuilt.append(item)

        finish_section()
        for item in rebuilt:
            if isinstance(item, QWidget):
                layout.addWidget(item)
            else:
                append_item(layout, item)

    def set_profile_action_state(
        self,
        available: bool,
        signed_in: bool,
        published: bool,
        busy: bool,
    ) -> None:
        """Update the Settings account controls from the profile owner."""
        available = bool(available)
        signed_in = bool(signed_in)
        published = bool(published)
        busy = bool(busy)

        self.btn_profile_auth.setEnabled(available and not busy)
        self.btn_profile_auth.setText("Sign out" if signed_in else "Sign in")
        self.btn_profile_auth.setIcon(get_icon(
            "ph.sign-out-bold" if signed_in else "ph.sign-in-bold",
            color="#FFFFFF",
        ))
        self.btn_profile_auth.setToolTip(
            "Sign out of the central profile service."
            if signed_in else "Sign in to manage and publish your public profile."
        )

        self.btn_profile_publish.setEnabled(available and signed_in and not busy)
        self.btn_profile_publish.setText("Unpublish profile" if published else "Publish profile")
        self.btn_profile_publish.setIcon(get_icon(
            "ph.eye-slash-bold" if published else "ph.upload-simple-bold",
            color="#FFFFFF",
        ))
        self.btn_profile_publish.setToolTip(
            "Remove this profile from the public service."
            if published else "Publish this profile to the central service."
        )

        self.btn_profile_resync.setEnabled(available and not busy)
        if busy:
            self._profile_action_status_custom = False
            self.lbl_profile_action_status.setStyleSheet("color: #A1A1AA; font-size: 12px;")
            self.lbl_profile_action_status.setText("Working with the central profile service…")
        elif not available:
            self._profile_action_status_custom = False
            self.lbl_profile_action_status.setStyleSheet("color: #A1A1AA; font-size: 12px;")
            self.lbl_profile_action_status.setText("Profile account controls are unavailable in this context.")
        elif not self._profile_action_status_custom:
            self.lbl_profile_action_status.setStyleSheet("color: #A1A1AA; font-size: 12px;")
            self.lbl_profile_action_status.setText(
                "Connected · public profile is published."
                if signed_in and published else
                "Connected · profile is private until you publish it."
                if signed_in else
                "Not signed in · publishing and resync are unavailable."
            )

    def set_profile_action_status(self, message: str, error: bool = False) -> None:
        """Show the latest account operation result while Settings is open."""
        self._profile_action_status_custom = True
        self.lbl_profile_action_status.setStyleSheet(
            f"color: {'#F87171' if error else '#A1A1AA'}; font-size: 12px;"
        )
        self.lbl_profile_action_status.setText(str(message or ""))

    # -------------------------------------------------------------
    # TAB 1: General & Profile
    # -------------------------------------------------------------
    def _create_general_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("settingsScroll")

        page = QWidget()
        page.setObjectName("settingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        sec_profile_account = QLabel("Public Profile Account")
        sec_profile_account.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_profile_account.setStyleSheet("color: #FFFFFF; padding-bottom: 2px; margin-top: 6px;")
        layout.addWidget(sec_profile_account)
        self._add_section_divider(layout)

        profile_account_hint = self.info_hint(
            "Manage central sign-in, public visibility, and private profile metadata resync here. "
            "These actions affect the profile other people can view; they are separate from game-save cloud sync.",
            tooltip="Public profile settings are separate from private cloud-save synchronization.",
        )
        layout.addWidget(profile_account_hint)

        profile_actions = QHBoxLayout()
        profile_actions.setSpacing(8)
        self.btn_profile_auth = QPushButton("Sign in")
        self.btn_profile_auth.setObjectName("settingsProfileAction")
        self.btn_profile_auth.setAccessibleName("Profile sign in or sign out")
        self.btn_profile_auth.setIcon(get_icon("ph.sign-in-bold", color="#FFFFFF"))
        self.btn_profile_auth.setIconSize(QSize(16, 16))
        self.btn_profile_auth.clicked.connect(self.profile_auth_requested.emit)
        profile_actions.addWidget(self.btn_profile_auth)

        self.btn_profile_publish = QPushButton("Publish profile")
        self.btn_profile_publish.setObjectName("settingsProfileAction")
        self.btn_profile_publish.setIcon(get_icon("ph.upload-simple-bold", color="#FFFFFF"))
        self.btn_profile_publish.setIconSize(QSize(16, 16))
        self.btn_profile_publish.clicked.connect(self.profile_publish_requested.emit)
        profile_actions.addWidget(self.btn_profile_publish)

        self.btn_profile_resync = QPushButton("Resync")
        self.btn_profile_resync.setObjectName("settingsProfileAction")
        self.btn_profile_resync.setAccessibleName("Resync private profile data")
        self.btn_profile_resync.setToolTip("Resync private profile data")
        self.btn_profile_resync.setIcon(get_icon("ph.arrows-clockwise-bold", color="#A1A1AA"))
        self.btn_profile_resync.setIconSize(QSize(16, 16))
        self.btn_profile_resync.clicked.connect(self.profile_resync_requested.emit)
        profile_actions.addWidget(self.btn_profile_resync)
        profile_actions.addStretch()
        layout.addLayout(profile_actions)

        self.lbl_profile_action_status = QLabel("Profile account controls are loading…")
        self.lbl_profile_action_status.setObjectName("profileActionStatus")
        self.lbl_profile_action_status.setWordWrap(True)
        self.lbl_profile_action_status.setStyleSheet("color: #A1A1AA; font-size: 12px;")
        layout.addWidget(self.lbl_profile_action_status)
        self.set_profile_action_state(False, False, False, False)

        date_form = QFormLayout()
        date_form.setSpacing(10)
        date_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.combo_date_format = QComboBox()
        for key, _python_format, _qt_format in date_format_choices():
            self.combo_date_format.addItem(key, key)
        date_index = self.combo_date_format.findData(self.date_format)
        self.combo_date_format.setCurrentIndex(max(0, date_index))
        date_form.addRow("Date Format:", self.combo_date_format)
        self._polish_settings_form(date_form)
        layout.addLayout(date_form)

        sec_profile = QLabel("Profile & Paths")
        sec_profile.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_profile.setStyleSheet("color: #FFFFFF; padding-bottom: 2px; margin-top: 6px;")
        layout.addWidget(sec_profile)
        self._add_section_divider(layout)

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
        self._polish_settings_form(form)
        layout.addLayout(form)

        sec_desktop = QLabel("Desktop & Menu Integration")
        sec_desktop.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_desktop.setStyleSheet("color: #FFFFFF; padding-bottom: 2px; margin-top: 6px;")
        layout.addWidget(sec_desktop)
        self._add_section_divider(layout)

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
        sec_startup.setStyleSheet("color: #FFFFFF; padding-bottom: 2px; margin-top: 6px;")
        layout.addWidget(sec_startup)
        self._add_section_divider(layout)

        self.chk_welcome = QCheckBox("Show introduction wizard on startup")
        self.chk_welcome.setChecked(self.show_welcome_wizard)
        layout.addWidget(self.chk_welcome)

        sec_achievements = QLabel("Achievement Tracking & Notifications")
        sec_achievements.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_achievements.setStyleSheet("color: #FFFFFF; padding-bottom: 2px; margin-top: 6px;")
        layout.addWidget(sec_achievements)
        self._add_section_divider(layout)

        settings = QSettings("SafeLauncher", "SafeLauncher")
        toasts_on = settings.value("achievement_notifications_enabled", True, type=bool)
        desktop_on = settings.value("achievement_desktop_notifications", True, type=bool)

        self.chk_achievement_notifications = QCheckBox("Show in-app floating achievement notification banners")
        self.chk_achievement_notifications.setChecked(toasts_on)
        layout.addWidget(self.chk_achievement_notifications)

        self.chk_achievement_desktop = QCheckBox("Send native desktop notifications (notify-send)")
        self.chk_achievement_desktop.setChecked(desktop_on)
        layout.addWidget(self.chk_achievement_desktop)

        sec_updates = QLabel("Application Updates")
        sec_updates.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_updates.setStyleSheet("color: #FFFFFF; padding-bottom: 2px; margin-top: 6px;")
        layout.addWidget(sec_updates)
        self._add_section_divider(layout)

        update_row = QHBoxLayout()
        lbl_version_info = QLabel(f"Current SafeLauncher Version: <b>v{APP_VERSION}</b>")
        lbl_version_info.setStyleSheet("color: #A1A1AA; font-size: 13px;")
        update_row.addWidget(lbl_version_info)
        update_row.addStretch()

        self.btn_check_app_updates = QPushButton("Check for SafeLauncher Updates")
        self.btn_check_app_updates.clicked.connect(self._manual_check_updates)
        update_row.addWidget(self.btn_check_app_updates)
        layout.addLayout(update_row)

        self.lbl_update_status = QLabel("")
        self.lbl_update_status.setWordWrap(True)
        self.lbl_update_status.setStyleSheet("color: #9CA3AF; font-size: 12px;")
        layout.addWidget(self.lbl_update_status)

        layout.addStretch()
        self._wrap_settings_sections(layout)

        scroll.setWidget(page)
        return scroll

    # -------------------------------------------------------------
    # TAB 2: Container Security (Clean flat header & diagnostics)
    # -------------------------------------------------------------
    def _create_security_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("settingsScroll")

        page = QWidget()
        page.setObjectName("settingsPage")
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
        sec_subsys.setStyleSheet("color: #ffffff; padding-bottom: 2px; margin-top: 4px;")
        layout.addWidget(sec_subsys)
        self._add_section_divider(layout)

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
        self._polish_settings_grid(grid)

        # GPU Caches
        sec_gpu = QLabel("GPU Shader Cache Whitelists")
        sec_gpu.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_gpu.setStyleSheet("color: #ffffff; padding-bottom: 2px; margin-top: 6px;")
        layout.addWidget(sec_gpu)
        self._add_section_divider(layout)

        gpu_grid = QGridLayout()
        gpu_grid.setSpacing(6)
        gpu_grid.setContentsMargins(0, 4, 0, 4)

        for i, cache in enumerate(report.gpu_caches):
            gpu_grid.addWidget(QLabel(f"{cache['name']} ({cache['path']}):"), i, 0)
            status_str = f"Available ({cache['size_formatted']})" if cache['exists'] else "Not created yet"
            gpu_grid.addWidget(QLabel(status_str), i, 1)

        layout.addLayout(gpu_grid)
        self._polish_settings_grid(gpu_grid)

        # Live probe test
        sec_probe = QLabel("Sandbox Verification")
        sec_probe.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_probe.setStyleSheet("color: #ffffff; padding-bottom: 2px; margin-top: 6px;")
        layout.addWidget(sec_probe)
        self._add_section_divider(layout)

        btn_run_test = QPushButton("Run Sandbox Isolation Test")
        btn_run_test.setFixedWidth(220)
        btn_run_test.clicked.connect(lambda: self._execute_live_probe(btn_run_test))
        layout.addWidget(btn_run_test)

        self.probe_output_lbl = QLabel("")
        self.probe_output_lbl.setWordWrap(True)
        self.probe_output_lbl.setStyleSheet("font-size: 11px; padding: 4px 0px;")
        layout.addWidget(self.probe_output_lbl)

        layout.addStretch()
        self._wrap_settings_sections(layout)
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
        self._add_section_divider(layout)

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
        self._sandbox_size_ready.connect(self._on_sandbox_size_ready)
        size_worker = [None]
        def _calc_sandbox():
            try:
                return get_dir_size(
                    sandbox_dir,
                    cancel_callback=(
                        size_worker[0].isInterruptionRequested
                        if size_worker[0] is not None
                        else None
                    ),
                )
            except Exception:
                return 0
        size_worker[0] = self._task_supervisor.start(
            "SafeLauncher-StorageCalc", _calc_sandbox, self._on_sandbox_size_ready
        )

        self.combo_screenshot_screen = QComboBox()
        screens = get_available_screens()
        selected_ss_idx = 0
        for i, (s_val, s_lbl) in enumerate(screens):
            self.combo_screenshot_screen.addItem(s_lbl, s_val)
            if s_val == self.screenshot_screen:
                selected_ss_idx = i
        self.combo_screenshot_screen.setCurrentIndex(selected_ss_idx)
        form_disk.addRow("Screenshot Monitor / Display:", self.combo_screenshot_screen)
        self._polish_settings_form(form_disk)

        # Cloud saves directory moved to the dedicated Cloud tab (index 3);
        # Storage keeps only local disk widgets.
        layout.addLayout(form_disk)

        sec_logs = QLabel("Logs & Diagnostics")
        sec_logs.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_logs.setStyleSheet("color: #ffffff; padding-bottom: 2px; margin-top: 8px;")
        layout.addWidget(sec_logs)
        self._add_section_divider(layout)

        diag_dir = diagnostics_directory()
        diag_count = len(os.listdir(diag_dir)) if os.path.exists(diag_dir) else 0

        self.lbl_diag_info = QLabel(f"{diag_count} saved launch reports")
        layout.addWidget(self.lbl_diag_info)

        log_btns = QHBoxLayout()
        log_btns.setSpacing(8)

        btn_open_diag = QPushButton("Open Logs Directory")
        btn_open_diag.clicked.connect(lambda: self._open_folder(diag_dir))
        log_btns.addWidget(btn_open_diag)

        btn_export_runtime = QPushButton("Export Runtime Diagnostics")
        btn_export_runtime.setToolTip(
            "Export version, platform, and request performance counters without secrets or game paths"
        )
        btn_export_runtime.clicked.connect(self._export_runtime_diagnostics)
        log_btns.addWidget(btn_export_runtime)

        btn_clear_diag = QPushButton("Clear Saved Reports")
        btn_clear_diag.clicked.connect(self._clear_diagnostics)
        log_btns.addWidget(btn_clear_diag)

        btn_open_shots = QPushButton("Open Screenshots")
        shots_dir = os.path.join(_APP_DATA_DIR, "screenshots")
        btn_open_shots.clicked.connect(lambda: self._open_folder(shots_dir))
        log_btns.addWidget(btn_open_shots)

        layout.addLayout(log_btns)
        layout.addStretch()
        self._wrap_settings_sections(layout)

        scroll.setWidget(page)
        return scroll

    # -------------------------------------------------------------
    # TAB 4: Cloud (account, backend mode, endpoints)
    # -------------------------------------------------------------
    def _create_cloud_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("settingsScroll")

        page = QWidget()
        page.setObjectName("settingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        sec_account = QLabel("Cloud Center · Connection")
        sec_account.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_account.setStyleSheet("color: #ffffff; padding-bottom: 2px;")
        layout.addWidget(sec_account)
        self._add_section_divider(layout)

        from core.cloud_backend import get_site_url, normalize_site_url
        settings = QSettings("SafeLauncher", "SafeLauncher")

        form_mode = QFormLayout()
        form_mode.setSpacing(10)

        self.combo_cloud_mode = QComboBox()
        self.combo_cloud_mode.addItem("Local folder sync", "local")
        self.combo_cloud_mode.addItem("Private Convex Cloud (SafeLauncherCloud)", "convex")
        self.combo_cloud_mode.setCurrentIndex(
            1 if self.cloud_account_service.mode() == "convex" else 0
        )
        self.combo_cloud_mode.currentIndexChanged.connect(self._on_cloud_mode_changed)
        form_mode.addRow("Cloud Backend:", self.combo_cloud_mode)

        # This is deliberately separate from the cloud backend selector:
        # offline mode also suppresses Steam, artwork, profile, telemetry,
        # update, and other optional background requests.
        self.chk_offline_mode = QCheckBox("Offline mode (disable automatic internet access)")
        self.chk_offline_mode.setChecked(is_offline_mode(settings))
        self.chk_offline_mode.setToolTip(
            "Use cached/local data only. SafeLauncher will not start automatic "
            "artwork, Steam, cloud, profile, telemetry, or update requests."
        )
        form_mode.addRow("Network:", self.chk_offline_mode)
        offline_hint = self.info_hint(
            offline_status_text(settings),
            tooltip="Offline mode prevents automatic internet access and uses local or cached data.",
        )
        self.lbl_offline_mode_help = offline_hint.findChild(QLabel, "propertyHintText")
        self.chk_offline_mode.toggled.connect(
            lambda enabled: self.lbl_offline_mode_help.setText(
                "Offline mode enabled — automatic internet access is disabled."
                if enabled else
                "Online mode — automatic internet access is enabled."
            )
        )
        form_mode.addRow("", offline_hint)

        # Convex Site URL
        self.edit_convex_url = QLineEdit(get_site_url())
        self.edit_convex_url.setPlaceholderText("Your Convex deployment URL")
        form_mode.addRow("Convex Site URL:", self.edit_convex_url)

        # Secret Access Key
        saved_key = get_secret("cloud_secret_key", legacy_name="cloud_secret_key")
        key_row = QHBoxLayout()
        self.edit_cloud_secret_key = QLineEdit(saved_key)
        self.edit_cloud_secret_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_cloud_secret_key.setPlaceholderText("Required for cloud status and sync")
        self.edit_cloud_secret_key.setToolTip("Required: the Secret Access Key configured on your SafeLauncherCloud backend.")
        key_row.addWidget(self.edit_cloud_secret_key, 1)

        btn_toggle_key = QPushButton("Show")
        btn_toggle_key.setFixedWidth(50)
        btn_toggle_key.setToolTip("Show / Hide Secret Key")

        def _toggle_key():
            if self.edit_cloud_secret_key.echoMode() == QLineEdit.EchoMode.Password:
                self.edit_cloud_secret_key.setEchoMode(QLineEdit.EchoMode.Normal)
                btn_toggle_key.setText("Hide")
            else:
                self.edit_cloud_secret_key.setEchoMode(QLineEdit.EchoMode.Password)
                btn_toggle_key.setText("Show")

        btn_toggle_key.clicked.connect(_toggle_key)
        key_row.addWidget(btn_toggle_key)
        form_mode.addRow("Secret Key (Required):", key_row)

        # A deployment key is deliberately separate from the save API key.
        # It is only used by the optional one-click backend updater and is
        # scoped by Convex to a single deployment.
        saved_deploy_key = get_secret("convex_deploy_key", legacy_name="convex_deploy_key")
        deploy_key_row = QHBoxLayout()
        self.edit_convex_deploy_key = QLineEdit(saved_deploy_key)
        self.edit_convex_deploy_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_convex_deploy_key.setPlaceholderText("Optional — enables source-free backend updates")
        self.edit_convex_deploy_key.setToolTip(
            "Optional Convex deployment key. It lets SafeLauncher update this backend "
            "without keeping a server repository on this computer."
        )
        deploy_key_row.addWidget(self.edit_convex_deploy_key, 1)
        btn_toggle_deploy_key = QPushButton("Show")
        btn_toggle_deploy_key.setFixedWidth(50)

        def _toggle_deploy_key():
            hidden = self.edit_convex_deploy_key.echoMode() == QLineEdit.EchoMode.Password
            self.edit_convex_deploy_key.setEchoMode(
                QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password
            )
            btn_toggle_deploy_key.setText("Hide" if hidden else "Show")

        btn_toggle_deploy_key.clicked.connect(_toggle_deploy_key)
        deploy_key_row.addWidget(btn_toggle_deploy_key)
        btn_setup_deploy_key = QPushButton("Wizard…")
        btn_setup_deploy_key.setToolTip("Generate or securely configure a Convex deployment key")
        btn_setup_deploy_key.clicked.connect(self._open_deploy_key_wizard)
        deploy_key_row.addWidget(btn_setup_deploy_key)
        btn_forget_deploy_key = QPushButton("Forget")
        btn_forget_deploy_key.setToolTip("Remove the deploy key from this device; it does not revoke the key in Convex")
        btn_forget_deploy_key.clicked.connect(self._forget_deploy_key)
        deploy_key_row.addWidget(btn_forget_deploy_key)
        form_mode.addRow("Convex Deploy Key:", deploy_key_row)
        deploy_key_help = self.info_hint(
            "Optional. Create a deployment-scoped key in Convex Dashboard for updates on machines not signed into the Convex CLI.",
            tooltip="Deployment keys are optional and are stored in the local secret store, never in request keys or logs.",
        )
        form_mode.addRow("", deploy_key_help)

        # Device Identity & Concurrent Devices
        from core.cloud_backend import get_device_identity
        _, current_dev_name, _ = get_device_identity()
        self.edit_device_name = QLineEdit(current_dev_name)
        self.edit_device_name.setPlaceholderText("Device Name (e.g. Steam Deck, Main Gaming PC)")
        form_mode.addRow("This Device Name:", self.edit_device_name)

        self.lbl_connected_devices = QLabel("1 active device (this machine)")
        self.lbl_connected_devices.setStyleSheet("color: #38BDF8; font-weight: bold;")
        form_mode.addRow("Concurrent Devices:", self.lbl_connected_devices)

        self.spin_sync_workers = QSpinBox()
        self.spin_sync_workers.setRange(1, 5)
        self.spin_sync_workers.setValue(settings.value("cloud_sync_workers", 3, type=int))
        self.spin_sync_workers.setToolTip("Simultaneous background worker threads for checking and syncing cloud saves.")
        form_mode.addRow("Concurrent Sync Workers:", self.spin_sync_workers)

        # Local fallback directory used when the backend is 'local' or offline.
        cloud_row = QHBoxLayout()
        self.edit_cloud_saves_dir = QLineEdit(self.cloud_saves_dir)
        cloud_row.addWidget(self.edit_cloud_saves_dir, 1)
        btn_browse_cloud = QPushButton("Browse...")
        btn_browse_cloud.clicked.connect(self._browse_cloud_dir)
        cloud_row.addWidget(btn_browse_cloud)
        form_mode.addRow("Local Sync Folder:", cloud_row)

        self.lbl_account_status = QLabel("Not connected.")
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
        self._polish_settings_form(form_mode)
        layout.addLayout(form_mode)

        acct_btns = QHBoxLayout()
        acct_btns.setSpacing(8)
        self.btn_wizard = QPushButton("Setup Wizard…")
        self.btn_wizard.clicked.connect(self._open_cloud_wizard)
        acct_btns.addWidget(self.btn_wizard)
        self.btn_sign_in = QPushButton("Test & Connect…")
        self.btn_sign_in.clicked.connect(self._cloud_connect)
        acct_btns.addWidget(self.btn_sign_in)
        btn_account_mgr = QPushButton("Advanced history & devices…")
        btn_account_mgr.clicked.connect(self._open_account_manager)
        acct_btns.addWidget(btn_account_mgr)
        self.btn_review_conflicts = QPushButton("Review conflicts")
        self.btn_review_conflicts.setIcon(get_icon("ph.warning-bold", color="#A1A1AA"))
        self.btn_review_conflicts.setEnabled(False)
        self.btn_review_conflicts.setToolTip("Available when this account has cloud-save conflicts")
        self.btn_review_conflicts.clicked.connect(self._open_conflict_review)
        acct_btns.addWidget(self.btn_review_conflicts)
        self.btn_refresh_quota = QPushButton("Refresh Quota")
        self.btn_refresh_quota.clicked.connect(self._refresh_account_status)
        self.btn_refresh_quota.clicked.connect(self._refresh_cloud_conflict_summary)
        acct_btns.addWidget(self.btn_refresh_quota)
        self.btn_logout = QPushButton("Disconnect")
        self.btn_logout.clicked.connect(self._cloud_disconnect)
        acct_btns.addWidget(self.btn_logout)
        acct_btns.addStretch()
        layout.addLayout(acct_btns)

        # ── Live Backend Health & Version Synchronization Card ───────────────
        self.card_backend_health = QFrame()
        self.card_backend_health.setStyleSheet("""
            QFrame {
                background-color: #18181B;
                border: 1px solid #27272A;
                border-radius: 8px;
                padding: 12px;
                margin-top: 6px;
            }
        """)
        bh_layout = QVBoxLayout(self.card_backend_health)
        bh_layout.setContentsMargins(10, 10, 10, 10)
        bh_layout.setSpacing(8)

        bh_header = QHBoxLayout()
        bh_title = QLabel("<b>Convex Backend Health & Synchronization</b>")
        bh_title.setStyleSheet("color: #FFFFFF; font-size: 13px;")
        bh_header.addWidget(bh_title)
        bh_header.addStretch()

        self.lbl_health_latency = QLabel("-- ms")
        self.lbl_health_latency.setStyleSheet("""
            background: #27272A;
            color: #A1A1AA;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: bold;
        """)
        bh_header.addWidget(self.lbl_health_latency)

        self.btn_probe_health = QPushButton("Check Health")
        self.btn_probe_health.setStyleSheet("padding: 4px 10px; font-size: 11px;")
        self.btn_probe_health.clicked.connect(self._refresh_backend_health)
        bh_header.addWidget(self.btn_probe_health)
        bh_layout.addLayout(bh_header)

        bh_grid = QGridLayout()
        bh_grid.setSpacing(6)
        bh_grid.addWidget(QLabel("Connection Status:"), 0, 0)
        self.lbl_health_status = QLabel("Probing…")
        self.lbl_health_status.setStyleSheet("color: #9CA3AF; font-weight: 500;")
        bh_grid.addWidget(self.lbl_health_status, 0, 1)

        bh_grid.addWidget(QLabel("Backend Version:"), 1, 0)
        self.lbl_health_version = QLabel("Checking…")
        self.lbl_health_version.setStyleSheet("color: #9CA3AF; font-weight: 500;")
        bh_grid.addWidget(self.lbl_health_version, 1, 1)
        bh_layout.addLayout(bh_grid)
        self._polish_settings_grid(bh_grid)

        self.lbl_version_warning = QLabel("")
        self.lbl_version_warning.setWordWrap(True)
        self.lbl_version_warning.setStyleSheet("font-size: 12px;")
        bh_layout.addWidget(self.lbl_version_warning)

        self.health_action_row = QHBoxLayout()
        self.btn_redeploy = QPushButton("One-Click Redeploy Backend")
        self.btn_redeploy.setStyleSheet("background: #0284C7; font-weight: bold; padding: 6px 14px;")
        self.btn_redeploy.clicked.connect(self._redeploy_backend)
        self.btn_redeploy.setVisible(False)
        self.health_action_row.addWidget(self.btn_redeploy)

        self.btn_open_dashboard = QPushButton("Open Convex Dashboard ↗")
        self.btn_open_dashboard.setStyleSheet("padding: 6px 14px;")
        self.btn_open_dashboard.clicked.connect(self._open_convex_dashboard)
        self.btn_open_dashboard.setVisible(False)
        self.health_action_row.addWidget(self.btn_open_dashboard)

        self.health_action_row.addStretch()
        bh_layout.addLayout(self.health_action_row)

        layout.addWidget(self.card_backend_health)

        layout.addStretch()
        self._wrap_settings_sections(layout)

        self.accountStatusReady.connect(self._apply_account_status)
        self.backendHealthReady.connect(self._apply_backend_health)
        self.backendDeployReady.connect(self._apply_backend_deploy_result)
        self.appUpdateReady.connect(self._apply_manual_update_result)
        self.appDownloadProgress.connect(self._on_app_download_progress)
        self.appDownloadFinished.connect(self._on_app_download_finished)
        self.appDownloadFailed.connect(self._on_app_download_failed)

        self._refresh_account_status()
        self._refresh_backend_health()
        self._refresh_cloud_conflict_summary()

        scroll.setWidget(page)
        return scroll

    def _open_conflict_review(self) -> None:
        if getattr(self, "btn_review_conflicts", None) is not None and self.btn_review_conflicts.isEnabled():
            self.conflicts_requested.emit()

    def _refresh_cloud_conflict_summary(self) -> None:
        """Enable global conflict review only for the current cloud context."""
        button = getattr(self, "btn_review_conflicts", None)
        service = self.cloud_center_service
        if button is None:
            return
        button.setEnabled(False)
        if service is None or self.request_manager is None:
            button.setToolTip("Available when this account has cloud-save conflicts")
            return
        if self._cloud_overview_binding is not None:
            self._cloud_overview_binding.close()
            self._cloud_overview_binding = None
        try:
            handle = service.request_overview()
        except Exception:
            button.setToolTip("Cloud conflict status is unavailable")
            return

        def _apply(result):
            if result.status in (ResourceStatus.IDLE, ResourceStatus.LOADING):
                return
            if result.status not in (ResourceStatus.READY, ResourceStatus.STALE):
                button.setToolTip("Cloud conflict status is unavailable")
                return
            overview = CloudOverview.from_payload(result.value)
            has_conflicts = int(overview.conflict_count or 0) > 0
            button.setEnabled(has_conflicts)
            button.setToolTip(
                "Open Save History to review current cloud-save conflicts"
                if has_conflicts else
                "No cloud-save conflicts in the current account"
            )

        self._cloud_overview_binding = bind_request(
            self.request_manager,
            handle,
            _apply,
            self,
            cancel_on_close=True,
        )

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
                    from core.cloud_backend import BASE_FREE_QUOTA_BYTES
                    color = "#F59E0B" if used > BASE_FREE_QUOTA_BYTES else (
                        "#3B9FE8" if pct < 0.75 else ("#EAB308" if pct < 0.92 else "#EF4444")
                    )
                    self.lbl_account_status.setStyleSheet(
                        f"color: {'#F59E0B' if used > BASE_FREE_QUOTA_BYTES else '#9ca3af'};"
                    )
                    style = (
                        "QProgressBar { background: #18181B; border: 1px solid #27272A;"
                        " border-radius: 7px; }\n"
                        f"QProgressBar::chunk {{ background: {color}; border-radius: 6px; }}"
                    )
                    bar.setValue(int(pct * 1000))
                    bar.setStyleSheet(style)

            if hasattr(self, "lbl_connected_devices"):
                import re as _re
                match_dev = _re.search(r"(\d+) concurrent device\(s\) online", message)
                if match_dev:
                    count = match_dev.group(1)
                    self.lbl_connected_devices.setText(f"{count} active online (shared backend)")
                elif message.startswith(("Not connected", "Disconnected")):
                    self.lbl_connected_devices.setText("Not connected")
        except RuntimeError:
            pass  # dialog already destroyed

    # -------------------------------------------------------------
    # TAB 5: Plugins & Addons (wl-screenrec)
    # -------------------------------------------------------------
    def _create_plugins_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("settingsScroll")

        page = QWidget()
        page.setObjectName("settingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        sec_title = QLabel("Hardware Accelerated Video Recording")
        sec_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_title.setStyleSheet("color: #ffffff; padding-bottom: 2px;")
        layout.addWidget(sec_title)
        self._add_section_divider(layout)

        desc = self.info_hint(
            "GPU Screen Recorder is a high-performance Linux recorder using NVIDIA NVENC, AMD VAAPI, or Intel QuickSync.",
            tooltip="Recording runs through the detected local backend. SafeLauncher does not upload recordings to private cloud storage.",
        )
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
            status_text = "No recorder backend found. Install gpu-screen-recorder via paru, yay, or flatpak to enable hardware NVENC recording."

        banner = QLabel(status_text)
        banner.setWordWrap(True)
        banner.setStyleSheet(f"background-color: {bg_col}; color: #ffffff; padding: 10px 12px; font-weight: 600; font-size: 11px;")
        layout.addWidget(banner)

        # Installation Helper (if gpu-screen-recorder not installed)
        if backend != "gpu-screen-recorder":
            install_row = QHBoxLayout()
            self.install_option_combo = QComboBox()
            for label, cmd in WlScreenrecService.get_install_options():
                self.install_option_combo.addItem(label, cmd)
            self.install_option_combo.currentIndexChanged.connect(self._on_install_option_changed)
            install_row.addWidget(self.install_option_combo)

            self.install_cmd_box = QLineEdit(WlScreenrecService.get_install_options()[0][1])
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
            "QKeySequenceEdit { background: #20242C; color: #ffffff; border: 1px solid #30353F;"
            " border-top-color: #414752; border-bottom-color: #252A32; border-radius: 7px;"
            " padding: 8px 10px; font-size: 12px; }"
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
            "QKeySequenceEdit { background: #20242C; color: #ffffff; border: 1px solid #30353F;"
            " border-top-color: #414752; border-bottom-color: #252A32; border-radius: 7px;"
            " padding: 8px 10px; font-size: 12px; }"
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

        self._polish_settings_form(form)
        layout.addLayout(form)
        layout.addStretch()
        self._wrap_settings_sections(layout)

        scroll.setWidget(page)
        return scroll

    def _open_install_notice(self):
        dlg = PluginInstallNoticeDialog(self)
        dlg.exec()

    def _on_install_option_changed(self, index: int):
        cmd = self.install_option_combo.itemData(index)
        if cmd:
            self.install_cmd_box.setText(cmd)

    def _copy_install_command(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.install_cmd_box.text())

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

    def _export_runtime_diagnostics(self):
        """Export support-safe request/resource health counters."""
        target, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Runtime Diagnostics",
            os.path.join(os.path.expanduser("~"), "safelauncher-runtime-diagnostics.json"),
            "JSON files (*.json)",
        )
        if not target:
            return
        request_metrics = None
        if self.request_manager is not None:
            try:
                request_metrics = self.request_manager.metrics()
            except Exception:
                request_metrics = None
        report = build_runtime_diagnostics(
            app_version=APP_VERSION,
            request_metrics=request_metrics,
            offline=not self._dialog_network_allowed(),
        )
        try:
            export_runtime_diagnostics(target, report)
        except (OSError, TypeError, ValueError) as error:
            QMessageBox.warning(self, "Diagnostics Export Failed", str(error))
            return
        QMessageBox.information(
            self,
            "Diagnostics Exported",
            "Runtime diagnostics were exported without credentials, game paths, or resource contents.",
        )

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
            settings = QSettings("SafeLauncher", "SafeLauncher")
            url = normalize_site_url(self.edit_convex_url.text())
            if url and not url.startswith(("http://", "https://")):
                url = "https://" + url
            settings.setValue("convex_site_url", url)

            key = self.edit_cloud_secret_key.text().strip()
            if key:
                set_secret("cloud_secret_key", key)
            else:
                delete_secret("cloud_secret_key")

            deploy_key = self.edit_convex_deploy_key.text().strip()
            if deploy_key:
                set_secret("convex_deploy_key", deploy_key)
            else:
                delete_secret("convex_deploy_key")

            self.cloud_account_service.reset_backend()
            mode = self.combo_cloud_mode.currentData() or "local"
            self.cloud_account_service.set_mode(mode)
            set_offline_mode(self.chk_offline_mode.isChecked(), settings)

            cloud_dir = self.edit_cloud_saves_dir.text().strip()
            if cloud_dir:
                settings.setValue("cloud_saves_dir", cloud_dir)

            dev_name = self.edit_device_name.text().strip()
            if dev_name:
                settings.setValue("cloud_device_name", dev_name)
            settings.setValue("cloud_sync_workers", self.spin_sync_workers.value())

            if hasattr(self, "chk_achievement_notifications"):
                settings.setValue("achievement_notifications_enabled", self.chk_achievement_notifications.isChecked())
            if hasattr(self, "chk_achievement_desktop"):
                settings.setValue("achievement_desktop_notifications", self.chk_achievement_desktop.isChecked())
            if hasattr(self, "combo_date_format"):
                self.date_format = self.combo_date_format.currentData() or get_date_format_key()
                settings.setValue("date_format", self.date_format)

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

    def _dialog_network_allowed(self) -> bool:
        """Apply the unsaved offline checkbox immediately inside Settings."""
        if getattr(self, "chk_offline_mode", None) is not None:
            try:
                if self.chk_offline_mode.isChecked():
                    return False
            except RuntimeError:
                return False
        return automatic_network_allowed(QSettings("SafeLauncher", "SafeLauncher"))

    def _on_cloud_mode_changed(self, index: int):
        self.cloud_account_service.set_mode(self.combo_cloud_mode.itemData(index) or "local")

    def _open_cloud_wizard(self):
        """Launch the step-by-step Convex cloud setup wizard."""
        try:
            from ui.dialogs.cloud_wizard_dialog import CloudWizardDialog
            wizard = CloudWizardDialog(self)
            if wizard.exec():
                from core.cloud_backend import get_site_url
                self.edit_convex_url.setText(get_site_url())
                settings = QSettings("SafeLauncher", "SafeLauncher")
                self.edit_cloud_secret_key.setText(get_secret("cloud_secret_key", legacy_name="cloud_secret_key"))
                self.edit_convex_deploy_key.setText(get_secret("convex_deploy_key", legacy_name="convex_deploy_key"))
                self.combo_cloud_mode.setCurrentIndex(1)
                self._refresh_account_status()
                self._refresh_backend_health()
        except Exception as e:
            QMessageBox.warning(self, "Setup Wizard", f"Could not open wizard: {e}")

    def _open_deploy_key_wizard(self):
        """Open the dedicated deploy-key setup and storage flow."""
        try:
            from ui.dialogs.deploy_key_wizard_dialog import DeployKeyWizardDialog
            wizard = DeployKeyWizardDialog(self)
            if wizard.exec():
                self.edit_convex_deploy_key.setText(get_secret("convex_deploy_key", legacy_name="convex_deploy_key"))
                self.edit_cloud_secret_key.setText(get_secret("cloud_secret_key", legacy_name="cloud_secret_key"))
                self._refresh_backend_health()
        except Exception as e:
            QMessageBox.warning(self, "Deploy Key Setup", f"Could not open wizard: {e}")

    def _forget_deploy_key(self):
        """Remove the local deploy credential without touching Convex."""
        answer = QMessageBox.question(
            self,
            "Forget Deploy Key",
            "Remove this device's deploy key? This will not revoke it from Convex; it only disables local automatic backend updates.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            if delete_secret("convex_deploy_key"):
                self.edit_convex_deploy_key.clear()
                self._refresh_backend_health()

    def _open_account_manager(self):
        """Launch the full profile/quota/version manager dialog."""
        try:
            from ui.dialogs.account_dialog import AccountDialog
            parent = self.parent()
            dialog = AccountDialog(
                self,
                request_manager=self.request_manager,
                cloud_account_service=getattr(parent, "cloud_account_service", None),
                cloud_operation_service=getattr(parent, "cloud_operation_service", None),
            )
            dialog.exec()
            self._refresh_account_status()  # picker may have changed session/state
            self._refresh_backend_health()
        except Exception as e:
            QMessageBox.warning(self, "Account Manager", f"Could not open: {e}")

    def _cloud_connect(self):
        """Test and save Convex cloud backend connection."""
        url = normalize_site_url(self.edit_convex_url.text())
        key = self.edit_cloud_secret_key.text().strip()
        if not url:
            QMessageBox.warning(self, "Cloud Setup", "Please enter your Convex Site URL.")
            return

        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
            self.edit_convex_url.setText(url)

        settings = QSettings("SafeLauncher", "SafeLauncher")
        settings.setValue("convex_site_url", url)
        if key:
            set_secret("cloud_secret_key", key)
        else:
            delete_secret("cloud_secret_key")

        self.cloud_account_service.reset_backend()
        self.cloud_account_service.set_mode("convex")
        self.combo_cloud_mode.setCurrentIndex(1)
        self.lbl_account_status.setText("Connecting to cloud…")
        self._refresh_account_status()
        self._refresh_backend_health()
        self._refresh_cloud_conflict_summary()

    def _cloud_disconnect(self):
        """Revert cloud backend to local folder sync."""
        self.cloud_account_service.set_mode("local")
        self.cloud_account_service.reset_backend()
        self.combo_cloud_mode.setCurrentIndex(0)
        self.accountStatusReady.emit("Disconnected (using Local sync).")
        self._refresh_backend_health()
        self._refresh_cloud_conflict_summary()

    def _start_managed_task(self, name: str, work, on_complete):
        """Run a settings operation and expose it in the global Activity drawer."""
        parent = self.parent()
        registry = getattr(parent, "operation_registry", None)
        operation = None
        if registry is not None:
            operation = registry.start(
                name.replace("SafeLauncher-", "").replace("-", " ").strip(),
                category="Settings",
            )

        if self.request_manager is not None:
            key = self.cloud_account_service.request_key("settings-task", name)
            handle = self.request_manager.request(
                key,
                lambda token: (token.raise_if_cancelled(), work(), token.raise_if_cancelled())[1],
                priority=RequestPriority.NORMAL,
                timeout_seconds=120,
            )
            if operation is not None:
                operation.cancel = handle.cancel
                operation.retry = lambda: self._start_managed_task(name, work, on_complete)
            request_id = handle.request_id

            def _deliver(result):
                if result.status in {ResourceStatus.IDLE, ResourceStatus.LOADING}:
                    return
                binding = self._resource_bindings.pop(request_id, None)
                if binding is not None:
                    binding.close()
                if result.status == ResourceStatus.CANCELLED:
                    if operation is not None:
                        operation_registry = getattr(self.parent(), "operation_registry", None)
                        if operation_registry is not None:
                            operation_registry.finish(operation.operation_id, state="cancelled")
                    return
                value = result.value if result.status == ResourceStatus.READY else {
                    "error": str(result.error or result.status.value)
                }
                if operation is not None:
                    operation_registry = getattr(self.parent(), "operation_registry", None)
                    if operation_registry is not None:
                        operation_registry.finish_result(operation.operation_id, value)
                on_complete(value)

            self._resource_bindings[request_id] = bind_request(
                self.request_manager,
                handle,
                _deliver,
                self,
                cancel_on_close=True,
            )
            return handle

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
        """Keep Qt workers alive until their cooperative cancellation completes."""
        if self.request_manager is not None:
            if self._cloud_overview_binding is not None:
                self._cloud_overview_binding.close()
                self._cloud_overview_binding = None
            for binding in tuple(self._resource_bindings.values()):
                binding.close()
            self._resource_bindings.clear()
        self._task_supervisor.cancel_all(100)
        if self._task_supervisor.has_running_tasks():
            QTimer.singleShot(100, self.close)
            event.ignore()
            return
        super().closeEvent(event)

    def _refresh_account_status(self):
        self._account_probe_generation += 1
        generation = self._account_probe_generation

        if not self._dialog_network_allowed():
            self.accountStatusReady.emit("Offline mode — cloud account checks are disabled.")
            return

        def _probe():
            try:
                from core.cloud_backend import get_site_url
                site = get_site_url()
                if not site:
                    return "Not connected."
                overview = self.cloud_account_service.account()
                used = overview.get("bytesUsed", 0)
                quota = overview.get("quotaBytes", 1)
                games = len(overview.get("games", []))
                concurrent = overview.get("concurrentDevices", 1)
                devices_total = overview.get("totalDevices", 1)
                msg = f"Connected ({format_bytes(used)} / {format_bytes(quota)} used · {games} game(s) · {concurrent} concurrent device(s) online · 1 GB free, referrals can expand storage)"
                return msg
            except Exception as e:
                from core.cloud_backend import describe_cloud_error
                return describe_cloud_error(e)

        def _apply_if_current(message, expected=generation):
            if expected == self._account_probe_generation:
                self.accountStatusReady.emit(message)

        self._start_managed_task("SafeLauncher-AccountProbe", _probe, _apply_if_current)

    def _refresh_backend_health(self):
        """Probe backend health endpoint, measure latency, and check version parity."""
        self._health_probe_generation += 1
        generation = self._health_probe_generation
        if not self._dialog_network_allowed():
            self.lbl_health_status.setText("Offline mode")
            self.lbl_health_latency.setText("--")
            self.lbl_health_version.setText("Not checked")
            self.lbl_version_warning.setText(
                "Offline mode is enabled; backend health is not probed."
            )
            return
        url = normalize_site_url(self.edit_convex_url.text())
        key = self.edit_cloud_secret_key.text().strip()
        if not url:
            from core.cloud_backend import get_site_url
            url = get_site_url()

        self.lbl_health_status.setText("Probing backend…")
        self.lbl_health_latency.setText("...")
        self.lbl_health_latency.setStyleSheet("background: #27272A; color: #A1A1AA; padding: 3px 8px; border-radius: 4px; font-size: 11px;")

        def _worker():
            return self.cloud_account_service.health(url, key)

        def _apply_if_current(result, expected=generation):
            if expected == self._health_probe_generation:
                self.backendHealthReady.emit(result)

        self._start_managed_task("SafeLauncher-HealthProbe", _worker, _apply_if_current)

    def _apply_backend_health(self, health: dict):
        """Update live health card with latency, status, and version parity badges."""
        status = health.get("status", "unreachable")
        lat = health.get("latency_ms", -1)
        ver = health.get("version", "unknown")
        is_outdated = health.get("is_outdated", False)
        min_ver = health.get("min_version", MIN_CONVEX_BACKEND_VERSION)

        if lat >= 0:
            color = "#34D399" if lat < 150 else ("#FBBF24" if lat < 400 else "#F87171")
            self.lbl_health_latency.setText(f"{lat} ms")
            self.lbl_health_latency.setStyleSheet(
                f"background: #1F2937; color: {color}; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;"
            )
        else:
            self.lbl_health_latency.setText("-- ms")
            self.lbl_health_latency.setStyleSheet(
                "background: #27272A; color: #6B7280; padding: 3px 8px; border-radius: 4px; font-size: 11px;"
            )

        if status == "connected":
            self.lbl_health_status.setText("<font color='#10B981'>● Connected & Healthy</font>")
        elif status == "legacy":
            self.lbl_health_status.setText("<font color='#F59E0B'>● Legacy Backend (Missing /api/health)</font>")
        elif status == "unauthorized":
            self.lbl_health_status.setText("<font color='#EF4444'>● Unauthorized (Secret Key required or invalid)</font>")
        elif status == "unconfigured":
            self.lbl_health_status.setText("<font color='#9CA3AF'>● Not Configured</font>")
        else:
            err = health.get("error") or "Unreachable"
            self.lbl_health_status.setText(f"<font color='#EF4444'>● Unreachable ({err})</font>")

        if ver != "unknown":
            self.lbl_health_version.setText(f"v{ver}")
            if is_outdated:
                self.lbl_version_warning.setText(
                    f"<font color='#F59E0B'>Backend update required: installed <b>v{ver}</b> is older than minimum supported <b>v{min_ver}</b>. "
                    "Redeploy the latest SafeLauncher backend to restore full cloud features.</font>"
                )
                self.btn_redeploy.setVisible(True)
                self.btn_redeploy.setText("Update & Redeploy Backend")
                self.btn_redeploy.setToolTip(
                    "Downloads the current backend temporarily and deploys it to your configured Convex project."
                )
                self.btn_open_dashboard.setVisible(True)
            else:
                cloud_key_configured = bool(
                    self.edit_cloud_secret_key.text().strip()
                    or get_secret("cloud_secret_key", legacy_name="cloud_secret_key")
                )
                self.lbl_version_warning.setText(
                    (
                        f"<font color='#10B981'>Backend functions are up to date (v{ver} >= v{min_ver}). "
                        "Cloud Save is ready.</font>"
                    ) if cloud_key_configured else (
                        f"<font color='#10B981'>Backend functions are up to date (v{ver} >= v{min_ver}).</font> "
                        "<font color='#FBBF24'>Cloud Save still needs its Secret Access Key in this app.</font>"
                    )
                )
                self.btn_redeploy.setVisible(True)
                self.btn_open_dashboard.setVisible(True)
        else:
            self.lbl_health_version.setText("Unknown")
            self.lbl_version_warning.setText("")
            self.btn_redeploy.setVisible(False)
            self.btn_open_dashboard.setVisible(False)

    def _apply_backend_deploy_result(self, success: bool, message: str):
        """Handle redeploy completion on the Qt GUI thread."""
        self.btn_redeploy.setEnabled(True)
        if success:
            saved_cloud_secret = get_secret("cloud_secret_key", legacy_name="cloud_secret_key")
            if saved_cloud_secret and not self.edit_cloud_secret_key.text().strip():
                self.edit_cloud_secret_key.setText(saved_cloud_secret)
            if self.edit_cloud_secret_key.text().strip() or saved_cloud_secret:
                self.lbl_version_warning.setText(f"<font color='#10B981'>{message}</font>")
            else:
                self.lbl_version_warning.setText(
                    f"<font color='#10B981'>{message}</font> "
                    "<font color='#FBBF24'>Cloud Save still needs the Secret Access Key in Settings → Cloud.</font>"
                )
            self._refresh_backend_health()
        else:
            # Diagnostics must be useful without leaking either credential if
            # a CLI happens to echo an argument in its own error output.
            for sensitive in (
                self.edit_cloud_secret_key.text().strip(),
                self.edit_convex_deploy_key.text().strip(),
            ):
                if sensitive:
                    message = message.replace(sensitive, "[redacted]")
            self.lbl_version_warning.setText(
                f"<font color='#EF4444'>{html.escape(message).replace(chr(10), '<br>')}</font>"
            )
            QMessageBox.critical(
                self,
                "Backend redeploy failed",
                message + "\n\nThe deployment command already used --yes; no hidden terminal confirmation is required.",
            )

    def _redeploy_backend(self):
        """Redeploy fresh backend source without modifying a local checkout."""
        info = detect_local_cloud_installation()
        from core.cloud_cli_wizard import _convex_cli_env, _has_convex_project_config
        from pathlib import Path
        backend_path = Path(info["path"]) if info and info.get("path") else None
        settings = QSettings("SafeLauncher", "SafeLauncher")
        has_saved_deploy_key = bool(get_secret("convex_deploy_key", legacy_name="convex_deploy_key"))
        has_saved_deployment = bool(settings.value("convex_deployment", "", type=str).strip())
        if not has_saved_deploy_key and not has_saved_deployment and (
            not backend_path or not _has_convex_project_config(_convex_cli_env(backend_path))
        ):
            QMessageBox.information(
                self,
                "Backend linking required",
                "SafeLauncher needs a one-time Convex deployment credential before it can "
                "perform fully automatic backend updates.\n\n"
                "Open Cloud Setup on a machine that can sign in to Convex, or add a Convex "
                "deploy key in Settings → Cloud. Server source files are not required.",
            )
            return

        reply = QMessageBox.question(
            self, "Redeploy Backend",
            "Download the latest SafeLauncherCloud backend into temporary staging and "
            "deploy it to your configured Convex project?\n\n"
            "No server repository will be created or changed on your computer.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            from core.cloud_cli_wizard import deploy_latest_convex_backend
            self.lbl_version_warning.setText("<font color='#3B82F6'>Deploying backend functions…</font>")
            self.btn_redeploy.setEnabled(False)

            def _worker():
                try:
                    # The confirmation above is the user's consent. The CLI
                    # runs in a background worker with no visible terminal,
                    # so pass its non-interactive confirmation flag here.
                    output = StringIO()
                    with redirect_stdout(output), redirect_stderr(output):
                        deployed_url = deploy_latest_convex_backend(
                            str(backend_path) if backend_path else None, assume_yes=True
                        )
                    if deployed_url:
                        return True, "Backend redeployed. Rechecking its version…"
                    diagnostic = output.getvalue().strip()
                    if diagnostic:
                        diagnostic = diagnostic[-3500:]
                        return False, f"Backend redeploy did not complete:\n{diagnostic}"
                    return False, "Backend redeploy did not complete without diagnostic output. Verify the Convex deploy key or one-time CLI login."
                except Exception as exc:
                    return False, f"Backend redeploy failed: {exc}"

            self._start_managed_task(
                "SafeLauncher-Redeploy",
                _worker,
                lambda result: self.backendDeployReady.emit(*result),
            )

    def _open_convex_dashboard(self):
        """Open Convex dashboard in browser."""
        QDesktopServices.openUrl(QUrl("https://dashboard.convex.dev"))

    # ---- Application Updater handlers ---------------------------------------

    def _manual_check_updates(self):
        """Manual trigger to check for SafeLauncher application updates."""
        if not self._dialog_network_allowed():
            self.lbl_update_status.setText(
                "<font color='#9CA3AF'>Offline mode — update checks are disabled.</font>"
            )
            return
        self.btn_check_app_updates.setEnabled(False)
        self.lbl_update_status.setText("<font color='#3B82F6'>Checking GitHub Releases for updates…</font>")

        def _worker():
            return check_for_updates()

        self._start_managed_task(
            "SafeLauncher-ManualUpdate", _worker, self.appUpdateReady.emit
        )

    def _apply_manual_update_result(self, info: dict):
        """Handle result of manual update check."""
        self.btn_check_app_updates.setEnabled(True)
        if info.get("error"):
            self.lbl_update_status.setText(f"<font color='#EF4444'>Update check failed: {info['error']}</font>")
            return

        latest = info.get("latest_version", "")
        if info.get("update_available"):
            self.lbl_update_status.setText(
                f"<font color='#10B981'><b>Update available:</b> v{latest} (Current: v{info['current_version']})</font>"
            )
            if info.get("is_appimage") and info.get("appimage_asset"):
                reply = QMessageBox.question(
                    self, "Update Available",
                    f"A new version of SafeLauncher is available: {latest}\n\nWould you like to download and install this update now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._start_appimage_download(info["appimage_asset"]["download_url"])
            else:
                QMessageBox.information(
                    self, "Update Available",
                    f"SafeLauncher {latest} is available on GitHub!\n\nRunning from source/git. Please pull the latest changes or download the latest release from GitHub."
                )
        else:
            self.lbl_update_status.setText(
                f"<font color='#10B981'>SafeLauncher is up to date (v{info['current_version']}).</font>"
            )

    def _start_appimage_download(self, asset_url: str):
        """Download new AppImage in background thread with progress reporting."""
        if not self._dialog_network_allowed():
            self.lbl_update_status.setText(
                "<font color='#9CA3AF'>Offline mode — update downloads are disabled.</font>"
            )
            return
        self.lbl_update_status.setText("<font color='#3B82F6'>Downloading update…</font>")

        def _worker():
            try:
                target = download_and_apply_appimage_update(
                    asset_url,
                    progress_callback=lambda d, t: self.appDownloadProgress.emit(d, t)
                )
                return True, target
            except Exception as e:
                return False, str(e)

        def _deliver(result):
            success, value = result
            if success:
                self.appDownloadFinished.emit(value)
            else:
                self.appDownloadFailed.emit(value)

        self._start_managed_task(
            "SafeLauncher-AppImageDownload", _worker, _deliver
        )

    def _on_app_download_progress(self, downloaded: int, total: int):
        if total > 0:
            pct = int((downloaded / total) * 100)
            self.lbl_update_status.setText(
                f"<font color='#3B82F6'>Downloading update: {pct}% ({downloaded // (1024*1024)} MB / {total // (1024*1024)} MB)…</font>"
            )

    def _on_app_download_finished(self, target_path: str):
        self.lbl_update_status.setText("<font color='#10B981'><b>Update Ready!</b> Restart required.</font>")
        reply = QMessageBox.question(
            self, "Update Installed",
            "The update has been downloaded and verified.\n\nRestart SafeLauncher now to apply the update?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            restart_application()

    def _on_app_download_failed(self, error: str):
        self.lbl_update_status.setText(f"<font color='#EF4444'>Download failed: {error}</font>")
        QMessageBox.critical(self, "Update Failed", f"Failed to download AppImage update:\n{error}")

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

    def get_date_format(self) -> str:
        return self.combo_date_format.currentData() if hasattr(self, "combo_date_format") else self.date_format

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


class PluginInstallNoticeDialog(PopupDialog):
    """Notice dialog explaining why root/sudo privileges are required for AUR package installation."""
    def __init__(self, parent=None):
        super().__init__("Install GPU Screen Recorder", parent)
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

        layout = self.popup_layout(margins=(20, 18, 20, 18), spacing=14)

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
            "You can choose to install automatically via your terminal AUR helper (paru/yay), "
            "via flatpak, or copy the command and install it manually."
        )
        msg = QLabel(notice_text)
        msg.setWordWrap(True)
        msg.setStyleSheet("color: #a1a1aa;")
        layout.addWidget(msg)

        self.notice_option_combo = QComboBox()
        for label, cmd in WlScreenrecService.get_install_options():
            self.notice_option_combo.addItem(label, cmd)
        layout.addWidget(self.notice_option_combo)

        cmd_box = QLineEdit(WlScreenrecService.get_install_options()[0][1])
        cmd_box.setReadOnly(True)
        self.notice_cmd_box = cmd_box
        self.notice_option_combo.currentIndexChanged.connect(
            lambda idx: cmd_box.setText(self.notice_option_combo.itemData(idx) or ""))
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

        btn_install = QPushButton("Install via Terminal")
        btn_install.setStyleSheet("QPushButton { background: #2563eb; color: #ffffff; border: none; } QPushButton:hover { background: #1d4ed8; }")
        btn_install.clicked.connect(self._launch_install)
        btn_box.addWidget(btn_install)

        layout.addLayout(btn_box)

    def _copy_and_close(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.notice_cmd_box.text())
        self.accept()

    def _launch_install(self):
        WlScreenrecService.launch_terminal_installer(self)
        self.accept()


class ScreenshotLightboxDialog(PopupDialog):
    """Clean, high-resolution 16:9 lightbox modal with navigation and folder opening."""

    def __init__(self, filepaths: list, current_index: int = 0, parent=None):
        super().__init__("Screenshot Preview", parent)
        self.filepaths = [f for f in filepaths if os.path.exists(f)]
        self.current_index = max(0, min(current_index, len(self.filepaths) - 1)) if self.filepaths else 0
        self.gallery_parent = parent

        self.setMinimumSize(850, 580)
        self.resize(1000, 680)

        root = self._popup_root

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


class ScreenshotGalleryDialog(PopupDialog):
    """Custom dark modal dialog for browsing in-game screenshots with 16:9 ratio and Lightbox."""
    def __init__(self, game_id: int, game_name: str, parent=None):
        super().__init__(f"Screenshots - {game_name}", parent)
        self.game_id = game_id
        self.game_name = game_name

        self.setMinimumSize(780, 520)
        self.resize(840, 560)
        self.setSizeGripEnabled(True)

        root_layout = self._popup_root

        # Body container
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(18, 14, 18, 14)
        body_layout.setSpacing(12)

        # Grid scroll area for screenshots
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { background: #0D0F14; border: none; }")

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
        btn_capture.setStyleSheet("QPushButton { background: #161A22; color: #F4F4F5; border: none; border-radius: 6px; padding: 7px 14px; font-weight: 600; } QPushButton:hover { background: #202633; }")
        btn_capture.clicked.connect(self._capture_screen)
        action_layout.addWidget(btn_capture)

        btn_open_folder = QPushButton("Open Directory")
        btn_open_folder.setIcon(get_icon("ph.folder-open-bold"))
        btn_open_folder.setStyleSheet("QPushButton { background: #161A22; color: #F4F4F5; border: none; border-radius: 6px; padding: 7px 14px; font-weight: 600; } QPushButton:hover { background: #202633; }")
        btn_open_folder.clicked.connect(self._open_folder)
        action_layout.addWidget(btn_open_folder)

        action_layout.addStretch()

        btn_close = QPushButton("Close")
        btn_close.setMinimumWidth(80)
        btn_close.setStyleSheet("QPushButton { background: #161A22; color: #F4F4F5; border: none; border-radius: 6px; padding: 7px 16px; font-weight: 600; } QPushButton:hover { background: #202633; }")
        btn_close.clicked.connect(self.accept)
        action_layout.addWidget(btn_close)

        body_layout.addLayout(action_layout)

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
                    border: none;
                    border-radius: 10px;
                }
                QFrame:hover {
                    border: none;
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
            btn_del.setStyleSheet("QPushButton { background: #2A1212; color: #EF4444; border: none; border-radius: 6px; font-size: 11px; padding: 4px; } QPushButton:hover { background: #7F1D1D; color: white; }")
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


class VideoGalleryDialog(PopupDialog):
    """Browse recordings and replay clips belonging to one game."""

    VIDEO_EXTENSIONS = (".mp4", ".mkv", ".webm", ".mov", ".avi")

    def __init__(self, game_id: int, game_name: str, output_dir: str = DEFAULT_RECORDINGS_DIR, parent=None):
        super().__init__(f"Videos - {game_name}", parent)
        self.game_id = game_id
        self.game_name = game_name
        self.video_dir = os.path.abspath(os.path.expanduser(output_dir))
        self.game_prefix = re.sub(r"[^a-z0-9]+", "_", game_name.strip().lower()).strip("_") or "gameplay"

        self.setMinimumSize(700, 480)
        self.resize(820, 560)
        self.setSizeGripEnabled(True)

        root_layout = self._popup_root

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
            detail = QLabel(f"{size_mb:.1f} MB  •  {format_datetime_timestamp(modified, '%H:%M')}")
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


class DiskManagerDialog(PopupDialog):
    """Show a readable, non-blocking summary of sandbox disk usage.

    Archived records do not have a local directory and are intentionally not
    listed.  Directory walks stay on one supervised worker so opening or
    closing this dialog cannot block the GUI or leave an unowned QThread
    behind.
    """
    _sizes_ready = pyqtSignal(list)  # [(name, path, bytes)] sorted desc

    def __init__(self, games: list, parent=None):
        super().__init__("Sandbox Disk Space Manager", parent)
        self.games = list(games or [])

        self.setMinimumSize(760, 520)

        body_layout = self.popup_layout(margins=(18, 14, 18, 14), spacing=12)

        sandbox_dir = ensure_sandbox_dir()
        total_drive, used_drive, free_drive = get_disk_usage(sandbox_dir)

        # Compact summary cards avoid the old QFormLayout's uneven label/value
        # alignment and remain readable when the dialog is resized.
        summary = QGridLayout()
        summary.setHorizontalSpacing(10)
        summary.setVerticalSpacing(8)
        self.lbl_total_sandbox = self._summary_value("Calculating…")
        self.lbl_drive_free = self._summary_value(format_size(free_drive))
        self.lbl_drive_total = self._summary_value(format_size(total_drive))
        summary.addWidget(self._summary_label("Installed game storage"), 0, 0)
        summary.addWidget(self._summary_label("Drive free"), 0, 1)
        summary.addWidget(self._summary_label("Drive capacity"), 0, 2)
        summary.addWidget(self.lbl_total_sandbox, 1, 0)
        summary.addWidget(self.lbl_drive_free, 1, 1)
        summary.addWidget(self.lbl_drive_total, 1, 2)
        for column in range(3):
            summary.setColumnStretch(column, 1)
        body_layout.addLayout(summary)

        self.drive_bar = QProgressBar()
        self.drive_bar.setRange(0, 1000)
        self.drive_bar.setTextVisible(False)
        self.drive_bar.setFixedHeight(8)
        used_ratio = (used_drive / total_drive) if total_drive > 0 else 0.0
        self.drive_bar.setValue(max(0, min(1000, int(used_ratio * 1000))))
        self.drive_bar.setToolTip(
            f"{format_size(used_drive)} used · {format_size(free_drive)} free"
        )
        self.drive_bar.setStyleSheet("""
            QProgressBar { background: #202026; border: none; border-radius: 4px; }
            QProgressBar::chunk { background: #4B9FFF; border-radius: 4px; }
        """)
        body_layout.addWidget(self.drive_bar)

        path_label = QLabel(f"Sandbox root: {sandbox_dir}")
        path_label.setStyleSheet("color: #8E8E93; font-size: 11px;")
        path_label.setToolTip(sandbox_dir)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body_layout.addWidget(path_label)

        lbl_rank = QLabel("Installed games by local storage")
        lbl_rank.setStyleSheet("color: #FFFFFF; font-weight: 700; padding-top: 4px;")
        body_layout.addWidget(lbl_rank)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: #18181B; border: none; }")

        list_widget = QWidget()
        list_widget.setObjectName("diskUsageList")
        self._game_rows_layout = QVBoxLayout(list_widget)
        self._game_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._game_rows_layout.setSpacing(4)

        lbl_wait = QLabel("Calculating game sizes…")
        lbl_wait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_wait.setStyleSheet("color: #8E8E93; font-size: 12px; padding: 22px;")
        self._game_rows_layout.addWidget(lbl_wait)
        self._placeholder_row = lbl_wait

        # Only real, unique directories are measurable. This also prevents a
        # duplicated database row from producing duplicated disk rows.
        measured_games = []
        seen_paths = set()
        for game in self.games:
            try:
                if hasattr(game, "path"):
                    name, path = str(game.name or "Unknown game"), str(game.path or "")
                else:
                    name = str(game[1] if len(game) > 1 else "Unknown game")
                    path = str(game[2] if len(game) > 2 else "")
            except (IndexError, TypeError, AttributeError):
                continue
            path = os.path.realpath(path) if path else ""
            if not path or not os.path.isdir(path) or path in seen_paths:
                continue
            seen_paths.add(path)
            measured_games.append((name, path))

        size_worker = [None]

        def _compute_sizes():
            results = []
            for name, path in measured_games:
                if size_worker[0] is not None and size_worker[0].isInterruptionRequested():
                    return []
                try:
                    sz = get_dir_size(
                        path,
                        cancel_callback=(
                            size_worker[0].isInterruptionRequested
                            if size_worker[0] is not None
                            else None
                        ),
                    )
                except Exception:
                    sz = 0
                if size_worker[0] is not None and size_worker[0].isInterruptionRequested():
                    return []
                results.append((name, path, sz))
            results.sort(key=lambda x: x[2], reverse=True)
            return results
        self._task_supervisor = TaskSupervisor(self, logger)
        size_worker[0] = self._task_supervisor.start(
            "SafeLauncher-DiskSizes", _compute_sizes, self._on_sizes_ready
        )

        scroll.setWidget(list_widget)
        body_layout.addWidget(scroll)

        # Close button
        btn_close = QPushButton("Close")
        btn_close.setObjectName("popupSecondary")
        btn_close.clicked.connect(self.accept)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        body_layout.addLayout(btn_row)

        self.setStyleSheet("""
            QDialog#safeLauncherPopup QWidget#diskUsageList { background: #18181B; }
            QDialog#safeLauncherPopup QFrame#diskUsageRow {
                background: #202026;
                border: none;
                border-radius: 6px;
            }
            QDialog#safeLauncherPopup QPushButton#popupSecondary {
                background: #222228;
                color: #F4F4F5;
                border: 1px solid #34343C;
                border-radius: 6px;
                padding: 6px 16px;
            }
            QDialog#safeLauncherPopup QPushButton#popupSecondary:hover {
                background: #2B2B32;
            }
        """)

    @staticmethod
    def _summary_label(text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setStyleSheet("color: #8E8E93; font-size: 10px; font-weight: 700;")
        return label

    @staticmethod
    def _summary_value(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #FFFFFF; font-size: 16px; font-weight: 700;")
        return label

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
            if not results:
                empty = QLabel("No installed sandbox game directories found.")
                empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
                empty.setStyleSheet("color: #8E8E93; padding: 24px;")
                self._game_rows_layout.addWidget(empty)
                return
            for name, path, sz in results:
                self._game_rows_layout.addWidget(self._build_size_row(name, path, sz))
        except RuntimeError:
            pass  # dialog already destroyed

    def closeEvent(self, event):
        self._task_supervisor.cancel_all(100)
        if self._task_supervisor.has_running_tasks():
            QTimer.singleShot(100, self.close)
            event.ignore()
            return
        super().closeEvent(event)

    def _build_size_row(self, name: str, path: str, sz: int) -> QFrame:
        row_frame = QFrame()
        row_frame.setObjectName("diskUsageRow")
        r_layout = QHBoxLayout(row_frame)
        r_layout.setContentsMargins(12, 8, 12, 8)
        r_layout.setSpacing(10)

        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("color: #FFFFFF; font-weight: 700; font-size: 12px;")
        name_lbl.setToolTip(path)
        r_layout.addWidget(name_lbl)

        r_layout.addStretch()

        size_badge = QLabel(format_size(sz))
        size_badge.setStyleSheet("color: #A1A1AA; font-size: 11px; font-weight: 600;")
        r_layout.addWidget(size_badge)

        btn_folder = QPushButton("Open")
        btn_folder.setToolTip("Open this game's sandbox directory")
        btn_folder.clicked.connect(lambda _, p=path: self._open_path(p))
        r_layout.addWidget(btn_folder)
        return row_frame

    def _open_path(self, path: str):
        try:
            if path and os.path.exists(path):
                subprocess.Popen(["xdg-open", path], env=host_process_env())
        except Exception:
            pass
