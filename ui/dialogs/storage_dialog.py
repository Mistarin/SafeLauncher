import os
import subprocess
from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget, QScrollArea, QGridLayout, QFrame, QProgressBar
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from core.disk_utils import get_dir_size, get_disk_usage, format_size
from core.host_process import host_process_env
from ui.components.popup_shell import PopupDialog
from ui.dialogs.game_dialogs import ensure_sandbox_dir
from core.safe_thread import TaskSupervisor
from core.logger import get_logger
logger = get_logger("storage_dialog")


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
            QProgressBar { background: #202024; border: none; border-radius: 4px; }
            QProgressBar::chunk { background: #4B9FFF; border-radius: 4px; }
        """)
        body_layout.addWidget(self.drive_bar)

        path_label = QLabel(f"Sandbox root: {sandbox_dir}")
        path_label.setStyleSheet("color: #A1A1AA; font-size: 11px;")
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
        lbl_wait.setStyleSheet("color: #A1A1AA; font-size: 12px; padding: 22px;")
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
                background: #202024;
                border: none;
                border-radius: 6px;
            }
            QDialog#safeLauncherPopup QPushButton#popupSecondary {
                background: #202024;
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
        label.setStyleSheet("color: #A1A1AA; font-size: 10px; font-weight: 700;")
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
                empty.setStyleSheet("color: #A1A1AA; padding: 24px;")
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
