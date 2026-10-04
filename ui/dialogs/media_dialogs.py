import os
import re
import subprocess
from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget, QScrollArea, QGridLayout, QFrame, QSizePolicy, QMessageBox
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPixmap
from core.host_process import host_process_env
from database import _APP_DATA_DIR
from core.date_formatting import format_datetime_timestamp
from core.screenshot_capture import capture_desktop_screenshot
from core.plugins.gpu_screen_recorder import DEFAULT_RECORDINGS_DIR
from ui.icons import get_icon, get_icon_pixmap
from ui.components.popup_shell import PopupDialog
from core.logger import get_logger
logger = get_logger("media_dialogs")


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
        btn_open_folder.setStyleSheet("QPushButton { background: #27272a; color: #ffffff; border: 1px solid #2A2A2E; border-radius: 4px; padding: 6px 12px; font-weight: 600; } QPushButton:hover { background: #2A2A2E; }")
        btn_open_folder.clicked.connect(self._open_current_folder)
        h_layout.addWidget(btn_open_folder)

        btn_delete = QPushButton(" Delete")
        btn_delete.setIcon(get_icon("ph.trash-bold", color="#ef4444"))
        btn_delete.setStyleSheet("QPushButton { background: #2a1212; color: #ef4444; border: 1px solid #7f1d1d; border-radius: 4px; padding: 6px 12px; font-weight: 600; } QPushButton:hover { background: #7f1d1d; color: #ffffff; }")
        btn_delete.clicked.connect(self._delete_current)
        h_layout.addWidget(btn_delete)

        btn_close = QPushButton("✕ Close")
        btn_close.setStyleSheet("QPushButton { background: #27272a; color: #ffffff; border: 1px solid #2A2A2E; border-radius: 4px; padding: 6px 12px; font-weight: 600; } QPushButton:hover { background: #2A2A2E; }")
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
        self.btn_prev.setStyleSheet("QPushButton { background: rgba(39, 39, 42, 0.6); color: #ffffff; border: 1px solid #2A2A2E; border-radius: 6px; font-size: 16px; font-weight: bold; } QPushButton:hover { background: rgba(63, 63, 70, 0.9); }")
        self.btn_prev.clicked.connect(self._prev_image)
        b_layout.addWidget(self.btn_prev)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet("background: transparent;")
        b_layout.addWidget(self.image_label, 1)

        self.btn_next = QPushButton("▶")
        self.btn_next.setFixedSize(44, 80)
        self.btn_next.setStyleSheet("QPushButton { background: rgba(39, 39, 42, 0.6); color: #ffffff; border: 1px solid #2A2A2E; border-radius: 6px; font-size: 16px; font-weight: bold; } QPushButton:hover { background: rgba(63, 63, 70, 0.9); }")
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
        scroll_area.setStyleSheet("QScrollArea { background: #121214; border: none; }")

        self.grid_widget = QWidget()
        self.grid_widget.setObjectName("screenshotGrid")
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setContentsMargins(14, 14, 14, 14)
        self.grid_layout.setSpacing(14)

        scroll_area.setWidget(self.grid_widget)
        body_layout.addWidget(scroll_area)

        # Bottom Action Bar
        action_layout = QHBoxLayout()

        btn_capture = QPushButton("Capture Screen")
        btn_capture.setIcon(get_icon("ph.camera-bold"))
        btn_capture.setStyleSheet("QPushButton { background: #18181B; color: #F4F4F5; border: none; border-radius: 6px; padding: 7px 14px; font-weight: 600; } QPushButton:hover { background: #202024; }")
        btn_capture.clicked.connect(self._capture_screen)
        action_layout.addWidget(btn_capture)

        btn_open_folder = QPushButton("Open Directory")
        btn_open_folder.setIcon(get_icon("ph.folder-open-bold"))
        btn_open_folder.setStyleSheet("QPushButton { background: #18181B; color: #F4F4F5; border: none; border-radius: 6px; padding: 7px 14px; font-weight: 600; } QPushButton:hover { background: #202024; }")
        btn_open_folder.clicked.connect(self._open_folder)
        action_layout.addWidget(btn_open_folder)

        action_layout.addStretch()

        btn_close = QPushButton("Close")
        btn_close.setMinimumWidth(80)
        btn_close.setStyleSheet("QPushButton { background: #18181B; color: #F4F4F5; border: none; border-radius: 6px; padding: 7px 16px; font-weight: 600; } QPushButton:hover { background: #202024; }")
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
        for column in range(cols):
            self.grid_layout.setColumnStretch(column, 1)

        for idx, filepath in enumerate(self.files):
            card = QFrame()
            card.setObjectName("screenshotCard")
            card.setMinimumWidth(card_w + 12)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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
            thumb_label.setObjectName("screenshotThumbnail")
            thumb_label.setFixedSize(card_w, card_h)
            thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb_label.setCursor(Qt.CursorShape.PointingHandCursor)
            thumb_label.setToolTip("Click to view in full Lightbox")

            pixmap = QPixmap(filepath)
            if not pixmap.isNull():
                thumb_label.setPixmap(pixmap.scaled(card_w, card_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

            # Make thumbnail clickable to open Lightbox
            thumb_label.mousePressEvent = lambda event, i=idx: self._open_lightbox(i)
            c_layout.addWidget(thumb_label)

            # Card info row
            fn_label = QLabel(os.path.basename(filepath))
            fn_label.setObjectName("screenshotFilename")
            fn_label.setStyleSheet("color: #a1a1aa; font-size: 10px; background: transparent;")
            fn_label.setWordWrap(True)
            fn_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            c_layout.addWidget(fn_label)

            btn_del = QPushButton("Delete")
            btn_del.setObjectName("screenshotDelete")
            btn_del.setFixedHeight(28)
            btn_del.setStyleSheet("QPushButton { background: #2A1212; color: #F05D6C; border: none; border-radius: 6px; font-size: 11px; padding: 4px; } QPushButton:hover { background: #7F1D1D; color: white; }")
            btn_del.clicked.connect(lambda _, p=filepath: self._delete_screenshot(p))
            c_layout.addWidget(btn_del)

            row, col = divmod(idx, cols)
            self.grid_layout.addWidget(card, row, col)

    def _open_lightbox(self, index: int):
        dlg = ScreenshotLightboxDialog(self.files, index, parent=self)
        dlg.exec()

    def _delete_screenshot(self, filepath: str):
        answer = QMessageBox.question(
            self,
            "Delete screenshot",
            f"Delete {os.path.basename(filepath)}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
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
        self.list_widget.setObjectName("videoList")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(10, 10, 10, 10)
        self.list_layout.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("videoScroll")
        scroll.setStyleSheet("QScrollArea { background: #121214; border: none; }")
        scroll.setWidget(self.list_widget)
        body_layout.addWidget(scroll)

        actions = QHBoxLayout()
        open_folder = QPushButton("Open Directory")
        open_folder.setObjectName("videoOpenFolder")
        open_folder.setIcon(get_icon("ph.folder-open-bold"))
        open_folder.setMinimumHeight(32)
        open_folder.clicked.connect(self._open_folder)
        actions.addWidget(open_folder)
        actions.addStretch()
        close = QPushButton("Close")
        close.setObjectName("videoClose")
        close.setMinimumWidth(80)
        close.setMinimumHeight(32)
        close.clicked.connect(self.accept)
        actions.addWidget(close)
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
            row.setObjectName("videoRow")
            row.setMinimumHeight(64)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(12, 10, 12, 10)
            icon = QLabel()
            icon.setObjectName("videoIcon")
            icon.setPixmap(get_icon_pixmap("ph.video-camera-bold", 28))
            layout.addWidget(icon)
            info = QVBoxLayout()
            info.setSpacing(3)
            name = QLabel(os.path.basename(filepath))
            name.setObjectName("videoFilename")
            name.setStyleSheet("color: #f4f4f5; font-weight: 600; background: transparent;")
            name.setTextFormat(Qt.TextFormat.PlainText)
            info.addWidget(name)
            size_mb = os.path.getsize(filepath) / (1024 * 1024)
            modified = os.path.getmtime(filepath)
            detail = QLabel(f"{size_mb:.1f} MB  •  {format_datetime_timestamp(modified, '%H:%M')}")
            detail.setObjectName("videoDetails")
            detail.setStyleSheet("color: #a1a1aa; font-size: 11px; background: transparent;")
            info.addWidget(detail)
            layout.addLayout(info, 1)
            play = QPushButton("Play")
            play.setObjectName("videoPlay")
            play.setFixedWidth(72)
            play.clicked.connect(lambda _, p=filepath: self._play(p))
            layout.addWidget(play)
            reveal = QPushButton("Show")
            reveal.setObjectName("videoShow")
            reveal.setFixedWidth(72)
            reveal.clicked.connect(lambda _, p=filepath: self._show_file(p))
            layout.addWidget(reveal)
            delete = QPushButton("Delete")
            delete.setObjectName("videoDelete")
            delete.setFixedWidth(72)
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
