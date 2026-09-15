"""Simple overview and advanced controls for private SafeLauncherCloud."""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
)

from core.cloud_center_service import CloudCenterService, CloudOverview
from core.request_contracts import ResourceStatus
from ui.components.popup_shell import PopupDialog
from ui.icons import get_icon
from ui.resource_binding import ResourceBinding, bind_request


def _format_bytes(value: int) -> str:
    size = float(max(0, int(value or 0)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return "0 B"


def _relative_time(timestamp: float) -> str:
    if not timestamp:
        return "Not synced this session"
    age = max(0.0, time.time() - float(timestamp))
    if age < 60:
        return "Synced just now"
    if age < 3600:
        return f"Synced {int(age // 60)} min ago"
    if age < 86400:
        return f"Synced {int(age // 3600)} hr ago"
    return f"Synced {int(age // 86400)} day ago"


class CloudCenterDialog(PopupDialog):
    """One private-cloud management surface with optional advanced sections."""

    setup_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    history_requested = pyqtSignal()
    conflicts_requested = pyqtSignal()
    sync_finished = pyqtSignal(object)
    overview_changed = pyqtSignal(object)

    def __init__(
        self,
        parent=None,
        *,
        cloud_center_service: CloudCenterService,
        db_path: str | None = None,
        last_sync_at: float = 0.0,
    ):
        super().__init__("Cloud Center", parent)
        self.cloud_center_service = cloud_center_service
        self.db_path = db_path
        self.last_sync_at = float(last_sync_at or 0)
        self._overview_binding: ResourceBinding | None = None
        self._sync_binding: ResourceBinding | None = None
        self._probe_binding: ResourceBinding | None = None
        self._syncing = False

        self.setMinimumSize(700, 560)
        self.resize(760, 650)
        layout = self.popup_layout(margins=(22, 18, 22, 18), spacing=12)

        intro = self.info_hint(
            "Private cloud saves, devices, and synchronization in one place.",
            tooltip="This private-cloud center manages synchronization and save history. Public profile data remains separate.",
        )
        layout.addWidget(intro)

        status_card = QFrame()
        status_card.setObjectName("cloudCenterStatusCard")
        status_card.setStyleSheet(
            "QFrame#cloudCenterStatusCard { background: #18181B; border: 1px solid #27272A; border-radius: 10px; }"
        )
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(16, 14, 16, 14)
        status_layout.setSpacing(12)
        self.lbl_status_icon = QLabel()
        self.lbl_status_icon.setFixedSize(28, 28)
        self.lbl_status_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_layout.addWidget(self.lbl_status_icon)
        status_text = QVBoxLayout()
        status_text.setSpacing(2)
        self.lbl_status = QLabel("Checking cloud connection…")
        self.lbl_status.setStyleSheet("font-size: 14px; font-weight: 700; color: #F5F7FA;")
        self.lbl_message = QLabel("Loading your private-cloud overview.")
        self.lbl_message.setWordWrap(True)
        self.lbl_message.setStyleSheet("color: #A1A1AA; font-size: 11px;")
        status_text.addWidget(self.lbl_status)
        status_text.addWidget(self.lbl_message)
        status_layout.addLayout(status_text, 1)
        self.btn_sync = QPushButton("Sync now")
        self.btn_sync.setIcon(get_icon("ph.arrows-clockwise-bold", "#FFFFFF"))
        self.btn_sync.setMinimumWidth(120)
        self.btn_sync.clicked.connect(self._sync_now)
        status_layout.addWidget(self.btn_sync)
        layout.addWidget(status_card)

        summary = QGridLayout()
        summary.setHorizontalSpacing(8)
        summary.setVerticalSpacing(8)
        self.summary_values = {}
        summary_specs = (
            ("pending", "Pending changes"),
            ("conflicts", "Conflicts"),
            ("devices", "Devices"),
            ("storage", "Cloud storage"),
        )
        for index, (key, title) in enumerate(summary_specs):
            card = QFrame()
            card.setObjectName("cloudCenterSummaryCard")
            card.setStyleSheet(
                "QFrame#cloudCenterSummaryCard { background: #18181B; border: 1px solid #27272A; border-radius: 8px; }"
            )
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(3)
            label = QLabel(title.upper())
            label.setStyleSheet("color: #71717A; font-size: 9px; font-weight: 700; letter-spacing: 0.6px;")
            value = QLabel("—")
            value.setStyleSheet("color: #F5F7FA; font-size: 16px; font-weight: 700;")
            card_layout.addWidget(label)
            card_layout.addWidget(value)
            summary.addWidget(card, index // 2, index % 2)
            self.summary_values[key] = value
        layout.addLayout(summary)

        self.lbl_last_sync = QLabel(_relative_time(self.last_sync_at))
        self.lbl_last_sync.setStyleSheet("color: #71717A; font-size: 10px;")
        layout.addWidget(self.lbl_last_sync)

        devices_header = QHBoxLayout()
        devices_title = QLabel("DEVICES USING THIS PRIVATE CLOUD")
        devices_title.setStyleSheet("color: #A1A1AA; font-size: 10px; font-weight: 700; letter-spacing: 0.7px;")
        devices_header.addWidget(devices_title)
        devices_header.addStretch()
        self.btn_history = QPushButton("Save history")
        self.btn_history.setIcon(get_icon("ph.clock-counter-clockwise-bold", "#A1A1AA"))
        self.btn_history.clicked.connect(self._open_history)
        devices_header.addWidget(self.btn_history)
        self.btn_review_conflicts = QPushButton("Review conflicts")
        self.btn_review_conflicts.setIcon(get_icon("ph.warning-bold", "#A1A1AA"))
        self.btn_review_conflicts.setEnabled(False)
        self.btn_review_conflicts.setToolTip("Available when this account has cloud-save conflicts")
        self.btn_review_conflicts.clicked.connect(self._open_conflicts)
        devices_header.addWidget(self.btn_review_conflicts)
        layout.addLayout(devices_header)

        self.device_list = QListWidget()
        self.device_list.setMinimumHeight(76)
        self.device_list.setMaximumHeight(120)
        self.device_list.setStyleSheet(
            "QListWidget { background: #18181B; border: 1px solid #27272A; border-radius: 8px; color: #E5E7EB; }"
            "QListWidget::item { padding: 7px 8px; }"
        )
        layout.addWidget(self.device_list)

        advanced_toggle = QToolButton()
        advanced_toggle.setText("Advanced cloud controls")
        advanced_toggle.setCheckable(True)
        advanced_toggle.setChecked(False)
        advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        advanced_toggle.setStyleSheet(
            "QToolButton { color: #A1A1AA; background: transparent; border: none; padding: 7px 2px; font-weight: 600; text-align: left; }"
            "QToolButton:hover { color: #FFFFFF; }"
        )
        layout.addWidget(advanced_toggle)

        self.advanced_frame = QFrame()
        self.advanced_frame.setObjectName("cloudCenterAdvanced")
        self.advanced_frame.setStyleSheet(
            "QFrame#cloudCenterAdvanced { background: #18181B; border: 1px solid #27272A; border-radius: 8px; }"
        )
        advanced_layout = QVBoxLayout(self.advanced_frame)
        advanced_layout.setContentsMargins(12, 10, 12, 10)
        advanced_layout.setSpacing(6)
        self.lbl_endpoint = QLabel("Endpoint: —")
        self.lbl_endpoint.setStyleSheet("color: #A1A1AA; font-size: 11px;")
        advanced_layout.addWidget(self.lbl_endpoint)
        self.lbl_account = QLabel("Account: —")
        self.lbl_account.setStyleSheet("color: #A1A1AA; font-size: 11px;")
        advanced_layout.addWidget(self.lbl_account)
        advanced_buttons = QHBoxLayout()
        self.btn_setup = QPushButton("Set up / fix connection")
        self.btn_setup.clicked.connect(self._open_setup)
        advanced_buttons.addWidget(self.btn_setup)
        self.btn_settings = QPushButton("Connection settings")
        self.btn_settings.clicked.connect(self._open_settings)
        advanced_buttons.addWidget(self.btn_settings)
        self.btn_probe = QPushButton("Test connection")
        self.btn_probe.clicked.connect(self._probe_connection)
        advanced_buttons.addWidget(self.btn_probe)
        advanced_buttons.addStretch()
        advanced_layout.addLayout(advanced_buttons)
        self.lbl_probe = QLabel("")
        self.lbl_probe.setWordWrap(True)
        self.lbl_probe.setStyleSheet("color: #71717A; font-size: 10px;")
        advanced_layout.addWidget(self.lbl_probe)
        self.advanced_frame.setVisible(False)
        layout.addWidget(self.advanced_frame)

        advanced_toggle.toggled.connect(self._toggle_advanced)

        footer = QHBoxLayout()
        footer.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)

        self._request_overview()

    def _toggle_advanced(self, expanded: bool) -> None:
        sender = self.sender()
        if isinstance(sender, QToolButton):
            sender.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.advanced_frame.setVisible(bool(expanded))

    def _open_setup(self) -> None:
        self.setup_requested.emit()
        QTimer.singleShot(0, lambda: self._request_overview(force=True))

    def _open_settings(self) -> None:
        self.settings_requested.emit()
        QTimer.singleShot(0, lambda: self._request_overview(force=True))

    def _open_history(self) -> None:
        self.history_requested.emit()
        QTimer.singleShot(0, lambda: self._request_overview(force=True))

    def _open_conflicts(self) -> None:
        if not self.btn_review_conflicts.isEnabled():
            return
        self.conflicts_requested.emit()
        QTimer.singleShot(0, lambda: self._request_overview(force=True))

    def _probe_connection(self) -> None:
        self._close_binding(self._probe_binding)
        self.btn_probe.setEnabled(False)
        self.lbl_probe.setText("Testing the configured backend…")
        handle = self.cloud_center_service.request_connection_probe()
        self._probe_binding = bind_request(
            self.cloud_center_service.request_manager,
            handle,
            self._apply_probe_result,
            self,
            cancel_on_close=True,
        )

    def _apply_probe_result(self, result) -> None:
        if result.status in (ResourceStatus.IDLE, ResourceStatus.LOADING):
            return
        self.btn_probe.setEnabled(True)
        if result.status in (ResourceStatus.CANCELLED,):
            return
        if result.status not in (ResourceStatus.READY, ResourceStatus.STALE):
            self.lbl_probe.setText(str(result.error or "Connection probe failed."))
            return
        payload = result.value if isinstance(result.value, dict) else {}
        if payload.get("healthy"):
            version = payload.get("version") or "unknown"
            latency = payload.get("latency_ms")
            suffix = f" · {latency} ms" if latency else ""
            self.lbl_probe.setText(f"Backend reachable · v{version}{suffix}")
        else:
            self.lbl_probe.setText(str(payload.get("message") or "Backend is not available."))

    def _close_binding(self, binding: ResourceBinding | None) -> None:
        if binding is not None:
            binding.close()

    def _request_overview(self, *, force: bool = False) -> None:
        self._close_binding(self._overview_binding)
        self._overview_binding = None
        handle = self.cloud_center_service.request_overview(force=force)
        self._overview_binding = bind_request(
            self.cloud_center_service.request_manager,
            handle,
            self._apply_overview_result,
            self,
            cancel_on_close=True,
        )

    def _apply_overview_result(self, result) -> None:
        if result.status in (ResourceStatus.IDLE, ResourceStatus.LOADING):
            self.lbl_status.setText("Checking cloud connection…")
            self.btn_review_conflicts.setEnabled(False)
            return
        if result.status == ResourceStatus.CANCELLED:
            return
        if result.status != ResourceStatus.READY and result.status != ResourceStatus.STALE:
            self._show_resource_error(result)
            return
        overview = CloudOverview.from_payload(result.value)
        self._render_overview(overview, stale=result.status == ResourceStatus.STALE)

    def _show_resource_error(self, result) -> None:
        self.btn_review_conflicts.setEnabled(False)
        status = result.status
        if status == ResourceStatus.AUTHENTICATION_REQUIRED:
            title = "Cloud setup required"
            message = "Add the Secret Access Key in the advanced connection settings."
        elif status == ResourceStatus.PERMISSION_DENIED:
            title = "Cloud access denied"
            message = "This account is not allowed to access the configured private cloud."
        elif status == ResourceStatus.CONFLICT:
            title = "Sync needs attention"
            message = "A cloud revision conflict needs to be resolved from Save history."
        elif status == ResourceStatus.OFFLINE:
            title = "Offline"
            message = "Offline mode is enabled. Previously cached cloud data is still usable."
        elif status == ResourceStatus.UNAVAILABLE:
            title = "Cloud unavailable"
            message = "The backend is unavailable or its SafeLauncher API is not deployed."
        elif status == ResourceStatus.CANCELLED:
            title = "Cloud request cancelled"
            message = "The cloud request was cancelled before it completed."
        else:
            title = "Cloud status unavailable"
            message = str(result.error or "Could not load the cloud overview.")
        self.lbl_status.setText(title)
        self.lbl_message.setText(message)
        self.btn_sync.setText(
            "Fix connection"
            if status in (ResourceStatus.AUTHENTICATION_REQUIRED, ResourceStatus.UNAVAILABLE)
            else "Retry"
        )
        self.btn_sync.setEnabled(True)
        self.lbl_status_icon.setText("!")
        self.lbl_status_icon.setStyleSheet("color: #F59E0B; font-size: 20px; font-weight: 700;")

    def _render_overview(self, overview: CloudOverview, *, stale: bool = False) -> None:
        labels = {
            "ready": ("Cloud connected", "#35C98A"),
            "local": ("Local sync active", "#8E8E93"),
            "setup_required": ("Cloud setup required", "#F59E0B"),
            "offline": ("Cloud offline", "#F59E0B"),
        }
        title, color = labels.get(overview.connection, ("Cloud unavailable", "#F05D6C"))
        if stale and overview.connection == "ready":
            title = "Cloud connected · refreshing"
        self.lbl_status.setText(title)
        self.lbl_status.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {color};")
        self.lbl_message.setText(overview.message)
        self.lbl_status_icon.setText("✓" if overview.connection == "ready" else "•")
        self.lbl_status_icon.setStyleSheet(f"color: {color}; font-size: 22px; font-weight: 700;")
        self.overview_changed.emit(overview)
        self.btn_sync.setText("Sync now" if overview.connection in ("ready", "offline") else "Fix connection")
        self.btn_sync.setEnabled(not self._syncing)

        self.summary_values["pending"].setText(str(overview.pending_changes))
        self.summary_values["conflicts"].setText(str(overview.conflict_count))
        has_conflicts = int(overview.conflict_count or 0) > 0
        self.btn_review_conflicts.setEnabled(has_conflicts)
        self.btn_review_conflicts.setToolTip(
            "Open Save History to review current cloud-save conflicts"
            if has_conflicts else
            "Available when this account has cloud-save conflicts"
        )
        self.summary_values["devices"].setText(
            f"{overview.online_device_count}/{overview.device_count} online"
            if overview.device_count else "None"
        )
        storage = _format_bytes(overview.quota_used)
        if overview.quota_total:
            storage = f"{storage} / {_format_bytes(overview.quota_total)}"
        self.summary_values["storage"].setText(storage)
        self.lbl_last_sync.setText(_relative_time(self.last_sync_at))
        self.lbl_endpoint.setText(f"Endpoint: {overview.endpoint or 'Not configured'}")
        self.lbl_account.setText(f"Account: {overview.account_label or 'Not connected'}")

        self.device_list.clear()
        if not overview.devices:
            item = QListWidgetItem("No other registered devices.")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.device_list.addItem(item)
        else:
            for device in overview.devices:
                state = "online" if device.online else "offline"
                platform = f" · {device.platform}" if device.platform else ""
                self.device_list.addItem(f"{device.name}{platform} · {state}")

    def _sync_now(self) -> None:
        context = self.cloud_center_service.current_context()
        # Do not create a network operation when credentials are missing. A
        # local-folder sync and an offline private-cloud sync are still valid
        # managed operations and may queue work for later.
        if context.backend_active and not context.authentication_configured:
            self.setup_requested.emit()
            QTimer.singleShot(0, lambda: self._request_overview(force=True))
            return
        if self._syncing:
            return
        self._syncing = True
        self.btn_sync.setEnabled(False)
        self.btn_sync.setText("Syncing…")
        self._close_binding(self._sync_binding)
        try:
            handle = self.cloud_center_service.request_sync(self.db_path)
        except Exception as error:
            self._syncing = False
            self.btn_sync.setEnabled(True)
            self.btn_sync.setText("Retry")
            self.lbl_message.setText(str(error))
            return
        self._sync_binding = bind_request(
            self.cloud_center_service.request_manager,
            handle,
            self._apply_sync_result,
            self,
            cancel_on_close=True,
        )

    def _apply_sync_result(self, result) -> None:
        if result.status in (ResourceStatus.IDLE, ResourceStatus.LOADING):
            return
        if result.status == ResourceStatus.CANCELLED:
            self._syncing = False
            return
        self._syncing = False
        if result.status == ResourceStatus.READY:
            value = result.value
            success = bool(getattr(value, "success", False))
            if success:
                self.last_sync_at = time.time()
                self.lbl_last_sync.setText(_relative_time(self.last_sync_at))
                self.lbl_message.setText("Local metadata synchronized. Refreshing cloud status…")
                self.sync_finished.emit(value)
            else:
                self.lbl_message.setText("Sync is queued locally and will retry when the cloud is available.")
        else:
            self.lbl_message.setText(str(result.error or "Sync failed; local data was preserved."))
        self._request_overview(force=True)

    def closeEvent(self, event) -> None:
        self._close_binding(self._overview_binding)
        self._close_binding(self._sync_binding)
        self._close_binding(self._probe_binding)
        self._overview_binding = None
        self._sync_binding = None
        self._probe_binding = None
        super().closeEvent(event)


__all__ = ["CloudCenterDialog"]
