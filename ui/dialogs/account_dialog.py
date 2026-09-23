"""
Cloud Account manager dialog — profile, quota, and per-game save versions.

Opened from Settings → Cloud ("Open Account Manager…"). Shows the signed-in
identity, a visual quota bar against the server-enforced budget, and lets the
user inspect/delete historical cloud save versions hosted on the Convex backend.
Network work happens on daemon threads; results marshal back via signals.
"""

import os
import time
from typing import Any

from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton,
    QProgressBar, QListWidget, QListWidgetItem, QMessageBox, QSplitter,
    QComboBox,
)

from ui.components.sidebar import DialogTitleBar, add_soft_shadow
from ui.components.popup_shell import PopupDialog
from ui.components.save_history_timeline import SaveHistoryTimeline
from ui.components.cloud_ui import confirm_delete, confirm_restore, set_accessible_status
from core.logger import get_logger
from core.safe_thread import TaskSupervisor
from core.cloud_account_service import CloudAccountService, CloudAccountSnapshot
from core.cloud_operation_service import CloudOperationService, CloudOperationTarget
from core.request_contracts import RequestKey, RequestPriority, ResourceStatus
from core.date_formatting import format_datetime_timestamp
from ui.resource_binding import ResourceBinding, bind_request

logger = get_logger("AccountDialog")

try:
    from ui.dialogs.save_conflict_dialog import format_bytes
except ImportError:  # pragma: no cover - standalone safety
    def format_bytes(size_bytes: int) -> str:
        size = float(size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


def _relative_time(ts: float) -> str:
    if ts <= 0:
        return "never"
    delta = time.time() - ts
    for label, span in (("min", 60), ("hr", 3600), ("day", 86400)):
        if delta < span:
            unit = 1 if label == "min" else (3600 if label == "hr" else 86400)
            value = max(1, int(delta // unit))
            plural = "" if value == 1 else "s"
            return f"{value} {label}{plural} ago"
    weeks = int(delta // 604800)
    return f"{weeks} week{'s' if weeks != 1 else ''} ago"


class AccountDialog(PopupDialog):
    """Frameless profile manager for SafeLauncher cloud saves."""

    _data_ready = pyqtSignal(object)   # {'ok': {...}} | {'error': str}
    _op_done = pyqtSignal(object)

    def __init__(
        self,
        parent=None,
        request_manager=None,
        *,
        cloud_account_service=None,
        cloud_operation_service=None,
    ):
        super().__init__("Cloud History & Devices", parent)
        self.setMinimumSize(700, 500)
        self.resize(860, 620)
        self.setSizeGripEnabled(True)
        self._games = []
        self._quota = {}
        self._busy = False
        self._task_supervisor = TaskSupervisor(self, logger)
        self.request_manager = request_manager
        self.cloud_account_service = cloud_account_service or CloudAccountService()
        self.cloud_operation_service = cloud_operation_service or (
            CloudOperationService(request_manager) if request_manager is not None else None
        )
        self._resource_bindings: dict[str, ResourceBinding] = {}

        body_layout = self.popup_layout(margins=(20, 16, 20, 16), spacing=12)

        # --- identity header -------------------------------------------------
        header_row = QHBoxLayout()
        self.lbl_avatar = QLabel("?")
        self.lbl_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_avatar.setFixedSize(44, 44)
        set_accessible_status(
            self.lbl_avatar,
            "Cloud account avatar",
            "Initials placeholder for the connected private cloud account.",
        )
        self._style_avatar("?", ok=False)
        header_row.addWidget(self.lbl_avatar)

        ident_col = QVBoxLayout()
        ident_col.setSpacing(2)
        self.lbl_email = QLabel("Not signed in")
        self.lbl_email.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        self.lbl_email.setStyleSheet("color: #F5F7FA;")
        set_accessible_status(self.lbl_email, "Cloud account identity")
        ident_col.addWidget(self.lbl_email)
        self.lbl_subject = QLabel("")
        self.lbl_subject.setStyleSheet("color: #6F7682; font-size: 11px;")
        set_accessible_status(self.lbl_subject, "Cloud account details")
        ident_col.addWidget(self.lbl_subject)
        header_row.addLayout(ident_col)
        header_row.addStretch()

        self.combo_backend = QComboBox()
        self.combo_backend.addItem("Backend: Local folder sync", "local")
        self.combo_backend.addItem("Backend: Convex account", "convex")
        self.combo_backend.setCurrentIndex(1 if self.cloud_account_service.mode() == "convex" else 0)
        self.combo_backend.currentIndexChanged.connect(self._on_backend_changed)
        self.combo_backend.setMinimumWidth(220)
        self.combo_backend.setAccessibleName("Cloud backend")
        header_row.addWidget(self.combo_backend)
        body_layout.addLayout(header_row)

        # --- quota bar --------------------------------------------------------
        quota_box = QWidget()
        quota_layout = QVBoxLayout(quota_box)
        quota_layout.setContentsMargins(0, 0, 0, 0)
        quota_layout.setSpacing(4)
        self.bar_quota = QProgressBar()
        self.bar_quota.setFixedHeight(14)
        self.bar_quota.setTextVisible(False)
        self.bar_quota.setRange(0, 1000)
        self.bar_quota.setValue(0)
        self.bar_quota.setStyleSheet("""
            QProgressBar {
                background: #18181B;
                border: 1px solid #27272A;
                border-radius: 7px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #3B9FE8, stop:1 #7C5CFF);
                border-radius: 6px;
            }
        """)
        quota_layout.addWidget(self.bar_quota)
        self.lbl_quota_text = QLabel("Connect an account to see cloud usage.")
        self.lbl_quota_text.setStyleSheet("color: #9CA3AF; font-size: 11px;")
        set_accessible_status(self.lbl_quota_text, "Cloud storage usage")
        quota_layout.addWidget(self.lbl_quota_text)
        body_layout.addWidget(quota_box)

        # --- devices -----------------------------------------------------------
        devices_row = QHBoxLayout()
        devices_row.setSpacing(8)
        lbl_devices = QLabel("Devices")
        lbl_devices.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        lbl_devices.setStyleSheet("color: #FFFFFF;")
        devices_row.addWidget(lbl_devices)

        self.lst_devices = QListWidget()
        self.lst_devices.setMaximumHeight(86)
        self.lst_devices.setStyleSheet(
            "QListWidget { background:#121214; border:1px solid #27272A; border-radius:6px; color:#E5E7EB; }"
            "QListWidget::item { padding:5px; }"
            "QListWidget::item:selected { background:#3B9FE8; color:white; }"
        )
        devices_row.addWidget(self.lst_devices, 1)

        self.btn_revoke_device = QPushButton("Revoke Selected")
        self.btn_revoke_device.setStyleSheet(
            "QPushButton { background:#27272A; color:#F05D6C; border:1px solid #3F3F46;"
            "border-radius:5px; padding:6px 12px; }"
            "QPushButton:hover { border-color:#F05D6C; }"
        )
        self.btn_revoke_device.clicked.connect(self._revoke_selected_device)
        self.btn_revoke_device.setAccessibleName("Revoke selected device")
        self.btn_revoke_device.hide()
        devices_row.addWidget(self.btn_revoke_device)
        body_layout.addLayout(devices_row)

        # --- games / versions -------------------------------------------------
        self.games_split = QSplitter(Qt.Orientation.Horizontal)
        self.games_split.setChildrenCollapsible(False)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        lbl_games = QLabel("Games in your cloud")
        lbl_games.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        lbl_games.setStyleSheet("color: #FFFFFF;")
        left_layout.addWidget(lbl_games)
        self.lst_games = QListWidget()
        self.lst_games.currentRowChanged.connect(self._on_game_selected)
        self.lst_games.setStyleSheet(
            "QListWidget { background:#121214; border:1px solid #27272A; border-radius:6px; color:#E5E7EB; }"
            "QListWidget::item { padding:7px; }"
            "QListWidget::item:selected { background:#3B9FE8; color:white; }"
        )
        left_layout.addWidget(self.lst_games)
        self.games_split.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        lbl_versions = QLabel("Stored versions")
        lbl_versions.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        lbl_versions.setStyleSheet("color: #FFFFFF;")
        right_layout.addWidget(lbl_versions)
        self.history_timeline = SaveHistoryTimeline()
        self.history_timeline.setMinimumHeight(180)
        self.history_timeline.entry_selected.connect(self._on_history_entry_selected)
        ver_btn_row = QHBoxLayout()
        ver_btn_row.setSpacing(6)
        self.btn_restore = QPushButton("Restore selected version")
        self.btn_restore.setStyleSheet(
            "QPushButton { background:#3B9FE8; color:#FFFFFF; border:1px solid #2563EB;"
            "border-radius:5px; padding:6px 12px; font-weight:bold; }"
            "QPushButton:hover { background:#2563EB; }"
        )
        self.btn_restore.clicked.connect(self._restore_selected_version)
        self.btn_restore.setAccessibleName("Restore selected cloud save version")
        ver_btn_row.addWidget(self.btn_restore)

        self.btn_delete_generation = QPushButton("Delete selected version")
        self.btn_delete_generation.setStyleSheet(
            "QPushButton { background:#27272A; color:#F05D6C; border:1px solid #3F3F46;"
            "border-radius:5px; padding:6px 12px; }"
            "QPushButton:hover { border-color:#F05D6C; }"
        )
        self.btn_delete_generation.clicked.connect(self._delete_selected_version)
        self.btn_delete_generation.setAccessibleName("Delete selected cloud save version")
        ver_btn_row.addWidget(self.btn_delete_generation)
        right_layout.addWidget(self.history_timeline, 1)
        right_layout.addLayout(ver_btn_row)
        self.games_split.addWidget(right_panel)
        self.games_split.setStretchFactor(0, 3)
        self.games_split.setStretchFactor(1, 2)
        body_layout.addWidget(self.games_split, 1)

        # --- footer actions ----------------------------------------------------
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_refresh.setAccessibleName("Refresh cloud account data")
        footer.addWidget(self.btn_refresh)
        self.btn_auth_toggle = QPushButton("Sign In…")
        self.btn_auth_toggle.clicked.connect(self._auth_action)
        self.btn_auth_toggle.setAccessibleName("Cloud account connection")
        footer.addWidget(self.btn_auth_toggle)
        footer.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_close.setDefault(True)
        btn_close.setAccessibleName("Close cloud account manager")
        footer.addWidget(btn_close)
        body_layout.addLayout(footer)

        add_soft_shadow(self)

        self._data_ready.connect(self._apply_data)
        self._op_done.connect(self._apply_op)
        # The manager fetches a fresh listing while open; when it closes,
        # push a status re-check so badges reflect anything new immediately.
        self.finished.connect(self._notify_ancestor_poll)
        self.reload()

    # ------------------------------------------------------------------ #
    # Data loading                                                        #
    # ------------------------------------------------------------------ #

    def _start_task(self, name, work, on_complete):
        registry = getattr(self.parent(), "operation_registry", None)
        operation = None
        if registry is not None:
            operation = registry.start(
                name.replace("SafeLauncher-", "").replace("-", " ").strip(),
                category="Cloud Account",
            )

        if self.request_manager is not None:
            key = self.cloud_account_service.request_key("cloud-account-task", name)
            handle = self.request_manager.request(
                key,
                lambda token: (token.raise_if_cancelled(), work(), token.raise_if_cancelled())[1],
                priority=RequestPriority.NORMAL,
                timeout_seconds=60,
            )
            if operation is not None:
                operation.cancel = handle.cancel
                operation.retry = lambda: self._start_task(name, work, on_complete)
            request_id = handle.request_id

            def _deliver(result):
                if result.status in {ResourceStatus.IDLE, ResourceStatus.LOADING}:
                    return
                binding = self._resource_bindings.pop(request_id, None)
                if binding is not None:
                    binding.close()
                self._deliver_managed_result(operation, on_complete, result)

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
            operation.retry = lambda: self._start_task(name, work, on_complete)
            worker.error_occurred.connect(
                lambda error, op_id=operation.operation_id: registry.fail(op_id, error)
            )
        return worker

    def _deliver_managed_result(self, operation, on_complete, result) -> None:
        try:
            if isinstance(result, Exception):
                result = {"error": str(result)}
        except Exception as error:
            result = {"error": str(error)}
        if hasattr(result, "status"):
            if result.status == ResourceStatus.READY:
                value = result.value
            elif result.status == ResourceStatus.CANCELLED:
                if operation is not None:
                    operation_registry = getattr(self.parent(), "operation_registry", None)
                    if operation_registry is not None:
                        operation_registry.finish(operation.operation_id, state="cancelled")
                return
            else:
                value = {"error": str(result.error or result.status.value)}
        else:
            value = result
        if operation is not None:
            operation_registry = getattr(self.parent(), "operation_registry", None)
            if operation_registry is not None:
                operation_registry.finish_result(operation.operation_id, value)
        on_complete(value)

    def _bind_cloud_operation(self, name, handle, on_complete, transform):
        """Deliver a managed cloud-operation result through the dialog UI bridge."""
        registry = getattr(self.parent(), "operation_registry", None)
        operation = None
        if registry is not None:
            operation = registry.start(
                name.replace("SafeLauncher-", "").replace("-", " ").strip(),
                category="Cloud Account",
            )
            operation.cancel = handle.cancel
        request_id = handle.request_id

        def _deliver(result):
            if result.status in {ResourceStatus.IDLE, ResourceStatus.LOADING}:
                return
            binding = self._resource_bindings.pop(request_id, None)
            if binding is not None:
                binding.close()
            if result.status == ResourceStatus.CANCELLED:
                if operation is not None:
                    registry.finish(operation.operation_id, state="cancelled")
                return
            if result.status == ResourceStatus.READY:
                value = transform(result.value)
            else:
                value = {"error": str(result.error or result.status.value)}
            if operation is not None:
                registry.finish_result(operation.operation_id, value)
            on_complete(value)

        self._resource_bindings[request_id] = bind_request(
            self.request_manager,
            handle,
            _deliver,
            self,
            cancel_on_close=True,
        )
        return handle

    def closeEvent(self, event):
        if self.request_manager is not None:
            for binding in tuple(self._resource_bindings.values()):
                binding.close()
            self._resource_bindings.clear()
        self._task_supervisor.cancel_all(100)
        if self._task_supervisor.has_running_tasks():
            QTimer.singleShot(100, self.close)
            event.ignore()
            return
        super().closeEvent(event)

    def reload(self):
        if self._busy:
            return
        self._busy = True
        self.lbl_quota_text.setText("Loading…")
        self._start_task("SafeLauncher-AccountLoad", self._load_worker, self._data_ready.emit)

    def _load_worker(self):
        try:
            from core.cloud_backend import get_site_url
            site = get_site_url()
            if not site:
                return {"ok": None}   # not connected state
            if (
                self.request_manager is not None
                and self.cloud_account_service.request_manager is not None
            ):
                handle = self.cloud_account_service.request_snapshot(
                    priority=RequestPriority.CRITICAL,
                    tag="account_dialog",
                )
                result = handle.future.result(timeout=25)
                if not result.usable:
                    # A stale cache may be exposed through the manager state
                    # after an offline/error completion, even when the Future
                    # carries the transport failure state.
                    state = self.request_manager.state(handle.key)
                    if state.usable:
                        result = state
                if not result.usable:
                    raise result.error or RuntimeError("Cloud account data unavailable")
                snapshot = CloudAccountSnapshot.from_payload(result.value)
                is_stale = result.status == ResourceStatus.STALE
            else:
                snapshot = self.cloud_account_service.snapshot()
                is_stale = False
            return {
                "ok": {
                    "email": snapshot.overview.get("email") or "SafeLauncher Cloud",
                    "listing": snapshot.listing,
                    "overview": snapshot.overview,
                },
                "stale": is_stale,
            }
        except Exception as e:
            logger.warning(f"Account data load failed: {e}")
            from core.cloud_backend import describe_cloud_error
            return {
                "error": describe_cloud_error(e),
                "status": getattr(e, "status_code", 0) or getattr(e, "status", 0),
            }

    def _apply_data(self, payload: dict):
        self._busy = False
        if "error" in payload:
            is_missing_endpoint = payload.get("status") == 404
            self.lbl_email.setText("Backend endpoint missing" if is_missing_endpoint else "Cloud unreachable")
            self.lbl_subject.setText(
                "The backend answered, but its SafeLauncher API is not deployed."
                if is_missing_endpoint else "Check the endpoint and Secret Access Key."
            )
            self.lbl_quota_text.setText(payload["error"])
            self.btn_auth_toggle.setText("Retry")
            self._populate_devices([])
            self._populate_games([])
            return

        snapshot = payload.get("ok")
        if snapshot is None:
            self._render_signed_out()
            return

        overview = snapshot["overview"]
        listing = snapshot["listing"]
        self._quota = {"used": listing.get("bytesUsed", 0),
                       "total": listing.get("quotaBytes", 1)}
        from core.cloud_backend import get_site_url
        site = get_site_url()
        concurrent = overview.get("concurrentDevices", 1)
        dev_summary = f" · {concurrent} device(s) online" if concurrent else ""
        is_stale = bool(payload.get("stale"))
        self.lbl_email.setText(
            "Private Cloud Connected · using cached data" if is_stale else "Private Cloud Connected"
        )
        self.lbl_subject.setText(
            f"Endpoint: {site}{dev_summary} · Server-enforced quota"
            + (" · refresh pending" if is_stale else "")
        )
        self._style_avatar("C", ok=True)

        used_bytes = self._quota["used"]
        total_bytes = self._quota["total"]
        pct = min(1.0, used_bytes / max(1, total_bytes))
        self.bar_quota.setValue(int(pct * 1000))
        from core.cloud_backend import BASE_FREE_QUOTA_BYTES
        over_free_tier = used_bytes > BASE_FREE_QUOTA_BYTES
        chunk_color = "#F59E0B" if over_free_tier else (
            "#3B9FE8" if pct < 0.75 else ("#EAB308" if pct < 0.92 else "#EF4444")
        )
        quota_style = self.bar_quota.styleSheet()
        for old_color in ("#3B9FE8", "#EAB308", "#EF4444", "#F59E0B"):
            quota_style = quota_style.replace(f"stop:0 {old_color}", f"stop:0 {chunk_color}")
        self.bar_quota.setStyleSheet(quota_style)
        free = total_bytes - used_bytes
        tier_note = (
            " · Free tier exceeded — referrals can expand storage"
            if over_free_tier else " · 1 GB free — referrals can expand storage"
        )
        self.lbl_quota_text.setStyleSheet(
            f"color: {'#F59E0B' if over_free_tier else '#9CA3AF'}; font-size: 11px;"
        )
        self.lbl_quota_text.setText(
            f"{format_bytes(used_bytes)} of {format_bytes(total_bytes)} "
            f"used ({format_bytes(max(0, free))} free) · max {format_bytes(overview.get('maxSaveBytes', 0))} per save · "
            f"keeping the last {overview.get('keepVersions', '?')} versions{tier_note}"
        )

        self.btn_auth_toggle.setText("Disconnect")
        self._populate_devices(overview.get("devices", []))
        self._populate_games(listing.get("games", []))

    def _populate_devices(self, devices):
        """Fill the device list and expose revocation for remote entries."""
        self._devices = devices or []
        self.lst_devices.clear()
        if not self._devices:
            item = QListWidgetItem("No registered devices.")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.lst_devices.addItem(item)
            self.btn_revoke_device.hide()
            return
        for d in self._devices:
            seen = format_datetime_timestamp(d.get("lastSeenAt", 0) / 1000, "%H:%M")
            state = "online" if d.get("isOnline") else "offline"
            item = QListWidgetItem(
                f"{d.get('deviceName', 'Device')} ({d.get('platform', '?')}) · "
                f"{state} · last seen {seen} · id {d.get('deviceId', '')[:8]}"
            )
            item.setData(Qt.ItemDataRole.UserRole, d.get("deviceId", ""))
            self.lst_devices.addItem(item)
        self.btn_revoke_device.show()

    def _revoke_selected_device(self):
        row = self.lst_devices.currentRow()
        if row < 0 or row >= len(self._devices):
            return
        device = self._devices[row]
        device_id = device.get("deviceId", "")
        if not device_id:
            return
        answer = QMessageBox.question(
            self, "Revoke Device",
            f"Revoke '{device.get('deviceName', device_id)}'?\n\nIt will be removed from the device "
            "list and its heartbeats will no longer register it. (Note: all devices share this "
            "deployment's Secret Key; rotating the key is required to block access completely.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.btn_revoke_device.setEnabled(False)

        if self.request_manager is not None and self.cloud_account_service.request_manager is not None:
            handle = self.cloud_account_service.request_revoke_device(
                device_id,
                priority=RequestPriority.NORMAL,
                tag="account_device_revoke",
            )
            self._bind_cloud_operation(
                "SafeLauncher-DeviceRevoke",
                handle,
                self._op_done.emit,
                lambda revoked: {"revoked": device_id} if revoked else {
                    "error": "The device could not be revoked.",
                },
            )
            return
        self._op_done.emit({
            "error": "Cloud service is not available in this view.",
            "guidance": "Open Cloud Center from the main SafeLauncher window and try again.",
        })

    def _render_signed_out(self):
        self.lbl_email.setText("Not connected")
        self.lbl_subject.setText("Convex backend not configured")
        self._style_avatar("?", ok=False)
        self.bar_quota.setValue(0)
        self.lbl_quota_text.setText(
            "Configure your private Convex cloud in Settings → Cloud or via "
            "'safelauncher --setup-cloud' to store encrypted saves."
        )
        self.btn_auth_toggle.setText("Setup Wizard…")
        self._populate_devices([])
        self._populate_games([])

    def _populate_games(self, games):
        self._games = games
        self.lst_games.clear()
        self.history_timeline.set_message("Select a game to see its retained history.")
        self.btn_restore.setEnabled(False)
        self.btn_delete_generation.setEnabled(False)
        if not games:
            item = QListWidgetItem("No saves uploaded yet.")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.lst_games.addItem(item)
            return
        for g in sorted(games, key=lambda x: x.get("latestSourceMtime", 0), reverse=True):
            count = len(g.get("versions", []))
            size_txt = format_bytes(g.get("totalBytes", 0))
            item = QListWidgetItem(
                f"{g.get('displayName', g.get('nameKey'))}\n"
                f"{count} version(s) · {size_txt} · updated {_relative_time(g.get('latestSourceMtime', 0))}"
            )
            item.setData(Qt.ItemDataRole.UserRole, g.get("nameKey"))
            self.lst_games.addItem(item)

    # ------------------------------------------------------------------ #
    # Interactions                                                        #
    # ------------------------------------------------------------------ #

    def _on_game_selected(self, row: int):
        self.btn_restore.setEnabled(False)
        self.btn_delete_generation.setEnabled(False)
        if row < 0 or row >= len(self._games):
            self.history_timeline.set_message("Select a game to see its retained history.")
            return
        game = sorted(self._games, key=lambda x: x.get("latestSourceMtime", 0), reverse=True)[row]
        entries = []
        for version in game.get("versions", []):
            if not isinstance(version, dict):
                continue
            entry = dict(version)
            entry.update({
                "source": "cloud",
                "display_name": f"Cloud save version {entry.get('version', '?')}",
                "size_bytes": entry.get("sizeBytes", 0),
                "created_at": entry.get("createdAt", 0),
                "uploaded_at": entry.get("uploadedAt", 0),
            })
            entries.append(entry)
        self.history_timeline.set_entries(entries)
        has_entries = bool(self.history_timeline.entries())
        self.btn_restore.setEnabled(has_entries)
        self.btn_delete_generation.setEnabled(has_entries)

    def _on_history_entry_selected(self, _entry=None):
        enabled = self.history_timeline.selected_entry() is not None
        self.btn_restore.setEnabled(enabled)
        self.btn_delete_generation.setEnabled(enabled)

    def _restore_selected_version(self):
        game_item = self.lst_games.currentItem()
        selected = self.history_timeline.selected_entry()
        if not game_item or selected is None:
            QMessageBox.information(self, "Nothing Selected",
                                    "Pick a game and a stored version first.")
            return
        name_key = game_item.data(Qt.ItemDataRole.UserRole)
        version = selected.raw.get("version")
        if version is None:
            return

        from database import GameDatabase
        db = GameDatabase()
        try:
            all_games = db.get_all_games()
        finally:
            try:
                db.close()
            except Exception:
                pass
        display_name = game_item.text().split("\n")[0].strip()
        matched_game = self.cloud_account_service.match_game_to_library(
            name_key, display_name, all_games
        )


        if not matched_game:
            QMessageBox.warning(
                self, "Game Not Found",
                f"Could not find an installed library game matching '{display_name}'.\n"
                "Please ensure the game is added to your SafeLauncher library."
            )
            return

        confirm = confirm_restore(
            self,
            game_name=matched_game.name,
            entry=selected,
            target_path=matched_game.path,
            title="Restore cloud save version",
        )
        if not confirm:
            return

        if self._busy:
            return
        self._busy = True
        self.lbl_quota_text.setText(f"Restoring cloud save version {version} for '{matched_game.name}'…")

        if self.cloud_operation_service is not None:
            target = CloudOperationTarget(
                matched_game.id,
                matched_game.name,
                matched_game.path,
                matched_game.steam_id or "",
            )
            handle = self.cloud_operation_service.request_restore(
                target,
                target_version=int(version),
                tag="account-history",
            )

            def _restore_result(result):
                if result.success:
                    return {
                        "restored": f"Successfully restored cloud save version {version} for '{matched_game.name}'.",
                        "name": matched_game.name,
                    }
                return {
                    "error": result.error or f"Failed to restore cloud save version {version} for '{matched_game.name}'.",
                    "guidance": result.guidance,
                    "category": result.category,
                }

            self._bind_cloud_operation(
                "SafeLauncher-SaveRestore",
                handle,
                self._op_done.emit,
                _restore_result,
            )
            return

        self._op_done.emit({
            "error": "Cloud service is not available in this view.",
            "guidance": "Open Cloud Center from the main SafeLauncher window and try again.",
        })

    def _delete_selected_version(self):
        game_item = self.lst_games.currentItem()
        selected = self.history_timeline.selected_entry()
        if not game_item or selected is None:
            QMessageBox.information(self, "Nothing selected",
                                    "Pick a game and a stored version first.")
            return
        name_key = game_item.data(Qt.ItemDataRole.UserRole)
        display_name = game_item.text().split("\n")[0].strip()
        version = selected.raw.get("version")
        confirm = confirm_delete(
            self,
            game_name=display_name or "this game",
            version=version,
        )
        if not confirm:
            return
        if self._busy:
            return
        self._busy = True

        if self.request_manager is not None and self.cloud_account_service.request_manager is not None:
            handle = self.cloud_account_service.request_delete_generation(
                name_key,
                int(version),
                priority=RequestPriority.NORMAL,
                tag="account_version_delete",
            )
            self._bind_cloud_operation(
                "SafeLauncher-SaveDelete",
                handle,
                self._op_done.emit,
                lambda deleted: {"deleted": bool(deleted), "name": display_name}
                if deleted else {"error": "The cloud save version could not be deleted."},
            )
            return

        self._op_done.emit({
            "error": "Cloud service is not available in this view.",
            "guidance": "Open Cloud Center from the main SafeLauncher window and try again.",
        })

    def _apply_op(self, payload: dict):
        self._busy = False
        if "error" in payload:
            guidance = payload.get("guidance", "")
            QMessageBox.warning(self, "Operation Failed", f"{payload['error']}\n\n{guidance}".strip())
            self.lbl_quota_text.setText("Operation failed.")
            return
        if "restored_err" in payload:
            QMessageBox.critical(self, "Restore Failed", payload["restored_err"])
            self.lbl_quota_text.setText("Restore failed.")
            return
        if "restored" in payload:
            QMessageBox.information(self, "Restore Completed", payload["restored"])
            self.lbl_quota_text.setText(payload["restored"])
            self.reload()
            return
        if "revoked" in payload:
            self.btn_revoke_device.setEnabled(True)
            self.lbl_quota_text.setText("Device revoked.")
            self.reload()
            return
        name_val = payload.get("name", "game")
        self._show_toast_like(name_val)
        self.reload()

    def _show_toast_like(self, game_name: str):
        self.lbl_quota_text.setText(f"Deleted an older cloud save version for '{game_name}'.")

    def _notify_ancestor_cloud_changed(self):
        """Cloud config just changed here — make the main window drop every
        cached badge/status right away instead of after settings closes."""
        parent = self.parent()
        while parent is not None:
            if hasattr(parent, "_refresh_cloud_after_config_change"):
                parent._refresh_cloud_after_config_change()
                return
            parent = parent.parent()

    def _notify_ancestor_poll(self, *_):
        parent = self.parent()
        while parent is not None:
            if hasattr(parent, "request_cloud_recheck"):
                parent.request_cloud_recheck([], "manager-close")
                return
            parent = parent.parent()

    def _auth_action(self):
        from core.cloud_backend import get_site_url
        if get_site_url():
            confirm = QMessageBox.question(
                self, "Disconnect Cloud",
                "Disconnect from this Convex cloud save backend?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm == QMessageBox.StandardButton.Yes:
                self.cloud_account_service.set_mode("local")
                from PyQt6.QtCore import QSettings
                from core.secret_store import delete_secret
                settings = QSettings("SafeLauncher", "SafeLauncher")
                settings.remove("convex_site_url")
                delete_secret("cloud_secret_key")
                delete_secret("convex_deploy_key")
                self.reload()
                self._notify_ancestor_cloud_changed()
        else:
            from ui.dialogs.cloud_wizard_dialog import CloudWizardDialog
            wizard = CloudWizardDialog(self)
            if wizard.exec():
                self.reload()
                self._notify_ancestor_cloud_changed()

    def _on_backend_changed(self, index: int):
        self.cloud_account_service.set_mode(self.combo_backend.itemData(index) or "local")

    def _style_avatar(self, text: str, ok: bool):
        self.lbl_avatar.setText(text)
        color = "#3B9FE8" if ok else "#4B5563"
        self.lbl_avatar.setStyleSheet(
            "QLabel { background: %s; border-radius: 22px; color: white; "
            "font-size: 18px; font-weight: bold; }" % color
        )
