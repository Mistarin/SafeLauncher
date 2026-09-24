"""
Save Manager Dialog for SafeLauncher.
Visual save inspector powered by LudusaviDetector and ZipBackupManager.
"""

import os
import time
import json
from datetime import datetime
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QFileDialog, QFrame, QScrollArea, QMessageBox, QCheckBox, QProgressBar,
    QTabWidget, QApplication
)
from PyQt6.QtWidgets import QToolButton
from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QIcon, QDesktopServices
from PyQt6.QtCore import QUrl

from ui.icons import get_icon, get_app_icon
from ui.components.sidebar import DialogTitleBar
from ui.components.popup_shell import PopupDialog
from ui.components.check_field import CheckField as QCheckBox
from core.ludusavi_detector import LudusaviDetector, SaveLocation
from core.save_validation import (
    describe_validation_failures,
    snapshot_from_validation,
    validate_save_locations,
)
from core.save_models import SaveOperationResult
from core.save_state import SaveStateStore
from core.cloud_operations import classify_cloud_error
from core.cloud_operation_service import CloudOperationTarget
from core.request_contracts import RequestPriority, ResourceStatus
from core.zip_backup import ZipBackupManager
from core.safe_thread import TaskSupervisor
from core.logger import get_logger
from core.date_formatting import format_datetime_timestamp
from ui.resource_binding import ResourceBinding, bind_request
from ui.components.save_history_timeline import SaveHistoryTimeline
from ui.components.cloud_ui import (
    CloudStatusPanel,
    cloud_progress,
    confirm_restore,
    set_accessible_status,
    set_cloud_focus_order,
    set_cloud_initial_focus,
)

logger = get_logger("SaveManagerDialog")


def format_bytes(size_bytes: int) -> str:
    """Format bytes to human readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


class SaveManagerDialog(PopupDialog):
    """Interactive save snapshot dialog displaying detected locations and metadata."""

    _restore_done = pyqtSignal(bool, str)
    _upload_done = pyqtSignal(object)
    _history_loaded = pyqtSignal(object)

    def __init__(self, game_id: int, game_name: str, game_path: str, steam_id: str = "", parent=None, cloud_coordinator=None):
        super().__init__(f"Save Manager: {game_name}", parent)
        self.game_id = game_id
        self.game_name = game_name
        self.game_path = game_path
        self.steam_id = steam_id
        # Retain the old parameter for embedders, but never construct or use
        # a dialog-local coordinator. Cloud work must come from the managed
        # application services injected by MainWindow.
        self.cloud_center_service = getattr(parent, "cloud_center_service", None)
        self.cloud_operation_service = getattr(parent, "cloud_operation_service", None)
        self.request_manager = getattr(parent, "request_manager", None)
        self.backup_mgr = ZipBackupManager()
        self.save_state_store = getattr(parent, "save_state_store", None) or SaveStateStore()
        self.save_locations: list[SaveLocation] = []
        self.checkboxes: list[tuple[QCheckBox, SaveLocation]] = []
        self._file_path_labels: list[QLabel] = []
        self._show_file_details = False
        # Every operation that can touch the cloud or package saves belongs to
        # this dialog. Keeping explicit references prevents a QThread from
        # being garbage-collected while it is running and lets closeEvent wait
        # for cooperative cancellation instead of racing a deleted widget.
        self._task_supervisor = TaskSupervisor(self, logger)
        self._resource_bindings: dict[str, ResourceBinding] = {}
        self._closing = False
        self._restore_done.connect(self._on_restore_done)
        self._upload_done.connect(self._on_upload_done)
        self._history_loaded.connect(self._on_history_loaded)


        available = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
        available_width = max(1, available.width() - 32) if available else None
        available_height = max(1, available.height() - 48) if available else None
        min_width = min(600, available_width) if available_width else 600
        min_height = min(500, available_height) if available_height else 500
        self.setMinimumSize(min_width, min_height)
        self.resize(
            min(760, max(min_width, available_width)) if available_width else 760,
            min(620, max(min_height, available_height)) if available_height else 620,
        )
        self.setSizeGripEnabled(True)
        body_layout = self.popup_layout(margins=(20, 16, 20, 16), spacing=12)

        # Header Info Banner
        header_frame = QFrame()
        header_frame.setStyleSheet("""
            QFrame {
                background: #18181B;
                border: none;
                border-radius: 10px;
                padding: 12px;
            }
        """)
        h_layout = QVBoxLayout(header_frame)
        h_layout.setContentsMargins(4, 4, 4, 4)
        h_layout.setSpacing(4)

        info_title = QLabel(f"<b>{game_name}</b>")
        info_title.setFont(QFont("Arial", 12))
        info_title.setStyleSheet("color: #F5F7FA;")
        h_layout.addWidget(info_title)

        source_note = "Ludusavi CLI Engine" if LudusaviDetector.is_cli_available() else "Heuristic Wine/UMU Prefix Detector"
        self.lbl_status = QLabel(f"<font color='#6F7682'>Discovery Engine:</font> <font color='#3B9FE8'>{source_note}</font>")
        self.lbl_status.setStyleSheet("font-size: 11px;")
        set_accessible_status(
            self.lbl_status,
            "Save manager status",
            "Current local save discovery and cloud operation status.",
        )
        h_layout.addWidget(self.lbl_status)

        body_layout.addWidget(header_frame)

        # Persistent recovery surface: errors remain actionable instead of
        # disappearing into a one-shot message box.
        self.recovery_frame = QFrame()
        self.recovery_frame.setStyleSheet("""
            QFrame#saveRecoveryFrame {
                background: rgba(240, 93, 108, 0.10);
                border: 1px solid rgba(240, 93, 108, 0.32);
                border-radius: 8px;
            }
            QLabel { background: transparent; }
            QPushButton {
                background: rgba(255, 255, 255, 0.06);
                color: #F4F4F5;
                border: none;
                border-radius: 5px;
                padding: 5px 8px;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.12); }
        """)
        self.recovery_frame.setObjectName("saveRecoveryFrame")
        recovery_layout = QVBoxLayout(self.recovery_frame)
        recovery_layout.setContentsMargins(12, 10, 12, 10)
        recovery_layout.setSpacing(6)
        self.recovery_title = QLabel("Save operation needs attention")
        self.recovery_title.setStyleSheet("color: #F05D6C; font-weight: 700;")
        recovery_layout.addWidget(self.recovery_title)
        self.recovery_message = QLabel()
        self.recovery_message.setWordWrap(True)
        self.recovery_message.setStyleSheet("color: #E4E4E7; font-size: 11px;")
        recovery_layout.addWidget(self.recovery_message)
        recovery_buttons = QHBoxLayout()
        self.btn_recovery_rescan = QPushButton("Rescan Saves")
        self.btn_recovery_rescan.clicked.connect(self._rescan_and_revalidate)
        recovery_buttons.addWidget(self.btn_recovery_rescan)
        self.btn_recovery_retry = QPushButton("Retry")
        self.btn_recovery_retry.clicked.connect(self._retry_last_operation)
        recovery_buttons.addWidget(self.btn_recovery_retry)
        self.btn_recovery_cloud = QPushButton("Cloud Settings")
        self.btn_recovery_cloud.clicked.connect(self._open_cloud_settings)
        recovery_buttons.addWidget(self.btn_recovery_cloud)
        self.btn_recovery_copy = QPushButton("Copy Details")
        self.btn_recovery_copy.clicked.connect(self._copy_error_details)
        recovery_buttons.addWidget(self.btn_recovery_copy)
        self.btn_recovery_logs = QPushButton("Open Logs")
        self.btn_recovery_logs.clicked.connect(self._open_operation_logs)
        recovery_buttons.addWidget(self.btn_recovery_logs)
        recovery_buttons.addStretch()
        recovery_layout.addLayout(recovery_buttons)
        self.recovery_frame.setVisible(False)
        body_layout.addWidget(self.recovery_frame)

        # Tabs container
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: none;
                border-radius: 8px;
                background: #141416;
            }
            QTabBar::tab {
                background: #18181B;
                color: #A7ADB8;
                border: none;
                border-bottom: 2px solid transparent;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 8px 16px;
                margin-right: 4px;
                font-size: 11px;
                font-weight: 500;
            }
            QTabBar::tab:selected {
                background: #1E293B;
                color: #F5F7FA;
                border-bottom-color: #3B9FE8;
            }
        """)

        # ── Tab 1: Live Save Files ──
        self.tab_files = QWidget()
        tab_files_layout = QVBoxLayout(self.tab_files)
        tab_files_layout.setContentsMargins(12, 12, 12, 12)
        tab_files_layout.setSpacing(10)

        list_header = QHBoxLayout()
        list_lbl = QLabel("Detected Save Locations")
        list_lbl.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        list_lbl.setStyleSheet("color: #F5F7FA;")
        list_header.addWidget(list_lbl)
        list_header.addStretch()

        details_toggle = QToolButton()
        details_toggle.setText("Technical details")
        details_toggle.setCheckable(True)
        details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        details_toggle.setAccessibleName("Show save technical details")
        details_toggle.setToolTip("Show local save paths and diagnostic details")
        details_toggle.toggled.connect(self._toggle_file_details)
        list_header.addWidget(details_toggle)

        btn_rescan = QPushButton("Rescan")
        btn_rescan.setIcon(get_icon("ph.arrows-clockwise-bold", color="#A7ADB8"))
        btn_rescan.setIconSize(QSize(12, 12))
        btn_rescan.setFixedHeight(24)
        btn_rescan.setStyleSheet("""
            QPushButton {
                background: #1A1E26;
                color: #A7ADB8;
                border: 1px solid #252A33;
                border-radius: 4px;
                padding: 0 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                color: #F5F7FA;
                border-color: #3B9FE8;
            }
        """)
        btn_rescan.clicked.connect(self._scan_saves)
        list_header.addWidget(btn_rescan)
        tab_files_layout.addLayout(list_header)

        # Scroll Area for Save Locations
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background: #141416;
                border: none;
                border-radius: 8px;
            }
        """)

        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(10, 10, 10, 10)
        self.scroll_layout.setSpacing(8)
        self.scroll_layout.addStretch()
        self.scroll_area.setWidget(self.scroll_content)
        tab_files_layout.addWidget(self.scroll_area)

        # Action Buttons Footer for Tab 1
        footer_layout = QHBoxLayout()
        footer_layout.setSpacing(10)

        btn_import = QPushButton("Import local save archive (.zip)")
        btn_import.setIcon(get_app_icon("import"))
        btn_import.setFixedHeight(36)
        btn_import.setStyleSheet("""
            QPushButton {
                background: #1A1E26;
                color: #F5F7FA;
                border: 1px solid #252A33;
                border-radius: 6px;
                padding: 0 16px;
                font-weight: 500;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #252A33;
                border-color: #3B9FE8;
            }
        """)
        btn_import.clicked.connect(self._import_snapshot)
        footer_layout.addWidget(btn_import)

        self.btn_cloud = QPushButton("Restore latest cloud save")
        self.btn_cloud.setIcon(get_icon("ph.cloud-arrow-down-bold", "#3B9FE8"))
        self.btn_cloud.setFixedHeight(36)
        self.btn_cloud.setStyleSheet("""
            QPushButton {
                background: #1A1E26;
                color: #3B9FE8;
                border: 1px solid #2563EB;
                border-radius: 6px;
                padding: 0 16px;
                font-weight: 500;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #1E293B;
                border-color: #60A5FA;
            }
        """)
        self.btn_cloud.clicked.connect(self._restore_from_cloud)
        self.btn_cloud.setAccessibleName("Restore latest cloud save")
        footer_layout.addWidget(self.btn_cloud)

        self.btn_upload = QPushButton("Upload local save")
        self.btn_upload.setIcon(get_icon("ph.cloud-arrow-up-bold", "#35C98A"))
        self.btn_upload.setFixedHeight(36)
        self.btn_upload.setEnabled(False)
        self.btn_upload.setStyleSheet("""
            QPushButton {
                background: #17251D;
                color: #35C98A;
                border: 1px solid #238636;
                border-radius: 6px;
                padding: 0 16px;
                font-weight: 500;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #1D3527;
                border-color: #35C98A;
            }
            QPushButton:disabled {
                background: #21262D;
                color: #6F7682;
                border-color: #30363D;
            }
        """)
        self.btn_upload.clicked.connect(self._upload_selected)
        self.btn_upload.setAccessibleName("Upload local save to cloud")
        footer_layout.addWidget(self.btn_upload)

        footer_layout.addStretch()

        self.btn_export = QPushButton("Export local save archive")
        self.btn_export.setIcon(get_app_icon("export"))
        self.btn_export.setFixedHeight(36)
        self.btn_export.setStyleSheet("""
            QPushButton {
                background: #238636;
                color: #FFFFFF;
                border: 1px solid #2ea043;
                border-radius: 6px;
                padding: 0 18px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #2ea043;
            }
            QPushButton:disabled {
                background: #21262d;
                color: #6F7682;
                border-color: #30363d;
            }
        """)
        self.btn_export.clicked.connect(self._export_selected)
        footer_layout.addWidget(self.btn_export)
        self.btn_export.setAccessibleName("Export local save archive")
        tab_files_layout.addLayout(footer_layout)

        self.tabs.addTab(self.tab_files, "Live Save Files")

        # ── Tab 2: Cloud versions and local backups ──
        self.tab_history = QWidget()
        tab_history_layout = QVBoxLayout(self.tab_history)
        tab_history_layout.setContentsMargins(12, 12, 12, 12)
        tab_history_layout.setSpacing(10)

        history_header = QHBoxLayout()
        lbl_hist = QLabel("Cloud save versions & local backups")
        lbl_hist.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        lbl_hist.setStyleSheet("color: #F5F7FA;")
        history_header.addWidget(lbl_hist)
        history_header.addStretch()

        btn_refresh_hist = QPushButton("Refresh")
        btn_refresh_hist.setIcon(get_icon("ph.arrows-clockwise-bold", color="#A7ADB8"))
        btn_refresh_hist.setIconSize(QSize(12, 12))
        btn_refresh_hist.setFixedHeight(24)
        btn_refresh_hist.setStyleSheet("""
            QPushButton {
                background: #1A1E26;
                color: #A7ADB8;
                border: 1px solid #252A33;
                border-radius: 4px;
                padding: 0 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                color: #F5F7FA;
                border-color: #3B9FE8;
            }
        """)
        btn_refresh_hist.clicked.connect(self._load_history)
        btn_refresh_hist.setAccessibleName("Refresh cloud save versions")
        history_header.addWidget(btn_refresh_hist)
        tab_history_layout.addLayout(history_header)

        self.cloud_status_panel = CloudStatusPanel(self, compact=True)
        self.cloud_status_panel.action_requested.connect(self._load_history)
        self.lbl_history_state = self.cloud_status_panel.lbl_status
        tab_history_layout.addWidget(self.cloud_status_panel)

        self.history_timeline = SaveHistoryTimeline()
        # Compatibility alias for older integrations that only inspect the
        # history collection. The timeline remains the sole selection owner.
        self.lst_history = self.history_timeline
        self.history_timeline.setMinimumHeight(180)
        self.history_timeline.entry_selected.connect(self._on_history_selection_changed)
        tab_history_layout.addWidget(self.history_timeline)

        history_footer = QHBoxLayout()
        history_footer.setSpacing(10)

        lbl_hint = self.info_hint(
            "Select a saved version above to restore it to your local game.",
            tooltip="Restoring a version preserves the current local save in a safety backup before replacement.",
        )
        history_footer.addWidget(lbl_hint)
        history_footer.addStretch()

        self.btn_restore_history = QPushButton("Restore selected version")
        self.btn_restore_history.setIcon(get_icon("ph.clock-counter-clockwise-bold"))
        self.btn_restore_history.setFixedHeight(36)
        self.btn_restore_history.setStyleSheet("""
            QPushButton {
                background: #3B9FE8;
                color: #FFFFFF;
                border: 1px solid #2563EB;
                border-radius: 6px;
                padding: 0 18px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #2563EB;
            }
            QPushButton:disabled {
                background: #21262d;
                color: #6F7682;
                border-color: #30363d;
            }
        """)
        self.btn_restore_history.setEnabled(False)
        self.btn_restore_history.clicked.connect(self._restore_selected_history_save)
        self.btn_restore_history.setAccessibleName("Restore selected cloud save version")
        history_footer.addWidget(self.btn_restore_history)

        tab_history_layout.addLayout(history_footer)
        self.tabs.addTab(self.tab_history, "Cloud Versions & Backups")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        body_layout.addWidget(self.tabs)

        self.setStyleSheet("""
            QDialog {
                background-color: #111113;
                border: none;
                border-radius: 12px;
            }
        """)

        # Run initial scan
        self._scan_saves()

        self._last_operation_retry = None
        self._last_operation_result: SaveOperationResult | None = None
        self._last_operation_log_path = os.path.expanduser("~/.local/state/safelauncher/safelauncher.log")

        set_cloud_focus_order(
            self,
            btn_import,
            self.btn_cloud,
            self.btn_upload,
            self.btn_export,
            self.tabs,
            btn_refresh_hist,
            self.btn_restore_history,
        )
        set_cloud_initial_focus(self, btn_import)

    def _start_managed_task(self, name: str, work, on_complete):
        """Run a dialog operation with an owned, observable lifetime."""
        registry = getattr(self.parent(), "operation_registry", None)
        operation = None
        if registry is not None:
            operation = registry.start(
                name.replace("SafeLauncher-", "").replace("-", " ").strip(),
                category="Save Manager",
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

    def _show_recovery(self, result: SaveOperationResult, retry=None, *, show_rescan: bool = True) -> None:
        self._last_operation_result = result
        self._last_operation_retry = retry
        self.save_state_store.set_operation(self.game_id, result)
        self.recovery_title.setText(f"{result.operation}: action required")
        message = result.error or result.guidance or "The operation could not be completed."
        if result.guidance and result.guidance not in message:
            message = f"{message}\n\n{result.guidance}"
        self.recovery_message.setText(message)
        self.btn_recovery_retry.setEnabled(retry is not None and result.retry_safe)
        self.btn_recovery_rescan.setVisible(show_rescan)
        self.btn_recovery_cloud.setVisible(result.category in {"backend_unavailable", "authentication", "quota"})
        self.btn_recovery_logs.setVisible(bool(result.log_path or self._last_operation_log_path))
        self.recovery_frame.setVisible(True)

    def _hide_recovery(self) -> None:
        self.recovery_frame.setVisible(False)
        self._last_operation_result = None
        self._last_operation_retry = None

    def _rescan_and_revalidate(self) -> None:
        self._scan_saves()
        self.recovery_message.setText("Save locations rescanned. Select readable locations and try again.")
        self.btn_recovery_retry.setEnabled(False)

    def _retry_last_operation(self) -> None:
        retry = self._last_operation_retry
        if retry is None:
            return
        self._hide_recovery()
        retry()

    def _validate_selected_locations(self, selected_locations, operation: str):
        """Return one fresh, fingerprinted snapshot or exact failures."""
        results = validate_save_locations(selected_locations)
        failures = describe_validation_failures(results)
        if failures:
            category = (
                "local_save_missing"
                if any("no longer exists" in result.reason for result in results if not result.valid)
                else "local_save_unreadable"
            )
            return [], SaveOperationResult(
                False,
                operation,
                self.game_name,
                error=failures,
                category=category,
                guidance=(
                    "Rescan Save Locations, confirm the files are readable, "
                    "and select the locations again before retrying."
                ),
                retry_safe=False,
                log_path=self._last_operation_log_path,
            )
        return snapshot_from_validation(
            self.game_name, self.game_path, results, source="save-manager"
        ), None

    @staticmethod
    def _save_operation_from_cloud_result(result) -> SaveOperationResult:
        """Keep the shared result intact at the UI boundary."""
        return result

    def _resource_operation_result(self, future, operation: str) -> SaveOperationResult:
        """Convert a manager resource completion into the dialog result model."""
        try:
            resource = future
            if hasattr(future, "result"):
                resource = future.result()
            if resource.status == ResourceStatus.READY and isinstance(resource.value, SaveOperationResult):
                return resource.value
            error = str(resource.error or f"{operation} failed.")
        except Exception as exc:
            error = str(exc)
        return SaveOperationResult(
            False,
            operation,
            self.game_name,
            error=error,
            category="backend_unavailable",
            guidance="Check the cloud connection and try again.",
        )

    def _bind_cloud_operation(self, handle, callback) -> ResourceBinding | None:
        """Deliver one cloud operation through the Qt resource bridge."""
        if self.request_manager is None:
            return None

        request_id = handle.request_id

        def _deliver(resource):
            if resource.status in {ResourceStatus.IDLE, ResourceStatus.LOADING}:
                return
            binding = self._resource_bindings.pop(request_id, None)
            try:
                callback(resource)
            finally:
                if binding is not None:
                    binding.close()

        binding = bind_request(
            self.request_manager,
            handle,
            _deliver,
            parent=self,
            cancel_on_close=True,
        )
        self._resource_bindings[request_id] = binding
        return binding

    def _close_resource_bindings(self) -> None:
        for binding in tuple(self._resource_bindings.values()):
            binding.close()
        self._resource_bindings.clear()

    def _open_cloud_settings(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "_open_settings"):
            self.hide()
            parent._open_settings()

    def _copy_error_details(self) -> None:
        result = self._last_operation_result
        if result is None:
            return
        details = {
            "operation": result.operation,
            "game": self.game_name,
            "category": result.category,
            "error": result.error,
            "guidance": result.guidance,
            "log_path": result.log_path or self._last_operation_log_path,
        }
        QApplication.clipboard().setText(json.dumps(details, indent=2, ensure_ascii=False))
        self.recovery_message.setText("Technical details copied to the clipboard.")

    def _open_operation_logs(self) -> None:
        path = (self._last_operation_result.log_path if self._last_operation_result else "") or self._last_operation_log_path
        target = path if os.path.exists(path) else os.path.dirname(path)
        if target:
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def closeEvent(self, event):
        """Do not destroy this dialog while an owned worker still runs."""
        self._closing = True
        self._close_resource_bindings()
        self._task_supervisor.cancel_all(100)
        if self._task_supervisor.has_running_tasks():
            QTimer.singleShot(100, self.close)
            event.ignore()
            return
        super().closeEvent(event)

    def _scan_saves(self):
        """Scan for save locations and populate scroll view."""
        if hasattr(self, "recovery_frame"):
            self._hide_recovery()
        # Clear existing items
        while self.scroll_layout.count() > 1:
            item = self.scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.checkboxes.clear()
        self._file_path_labels.clear()
        self.save_locations = LudusaviDetector.detect_saves(self.game_name, self.game_path, self.steam_id)
        self.save_state_store.set_locations(self.game_id, self.save_locations)
        validation_results = validate_save_locations(self.save_locations)
        self._location_validation = {
            os.path.abspath(result.location.path): result for result in validation_results
        }

        if not self.save_locations:
            empty_lbl = QLabel("No save files found yet for this title.\nThey will appear once the game is launched and creates its initial save.")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setStyleSheet("color: #6F7682; padding: 40px; font-size: 12px;")
            self.scroll_layout.insertWidget(0, empty_lbl)
            self.btn_export.setEnabled(False)
            self.btn_export.setText("Choose saves to export")
            self.btn_upload.setEnabled(False)
            return

        self.btn_export.setEnabled(False)
        self.btn_export.setText("Choose saves to export")
        self.btn_upload.setEnabled(False)

        for result in validation_results:
            loc = result.location if result.valid else result.location
            card = QFrame()
            card.setStyleSheet("""
                QFrame {
                    background: #1B1B1F;
                    border: none;
                    border-radius: 8px;
                    padding: 8px;
                }
                QFrame:hover {
                    background: #222228;
                }
            """)
            c_layout = QHBoxLayout(card)
            c_layout.setContentsMargins(6, 4, 6, 4)
            c_layout.setSpacing(10)

            cb = QCheckBox()
            cb.setChecked(False)
            cb.setEnabled(result.valid)
            cb.setToolTip(
                "Ready to package"
                if result.valid
                else f"Unavailable: {result.reason}. Rescan after fixing the path."
            )
            cb.setStyleSheet("color: #F4F4F5; background: transparent;")
            cb.stateChanged.connect(lambda _state: self._update_export_state())
            c_layout.addWidget(cb)
            self.checkboxes.append((cb, loc))

            info_vbox = QVBoxLayout()
            info_vbox.setSpacing(2)

            name_row = QHBoxLayout()
            lbl_name = QLabel(f"<b>{loc.display_name}</b>")
            lbl_name.setStyleSheet("color: #F5F7FA; font-size: 12px;")
            name_row.addWidget(lbl_name)

            size_str = format_bytes(loc.total_size_bytes)
            if result.valid:
                size_label = f"{loc.file_count} file(s) · {size_str} · Ready"
                size_color = "#35C98A"
            else:
                size_label = f"Unavailable · {result.reason}"
                size_color = "#F0A35B"
            lbl_size = QLabel(size_label)
            lbl_size.setStyleSheet(f"color: {size_color}; font-size: 11px; font-weight: 500;")
            name_row.addWidget(lbl_size)
            name_row.addStretch()
            info_vbox.addLayout(name_row)

            # Path & Date
            date_str = format_datetime_timestamp(loc.last_modified, "%H:%M", fallback="Unknown")
            lbl_path = QLabel(f"<font color='#6F7682'>{loc.path}</font> <font color='#555'>· Modified: {date_str}</font>")
            lbl_path.setStyleSheet("font-size: 10px;")
            lbl_path.setWordWrap(True)
            lbl_path.setVisible(self._show_file_details)
            self._file_path_labels.append(lbl_path)
            info_vbox.addWidget(lbl_path)

            c_layout.addLayout(info_vbox, 1)

            self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, card)

        self._update_export_state()

    def _toggle_file_details(self, expanded: bool) -> None:
        self._show_file_details = bool(expanded)
        sender = self.sender()
        if isinstance(sender, QToolButton):
            sender.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        for label in self._file_path_labels:
            label.setVisible(self._show_file_details)

    def _update_export_state(self):
        selected = any(cb.isChecked() for cb, _loc in self.checkboxes)
        self.btn_export.setEnabled(selected)
        self.btn_upload.setEnabled(selected)
        self.btn_export.setText("Export local save archive" if selected else "Choose saves to export")

    def _upload_selected(self):
        """Upload the checked detected save locations to the active cloud backend."""
        selected_locations = [loc for cb, loc in self.checkboxes if cb.isChecked()]
        if not selected_locations:
            QMessageBox.warning(self, "No Saves Selected", "Select at least one detected save location to upload.")
            return

        # Do not trust the detector's cached file counts. This catches the
        # common case where a game cleaned up its save directory after scan.
        snapshot, validation_error = self._validate_selected_locations(
            selected_locations, "Cloud upload"
        )
        if validation_error is not None:
            self._show_recovery(validation_error, retry=self._upload_selected)
            return

        confirm = QMessageBox.question(
            self,
            "Upload local save",
            f"Upload {len(snapshot.locations)} selected local save location(s) for '{self.game_name}'?\n\n"
            "This creates or updates a cloud save version. Your local files will not be changed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        if self.cloud_center_service is None and self.cloud_operation_service is None:
            self._upload_done.emit(self._cloud_unavailable_result("Cloud upload"))
            return

        self.btn_upload.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.btn_cloud.setEnabled(False)
        progress = cloud_progress(
            self, f"Uploading saves for '{self.game_name}'…"
        )
        self._upload_progress = progress
        self._last_operation_retry = self._upload_selected
        if self.cloud_operation_service is not None:
            target = CloudOperationTarget(
                self.game_id, self.game_name, self.game_path, self.steam_id
            )
            handle = self.cloud_operation_service.request_upload(
                target,
                priority=RequestPriority.NORMAL,
                tag="save_manager_upload",
                snapshot=snapshot,
            )
            self._bind_cloud_operation(
                handle,
                lambda resource: self._upload_done.emit(
                    self._resource_operation_result(resource, "Cloud upload")
                ),
            )
            return
        # The guard above makes this unreachable for a correctly constructed
        # dialog. Keep the service boundary explicit if an embedder mutates
        # dependencies while the dialog is open.
        self._upload_done.emit(self._cloud_unavailable_result("Cloud upload"))

    def _on_upload_done(self, result: SaveOperationResult):
        if hasattr(self, "_upload_progress") and self._upload_progress:
            try:
                self._upload_progress.close()
                self._upload_progress.deleteLater()
            except Exception:
                pass
            self._upload_progress = None

        self.btn_cloud.setEnabled(True)
        self.btn_export.setEnabled(any(cb.isChecked() for cb, _loc in self.checkboxes))
        self.btn_upload.setEnabled(any(cb.isChecked() for cb, _loc in self.checkboxes))
        if result.success:
            self.save_state_store.set_operation(self.game_id, result)
            self._hide_recovery()
            QMessageBox.information(self, "Upload Successful", f"'{result.title}' was uploaded to cloud storage.")
            self._load_history()
            self._notify_parent_changed()
        else:
            self.save_state_store.set_operation(self.game_id, result)
            self._show_recovery(result, retry=self._upload_selected)

    def _export_selected(self):
        selected_locations = [loc for cb, loc in self.checkboxes if cb.isChecked()]
        if not selected_locations:
            QMessageBox.warning(self, "Warning", "Please select at least one save location to export.")
            return

        date_suffix = datetime.now().strftime("%Y%m%d_%H%M")
        safe_name = "".join(c for c in self.game_name if c.isalnum() or c in "-_")
        default_filename = f"{safe_name}_save_{date_suffix}.zip"

        export_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Save Snapshot",
            default_filename,
            "ZIP Files (*.zip)"
        )

        if export_path:
            snapshot, validation_error = self._validate_selected_locations(
                selected_locations, "Save export"
            )
            if validation_error is not None:
                self._show_recovery(validation_error, retry=self._export_selected)
                return
            success = self.backup_mgr.export_save_locations(
                snapshot.locations,
                export_path,
                game_name=self.game_name,
                game_path=self.game_path,
                snapshot=snapshot,
            )
            if success:
                QMessageBox.information(self, "Export Successful", f"Local save archive saved to:\n{export_path}")
            else:
                result = SaveOperationResult(
                    False,
                    "Save export",
                    self.game_name,
                    error=(
                        "The save files changed or became unavailable while the snapshot was being packaged."
                    ),
                    category="local_save_unreadable",
                    guidance="Rescan Save Locations, confirm the files are readable, and export again.",
                    retry_safe=False,
                    log_path=self._last_operation_log_path,
                )
                self._show_recovery(result, retry=self._export_selected)

    def _import_snapshot(self):
        import_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select local save archive",
            "",
            "ZIP Files (*.zip)"
        )

        if import_path:
            # Target prefix root or game root
            target_dest = os.path.join(self.game_path, "prefix")
            if not os.path.isdir(target_dest):
                target_dest = self.game_path

            success = self.backup_mgr.import_save(import_path, target_dest, game_path=self.game_path)
            if success:
                QMessageBox.information(self, "Import Successful", "Local save archive restored successfully.")
                self._scan_saves()
                self._notify_parent_changed()
            else:
                QMessageBox.critical(self, "Import Error", "Failed to import the local save archive.")

    def _notify_parent_changed(self):
        """Notify parent window or dialog that saves changed so stats refresh immediately."""
        p = self.parent()
        if p is not None:
            if hasattr(p, "refresh_cloud_status_for_game"):
                p.refresh_cloud_status_for_game(self.game_id)
            elif hasattr(p, "request_cloud_recheck"):
                p.request_cloud_recheck([self.game_id], "save_restored")
            if hasattr(p, "_load_save_stats_async"):
                p._load_save_stats_async()
            if hasattr(p, "_notify_parent_cloud_changed"):
                p._notify_parent_cloud_changed()

    def _cloud_unavailable_result(self, operation: str) -> SaveOperationResult:
        """Return a safe standalone result without creating a second scheduler."""
        return SaveOperationResult(
            False,
            operation,
            self.game_name,
            error="Cloud service is not available in this view.",
            category="unavailable",
            guidance="Open this workflow from the main SafeLauncher window and check Cloud Center.",
            retry_safe=False,
        )

    def _on_tab_changed(self, index: int):
        if index == 1:
            self._load_history()

    def _on_history_selection_changed(self, _entry=None):
        entry = self.history_timeline.selected_entry()
        self.btn_restore_history.setEnabled(entry is not None)

    def _load_history(self):
        """Asynchronously fetch cloud save versions and local safety backups."""
        self.cloud_status_panel.set_loading("Loading cloud save versions and local safety backups…")
        self.history_timeline.set_message("Loading cloud save versions and local backups…")
        self.history_timeline.setEnabled(False)
        self.btn_restore_history.setEnabled(False)

        if self.cloud_center_service is not None or self.cloud_operation_service is not None:
            target = CloudOperationTarget(
                self.game_id, self.game_name, self.game_path, self.steam_id
            )
            if self.cloud_center_service is not None:
                handle = self.cloud_center_service.request_save_history(
                    self.game_id,
                    game_name=self.game_name,
                    game_path=self.game_path,
                    steam_id=self.steam_id,
                    priority=RequestPriority.NORMAL,
                )
            else:
                handle = self.cloud_operation_service.request_history(
                    target,
                    priority=RequestPriority.NORMAL,
                    tag="save_manager_history",
                )

            def _deliver(resource):
                try:
                    if resource.status in {ResourceStatus.READY, ResourceStatus.STALE}:
                        versions, error = resource.value
                        self._history_loaded.emit({
                            "versions": error if error is not None else versions,
                            "stale": resource.status == ResourceStatus.STALE,
                            "offline": resource.status == ResourceStatus.OFFLINE,
                        })
                        return
                    error = SaveOperationResult(
                        False,
                        "History load",
                        self.game_name,
                        error=str(resource.error or "Could not load cloud save versions."),
                        category="backend_unavailable",
                        guidance="Check the cloud connection and try again.",
                    )
                except Exception as exc:
                    error = SaveOperationResult(
                        False,
                        "History load",
                        self.game_name,
                        error=str(exc),
                        category="backend_unavailable",
                        guidance="Check the cloud connection and try again.",
                    )
                self._history_loaded.emit(error)

            self._bind_cloud_operation(handle, _deliver)
            return

        # A standalone dialog may still render local UI, but it must not
        # bypass the application's managed cloud boundary.
        self._history_loaded.emit(self._cloud_unavailable_result("History load"))

    def _on_history_loaded(self, versions):
        """Populate history list on the main thread after async worker finishes."""
        stale = False
        offline = False
        if isinstance(versions, dict) and "versions" in versions:
            stale = bool(versions.get("stale"))
            offline = bool(versions.get("offline"))
            versions = versions.get("versions")
        if offline:
            self.cloud_status_panel.set_offline(
                "Cloud is offline; cached save history may be unavailable.",
                action_text="Retry",
            )
        elif stale:
            self.cloud_status_panel.set_ready(
                "Cloud history is cached; refresh pending.",
                stale=True,
                action_text="Refresh",
            )
        else:
            self.cloud_status_panel.set_ready("Cloud history is up to date.", action_text="Refresh")
        if isinstance(versions, SaveOperationResult) and not versions.success:
            detail = (
                f"{versions.error or 'Could not load cloud save versions.'} "
                f"{versions.guidance or ''}"
            ).strip()
            if offline:
                self.cloud_status_panel.set_offline(detail, action_text="Retry")
            else:
                self.cloud_status_panel.set_error(
                    detail,
                    title="Cloud history unavailable",
                    action_text="Retry",
                )
            self.history_timeline.setEnabled(True)
            self.history_timeline.set_message("Cloud save versions could not be loaded. Use Retry below to try again.")
            self.save_state_store.set_operation(self.game_id, versions)
            self._show_recovery(
                versions,
                retry=self._load_history,
                show_rescan=False,
            )
            return
        self.history_timeline.setEnabled(True)
        self.history_timeline.set_entries(versions)
        self.btn_restore_history.setEnabled(self.history_timeline.selected_entry() is not None)


    def _restore_selected_history_save(self):
        selected = self.history_timeline.selected_entry()
        if selected is None:
            QMessageBox.information(self, "Nothing Selected", "Please select a save from the list first.")
            return
        entry = selected.raw
        if not entry:
            return

        title = selected.title
        is_cloud_version = entry.get("source") == "cloud"
        confirm = confirm_restore(
            self,
            game_name=self.game_name,
            entry=selected,
            target_path=self.game_path,
            title="Restore cloud save version" if is_cloud_version else "Restore previous version",
        )
        if not confirm:
            return

        self.btn_restore_history.setEnabled(False)
        self.btn_export.setEnabled(False)
        if hasattr(self, "btn_cloud"):
            self.btn_cloud.setEnabled(False)

        if (self.cloud_center_service is not None or self.cloud_operation_service is not None) and is_cloud_version:
            v_num = entry.get("version")
            target = CloudOperationTarget(
                self.game_id, self.game_name, self.game_path, self.steam_id
            )
            operation_service = self.cloud_center_service or self.cloud_operation_service
            handle = operation_service.request_restore(
                target,
                priority=RequestPriority.CRITICAL,
                tag="save_manager_history_restore",
                target_version=int(v_num) if v_num else None,
            )

            def _deliver(resource):
                result = self._resource_operation_result(resource, "Cloud restore")
                self._restore_done.emit(
                    bool(result.success),
                    title if result.success else f"__restore_error__{result.error or result.guidance}",
                )

            self._bind_cloud_operation(handle, _deliver)
            return

        if not is_cloud_version:
            fork_path = str(entry.get("path") or "")
            if not fork_path or not os.path.isfile(fork_path):
                self._restore_done.emit(
                    False,
                    "__restore_error__The selected local safety backup is no longer available.",
                )
                return

            current_locations = list(self.save_locations)
            target_dest = os.path.join(self.game_path, "prefix")
            if not os.path.isdir(target_dest):
                target_dest = self.game_path

            def _restore_local_backup():
                """Back up the current local files, then import the selected fork."""
                manager = ZipBackupManager()
                if current_locations:
                    validation = validate_save_locations(current_locations)
                    failures = describe_validation_failures(validation)
                    if failures:
                        return SaveOperationResult(
                            False,
                            "Restore previous version",
                            self.game_name,
                            error=failures,
                            category="local_save_unreadable",
                            guidance="Rescan Save Locations before restoring the local backup.",
                        )
                    snapshot = snapshot_from_validation(
                        self.game_name,
                        self.game_path,
                        validation,
                        source="save-manager-restore",
                    )
                    prefix = os.path.basename(fork_path).split("_fork_", 1)[0] or "game"
                    safety_path = os.path.join(
                        os.path.dirname(fork_path),
                        f"{prefix}_fork_{int(time.time())}_before_restore.zip",
                    )
                    if not manager.export_save_locations(
                        current_locations,
                        safety_path,
                        game_name=self.game_name,
                        game_path=self.game_path,
                        snapshot=snapshot,
                    ):
                        return SaveOperationResult(
                            False,
                            "Restore previous version",
                            self.game_name,
                            error=manager.last_error or "Could not create a safety backup of the current local save.",
                            category="local_save_unreadable",
                            guidance="The selected backup was not restored; your current save was left unchanged.",
                        )

                success = manager.import_save(
                    fork_path,
                    target_dest,
                    game_path=self.game_path,
                )
                return SaveOperationResult(
                    bool(success),
                    "Restore previous version",
                    self.game_name,
                    error="The selected local safety backup could not be restored." if not success else "",
                    category="local_save_unreadable" if not success else "unknown",
                    guidance="Check the backup file and save locations, then try again." if not success else "",
                )

            def _local_restore_done(result):
                self._restore_done.emit(
                    bool(result.success),
                    title if result.success else f"__restore_error__{result.error or result.guidance}",
                )

            self._start_managed_task(
                "SafeLauncher-LocalBackupRestore",
                _restore_local_backup,
                _local_restore_done,
            )
            return

        self._restore_done.emit(
            False,
            f"__restore_error__{self._cloud_unavailable_result('Cloud restore').error}",
        )

    def _restore_from_cloud(self):
        """Restore the latest cloud save, or switch to the history tab if multiple versions exist.

        The preflight check (get_available_versions + check_sync_status) involves
        network I/O and must not block the main thread.  We dispatch it to a worker
        thread immediately and resume in ``_on_cloud_restore_preflight_done``.
        """
        if self.cloud_center_service is None and self.cloud_operation_service is None:
            self._show_recovery(
                self._cloud_unavailable_result("Cloud preflight"),
                retry=self._restore_from_cloud,
                show_rescan=False,
            )
            return

        # Disable buttons immediately so the user can't trigger a second restore.
        self.btn_restore_history.setEnabled(False)
        self.btn_export.setEnabled(False)
        if hasattr(self, "btn_cloud"):
            self.btn_cloud.setEnabled(False)

        prog = cloud_progress(
            self, f"Checking cloud saves for '{self.game_name}'…"
        )
        self._cloud_preflight_progress = prog

        if self.cloud_operation_service is not None:
            target = CloudOperationTarget(
                self.game_id, self.game_name, self.game_path, self.steam_id
            )
            handle = self.cloud_operation_service.request_restore_preflight(
                target,
                priority=RequestPriority.CRITICAL,
                tag="save_manager_restore_preflight",
            )

            def _deliver(resource):
                try:
                    value = resource.value if resource.status == ResourceStatus.READY else None
                except Exception:
                    value = None
                if value is None or value.get("kind") == "error":
                    self._restore_done.emit(False, "__preflight_error__")
                elif value.get("kind") == "history":
                    self._restore_done.emit(False, "__switch_to_history__")
                else:
                    self._restore_done.emit(
                        False,
                        f"__preflight_ok__{value.get('display_path', 'Unavailable')}__exists__{bool(value.get('cloud_exists'))}",
                    )

            self._bind_cloud_operation(handle, _deliver)
            return

        # The managed operation service owns both history and preflight. This
        # branch is kept as a defensive fallback for an embedder that swaps
        # dependencies after opening the dialog.
        self._restore_done.emit(False, "__preflight_error__")

    def _on_restore_done(self, success: bool, title: str):
        # Close any open preflight progress dialog first
        if hasattr(self, "_cloud_preflight_progress") and self._cloud_preflight_progress:
            try:
                self._cloud_preflight_progress.close()
                self._cloud_preflight_progress.deleteLater()
            except Exception:
                pass
            self._cloud_preflight_progress = None

        # Handle preflight protocol messages
        if title == "__switch_to_history__":
            self.btn_restore_history.setEnabled(True)
            self.btn_export.setEnabled(True)
            if hasattr(self, "btn_cloud"):
                self.btn_cloud.setEnabled(True)
            self.tabs.setCurrentIndex(1)
            return

        if title.startswith("__preflight_ok__"):
            # Parse display_path and exists flag from the sentinel
            rest = title[len("__preflight_ok__"):]
            exists_marker = "__exists__"
            if exists_marker in rest:
                display_path, exists_str = rest.split(exists_marker, 1)
                cloud_exists = (exists_str.strip().lower() == "true")
            else:
                display_path = rest
                cloud_exists = True

            self.btn_restore_history.setEnabled(True)
            self.btn_export.setEnabled(True)
            if hasattr(self, "btn_cloud"):
                self.btn_cloud.setEnabled(True)

            if not cloud_exists:
                QMessageBox.information(
                    self, "No Cloud Saves",
                    f"No cloud save archive found for '{self.game_name}'."
                )
                return

            confirm = confirm_restore(
                self,
                game_name=self.game_name,
                target_path=self.game_path,
                technical_details=f"Cloud archive: {display_path}",
                title="Restore latest cloud save",
            )
            if not confirm:
                return

            # Now actually run the restore
            self.btn_restore_history.setEnabled(False)
            self.btn_export.setEnabled(False)
            if hasattr(self, "btn_cloud"):
                self.btn_cloud.setEnabled(False)

            if self.cloud_operation_service is not None:
                target = CloudOperationTarget(
                    self.game_id, self.game_name, self.game_path, self.steam_id
                )
                handle = self.cloud_operation_service.request_restore(
                    target,
                    priority=RequestPriority.CRITICAL,
                    tag="save_manager_restore",
                )

                def _deliver(resource):
                    result = self._resource_operation_result(resource, "Cloud restore")
                    self._restore_done.emit(
                        bool(result.success),
                        display_path if result.success else f"__restore_error__{result.error or result.guidance}",
                    )

                self._bind_cloud_operation(handle, _deliver)
                return

            self._restore_done.emit(
                False,
                f"__restore_error__{self._cloud_unavailable_result('Cloud restore').error}",
            )
            return

        if title == "__preflight_error__":
            self.btn_restore_history.setEnabled(True)
            self.btn_export.setEnabled(True)
            if hasattr(self, "btn_cloud"):
                self.btn_cloud.setEnabled(True)
            category, guidance = classify_cloud_error("Could not reach the cloud to check save status")
            self._show_recovery(
                SaveOperationResult(False, "Cloud preflight", self.game_name,
                                    "Could not reach the cloud to check save status.", category, guidance),
                retry=self._restore_from_cloud,
                show_rescan=False,
            )
            return

        if title.startswith("__restore_error__"):
            error = title[len("__restore_error__"):].strip()
            category, guidance = classify_cloud_error(error)
            self._show_recovery(
                SaveOperationResult(False, "Cloud restore", self.game_name,
                                    error or "Cloud restore failed.", category, guidance),
                retry=self._restore_from_cloud,
                show_rescan=True,
            )
            self.btn_restore_history.setEnabled(True)
            self.btn_export.setEnabled(True)
            if hasattr(self, "btn_cloud"):
                self.btn_cloud.setEnabled(True)
            return

        # --- Normal restore completion path (success/failure from _worker above) ---
        self.btn_restore_history.setEnabled(True)
        self.btn_export.setEnabled(True)
        if hasattr(self, "btn_cloud"):
            self.btn_cloud.setEnabled(True)
        if success:
            QMessageBox.information(
                self, "Restore Successful",
                f"Successfully restored '{title}'."
            )
            self._scan_saves()
            self._load_history()
            self._notify_parent_changed()
        else:
            category, guidance = classify_cloud_error(f"Failed to restore '{title}'.")
            self._show_recovery(
                SaveOperationResult(False, "Save restore", self.game_name,
                                    f"Failed to restore '{title}'.", category, guidance),
                retry=self._restore_selected_history_save,
            )
