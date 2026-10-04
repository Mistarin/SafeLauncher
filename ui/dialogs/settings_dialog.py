from ui.dialogs.storage_dialog import DiskManagerDialog
from ui.dialogs.media_dialogs import ScreenshotLightboxDialog, ScreenshotGalleryDialog, VideoGalleryDialog
from ui.dialogs.plugin_install_dialog import PluginInstallNoticeDialog
from ui.dialogs.settings_pages.plugins import PluginsSettingsPage, PluginsPageInputs
from ui.dialogs.settings_pages.cloud import CloudSettingsPage, CloudPageInputs
from ui.dialogs.settings_pages.storage import StorageSettingsPage, StoragePageInputs
from ui.dialogs.settings_pages.security import SecuritySettingsPage, SecurityPageInputs
from ui.dialogs.settings_pages.general import GeneralSettingsPage, GeneralPageInputs
import os
import re
import shutil
import subprocess
import html
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFormLayout,
    QFileDialog, QWidget, QScrollArea, QGridLayout, QFrame, QStackedWidget,
    QProgressBar, QSizeGrip, QSizePolicy, QCheckBox, QComboBox, QMessageBox, QSpinBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QSettings, QSize, QTimer
from PyQt6.QtGui import QFont, QIcon, QPixmap, QKeySequence
from PyQt6.QtWidgets import QKeySequenceEdit

from core.disk_utils import get_dir_size, get_disk_usage, format_size
from core.host_process import host_process_env
from database import _APP_DATA_DIR
from core.desktop_integration import (
    install_safelauncher_desktop_entry,
    remove_safelauncher_desktop_entry,
    is_desktop_entry_installed,
    is_desktop_shortcut_installed,
    is_startup_enabled,
    set_startup_enabled,
)
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
from ui.icons import get_icon, get_app_icon, get_icon_pixmap
from typing import Any, Optional
from ui.icons import LOGO_PATH
from ui.components.sidebar import DialogTitleBar
from ui.components.popup_shell import PopupDialog
from ui.components.check_field import CheckField as QCheckBox
from ui.components.sort_combo import SortComboBox
from ui.maintenance_dialogs import RuntimeInventoryDialog
from ui.dialogs.game_dialogs import ensure_sandbox_dir
from ui.dialogs.save_conflict_dialog import format_bytes


from core.version import APP_VERSION, MIN_CONVEX_BACKEND_VERSION
from core.updater import check_for_updates, download_and_apply_appimage_update, restart_application, is_appimage
from core.cloud_account_service import CloudAccountService, CloudAccountSnapshot
from core.cloud_center_service import CloudOverview
from core.cloud_backend import normalize_site_url
from core.cloud_detector import detect_local_cloud_installation
from core.safe_thread import TaskSupervisor
from core.settings_session import CloudSettingsSession
from ui.settings_dialog_services import SettingsDialogServices
from ui.managed_task_controller import ManagedTaskController
from ui.settings_account_controller import SettingsAccountController
from core.operation_registry import OperationRegistry
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

    def __init__(self, user_name: str, proton_path: str = "", show_welcome_wizard: bool = False, gpu_config: Optional[GpuRecorderConfig] = None, screenshot_screen: str = "current", screenshot_hotkey: str = "F12", cloud_saves_dir: str = "", parent=None, date_format: str = "", request_manager=None, initial_tab: int | None = None, *, services=None):
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
        self.services = services or SettingsDialogServices.from_parent(parent, request_manager)
        self._task_supervisor = TaskSupervisor(self, logger, worker_registry=self.services.worker_registry)
        self.request_manager = self.services.request_manager
        self.cloud_account_service = self.services.cloud_account or CloudAccountService(
            request_manager=self.request_manager,
        )
        self.cloud_center_service = self.services.cloud_center
        self._closing = False
        self._done_result = None
        registry = self.services.operation_registry or OperationRegistry(parent=self)
        self._managed_tasks = (ManagedTaskController(self.request_manager, registry,
                              category="Settings", parent=self) if self.request_manager is not None else None)
        self._resource_bindings: dict[str, ResourceBinding] = {}
        self._cloud_overview_binding: ResourceBinding | None = None
        self._account_probe_generation = 0
        self._health_probe_generation = 0
        self._profile_action_status_custom = False
        self._cloud_settings_committed = False
        self._cloud_session = CloudSettingsSession(
            QSettings("SafeLauncher", "SafeLauncher"), self.cloud_account_service,
            get_secret=get_secret, set_secret=set_secret, delete_secret=delete_secret)
        self._account_controller = SettingsAccountController(
            self.cloud_account_service, self.request_manager,
            allowed=self._dialog_network_allowed, show_status=self.accountStatusReady.emit,
            start_task=self._start_managed_task, parent=self)
        self._cloud_settings_snapshot = self._capture_cloud_settings()
        self._health_technical_error = ""
        self._probe_technical_error = ""

        self.setWindowIcon(QIcon(LOGO_PATH) if os.path.exists(LOGO_PATH) else QIcon())
        available = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
        available_width = max(1, available.width() - 32) if available else None
        available_height = max(1, available.height() - 48) if available else None
        min_width = min(820, available_width) if available_width else 820
        min_height = min(600, available_height) if available_height else 600
        self.setMinimumSize(min_width, min_height)
        self.resize(
            min(1040, max(min_width, available_width)) if available_width else 1040,
            min(720, max(min_height, available_height)) if available_height else 720,
        )
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMaximized)
        # PopupDialog is frameless and already has a controlled custom footer
        # grip. The native QDialog size grip conflicts with that surface on
        # some Wayland/X11 compositors and can crash while the popup is shown.

        self.setStyleSheet("""
            QDialog {
                background: #111113;
                color: #FFFFFF;
                border: none;
                border-radius: 12px;
            }
            QWidget#settingsPage {
                background: #121214;
            }
            QFrame#settingsSection {
                background: #18181B;
                border: none;
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
                background: transparent;
                color: #A1A1AA;
                border: none;
                padding: 4px 0;
                min-height: 20px;
            }
            QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QKeySequenceEdit {
                background: #1C1C1F;
                color: #FFFFFF;
                border: none;
                border-bottom: 1px solid #303036;
                border-radius: 6px;
                padding: 0 11px;
                min-height: 36px;
                font-size: 12px;
            }
            QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
            QComboBox:focus, QSpinBox:focus, QKeySequenceEdit:focus {
                border-color: #4B9FFF;
                background: #242428;
                border-bottom-color: #3B9FE8;
            }
            QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
            QComboBox:disabled, QSpinBox:disabled, QKeySequenceEdit:disabled {
                background: #161619;
                color: #71717A;
                border-bottom-color: #252529;
            }
            QComboBox QAbstractItemView {
                background: #18181B;
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
            QPushButton {
                background: #18181B;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 0 14px;
                min-height: 36px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: #202024;
            }
            QPushButton:pressed {
                background: #121214;
            }
            QPushButton:disabled {
                background: #121214;
                color: #71717A;
                border: none;
            }
            QCheckBox {
                spacing: 9px;
                color: #F4F4F5;
                padding: 5px 0;
            }
            QCheckBox::indicator {
                width: 17px; height: 17px;
                border-radius: 5px;
                border: 1px solid #4A4A54;
                background: #18181B;
            }
            QCheckBox::indicator:checked {
                background: #3B9FE8;
                border-color: #3B9FE8;
            }
            QFrame#settingsDivider {
                background: #252A31;
                border: none;
                min-height: 1px;
                max-height: 1px;
            }
            QWidget#propertyHint {
                background: transparent;
            }
            QLabel#propertyHintText {
                color: #71717A;
                background: transparent;
                font-size: 11px;
            }
            QToolButton#propertyInfo {
                background: transparent;
                color: #71717A;
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
        nav_frame.setStyleSheet("QFrame#settingsNav { background: #18181B; border: none; border-radius: 9px; }")
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
                    border: none;
                }
            """)
            btn.clicked.connect(lambda _, i=idx: self._switch_tab(i))
            nav_bar.addWidget(btn)
            self.tab_buttons.append(btn)

        # Cloud is an account-wide surface owned by Cloud Center. Keep the
        # page available for Cloud Center's technical-settings handoff and
        # compatibility callers, but remove it as a standalone Settings tab.
        self.tab_buttons[3].setVisible(False)

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
        self.btn_cancel = btn_cancel
        btn_cancel.clicked.connect(self.reject)
        bottom_bar.addWidget(btn_cancel)

        btn_save = QPushButton("Save")
        btn_save.setObjectName("settingsPrimaryButton")
        self.btn_save = btn_save
        btn_save.setStyleSheet("""
            QPushButton {
                background: #3B9FE8; color: #ffffff; border: none;
                border-radius: 6px; min-height: 36px; padding: 0 18px;
                font-size: 12px; font-weight: 700;
            }
            QPushButton:hover { background: #2789D0; }
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
        page_style = "QWidget#settingsPage { background: #121214; }"
        section_style = (
            "QFrame#settingsSection { background: #18181B; border: none; "
            "border-radius: 10px; }"
        )
        field_style = (
            "QLineEdit, QComboBox, QSpinBox, QKeySequenceEdit { "
            "background: #1C1C1F; color: #FFFFFF; border: none; "
            "border-bottom: 1px solid #303036; border-radius: 6px; "
            "padding: 0 11px; min-height: 36px; } "
            "QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QKeySequenceEdit:focus { "
            "background: #242428; border-bottom-color: #3B9FE8; }"
        )
        property_style = (
            "QLabel#propertyLabel, QLabel#propertyValue { "
            "background: transparent; color: #A1A1AA; border: none; "
            "border-radius: 0; padding: 4px 0; min-height: 20px; }"
        )
        button_style = (
            "QPushButton { background: #18181B; color: #F4F4F5; "
            "border: none; border-radius: 6px; padding: 0 14px; "
            "min-height: 36px; font-size: 12px; font-weight: 600; } "
            "QPushButton:hover { background: #202024; } "
            "QPushButton:pressed { background: #121214; } "
            "QPushButton:disabled { background: #121214; color: #71717A; border: none; }"
        )
        primary_button_style = (
            "QPushButton { background: #3B9FE8; color: #FFFFFF; border: none; "
            "border-radius: 6px; min-height: 36px; padding: 0 18px; "
            "font-size: 12px; font-weight: 700; } "
            "QPushButton:hover { background: #55ACED; } "
            "QPushButton:pressed { background: #2789D0; }"
        )
        destructive_button_style = (
            "QPushButton { background: #3A171B; color: #FFB4AE; "
            "border: none; border-radius: 6px; min-height: 36px; "
            "padding: 0 14px; font-size: 12px; font-weight: 600; } "
            "QPushButton:hover { background: #542027; color: #FFFFFF; } "
            "QPushButton:pressed { background: #2A1014; } "
            "QPushButton:disabled { background: #21171A; color: #80696C; border: none; }"
        )
        navigation_button_style = (
            "QPushButton { background: transparent; color: #A1A1AA; "
            "border: none; border-radius: 7px; "
            "padding: 8px 12px; font-size: 12px; font-weight: 600; } "
            "QPushButton:hover { background: transparent; color: #FFFFFF; } "
            "QPushButton:checked { background: #2A2A31; color: #FFFFFF; }"
        )
        divider_style = (
            "QFrame#settingsDivider { background: #252A31; border: none; "
            "min-height: 1px; max-height: 1px; }"
        )

        for page_scroll in (
            self.page_general, self.page_security, self.page_storage,
            self.page_cloud, self.page_plugins,
        ):
            page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            page_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
            page = page_scroll.widget()
            if page is None:
                continue
            page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
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
                    "color: #71717A; font-size: 11px; }"
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
                        "color: #71717A; font-size: 12px; }"
                    )
                elif label.objectName() == "settingsStatus":
                    label.setStyleSheet(
                        "QLabel#settingsStatus { background: transparent; color: #A1A1AA; "
                        "font-size: 12px; padding: 0; }"
                    )
                elif label.objectName() == "settingsMetaValue":
                    label.setStyleSheet(
                        "QLabel#settingsMetaValue { background: transparent; color: #A1A1AA; "
                        "font-size: 13px; padding: 0; }"
                    )
            for button in page.findChildren(QPushButton):
                role = button.property("settingsButtonRole")
                if button.objectName() == "settingsTab" or role == "navigation":
                    continue
                if role == "accent":
                    continue
                if role == "primary":
                    button.setStyleSheet(primary_button_style)
                elif role == "destructive":
                    button.setStyleSheet(destructive_button_style)
                else:
                    button.setStyleSheet(button_style)

        for button in self.tab_buttons:
            button.setProperty("settingsButtonRole", "navigation")
            button.setStyleSheet(navigation_button_style)

        for button in self.findChildren(QPushButton):
            role = button.property("settingsButtonRole")
            if button.objectName() == "settingsPrimaryButton" or role == "primary":
                button.setStyleSheet(primary_button_style)
            elif button.objectName() == "settingsActionButton" or role == "destructive":
                button.setStyleSheet(
                    destructive_button_style if role == "destructive" else button_style
                )
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
        form.setVerticalSpacing(12)
        form.setHorizontalSpacing(16)
        self.polish_property_form(form)
        for row in range(form.rowCount()):
            field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
            field = field_item.widget() if field_item is not None else None
            if field is not None:
                field.setMinimumWidth(0)
            elif field_item is not None and field_item.layout() is not None:
                for index in range(field_item.layout().count()):
                    child_item = field_item.layout().itemAt(index)
                    child = child_item.widget() if child_item is not None else None
                    if child is not None:
                        child.setMinimumWidth(0)

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
            f"color: {'#F05D6C' if error else '#A1A1AA'}; font-size: 12px;"
        )
        self.lbl_profile_action_status.setText(str(message or ""))

    # -------------------------------------------------------------
    # TAB 1: General & Profile
    # -------------------------------------------------------------
    def _create_general_page(self) -> QWidget:
        page = GeneralSettingsPage(GeneralPageInputs(
            add_section_divider=self._add_section_divider,
            browse_proton=self._browse_proton,
            manual_check_updates=self._manual_check_updates,
            polish_settings_form=self._polish_settings_form,
            toggle_desktop_entry=self._toggle_desktop_entry,
            wrap_settings_sections=self._wrap_settings_sections,
            date_format=self.date_format,
            info_hint=self.info_hint,
            profile_auth_requested=self.profile_auth_requested,
            profile_publish_requested=self.profile_publish_requested,
            profile_resync_requested=self.profile_resync_requested,
            proton_path=self.proton_path,
            show_welcome_wizard=self.show_welcome_wizard,
            user_name=self.user_name,
        ), parent=self)
        self.btn_check_app_updates = page.btn_check_app_updates
        self.btn_desktop_entry = page.btn_desktop_entry
        self.btn_profile_auth = page.btn_profile_auth
        self.btn_profile_publish = page.btn_profile_publish
        self.btn_profile_resync = page.btn_profile_resync
        self.chk_achievement_desktop = page.chk_achievement_desktop
        self.chk_achievement_notifications = page.chk_achievement_notifications
        self.chk_launch_startup = page.chk_launch_startup
        self.chk_welcome = page.chk_welcome
        self.combo_date_format = page.combo_date_format
        self.lbl_desktop_status = page.lbl_desktop_status
        self.lbl_profile_action_status = page.lbl_profile_action_status
        self.lbl_update_status = page.lbl_update_status
        self.name_input = page.name_input
        self.proton_input = page.proton_input
        self.set_profile_action_state(False, False, False, False)
        self._refresh_desktop_integration_controls()
        return page

    # -------------------------------------------------------------
    # TAB 2: Container Security (Clean flat header & diagnostics)
    # -------------------------------------------------------------
    def _create_security_page(self) -> QWidget:
        page = SecuritySettingsPage(SecurityPageInputs(
            add_section_divider=self._add_section_divider,
            copy_probe_details=self._copy_probe_details,
            execute_live_probe=self._execute_live_probe,
            polish_settings_grid=self._polish_settings_grid,
            wrap_settings_sections=self._wrap_settings_sections,
        ), parent=self)
        self.btn_copy_probe_details = page.btn_copy_probe_details
        self.probe_output_lbl = page.probe_output_lbl
        return page

    def _execute_live_probe(self, button: QPushButton):
        button.setEnabled(False)
        button.setText("Testing isolation…")
        self._probe_technical_error = ""
        self.btn_copy_probe_details.setVisible(False)
        self.probe_output_lbl.setStyleSheet("color: #A1A1AA; font-weight: 500;")
        self.probe_output_lbl.setText("Running a short sandbox verification in the background…")

        def _apply(result):
            button.setEnabled(True)
            button.setText("Run Sandbox Isolation Test")
            self._probe_technical_error = str(result.get("details") or "")
            self.btn_copy_probe_details.setVisible(bool(self._probe_technical_error))
            if result.get("success"):
                self.probe_output_lbl.setStyleSheet("color: #4ade80; font-weight: bold;")
                self.probe_output_lbl.setText(f"Pass: {result.get('message', 'Sandbox verification passed.')}")
            else:
                self.probe_output_lbl.setStyleSheet("color: #f87171; font-weight: bold;")
                self.probe_output_lbl.setText(f"Fail: {result.get('message', 'Sandbox verification failed.')}")

        self._start_managed_task(
            "SafeLauncher-SandboxVerification",
            run_live_sandbox_verification,
            _apply,
            allow_offline=True,
        )

    def _copy_probe_details(self) -> None:
        if self._probe_technical_error:
            QApplication.clipboard().setText(self._probe_technical_error)
            self.btn_copy_probe_details.setToolTip("Technical details copied to the clipboard")

    # -------------------------------------------------------------
    # TAB 3: Storage & Logs
    # -------------------------------------------------------------
    def _create_storage_page(self) -> QWidget:
        page = StorageSettingsPage(StoragePageInputs(
            add_section_divider=self._add_section_divider,
            clear_diagnostics=self._clear_diagnostics,
            is_closing=lambda: self._closing,
            export_runtime_diagnostics=self._export_runtime_diagnostics,
            on_sandbox_size_ready=self._on_sandbox_size_ready,
            on_sandbox_size_error=self._on_sandbox_size_error,
            open_folder=self._open_folder,
            polish_settings_form=self._polish_settings_form,
            sandbox_size_ready=self._sandbox_size_ready,
            start_managed_task=self._start_managed_task,
            wrap_settings_sections=self._wrap_settings_sections,
            screenshot_screen=self.screenshot_screen,
        ), parent=self)
        self.combo_screenshot_screen = page.combo_screenshot_screen
        self.lbl_diag_info = page.lbl_diag_info
        self.lbl_sandbox_size = page.lbl_sandbox_size
        return page

    # -------------------------------------------------------------
    # TAB 4: Cloud (account, backend mode, endpoints)
    # -------------------------------------------------------------
    def _create_cloud_page(self) -> QWidget:
        page = CloudSettingsPage(CloudPageInputs(
            refresh_cloud_conflict_summary=self._refresh_cloud_conflict_summary,
            add_section_divider=self._add_section_divider,
            apply_account_status=self._apply_account_status,
            apply_backend_deploy_result=self._apply_backend_deploy_result,
            apply_backend_health=self._apply_backend_health,
            apply_manual_update_result=self._apply_manual_update_result,
            browse_cloud_dir=self._browse_cloud_dir,
            cloud_connect=self._cloud_connect,
            cloud_disconnect=self._cloud_disconnect,
            copy_health_details=self._copy_health_details,
            forget_deploy_key=self._forget_deploy_key,
            on_app_download_failed=self._on_app_download_failed,
            on_app_download_finished=self._on_app_download_finished,
            on_app_download_progress=self._on_app_download_progress,
            on_cloud_mode_changed=self._on_cloud_mode_changed,
            open_cloud_center_from_settings=self._open_cloud_center_from_settings,
            open_cloud_wizard=self._open_cloud_wizard,
            open_conflict_review=self._open_conflict_review,
            open_convex_dashboard=self._open_convex_dashboard,
            open_deploy_key_wizard=self._open_deploy_key_wizard,
            polish_settings_form=self._polish_settings_form,
            polish_settings_grid=self._polish_settings_grid,
            redeploy_backend=self._redeploy_backend,
            refresh_account_status=self._refresh_account_status,
            refresh_backend_health=self._refresh_backend_health,
            wrap_settings_sections=self._wrap_settings_sections,
            accountStatusReady=self.accountStatusReady,
            appDownloadFailed=self.appDownloadFailed,
            appDownloadFinished=self.appDownloadFinished,
            appDownloadProgress=self.appDownloadProgress,
            appUpdateReady=self.appUpdateReady,
            backendDeployReady=self.backendDeployReady,
            backendHealthReady=self.backendHealthReady,
            cloud_account_service=self.cloud_account_service,
            cloud_saves_dir=self.cloud_saves_dir,
            info_hint=self.info_hint,
        ), parent=self)
        self.bar_quota_settings = page.bar_quota_settings
        self.btn_copy_health_details = page.btn_copy_health_details
        self.btn_logout = page.btn_logout
        self.btn_open_cloud_center = page.btn_open_cloud_center
        self.btn_open_dashboard = page.btn_open_dashboard
        self.btn_probe_health = page.btn_probe_health
        self.btn_redeploy = page.btn_redeploy
        self.btn_refresh_quota = page.btn_refresh_quota
        self.btn_review_conflicts = page.btn_review_conflicts
        self.btn_sign_in = page.btn_sign_in
        self.btn_wizard = page.btn_wizard
        self.card_backend_health = page.card_backend_health
        self.chk_offline_mode = page.chk_offline_mode
        self.combo_cloud_mode = page.combo_cloud_mode
        self.edit_cloud_saves_dir = page.edit_cloud_saves_dir
        self.edit_cloud_secret_key = page.edit_cloud_secret_key
        self.edit_convex_deploy_key = page.edit_convex_deploy_key
        self.edit_convex_url = page.edit_convex_url
        self.edit_device_name = page.edit_device_name
        self.health_action_row = page.health_action_row
        self.lbl_account_status = page.lbl_account_status
        self.lbl_connected_devices = page.lbl_connected_devices
        self.lbl_health_latency = page.lbl_health_latency
        self.lbl_health_status = page.lbl_health_status
        self.lbl_health_version = page.lbl_health_version
        self.lbl_offline_mode_help = page.lbl_offline_mode_help
        self.lbl_version_warning = page.lbl_version_warning
        self.spin_sync_workers = page.spin_sync_workers
        self._refresh_account_status()
        self._refresh_backend_health()
        self._refresh_cloud_conflict_summary()
        return page

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
                "Open cloud storage management to review current cloud-save conflicts"
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
                    color = "#3B9FE8" if pct < 0.75 else ("#E5A93D" if pct < 0.92 else "#F05D6C")
                    self.lbl_account_status.setStyleSheet(
                        f"color: {'#E5A93D' if pct >= 0.92 else '#9ca3af'};"
                    )
                    style = (
                        "QProgressBar { background: #18181B; border: 1px solid #202024;"
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
        page = PluginsSettingsPage(PluginsPageInputs(
            add_section_divider=self._add_section_divider,
            browse_recordings_dir=self._browse_recordings_dir,
            copy_install_command=self._copy_install_command,
            on_install_option_changed=self._on_install_option_changed,
            open_install_notice=self._open_install_notice,
            polish_settings_form=self._polish_settings_form,
            wrap_settings_sections=self._wrap_settings_sections,
            gpu_config=self.gpu_config,
            info_hint=self.info_hint,
            screenshot_hotkey=self.screenshot_hotkey,
        ), parent=self)
        self.chk_audio = page.chk_audio
        self.chk_overlay = page.chk_overlay
        self.chk_plugin_enabled = page.chk_plugin_enabled
        self.combo_audio_input = page.combo_audio_input
        self.combo_audio_output = page.combo_audio_output
        self.combo_bitrate = page.combo_bitrate
        self.combo_codec = page.combo_codec
        self.combo_mode = page.combo_mode
        self.combo_recording_monitor = page.combo_recording_monitor
        self.combo_replay = page.combo_replay
        self.edit_hotkey = page.edit_hotkey
        self.edit_screenshot_hotkey = page.edit_screenshot_hotkey
        if hasattr(page, "install_cmd_box"):
            self.install_cmd_box = page.install_cmd_box
            self.install_option_combo = page.install_option_combo
        self.output_dir_input = page.output_dir_input
        return page

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

    def _refresh_desktop_integration_controls(self):
        installed = is_desktop_entry_installed()
        shortcut = is_desktop_shortcut_installed()
        if installed:
            location = "Applications menu"
            if shortcut:
                location += " + Desktop shortcut"
            self.lbl_desktop_status.setText(f"Installed in {location}.")
            self.btn_desktop_entry.setText("Remove integration")
            self.btn_desktop_entry.setProperty("settingsButtonRole", "destructive")
        else:
            self.lbl_desktop_status.setText("Not installed in the Applications menu.")
            self.btn_desktop_entry.setText("Add to Applications menu")
            self.btn_desktop_entry.setProperty("settingsButtonRole", "primary")
        if hasattr(self, "page_security"):
            self._apply_settings_surfaces()
        self.btn_desktop_entry.style().unpolish(self.btn_desktop_entry)
        self.btn_desktop_entry.style().polish(self.btn_desktop_entry)

    def _toggle_desktop_entry(self):
        if is_desktop_entry_installed():
            success, message = remove_safelauncher_desktop_entry()
        else:
            success, message = install_safelauncher_desktop_entry()
        if not success:
            QMessageBox.warning(self, "Desktop Integration", message)
            return
        self._refresh_desktop_integration_controls()

    def _save(self):
        display_name = self.name_input.text().strip()
        if not display_name:
            self.name_input.setFocus()
            self.name_input.setStyleSheet("border: 1px solid #F05D6C;")
            self.lbl_profile_action_status.setStyleSheet("color: #F05D6C; font-size: 12px;")
            self.lbl_profile_action_status.setText("Display name cannot be empty.")
            self._switch_tab(0)
            return

        self.name_input.setStyleSheet("")
        if display_name:
            startup_success, startup_message = set_startup_enabled(self.chk_launch_startup.isChecked())
            if not startup_success:
                QMessageBox.warning(self, "Startup Integration", startup_message)
                return
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
            settings.setValue("cloud_saves_dir", cloud_dir)

            dev_name = self.edit_device_name.text().strip()
            settings.setValue("cloud_device_name", dev_name)
            settings.setValue("cloud_sync_workers", self.spin_sync_workers.value())

            if hasattr(self, "chk_achievement_notifications"):
                settings.setValue("achievement_notifications_enabled", self.chk_achievement_notifications.isChecked())
            if hasattr(self, "chk_achievement_desktop"):
                settings.setValue("achievement_desktop_notifications", self.chk_achievement_desktop.isChecked())
            if hasattr(self, "combo_date_format"):
                self.date_format = self.combo_date_format.currentData() or get_date_format_key()
                settings.setValue("date_format", self.date_format)

            self._cloud_settings_committed = True
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
        # Cloud settings are committed by Save.  Do not mutate QSettings or the
        # shared backend merely because the user browsed the combo box; this is
        # what makes Cancel behave like Cancel everywhere else in Settings.
        self._pending_cloud_mode = self.combo_cloud_mode.itemData(index) or "local"

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
                settings.setValue("cloud_mode", "convex")
                self.combo_cloud_mode.setCurrentIndex(1)
                self._remember_cloud_settings_as_baseline()
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
                self._remember_cloud_settings_as_baseline()
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
                self._remember_cloud_settings_as_baseline()
                self._refresh_backend_health()

    def _open_cloud_center_from_settings(self):
        """Open the canonical account-wide cloud dashboard."""
        open_center = self.services.open_cloud_center
        if open_center is None:
            QMessageBox.warning(
                self,
                "Cloud Center",
                "Cloud Center is available from the main SafeLauncher window.",
            )
            return
        self.hide()
        try:
            open_center()
        finally:
            self.show()
            self._remember_cloud_settings_as_baseline()
            self._refresh_account_status()
            self._refresh_backend_health()

    def _open_account_manager(self):
        """Compatibility alias for older callers; Cloud Center is canonical."""
        self._open_cloud_center_from_settings()

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
        settings.setValue("cloud_mode", "convex")
        if key:
            set_secret("cloud_secret_key", key)
        else:
            delete_secret("cloud_secret_key")

        self.cloud_account_service.reset_backend()
        self.cloud_account_service.set_mode("convex")
        self.combo_cloud_mode.setCurrentIndex(1)
        self._remember_cloud_settings_as_baseline()
        self.lbl_account_status.setText("Connecting to cloud…")
        self._refresh_account_status()
        self._refresh_backend_health()
        self._refresh_cloud_conflict_summary()

    def _cloud_disconnect(self):
        """Revert cloud backend to local folder sync."""
        self.cloud_account_service.set_mode("local")
        self.cloud_account_service.reset_backend()
        QSettings("SafeLauncher", "SafeLauncher").setValue("cloud_mode", "local")
        self.combo_cloud_mode.setCurrentIndex(0)
        self._remember_cloud_settings_as_baseline()
        self.accountStatusReady.emit("Disconnected (using Local sync).")
        self._refresh_backend_health()
        self._refresh_cloud_conflict_summary()

    def _capture_cloud_settings(self):
        return self._cloud_session.capture()

    def _restore_cloud_settings(self):
        self._cloud_session.committed = self._cloud_settings_committed
        self._cloud_session.rollback()

    def _remember_cloud_settings_as_baseline(self):
        self._cloud_session.remember()
        self._cloud_settings_snapshot = self._cloud_session.baseline

    def reject(self):
        self.done(QDialog.DialogCode.Rejected)

    def done(self, result):
        if self._done_result is not None:
            return
        self._done_result = result
        self._closing = True
        self._account_probe_generation += 1
        self._health_probe_generation += 1
        self._account_controller.dispose()
        if self._cloud_overview_binding is not None:
            self._cloud_overview_binding.close()
            self._cloud_overview_binding = None
        for binding in tuple(self._resource_bindings.values()):
            binding.close()
        self._resource_bindings.clear()
        if self._managed_tasks is not None:
            self._managed_tasks.dispose()
        self._task_supervisor.cancel_all(0)
        self.hide()
        self._finish_settings_done()

    def _finish_settings_done(self):
        if (self._task_supervisor.has_running_tasks()
                or (self._managed_tasks is not None and self._managed_tasks.has_pending_work())):
            QTimer.singleShot(50, self._finish_settings_done)
            return
        self._restore_cloud_settings()
        super().done(self._done_result)

    def closeEvent(self, event):
        self.reject()
        event.ignore()

    def _start_managed_task(self, name, work, on_complete, *, allow_offline=False, on_error=None):
        if self._closing:
            return None
        if self._managed_tasks is not None:
            return self._managed_tasks.start(name, work, on_complete,
                allow_offline=allow_offline,
                on_error=on_error or (lambda error: on_complete({"error": error})))
        # Only manager-less callers use QThread compatibility tasks.
        return self._task_supervisor.start(name, work,
            lambda value: on_complete(value) if not self._closing else None)

    def _refresh_account_status(self):
        self._account_controller.refresh()

    def _refresh_backend_health(self):
        """Probe backend health endpoint, measure latency, and check version parity."""
        self._health_probe_generation += 1
        generation = self._health_probe_generation
        self._health_technical_error = ""
        self.btn_copy_health_details.setVisible(False)
        self.btn_probe_health.setEnabled(False)
        self.btn_probe_health.setText("Checking…")
        if not self._dialog_network_allowed():
            self.lbl_health_status.setText("Offline mode")
            self.lbl_health_latency.setText("--")
            self.lbl_health_version.setText("Not checked")
            self.lbl_version_warning.setText(
                "Offline mode is enabled; backend health is not probed."
            )
            self.btn_probe_health.setEnabled(True)
            self.btn_probe_health.setText("Check Health")
            return
        url = normalize_site_url(self.edit_convex_url.text())
        key = self.edit_cloud_secret_key.text().strip()
        if not url:
            from core.cloud_backend import get_site_url
            url = get_site_url()

        self.lbl_health_status.setText("Probing backend…")
        self.lbl_health_latency.setText("...")
        self.lbl_health_latency.setStyleSheet("background: #202024; color: #A1A1AA; padding: 3px 8px; border-radius: 4px; font-size: 11px;")

        def _worker():
            return self.cloud_account_service.health(url, key)

        def _apply_if_current(result, expected=generation):
            if expected == self._health_probe_generation:
                self.backendHealthReady.emit(result)

        self._start_managed_task("SafeLauncher-HealthProbe", _worker, _apply_if_current)

    def _apply_backend_health(self, health: dict):
        """Update live health card with latency, status, and version parity badges."""
        self.btn_probe_health.setEnabled(True)
        self.btn_probe_health.setText("Check Health")
        self._health_technical_error = str(health.get("error") or "")
        self.btn_copy_health_details.setVisible(bool(self._health_technical_error))
        status = health.get("status", "unreachable")
        lat = health.get("latency_ms", -1)
        ver = health.get("version", "unknown")
        is_outdated = health.get("is_outdated", False)
        min_ver = health.get("min_version", MIN_CONVEX_BACKEND_VERSION)

        if lat >= 0:
            color = "#35C98A" if lat < 150 else ("#E5A93D" if lat < 400 else "#F05D6C")
            self.lbl_health_latency.setText(f"{lat} ms")
            self.lbl_health_latency.setStyleSheet(
                f"background: #1F2937; color: {color}; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;"
            )
        else:
            self.lbl_health_latency.setText("-- ms")
            self.lbl_health_latency.setStyleSheet(
                "background: #202024; color: #6B7280; padding: 3px 8px; border-radius: 4px; font-size: 11px;"
            )

        if status == "connected":
            self.lbl_health_status.setText("<font color='#35C98A'>● Connected & Healthy</font>")
        elif status == "legacy":
            self.lbl_health_status.setText("<font color='#E5A93D'>● Legacy Backend (Missing /api/health)</font>")
        elif status == "unauthorized":
            self.lbl_health_status.setText("<font color='#F05D6C'>● Unauthorized (Secret Key required or invalid)</font>")
        elif status == "unconfigured":
            self.lbl_health_status.setText("<font color='#A1A1AA'>● Not Configured</font>")
        else:
            err = str(health.get("error") or "The configured backend could not be reached.")
            self.lbl_health_status.setText(
                "<font color='#F05D6C'>● Unreachable</font>"
            )
            self.lbl_health_status.setToolTip(err)

        if ver != "unknown":
            self.lbl_health_version.setText(f"v{ver}")
            if is_outdated:
                self.lbl_version_warning.setText(
                    f"<font color='#E5A93D'>Backend update required: installed <b>v{ver}</b> is older than minimum supported <b>v{min_ver}</b>. "
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
                        f"<font color='#35C98A'>Backend functions are up to date (v{ver} >= v{min_ver}). "
                        "Cloud Save is ready.</font>"
                    ) if cloud_key_configured else (
                        f"<font color='#35C98A'>Backend functions are up to date (v{ver} >= v{min_ver}).</font> "
                        "<font color='#E5A93D'>Cloud Save still needs its Secret Access Key in this app.</font>"
                    )
                )
                self.btn_redeploy.setVisible(True)
                self.btn_open_dashboard.setVisible(True)
        else:
            self.lbl_health_version.setText("Unknown")
            self.lbl_version_warning.setText("")
            self.btn_redeploy.setVisible(False)
            self.btn_open_dashboard.setVisible(False)

    def _copy_health_details(self) -> None:
        if self._health_technical_error:
            QApplication.clipboard().setText(self._health_technical_error)
            self.btn_copy_health_details.setToolTip("Technical details copied to the clipboard")

    def _apply_backend_deploy_result(self, success: bool, message: str):
        """Handle redeploy completion on the Qt GUI thread."""
        self.btn_redeploy.setEnabled(True)
        if success:
            saved_cloud_secret = get_secret("cloud_secret_key", legacy_name="cloud_secret_key")
            if saved_cloud_secret and not self.edit_cloud_secret_key.text().strip():
                self.edit_cloud_secret_key.setText(saved_cloud_secret)
            if self.edit_cloud_secret_key.text().strip() or saved_cloud_secret:
                self.lbl_version_warning.setText(f"<font color='#35C98A'>{message}</font>")
            else:
                self.lbl_version_warning.setText(
                    f"<font color='#35C98A'>{message}</font> "
                    "<font color='#E5A93D'>Cloud Save still needs the Secret Access Key in Settings → Cloud.</font>"
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
                f"<font color='#F05D6C'>{html.escape(message).replace(chr(10), '<br>')}</font>"
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
            self.lbl_version_warning.setText("<font color='#3B9FE8'>Deploying backend functions…</font>")
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
                on_error=lambda error: self.backendDeployReady.emit(False, error),
            )

    def _open_convex_dashboard(self):
        """Open Convex dashboard in browser."""
        QDesktopServices.openUrl(QUrl("https://dashboard.convex.dev"))

    # ---- Application Updater handlers ---------------------------------------

    def _manual_check_updates(self):
        """Manual trigger to check for SafeLauncher application updates."""
        if not self._dialog_network_allowed():
            self.lbl_update_status.setText(
                "<font color='#A1A1AA'>Offline mode — update checks are disabled.</font>"
            )
            return
        self.btn_check_app_updates.setEnabled(False)
        self.lbl_update_status.setText("<font color='#3B9FE8'>Checking GitHub Releases for updates…</font>")

        def _worker():
            return check_for_updates()

        self._start_managed_task(
            "SafeLauncher-ManualUpdate", _worker, self.appUpdateReady.emit
        )

    def _apply_manual_update_result(self, info: dict):
        """Handle result of manual update check."""
        self.btn_check_app_updates.setEnabled(True)
        if info.get("error"):
            self.lbl_update_status.setText(f"<font color='#F05D6C'>Update check failed: {info['error']}</font>")
            return

        latest = info.get("latest_version", "")
        if info.get("update_available"):
            self.lbl_update_status.setText(
                f"<font color='#35C98A'><b>Update available:</b> v{latest} (Current: v{info['current_version']})</font>"
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
                f"<font color='#35C98A'>SafeLauncher is up to date (v{info['current_version']}).</font>"
            )

    def _start_appimage_download(self, asset_url: str):
        """Download new AppImage in background thread with progress reporting."""
        if not self._dialog_network_allowed():
            self.lbl_update_status.setText(
                "<font color='#A1A1AA'>Offline mode — update downloads are disabled.</font>"
            )
            return
        self.lbl_update_status.setText("<font color='#3B9FE8'>Downloading update…</font>")

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
            "SafeLauncher-AppImageDownload", _worker, _deliver,
            on_error=self.appDownloadFailed.emit,
        )

    def _on_app_download_progress(self, downloaded: int, total: int):
        if total > 0:
            pct = int((downloaded / total) * 100)
            self.lbl_update_status.setText(
                f"<font color='#3B9FE8'>Downloading update: {pct}% ({downloaded // (1024*1024)} MB / {total // (1024*1024)} MB)…</font>"
            )

    def _on_app_download_finished(self, target_path: str):
        self.lbl_update_status.setText("<font color='#35C98A'><b>Update Ready!</b> Restart required.</font>")
        reply = QMessageBox.question(
            self, "Update Installed",
            "The update has been downloaded and verified.\n\nRestart SafeLauncher now to apply the update?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            restart_application()

    def _on_app_download_failed(self, error: str):
        self.lbl_update_status.setText(f"<font color='#F05D6C'>Download failed: {error}</font>")
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
        mode = self.combo_mode.currentData() or "manual"
        capture_hotkey = cap_hk or getattr(self.gpu_config, "capture_hotkey", "F9") or "F9"
        replay_hotkey = (
            capture_hotkey
            if mode == "replay_buffer"
            else getattr(self.gpu_config, "replay_hotkey", "F10") or "F10"
        )
        return GpuRecorderConfig(
            enabled=self.chk_plugin_enabled.isChecked(),
            mode=mode,
            codec=self.combo_codec.currentData() or "auto",
            bitrate=self.combo_bitrate.currentData() or "12M",
            target_screen=self.combo_recording_monitor.currentData() or "screen",
            audio=self.chk_audio.isChecked(),
            audio_device=self.combo_audio_output.currentData() or "default_output",
            microphone_device=self.combo_audio_input.currentData() or "",
            history_seconds=int(self.combo_replay.currentData() or 60),
            output_dir=self.output_dir_input.text().strip() or DEFAULT_RECORDINGS_DIR,
            capture_hotkey=capture_hotkey,
            replay_hotkey=replay_hotkey,
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

    def _on_sandbox_size_error(self, _error: str):
        """Replace the calculating placeholder if the managed scan fails."""
        try:
            if hasattr(self, "lbl_sandbox_size"):
                self.lbl_sandbox_size.setText("Could not calculate sandbox size")
                self.lbl_sandbox_size.setToolTip("Check that the sandbox directory is readable, then reopen Settings.")
        except RuntimeError:
            pass

    def get_screenshot_target_screen(self) -> str:
        return self.combo_screenshot_screen.currentData() or "current"
