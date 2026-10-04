from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFormLayout, QWidget, QScrollArea, QCheckBox
from PyQt6.QtCore import Qt, QSettings, QSize
from PyQt6.QtGui import QFont
from core.desktop_integration import is_startup_enabled
from core.date_formatting import date_format_choices
from ui.icons import get_icon
from ui.components.check_field import CheckField as QCheckBox
from ui.components.sort_combo import SortComboBox
from core.version import APP_VERSION
from core.logger import get_logger
from dataclasses import dataclass
from typing import Callable
logger = get_logger("SettingsPage")


@dataclass(frozen=True)
class GeneralPageInputs:
    add_section_divider: Callable
    browse_proton: Callable
    manual_check_updates: Callable
    polish_settings_form: Callable
    toggle_desktop_entry: Callable
    wrap_settings_sections: Callable
    date_format: str
    info_hint: Callable
    profile_auth_requested: object
    profile_publish_requested: object
    profile_resync_requested: object
    proton_path: str
    show_welcome_wizard: bool
    user_name: str


class GeneralSettingsPage(QScrollArea):
    """Build this form only; workflow callbacks are explicitly supplied."""
    def __init__(self, inputs: GeneralPageInputs, parent=None):
        super().__init__(parent)
        scroll = self
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
        inputs.add_section_divider(layout)

        profile_account_hint = inputs.info_hint(
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
        self.btn_profile_auth.clicked.connect(inputs.profile_auth_requested.emit)
        profile_actions.addWidget(self.btn_profile_auth)

        self.btn_profile_publish = QPushButton("Publish profile")
        self.btn_profile_publish.setObjectName("settingsProfileAction")
        self.btn_profile_publish.setIcon(get_icon("ph.upload-simple-bold", color="#FFFFFF"))
        self.btn_profile_publish.setIconSize(QSize(16, 16))
        self.btn_profile_publish.clicked.connect(inputs.profile_publish_requested.emit)
        profile_actions.addWidget(self.btn_profile_publish)

        self.btn_profile_resync = QPushButton("Resync")
        self.btn_profile_resync.setObjectName("settingsProfileAction")
        self.btn_profile_resync.setAccessibleName("Resync private profile data")
        self.btn_profile_resync.setToolTip("Resync private profile data")
        self.btn_profile_resync.setIcon(get_icon("ph.arrows-clockwise-bold", color="#A1A1AA"))
        self.btn_profile_resync.setIconSize(QSize(16, 16))
        self.btn_profile_resync.clicked.connect(inputs.profile_resync_requested.emit)
        profile_actions.addWidget(self.btn_profile_resync)
        profile_actions.addStretch()
        layout.addLayout(profile_actions)

        self.lbl_profile_action_status = QLabel("Profile account controls are loading…")
        self.lbl_profile_action_status.setObjectName("profileActionStatus")
        self.lbl_profile_action_status.setWordWrap(True)
        self.lbl_profile_action_status.setStyleSheet("color: #A1A1AA; font-size: 12px;")
        layout.addWidget(self.lbl_profile_action_status)

        date_form = QFormLayout()
        date_form.setSpacing(10)
        date_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.combo_date_format = SortComboBox()
        for key, _python_format, _qt_format in date_format_choices():
            self.combo_date_format.addItem(key, key)
        date_index = self.combo_date_format.findData(inputs.date_format)
        self.combo_date_format.setCurrentIndex(max(0, date_index))
        date_form.addRow("Date Format:", self.combo_date_format)
        inputs.polish_settings_form(date_form)
        layout.addLayout(date_form)

        sec_profile = QLabel("Profile & Paths")
        sec_profile.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_profile.setStyleSheet("color: #FFFFFF; padding-bottom: 2px; margin-top: 6px;")
        layout.addWidget(sec_profile)
        inputs.add_section_divider(layout)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.name_input = QLineEdit(inputs.user_name)
        self.name_input.setPlaceholderText("Enter display name")
        form.addRow("Display Name:", self.name_input)

        proton_row = QHBoxLayout()
        self.proton_input = QLineEdit(inputs.proton_path)
        self.proton_input.setPlaceholderText("Leave blank for automatic detection (~/.local/share/umu/...)")
        proton_row.addWidget(self.proton_input)

        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(inputs.browse_proton)
        proton_row.addWidget(browse_btn)

        form.addRow("Proton Path:", proton_row)
        inputs.polish_settings_form(form)
        layout.addLayout(form)

        sec_desktop = QLabel("Desktop & Menu Integration")
        sec_desktop.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_desktop.setStyleSheet("color: #FFFFFF; padding-bottom: 2px; margin-top: 6px;")
        layout.addWidget(sec_desktop)
        inputs.add_section_divider(layout)

        desktop_row = QHBoxLayout()
        self.lbl_desktop_status = QLabel()
        self.lbl_desktop_status.setObjectName("settingsStatus")
        desktop_row.addWidget(self.lbl_desktop_status, 1)
        self.btn_desktop_entry = QPushButton()
        self.btn_desktop_entry.setObjectName("settingsActionButton")
        self.btn_desktop_entry.clicked.connect(inputs.toggle_desktop_entry)
        desktop_row.addWidget(self.btn_desktop_entry)
        layout.addLayout(desktop_row)

        sec_startup = QLabel("Startup Preferences")
        sec_startup.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_startup.setStyleSheet("color: #FFFFFF; padding-bottom: 2px; margin-top: 6px;")
        layout.addWidget(sec_startup)
        inputs.add_section_divider(layout)

        self.chk_launch_startup = QCheckBox("Launch SafeLauncher when I sign in")
        self.chk_launch_startup.setChecked(is_startup_enabled())
        layout.addWidget(self.chk_launch_startup)

        self.chk_welcome = QCheckBox("Show introduction wizard on startup")
        self.chk_welcome.setChecked(inputs.show_welcome_wizard)
        layout.addWidget(self.chk_welcome)

        sec_achievements = QLabel("Achievement Tracking & Notifications")
        sec_achievements.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_achievements.setStyleSheet("color: #FFFFFF; padding-bottom: 2px; margin-top: 6px;")
        layout.addWidget(sec_achievements)
        inputs.add_section_divider(layout)

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
        inputs.add_section_divider(layout)

        update_row = QHBoxLayout()
        lbl_version_info = QLabel(f"Current SafeLauncher Version: <b>v{APP_VERSION}</b>")
        lbl_version_info.setObjectName("settingsMetaValue")
        update_row.addWidget(lbl_version_info)
        update_row.addStretch()

        self.btn_check_app_updates = QPushButton("Check for SafeLauncher Updates")
        self.btn_check_app_updates.clicked.connect(inputs.manual_check_updates)
        update_row.addWidget(self.btn_check_app_updates)
        layout.addLayout(update_row)

        self.lbl_update_status = QLabel("")
        self.lbl_update_status.setWordWrap(True)
        self.lbl_update_status.setStyleSheet("color: #A1A1AA; font-size: 12px;")
        layout.addWidget(self.lbl_update_status)

        layout.addStretch()
        inputs.wrap_settings_sections(layout)

        scroll.setWidget(page)
