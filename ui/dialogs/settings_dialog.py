import os
import subprocess
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFormLayout,
    QFileDialog, QDialogButtonBox, QWidget, QScrollArea, QGridLayout, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QIcon, QPixmap

from core.logger import LOG_DIR
from core.disk_utils import get_dir_size, get_disk_usage, format_size
from core.host_process import host_process_env
from database import GameDatabase, _APP_DATA_DIR
from ui.icons import LOGO_PATH, get_app_icon
from ui.components.sidebar import DialogTitleBar, add_soft_shadow
from ui.maintenance_dialogs import RuntimeInventoryDialog
from ui.dialogs.game_dialogs import ensure_sandbox_dir


class UserSettingsDialog(QDialog):
    """Small settings popup for launcher-wide profile preferences."""
    runtime_manager_requested = pyqtSignal()
    proton_manager_requested = pyqtSignal()

    def __init__(self, user_name: str, proton_path: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("SafeLauncher Settings")
        self.setWindowIcon(QIcon(LOGO_PATH) if os.path.exists(LOGO_PATH) else QIcon())
        self.setMinimumWidth(420)
        self.setStyleSheet("""
            QDialog { background: #141414; color: #ffffff; }
            QLabel { color: #d4d4d8; font-weight: bold; }
            QLineEdit {
                background: #0d0d0d; color: #ffffff; border: 1px solid #333333;
                border-radius: 5px; padding: 8px;
            }
            QLineEdit:focus { border-color: #737780; }
            QPushButton {
                background: #52565e; color: #ffffff; border: none;
                border-radius: 5px; padding: 8px 18px; font-weight: bold;
            }
            QPushButton:hover { background: #6b707a; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        title = QLabel("Profile & Proton Settings")
        title.setFont(QFont("Arial", 15, QFont.Weight.Bold))
        layout.addWidget(title)

        btn_ge_manager = QPushButton("📦 Open GE-Proton Manager (GitHub Downloader)…")
        btn_ge_manager.setStyleSheet("QPushButton { background: #174735; color: #86efac; border: none; border-radius: 8px; padding: 10px 14px; } QPushButton:hover { background: #174735; }")
        add_soft_shadow(btn_ge_manager, blur=16, y=4, alpha=90)
        btn_ge_manager.clicked.connect(self._open_proton_manager)
        layout.addWidget(btn_ge_manager)

        runtime_manager = QPushButton("Open UMU Runtime Manager…")
        runtime_manager.setStyleSheet("QPushButton { background: #24272d; border: none; border-radius: 8px; padding: 10px 14px; } QPushButton:hover { background: #24272d; }")
        add_soft_shadow(runtime_manager, blur=16, y=4, alpha=80)
        runtime_manager.clicked.connect(self._open_runtime_manager)
        layout.addWidget(runtime_manager)

        inventory_button = QPushButton("Inspect installed runtimes…")
        inventory_button.setStyleSheet("QPushButton { background: #24262b; border: none; border-radius: 8px; padding: 10px 14px; } QPushButton:hover { background: #24262b; }")
        add_soft_shadow(inventory_button, blur=16, y=4, alpha=80)
        inventory_button.clicked.connect(self._open_runtime_inventory)
        layout.addWidget(inventory_button)

        form = QFormLayout()
        self.name_input = QLineEdit(user_name)
        self.name_input.setPlaceholderText("Your name")
        self.name_input.selectAll()
        form.addRow("Display name:", self.name_input)
        proton_row = QHBoxLayout()
        self.proton_input = QLineEdit(proton_path)
        self.proton_input.setPlaceholderText("Leave blank for UMU automatic detection")
        proton_row.addWidget(self.proton_input)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_proton)
        proton_row.addWidget(browse)
        form.addRow("Proton path:", proton_row)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._save)
        layout.addWidget(buttons)

    def _save(self):
        if self.name_input.text().strip():
            self.accept()

    def _browse_proton(self):
        path = QFileDialog.getExistingDirectory(self, "Select Proton tool directory", os.path.expanduser("~/.local/share"))
        if path:
            self.proton_input.setText(path)

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


class ScreenshotGalleryDialog(QDialog):
    """Custom dark modal dialog for browsing and managing in-game screenshots."""
    def __init__(self, game_id: int, game_name: str, parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.game_name = game_name

        self.setWindowTitle(f"Screenshots - {game_name}")
        self.setMinimumSize(680, 500)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Title bar
        self.title_bar = DialogTitleBar(self, f"📸 Screenshots - {game_name}")
        root_layout.addWidget(self.title_bar)

        # Body container
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 16, 20, 20)
        body_layout.setSpacing(15)

        # Grid scroll area for screenshots
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { background: #121212; border: 1px solid #2a2a2a; border-radius: 6px; }")

        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setContentsMargins(15, 15, 15, 15)
        self.grid_layout.setSpacing(15)

        scroll_area.setWidget(self.grid_widget)
        body_layout.addWidget(scroll_area)

        # Bottom Action Bar
        action_layout = QHBoxLayout()
        
        btn_open_folder = QPushButton("📂 Open Folder")
        btn_open_folder.setStyleSheet("""
            QPushButton {
                background: #1c1c1c; color: #fff; border: 1px solid #333; padding: 8px 16px; border-radius: 5px; font-weight: bold;
            }
            QPushButton:hover { background: #282828; }
        """)
        btn_open_folder.clicked.connect(self._open_folder)
        action_layout.addWidget(btn_open_folder)

        action_layout.addStretch()

        btn_close = QPushButton("Close")
        btn_close.setStyleSheet("""
            QPushButton {
                background: #52565e; color: #fff; border: none; padding: 8px 20px; border-radius: 5px; font-weight: bold;
            }
            QPushButton:hover { background: #6b707a; }
        """)
        btn_close.clicked.connect(self.accept)
        action_layout.addWidget(btn_close)

        body_layout.addLayout(action_layout)
        root_layout.addWidget(body)

        self.setStyleSheet("QDialog { background-color: #121212; border: 1px solid #2a2a2a; border-radius: 8px; }")

        self.screenshots_dir = os.path.join(_APP_DATA_DIR, "screenshots", str(game_id))
        os.makedirs(self.screenshots_dir, exist_ok=True)
        self.load_screenshots()

    def load_screenshots(self):
        while self.grid_layout.count() > 0:
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        files = sorted(
            [os.path.join(self.screenshots_dir, f) for f in os.listdir(self.screenshots_dir) if f.lower().endswith((".png", ".jpg", ".jpeg"))],
            reverse=True
        )

        if not files:
            empty_label = QLabel("📷 No screenshots captured yet.\nPress F12 while playing to take a screenshot!")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_label.setStyleSheet("color: #777777; font-size: 13px; padding: 40px;")
            self.grid_layout.addWidget(empty_label, 0, 0)
            return

        cols = 3
        for idx, filepath in enumerate(files):
            card = QFrame()
            card.setStyleSheet("QFrame { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 6px; }")
            c_layout = QVBoxLayout(card)
            c_layout.setContentsMargins(6, 6, 6, 6)
            c_layout.setSpacing(6)

            thumb_label = QLabel()
            thumb_label.setFixedSize(180, 115)
            thumb_label.setScaledContents(True)
            pixmap = QPixmap(filepath)
            if not pixmap.isNull():
                thumb_label.setPixmap(pixmap.scaled(180, 115, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
            c_layout.addWidget(thumb_label)

            btn_del = QPushButton("🗑️ Delete")
            btn_del.setStyleSheet("QPushButton { background: #2a1212; color: #ef4444; border: 1px solid #7f1d1d; font-size: 11px; padding: 3px; border-radius: 4px; } QPushButton:hover { background: #7f1d1d; color: white; }")
            btn_del.clicked.connect(lambda _, p=filepath: self._delete_screenshot(p))
            c_layout.addWidget(btn_del)

            row, col = divmod(idx, cols)
            self.grid_layout.addWidget(card, row, col)

    def _delete_screenshot(self, filepath: str):
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                self.load_screenshots()
        except Exception:
            pass

    def _open_folder(self):
        try:
            subprocess.Popen(["xdg-open", self.screenshots_dir], env=host_process_env())
        except Exception:
            pass


class DiskManagerDialog(QDialog):
    """Custom dark dialog for analyzing sandbox disk space consumption and largest installed games."""
    def __init__(self, games: list, parent=None):
        super().__init__(parent)
        self.games = games

        self.setWindowTitle("Disk Space Manager")
        self.setWindowIcon(get_app_icon("search"))
        self.setMinimumSize(620, 480)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Title bar
        self.title_bar = DialogTitleBar(self, "🔍 Disk Space Manager")
        root_layout.addWidget(self.title_bar)

        # Body container
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 16, 20, 20)
        body_layout.setSpacing(14)

        # Overview Stats Card
        sandbox_dir = ensure_sandbox_dir()
        total_sandbox_bytes = get_dir_size(sandbox_dir)
        total_drive, used_drive, free_drive = get_disk_usage(sandbox_dir)

        stats_card = QFrame()
        stats_card.setStyleSheet("QFrame { background: #181818; border: none; border-radius: 10px; padding: 12px; }")
        add_soft_shadow(stats_card, blur=20, y=5, alpha=90)
        sc_layout = QVBoxLayout(stats_card)
        sc_layout.setSpacing(6)

        lbl_sandbox = QLabel(f"📁 Total Sandbox Storage: {format_size(total_sandbox_bytes)}")
        lbl_sandbox.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: bold;")
        sc_layout.addWidget(lbl_sandbox)

        lbl_drive = QLabel(f"💽 Drive Free Space: {format_size(free_drive)} available out of {format_size(total_drive)}")
        lbl_drive.setStyleSheet("color: #aaaaaa; font-size: 12px; font-weight: bold;")
        sc_layout.addWidget(lbl_drive)

        body_layout.addWidget(stats_card)

        # List of Games ranked by size
        lbl_rank = QLabel("Installed Games by Size:")
        lbl_rank.setStyleSheet("color: #cccccc; font-size: 12px; font-weight: bold;")
        body_layout.addWidget(lbl_rank)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: #121212; border: none; border-radius: 8px; }")

        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.setContentsMargins(10, 10, 10, 10)
        list_layout.setSpacing(8)

        # Calculate sizes and sort descending
        game_sizes = []
        for g in games:
            game_id, name, path = g[0], g[1], g[2]
            sz = get_dir_size(path) if path and os.path.exists(path) else 0
            game_sizes.append((name, path, sz))

        game_sizes.sort(key=lambda x: x[2], reverse=True)

        for name, path, sz in game_sizes:
            row_frame = QFrame()
            row_frame.setStyleSheet("QFrame { background: #1a1a1a; border: none; border-radius: 8px; }")
            add_soft_shadow(row_frame, blur=12, y=3, alpha=60)
            r_layout = QHBoxLayout(row_frame)
            r_layout.setContentsMargins(10, 8, 10, 8)

            name_lbl = QLabel(name)
            name_lbl.setStyleSheet("color: #ffffff; font-weight: bold; font-size: 12px;")
            r_layout.addWidget(name_lbl)

            r_layout.addStretch()

            size_badge = QLabel(format_size(sz))
            size_badge.setStyleSheet("background: #24262b; color: #c4c7cc; border: none; border-radius: 4px; padding: 3px 8px; font-size: 11px; font-weight: bold;")
            r_layout.addWidget(size_badge)

            btn_folder = QPushButton("📂 Open Folder")
            btn_folder.setStyleSheet("QPushButton { background: #222222; color: #aaaaaa; border: none; border-radius: 6px; padding: 5px 9px; font-weight: bold; font-size: 11px; } QPushButton:hover { background: #222222; color: #aaaaaa; }")
            add_soft_shadow(btn_folder, blur=10, y=2, alpha=60)
            btn_folder.clicked.connect(lambda _, p=path: self._open_path(p))
            r_layout.addWidget(btn_folder)

            list_layout.addWidget(row_frame)

        scroll.setWidget(list_widget)
        body_layout.addWidget(scroll)

        # Close button
        btn_close = QPushButton("Close")
        btn_close.setStyleSheet("QPushButton { background: #52565e; color: #fff; border: none; padding: 8px 24px; border-radius: 5px; font-weight: bold; } QPushButton:hover { background: #6b707a; }")
        btn_close.clicked.connect(self.accept)
        
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        body_layout.addLayout(btn_row)

        root_layout.addWidget(body)

    def _open_path(self, path: str):
        try:
            if path and os.path.exists(path):
                subprocess.Popen(["xdg-open", path], env=host_process_env())
        except Exception:
            pass
