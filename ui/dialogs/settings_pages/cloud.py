from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFormLayout, QWidget, QScrollArea, QGridLayout, QFrame, QProgressBar, QCheckBox, QSpinBox
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QFont
from ui.icons import get_icon
from ui.components.check_field import CheckField as QCheckBox
from ui.components.sort_combo import SortComboBox
from core.secret_store import get_secret
from core.network_policy import is_offline_mode, offline_status_text
from core.logger import get_logger
from dataclasses import dataclass
from typing import Callable
logger = get_logger("SettingsPage")


@dataclass(frozen=True)
class CloudPageInputs:
    refresh_cloud_conflict_summary: Callable
    add_section_divider: Callable
    apply_account_status: Callable
    apply_backend_deploy_result: Callable
    apply_backend_health: Callable
    apply_manual_update_result: Callable
    browse_cloud_dir: Callable
    cloud_connect: Callable
    cloud_disconnect: Callable
    copy_health_details: Callable
    forget_deploy_key: Callable
    on_app_download_failed: Callable
    on_app_download_finished: Callable
    on_app_download_progress: Callable
    on_cloud_mode_changed: Callable
    open_cloud_center_from_settings: Callable
    open_cloud_wizard: Callable
    open_conflict_review: Callable
    open_convex_dashboard: Callable
    open_deploy_key_wizard: Callable
    polish_settings_form: Callable
    polish_settings_grid: Callable
    redeploy_backend: Callable
    refresh_account_status: Callable
    refresh_backend_health: Callable
    wrap_settings_sections: Callable
    accountStatusReady: object
    appDownloadFailed: object
    appDownloadFinished: object
    appDownloadProgress: object
    appUpdateReady: object
    backendDeployReady: object
    backendHealthReady: object
    cloud_account_service: object
    cloud_saves_dir: str
    info_hint: Callable


class CloudSettingsPage(QScrollArea):
    """Build this form only; workflow callbacks are explicitly supplied."""
    def __init__(self, inputs: CloudPageInputs, parent=None):
        super().__init__(parent)
        scroll = self
        scroll.setWidgetResizable(True)
        scroll.setObjectName("settingsScroll")

        page = QWidget()
        page.setObjectName("settingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        sec_account = QLabel("Cloud Connection · Technical Settings")
        sec_account.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_account.setStyleSheet("color: #ffffff; padding-bottom: 2px;")
        layout.addWidget(sec_account)
        inputs.add_section_divider(layout)

        layout.addWidget(inputs.info_hint(
            "Cloud Center is the single account-wide cloud surface. This technical page is opened from Cloud Center "
            "for backend connection and diagnostics only.",
            tooltip="Open Cloud Center for account status, storage, devices, conflicts, and cloud save versions.",
        ))

        from core.cloud_backend import get_site_url, normalize_site_url
        settings = QSettings("SafeLauncher", "SafeLauncher")

        form_mode = QFormLayout()
        form_mode.setSpacing(10)

        self.combo_cloud_mode = SortComboBox()
        self.combo_cloud_mode.addItem("Local folder sync", "local")
        self.combo_cloud_mode.addItem("Private Convex Cloud (SafeLauncherCloud)", "convex")
        self.combo_cloud_mode.setCurrentIndex(
            1 if inputs.cloud_account_service.mode() == "convex" else 0
        )
        self.combo_cloud_mode.currentIndexChanged.connect(inputs.on_cloud_mode_changed)
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
        offline_hint = inputs.info_hint(
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
        btn_setup_deploy_key.clicked.connect(inputs.open_deploy_key_wizard)
        deploy_key_row.addWidget(btn_setup_deploy_key)
        btn_forget_deploy_key = QPushButton("Forget")
        btn_forget_deploy_key.setProperty("settingsButtonRole", "destructive")
        btn_forget_deploy_key.setToolTip("Remove the deploy key from this device; it does not revoke the key in Convex")
        btn_forget_deploy_key.clicked.connect(inputs.forget_deploy_key)
        deploy_key_row.addWidget(btn_forget_deploy_key)
        form_mode.addRow("Convex Deploy Key:", deploy_key_row)
        deploy_key_help = inputs.info_hint(
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
        self.lbl_connected_devices.setStyleSheet("color: #3B9FE8; font-weight: bold;")
        form_mode.addRow("Concurrent Devices:", self.lbl_connected_devices)

        self.spin_sync_workers = QSpinBox()
        self.spin_sync_workers.setRange(1, 5)
        self.spin_sync_workers.setValue(settings.value("cloud_sync_workers", 3, type=int))
        self.spin_sync_workers.setToolTip("Simultaneous background worker threads for checking and syncing cloud saves.")
        form_mode.addRow("Concurrent Sync Workers:", self.spin_sync_workers)

        # Local fallback directory used when the backend is 'local' or offline.
        cloud_row = QHBoxLayout()
        self.edit_cloud_saves_dir = QLineEdit(inputs.cloud_saves_dir)
        cloud_row.addWidget(self.edit_cloud_saves_dir, 1)
        btn_browse_cloud = QPushButton("Browse...")
        btn_browse_cloud.clicked.connect(inputs.browse_cloud_dir)
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
            QProgressBar { background: #18181B; border: 1px solid #202024; border-radius: 7px; }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #3B9FE8, stop:1 #7C5CFF);
                border-radius: 6px;
            }
        """)
        form_mode.addRow("Quota:", self.bar_quota_settings)
        inputs.polish_settings_form(form_mode)
        layout.addLayout(form_mode)

        acct_btns = QHBoxLayout()
        acct_btns.setSpacing(8)
        self.btn_wizard = QPushButton("Setup Wizard…")
        self.btn_wizard.clicked.connect(inputs.open_cloud_wizard)
        acct_btns.addWidget(self.btn_wizard)
        self.btn_sign_in = QPushButton("Test & Connect…")
        self.btn_sign_in.clicked.connect(inputs.cloud_connect)
        acct_btns.addWidget(self.btn_sign_in)
        self.btn_open_cloud_center = QPushButton("Open Cloud Center…")
        self.btn_open_cloud_center.setAccessibleName("Open Cloud Center")
        self.btn_open_cloud_center.clicked.connect(inputs.open_cloud_center_from_settings)
        acct_btns.addWidget(self.btn_open_cloud_center)
        self.btn_review_conflicts = QPushButton("Review conflicts")
        self.btn_review_conflicts.setIcon(get_icon("ph.warning-bold", color="#A1A1AA"))
        self.btn_review_conflicts.setEnabled(False)
        self.btn_review_conflicts.setToolTip("Available when this account has cloud-save conflicts")
        self.btn_review_conflicts.clicked.connect(inputs.open_conflict_review)
        # Conflict review belongs to Cloud Center. Keep the object for
        # compatibility with older embedders, but do not duplicate that
        # account-wide action inside connection settings.
        self.btn_refresh_quota = QPushButton("Refresh Quota")
        self.btn_refresh_quota.setAccessibleName("Refresh cloud account status")
        self.btn_refresh_quota.setToolTip("Refresh cloud account usage and device status")
        self.btn_refresh_quota.clicked.connect(inputs.refresh_account_status)
        self.btn_refresh_quota.clicked.connect(inputs.refresh_cloud_conflict_summary)
        acct_btns.addWidget(self.btn_refresh_quota)
        # Quota is presented in Cloud Center alongside devices and storage.
        self.btn_logout = QPushButton("Disconnect")
        self.btn_logout.setProperty("settingsButtonRole", "destructive")
        self.btn_logout.clicked.connect(inputs.cloud_disconnect)
        acct_btns.addWidget(self.btn_logout)
        acct_btns.addStretch()
        layout.addLayout(acct_btns)

        # ── Live Backend Health & Version Synchronization Card ───────────────
        self.card_backend_health = QFrame()
        self.card_backend_health.setStyleSheet("""
            QFrame {
                background-color: #18181B;
                border: 1px solid #202024;
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
            background: #202024;
            color: #A1A1AA;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: bold;
        """)
        bh_header.addWidget(self.lbl_health_latency)

        self.btn_probe_health = QPushButton("Check Health")
        self.btn_probe_health.setStyleSheet("padding: 4px 10px; font-size: 11px;")
        self.btn_probe_health.clicked.connect(inputs.refresh_backend_health)
        bh_header.addWidget(self.btn_probe_health)
        self.btn_copy_health_details = QPushButton("Copy details")
        self.btn_copy_health_details.setAccessibleName("Copy backend health technical details")
        self.btn_copy_health_details.setToolTip("Copy the last backend health error for troubleshooting")
        self.btn_copy_health_details.clicked.connect(inputs.copy_health_details)
        self.btn_copy_health_details.setVisible(False)
        bh_header.addWidget(self.btn_copy_health_details)
        bh_layout.addLayout(bh_header)

        bh_grid = QGridLayout()
        bh_grid.setSpacing(6)
        bh_grid.addWidget(QLabel("Connection Status:"), 0, 0)
        self.lbl_health_status = QLabel("Probing…")
        self.lbl_health_status.setStyleSheet("color: #A1A1AA; font-weight: 500;")
        bh_grid.addWidget(self.lbl_health_status, 0, 1)

        bh_grid.addWidget(QLabel("Backend Version:"), 1, 0)
        self.lbl_health_version = QLabel("Checking…")
        self.lbl_health_version.setStyleSheet("color: #A1A1AA; font-weight: 500;")
        bh_grid.addWidget(self.lbl_health_version, 1, 1)
        bh_layout.addLayout(bh_grid)
        inputs.polish_settings_grid(bh_grid)

        self.lbl_version_warning = QLabel("")
        self.lbl_version_warning.setWordWrap(True)
        self.lbl_version_warning.setStyleSheet("font-size: 12px;")
        bh_layout.addWidget(self.lbl_version_warning)

        self.health_action_row = QHBoxLayout()
        self.btn_redeploy = QPushButton("One-Click Redeploy Backend")
        self.btn_redeploy.setProperty("settingsButtonRole", "primary")
        self.btn_redeploy.setStyleSheet("background: #0284C7; font-weight: bold; padding: 6px 14px;")
        self.btn_redeploy.clicked.connect(inputs.redeploy_backend)
        self.btn_redeploy.setVisible(False)
        self.health_action_row.addWidget(self.btn_redeploy)

        self.btn_open_dashboard = QPushButton("Open Convex Dashboard ↗")
        self.btn_open_dashboard.setStyleSheet("padding: 6px 14px;")
        self.btn_open_dashboard.clicked.connect(inputs.open_convex_dashboard)
        self.btn_open_dashboard.setVisible(False)
        self.health_action_row.addWidget(self.btn_open_dashboard)

        self.health_action_row.addStretch()
        bh_layout.addLayout(self.health_action_row)

        layout.addWidget(self.card_backend_health)

        layout.addStretch()
        inputs.wrap_settings_sections(layout)

        inputs.accountStatusReady.connect(inputs.apply_account_status)
        inputs.backendHealthReady.connect(inputs.apply_backend_health)
        inputs.backendDeployReady.connect(inputs.apply_backend_deploy_result)
        inputs.appUpdateReady.connect(inputs.apply_manual_update_result)
        inputs.appDownloadProgress.connect(inputs.on_app_download_progress)
        inputs.appDownloadFinished.connect(inputs.on_app_download_finished)
        inputs.appDownloadFailed.connect(inputs.on_app_download_failed)


        scroll.setWidget(page)
