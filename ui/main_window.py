import os
import shutil
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QGridLayout, QFileDialog, QMessageBox, QDialog, QLabel, QLineEdit,
    QComboBox, QFormLayout, QScrollArea, QFrame, QListWidget, QListWidgetItem, QMenu,
    QApplication
)
from PyQt6.QtCore import Qt, QSize, QPoint, QThread, pyqtSignal, QVariantAnimation, QEasingCurve, QTimer, QEvent
from PyQt6.QtGui import QPixmap, QFont, QColor, QIcon, QPainter
from core.interfaces import ISandboxRunner, IBackupManager
from core.steamgriddb_client import SteamGridDBClient
from core.playtime_tracker import PlaytimeTrackerThread
from core.archive_extractor import (
    DEFAULT_SANDBOX_DIR, ensure_sandbox_dir, extract_archive_sandboxed,
    find_executables, save_sandbox_config, load_sandbox_config, scan_sandbox_games
)
from database import GameDatabase
from ui.icons import get_app_icon, get_icon

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO_PATH = os.path.join(BASE_DIR, "assets", "logo.png")

class BannerFetcher(QThread):
    """Background thread for searching game banners - thread-safe"""
    results_found = pyqtSignal(list)
    error_occurred = pyqtSignal(str)
    
    def __init__(self, game_name: str, sgdb_client: SteamGridDBClient):
        super().__init__()
        self.game_name = game_name
        self.sgdb_client = sgdb_client
    
    def run(self):
        try:
            result = self.sgdb_client.search_game(self.game_name)
            if result and result.get('found') and result.get('results'):
                self.results_found.emit(result['results'])
            else:
                self.error_occurred.emit("No games found matching your search")
        except Exception as e:
            self.error_occurred.emit(f"Error searching banner: {str(e)}")

class BannerDownloader(QThread):
    """Background thread for downloading selected banner image - thread-safe"""
    download_complete = pyqtSignal(str)
    download_failed = pyqtSignal(str)
    
    def __init__(self, banner_url: str, sgdb_client: SteamGridDBClient):
        super().__init__()
        self.banner_url = banner_url
        self.sgdb_client = sgdb_client
        
    def run(self):
        try:
            path = self.sgdb_client.download_banner(self.banner_url)
            if path and os.path.exists(path):
                self.download_complete.emit(path)
            else:
                self.download_failed.emit("Failed to download banner image")
        except Exception as e:
            self.download_failed.emit(str(e))

class BannerAutoFetcher(QThread):
    """Background thread to auto-fetch missing cover art for games in library"""
    banner_auto_downloaded = pyqtSignal(int, str)  # (game_id, downloaded_file_path)
    
    def __init__(self, game_id: int, game_name: str, sgdb_client: SteamGridDBClient):
        super().__init__()
        self.game_id = game_id
        self.game_name = game_name
        self.sgdb_client = sgdb_client
        
    def run(self):
        try:
            res = self.sgdb_client.search_game(self.game_name)
            if res and res.get('found') and res.get('primary'):
                url = res['primary'].get('banner_url')
                if url:
                    path = self.sgdb_client.download_banner(url, self.game_id)
                    if path and os.path.exists(path):
                        self.banner_auto_downloaded.emit(self.game_id, path)
        except Exception as e:
            print(f"Error auto-fetching banner for {self.game_name}: {e}")

class ArchiveExtractorThread(QThread):
    """Background thread for extracting game archives safely in Firejail sandbox"""
    extraction_complete = pyqtSignal(str, str, bool)  # (game_name, dest_dir, success)
    
    def __init__(self, archive_path: str, dest_dir: str):
        super().__init__()
        self.archive_path = archive_path
        self.dest_dir = dest_dir
        
    def run(self):
        game_name = os.path.splitext(os.path.basename(self.archive_path))[0]
        if game_name.endswith(".tar"):
            game_name = os.path.splitext(game_name)[0]
        success = extract_archive_sandboxed(self.archive_path, self.dest_dir)
        self.extraction_complete.emit(game_name, self.dest_dir, success)

class GameBannerWidget(QFrame):
    """Individual borderless game banner card with pixel font title and LERP zoom"""
    clicked = pyqtSignal(int)
    doubleClicked = pyqtSignal(int)
    rightClicked = pyqtSignal(int, QPoint)

    def __init__(self, game_id: int, name: str, banner_path: str = None, playtime_seconds: int = 0):
        super().__init__()
        self.game_id = game_id
        self.name = name
        self.banner_path = banner_path
        self.playtime_seconds = playtime_seconds
        self.selected = False
        self.is_missing = False
        self._hover_progress = 0.0  # LERP progress: 0.0 (normal) -> 1.0 (hovered)
        
        # Smooth 180ms LERP animation setup with OutCubic easing curve
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(180)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._on_anim_frame)

        self.setFrameStyle(QFrame.Shape.NoFrame)
        self.setLineWidth(0)
        self.setFixedSize(QSize(200, 355))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        
        # Banner image (2:3 portrait aspect ratio matching Steam 600x900 library covers)
        self.image_label = QLabel()
        self.image_label.setFixedSize(QSize(200, 300))
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.image_label)
        
        # Game name label using bold pixel monospace font
        self.name_label = QLabel(name)
        self.name_label.setWordWrap(True)
        pixel_font = QFont("Monospace")
        pixel_font.setStyleHint(QFont.StyleHint.Monospace)
        pixel_font.setPixelSize(13)
        pixel_font.setBold(True)
        self.name_label.setFont(pixel_font)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.name_label)

        # Playtime label — small, dimmed, below the title
        self.playtime_label = QLabel(self._format_playtime(playtime_seconds))
        pt_font = QFont("Monospace")
        pt_font.setStyleHint(QFont.StyleHint.Monospace)
        pt_font.setPixelSize(10)
        self.playtime_label.setFont(pt_font)
        self.playtime_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.playtime_label.setStyleSheet("color: #888888; background: transparent;")
        layout.addWidget(self.playtime_label)
        
        self.update_appearance()

    @staticmethod
    def _format_playtime(seconds: int) -> str:
        """Convert raw seconds to a human-readable playtime string."""
        if not seconds or seconds < 60:
            return "⏱ Never played" if not seconds else f"⏱ {seconds}s"
        minutes = seconds // 60
        hours = minutes // 60
        remaining_mins = minutes % 60
        if hours > 0:
            return f"⏱ {hours}h {remaining_mins}m" if remaining_mins else f"⏱ {hours}h"
        return f"⏱ {minutes}m"

    def set_playtime(self, seconds: int):
        """Update displayed playtime without rebuilding the whole widget."""
        self.playtime_seconds = seconds
        self.playtime_label.setText(self._format_playtime(seconds))
        

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.game_id)
        elif event.button() == Qt.MouseButton.RightButton:
            self.rightClicked.emit(self.game_id, event.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit(self.game_id)
            
    def enterEvent(self, event):
        super().enterEvent(event)
        if not self.is_missing:
            self.anim.stop()
            self.anim.setStartValue(self._hover_progress)
            self.anim.setEndValue(1.0)
            self.anim.start()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if not self.is_missing:
            self.anim.stop()
            self.anim.setStartValue(self._hover_progress)
            self.anim.setEndValue(0.0)
            self.anim.start()

    def _on_anim_frame(self, value: float):
        self._hover_progress = value
        self.render_frame(value)

    def set_banner(self, banner_path: str):
        """Set the banner path and update render"""
        self.banner_path = banner_path
        self.update_appearance()
    
    def set_selected(self, selected: bool):
        """Toggle selected state"""
        self.selected = selected
        self.update_appearance()

    def set_missing(self, is_missing: bool):
        """Grey out card if game files are missing on drive"""
        self.is_missing = is_missing
        if is_missing:
            self.setToolTip(f"⚠️ Missing on Drive: Game directory does not exist")
        else:
            self.setToolTip("")
        self.update_appearance()

    def update_appearance(self):
        """Update container styling (borderless) and trigger frame render"""
        self.setStyleSheet("border: none; background: transparent;")
        
        if self.is_missing:
            self.name_label.setText(f"⚠️ {self.name} (Missing)")
            self.name_label.setStyleSheet(
                "padding: 5px 2px; background: transparent; color: #777777; "
                "font-family: 'Monospace', 'Courier New', monospace; font-size: 13px; font-weight: bold; font-style: italic;"
            )
            self.image_label.setStyleSheet("background: #0a0a0a; border-radius: 6px;")
        elif self.selected:
            self.name_label.setText(self.name)
            self.name_label.setStyleSheet(
                "padding: 5px 2px; background: #0d47a1; color: #ffffff; "
                "font-family: 'Monospace', 'Courier New', monospace; font-size: 13px; font-weight: bold; border-radius: 4px;"
            )
            self.image_label.setStyleSheet("background: #111; border-radius: 6px;")
        else:
            self.name_label.setText(self.name)
            self.name_label.setStyleSheet(
                "padding: 5px 2px; background: transparent; color: #eeeeee; "
                "font-family: 'Monospace', 'Courier New', monospace; font-size: 13px; font-weight: bold;"
            )
            self.image_label.setStyleSheet("background: #111; border-radius: 6px;")

        self.render_frame(self._hover_progress)

    def render_frame(self, progress: float):
        """Render cover art with LERP zoom or greyed-out missing overlay"""
        target_w, target_h = 200, 300
        
        # 1. Missing game state (greyed out)
        if self.is_missing:
            if self.banner_path and os.path.exists(self.banner_path):
                pixmap = QPixmap(self.banner_path)
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        QSize(target_w, target_h),
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    crop_x = max(0, (scaled.width() - target_w) // 2)
                    crop_y = max(0, (scaled.height() - target_h) // 2)
                    cropped = scaled.copy(crop_x, crop_y, target_w, target_h)
                    
                    # Heavy desaturated 65% dark overlay for missing games
                    greyed = QPixmap(cropped.size())
                    greyed.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(greyed)
                    painter.drawPixmap(0, 0, cropped)
                    painter.fillRect(greyed.rect(), QColor(20, 20, 20, 175))
                    painter.end()
                    
                    self.image_label.setPixmap(greyed)
                    self.image_label.setText("")
                    return
            
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText(f"⚠️\n\n{self.name}\n(Missing)")
            self.image_label.setStyleSheet(
                "background: #181818; color: #777777; font-weight: bold; font-size: 12px; padding: 10px; border-radius: 6px;"
            )
            return

        # 2. Normal game state with LERP hover zoom
        if self.banner_path and self.banner_path != "none" and os.path.exists(self.banner_path):
            pixmap = QPixmap(self.banner_path)
            if not pixmap.isNull():
                scale_factor = 1.0 + (0.04 * progress)
                zoom_w = int(target_w * scale_factor)
                zoom_h = int(target_h * scale_factor)
                
                scaled = pixmap.scaled(
                    QSize(zoom_w, zoom_h),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation
                )
                crop_x = max(0, (scaled.width() - target_w) // 2)
                crop_y = max(0, (scaled.height() - target_h) // 2)
                cropped = scaled.copy(crop_x, crop_y, target_w, target_h)
                
                self.image_label.setPixmap(cropped)
                self.image_label.setText("")
                return

        # 3. Placeholder card when cover art is cleared ('none') or missing
        placeholder = QPixmap(target_w, target_h)
        placeholder.fill(QColor("#181818"))
        painter = QPainter(placeholder)
        painter.setPen(QColor("#777777"))
        painter.setFont(QFont("Monospace", 12, QFont.Weight.Bold))
        painter.drawText(placeholder.rect(), Qt.AlignmentFlag.AlignCenter, self.name)
        painter.end()
        self.image_label.setPixmap(placeholder)

class ResponsiveGridContainer(QWidget):
    """Container widget that reflows game banner widgets dynamically into columns based on window width"""
    def __init__(self, parent=None, card_width: int = 200, spacing: int = 15):
        super().__init__(parent)
        self.card_width = card_width
        self.spacing = spacing
        self.widgets = []
        self.grid_layout = QGridLayout(self)
        self.grid_layout.setContentsMargins(10, 10, 10, 10)
        self.grid_layout.setSpacing(spacing)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

    def set_banner_widgets(self, widgets: list):
        # Hide and destroy previous widgets that are no longer active
        for old_w in self.widgets:
            if old_w not in widgets:
                old_w.hide()
                old_w.setParent(None)
                old_w.deleteLater()
        self.widgets = widgets
        self.reflow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def reflow(self):
        # Clear layout items without double-destroying child widgets
        while self.grid_layout.count() > 0:
            item = self.grid_layout.takeAt(0)
        
        if not self.widgets:
            return
            
        available_width = max(1, self.width() - 20)
        cols = max(1, available_width // (self.card_width + self.spacing))
        
        for index, widget in enumerate(self.widgets):
            row = index // cols
            col = index % cols
            self.grid_layout.addWidget(widget, row, col)

class DialogTitleBar(QFrame):
    """Custom top drag bar for modal dialogs with title and close button."""
    def __init__(self, dialog: QDialog, title: str):
        super().__init__(dialog)
        self.dialog = dialog
        self.drag_pos = None
        self.setFixedHeight(38)
        self.setStyleSheet("""
            QFrame {
                background: #090909;
                border-bottom: 1px solid #222222;
            }
            QLabel {
                color: #ffffff;
                font-weight: bold;
                font-size: 13px;
                background: transparent;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 8, 0)

        self.title_label = QLabel(title)
        layout.addWidget(self.title_label)
        layout.addStretch()

        btn_close = QPushButton()
        btn_close.setIcon(get_app_icon("close"))
        btn_close.setFixedSize(26, 26)
        btn_close.setStyleSheet("QPushButton { background: transparent; border-radius: 4px; padding: 0px; } QPushButton:hover { background: #c62828; }")
        btn_close.clicked.connect(self.dialog.reject)
        layout.addWidget(btn_close)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            widget = self.childAt(event.position().toPoint())
            if not isinstance(widget, QPushButton):
                handle = self.dialog.windowHandle()
                if handle and hasattr(handle, "startSystemMove"):
                    handle.startSystemMove()
                else:
                    self.drag_pos = event.globalPosition().toPoint() - self.dialog.frameGeometry().topLeft()
                event.accept()

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.MouseButton.LeftButton) and getattr(self, 'drag_pos', None) is not None:
            self.dialog.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_pos = None
        super().mouseReleaseEvent(event)


class AddGameDialog(QDialog):
    def __init__(self, parent=None, sgdb_client: SteamGridDBClient = None):
        super().__init__(parent)
        self.setWindowTitle("Add / Install Game")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setFixedSize(860, 680)
        if os.path.exists(LOGO_PATH):
            self.setWindowIcon(QIcon(LOGO_PATH))
            
        self.sgdb_client = sgdb_client
        self.banner_path = None
        self.fetcher_thread = None
        self.downloader_thread = None
        self.extractor_thread = None
        self.search_results = []
        
        ensure_sandbox_dir()

        # Root vertical layout (Title bar + Main body + Bottom action bar)
        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Custom Draggable Title Bar
        self.title_bar = DialogTitleBar(self, "➕ Add / Install Game")
        root_layout.addWidget(self.title_bar)

        # Main Body Widget (2-Column Grid Layout)
        body_widget = QWidget()
        body_layout = QHBoxLayout(body_widget)
        body_layout.setContentsMargins(25, 20, 25, 20)
        body_layout.setSpacing(25)

        # -------------------------------------------------------------
        # LEFT COLUMN: Game Configuration Form (~500px width)
        # -------------------------------------------------------------
        left_box = QVBoxLayout()
        left_box.setSpacing(14)

        sec_details = QLabel("Game Configuration")
        sec_details.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        sec_details.setStyleSheet("color: #ffffff; padding-bottom: 5px;")
        left_box.addWidget(sec_details)

        form_layout = QFormLayout()
        form_layout.setSpacing(12)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        # Name
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g., Portal 2, Cyberpunk 2077")
        self.name_input.setMinimumHeight(36)
        form_layout.addRow("Game Title:", self.name_input)

        # Game Path
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText(f"e.g., {DEFAULT_SANDBOX_DIR}/MyGame")
        self.path_input.setMinimumHeight(36)
        
        browse_folder_btn = QPushButton(" Browse...")
        browse_folder_btn.setIcon(get_app_icon("sandbox"))
        browse_folder_btn.setMinimumHeight(36)
        browse_folder_btn.clicked.connect(self._browse_path)

        path_row = QHBoxLayout()
        path_row.addWidget(self.path_input)
        path_row.addWidget(browse_folder_btn)
        form_layout.addRow("Game Directory:", path_row)

        # Executable
        self.exe_combo = QComboBox()
        self.exe_combo.setEditable(True)
        self.exe_combo.setPlaceholderText("e.g., game.exe, bin/game.exe, start.sh")
        self.exe_combo.setMinimumHeight(36)
        
        exe_browse_btn = QPushButton(" Browse...")
        exe_browse_btn.setIcon(get_app_icon("sandbox"))
        exe_browse_btn.setMinimumHeight(36)
        exe_browse_btn.clicked.connect(self._browse_exe)
        
        exe_row = QHBoxLayout()
        exe_row.addWidget(self.exe_combo)
        exe_row.addWidget(exe_browse_btn)
        form_layout.addRow("Executable File:", exe_row)

        # Launch Mode
        self.mode_combo = QComboBox()
        self.mode_combo.setMinimumHeight(36)
        self.mode_combo.addItem("🛡️ Primary: UMU (Proton/Wine - Offline)", "umu")
        self.mode_combo.addItem("🌐 Secondary: UMU (Network Enabled)", "umu_net")
        self.mode_combo.addItem("🍷 3rd: Legacy Wine (Standalone)", "wine")
        self.mode_combo.addItem("🐧 Native Linux Binary / Script", "linux")
        form_layout.addRow("Runner Mode:", self.mode_combo)

        left_box.addLayout(form_layout)

        # Status Label / Banner
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #4ade80; font-weight: bold; font-size: 11px; padding: 6px 0px;")
        left_box.addWidget(self.status_label)

        left_box.addStretch()
        body_layout.addLayout(left_box, stretch=3)

        # -------------------------------------------------------------
        # RIGHT COLUMN: Cover Art & Steam Grid DB Search (~280px width)
        # -------------------------------------------------------------
        right_box = QVBoxLayout()
        right_box.setSpacing(12)
        right_box.setAlignment(Qt.AlignmentFlag.AlignTop)

        sec_cover = QLabel("Cover Art Poster")
        sec_cover.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        sec_cover.setStyleSheet("color: #ffffff; padding-bottom: 5px;")
        right_box.addWidget(sec_cover)

        # Banner preview card (2:3 portrait aspect ratio)
        preview_container = QHBoxLayout()
        self.banner_label = QLabel()
        self.banner_label.setFixedSize(QSize(180, 270))
        self.banner_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner_label.setStyleSheet("border: 2px solid #333333; border-radius: 6px; background: #080808;")
        pixmap = QPixmap(180, 270)
        pixmap.fill(QColor("#1f1f1f"))
        self.banner_label.setPixmap(pixmap)
        preview_container.addWidget(self.banner_label)
        right_box.addLayout(preview_container)

        # Search cover art button
        self.fetch_btn = QPushButton(" Search Cover Art")
        self.fetch_btn.setIcon(get_app_icon("search"))
        self.fetch_btn.setMinimumHeight(34)
        self.fetch_btn.clicked.connect(self._fetch_banner)
        right_box.addWidget(self.fetch_btn)

        skip_btn = QPushButton(" Clear Cover")
        skip_btn.setIcon(get_icon("ph.x-circle-bold"))
        skip_btn.setMinimumHeight(32)
        skip_btn.clicked.connect(self._skip_banner)
        right_box.addWidget(skip_btn)

        body_layout.addLayout(right_box, stretch=2)
        root_layout.addWidget(body_widget)

        # -------------------------------------------------------------
        # BOTTOM ACTION TOOLBAR
        # -------------------------------------------------------------
        bottom_frame = QFrame()
        bottom_frame.setStyleSheet("QFrame { background: #090909; border-top: 1px solid #222222; }")
        bottom_layout = QHBoxLayout(bottom_frame)
        bottom_layout.setContentsMargins(25, 12, 25, 12)

        bottom_layout.addStretch()

        cancel_btn = QPushButton(" Cancel")
        cancel_btn.setIcon(get_app_icon("close"))
        cancel_btn.setMinimumSize(110, 38)
        cancel_btn.clicked.connect(self.reject)
        bottom_layout.addWidget(cancel_btn)

        self.add_btn = QPushButton(" Add Game")
        self.add_btn.setIcon(get_app_icon("add"))
        self.add_btn.setMinimumSize(140, 38)
        self.add_btn.setStyleSheet("QPushButton { background: #2e7d32; color: white; font-weight: bold; border-radius: 6px; } QPushButton:hover { background: #388e3c; }")
        self.add_btn.clicked.connect(self.accept)
        bottom_layout.addWidget(self.add_btn)

        root_layout.addWidget(bottom_frame)
        self.setLayout(root_layout)

        self.setStyleSheet("""
            QDialog { background: #121212; border: 1px solid #2a2a2a; border-radius: 8px; }
            QLabel { color: #e5e5e5; font-size: 12px; }
            QLineEdit { background: #1c1c1c; color: #fff; border: 1px solid #333333; padding: 6px 10px; border-radius: 5px; }
            QLineEdit:focus { border: 1px solid #2563eb; }
            QPushButton { background: #222222; color: white; border: 1px solid #333333; padding: 6px 14px; border-radius: 5px; font-weight: bold; }
            QPushButton:hover { background: #333333; }
            QComboBox { background: #1c1c1c; color: #fff; border: 1px solid #333333; padding: 6px 10px; border-radius: 5px; }
            QComboBox::drop-down { border: none; }
            QListWidget { background: #1c1c1c; color: #fff; border: 1px solid #333333; border-radius: 5px; }
            QListWidget::item { padding: 6px; }
            QListWidget::item:selected { background: #1e293b; color: #64b5f6; }
            QListWidget::item:hover { background: #262626; }
        """)

    def closeEvent(self, event):
        """Clean up background threads on dialog close"""
        for thread in [self.fetcher_thread, self.downloader_thread, self.extractor_thread]:
            if thread and thread.isRunning():
                thread.quit()
                thread.wait(1000)
        super().closeEvent(event)
    
    def _scan_and_populate_exes(self, path: str):
        """Scan directory for executables and populate dropdown"""
        exes = find_executables(path)
        self.exe_combo.clear()
        
        # Check if .sandbox-config exists
        cfg_exe = load_sandbox_config(path)
        if cfg_exe:
            if cfg_exe not in exes:
                exes.insert(0, cfg_exe)
            else:
                exes.remove(cfg_exe)
                exes.insert(0, cfg_exe)
                
        if exes:
            self.exe_combo.addItems(exes)
            self.exe_combo.setCurrentIndex(0)
    
    def _browse_path(self):
        """Browse for an existing game directory"""
        path = QFileDialog.getExistingDirectory(
            self,
            "Select Game Directory",
            DEFAULT_SANDBOX_DIR
        )
        if path:
            self.path_input.setText(path)
            folder_name = os.path.basename(path.rstrip("/\\"))
            if not self.name_input.text().strip() and folder_name:
                self.name_input.setText(folder_name)
            self._scan_and_populate_exes(path)
    
    def _install_archive(self):
        """Select and extract a game archive (.zip, .7z, .tar.gz) safely into ~/Games/Sandbox"""
        archive_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Game Archive",
            "",
            "Archives (*.zip *.7z *.tar.gz *.tgz *.tar);;All Files (*)"
        )
        if not archive_path:
            return
        
        game_name = os.path.splitext(os.path.basename(archive_path))[0]
        if game_name.endswith(".tar"):
            game_name = os.path.splitext(game_name)[0]
            
        dest_dir = os.path.join(DEFAULT_SANDBOX_DIR, game_name)
        
        self.status_label.setText(f"⏳ Extracting archive securely into {dest_dir}...")
        self.name_input.setText(game_name)
        self.path_input.setText(dest_dir)
        
        self.extractor_thread = ArchiveExtractorThread(archive_path, dest_dir)
        self.extractor_thread.extraction_complete.connect(self._on_extraction_complete)
        self.extractor_thread.start()
    
    def _on_extraction_complete(self, game_name: str, dest_dir: str, success: bool):
        if success:
            self.status_label.setText("✓ Archive extracted securely!")
            self._scan_and_populate_exes(dest_dir)
            if not self.search_results and self.sgdb_client:
                self._fetch_banner()
        else:
            self.status_label.setText("✗ Sandboxed extraction failed.")
            QMessageBox.critical(self, "Extraction Error", f"Failed to extract archive to {dest_dir}.")
    
    def _browse_exe(self):
        """Browse for executable file relative to game path if possible"""
        game_path = self.path_input.text().strip() or DEFAULT_SANDBOX_DIR
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Executable",
            game_path,
            "Executables (*.exe *.bat *.sh);;All Files (*)"
        )
        if path:
            if game_path and path.startswith(game_path):
                rel = os.path.relpath(path, start=game_path)
                self.exe_combo.setEditText(rel)
            else:
                filename = os.path.basename(path)
                self.exe_combo.setEditText(filename)
    
    def _fetch_banner(self):
        game_name = self.name_input.text().strip()
        if not game_name:
            QMessageBox.warning(self, "Error", "Please enter a game name first.")
            return
        
        if not self.sgdb_client:
            QMessageBox.warning(self, "Error", "Banner fetcher not available.")
            return
        
        # Disable button while fetching
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("🔄 Searching...")
        
        # Create and start fetcher thread
        self.fetcher_thread = BannerFetcher(game_name, self.sgdb_client)
        self.fetcher_thread.results_found.connect(self._on_results_found)
        self.fetcher_thread.error_occurred.connect(self._on_search_error)
        self.fetcher_thread.finished.connect(self._reset_fetch_button)
        self.fetcher_thread.start()
    
    def _on_results_found(self, results: list):
        """Display search results in a floating overlay popup menu right below the search button"""
        self.search_results = results
        if not results:
            if hasattr(self.parent(), '_show_toast'):
                self.parent()._show_toast("No cover art found on Steam.", is_error=True)
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #181818;
                color: #ffffff;
                border: 1px solid #333333;
                border-radius: 6px;
                padding: 6px;
            }
            QMenu::item {
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 12px;
            }
            QMenu::item:selected {
                background-color: #1e293b;
                color: #64b5f6;
            }
        """)

        for i, result in enumerate(results):
            name = result.get('name', 'Unknown')
            released = result.get('released', 'Unknown')
            action = menu.addAction(get_app_icon("library"), f"{name} ({released})")
            action.setData(i)

        # Position menu right underneath the fetch button
        pos = self.fetch_btn.mapToGlobal(QPoint(0, self.fetch_btn.height()))
        selected_action = menu.exec(pos)
        if selected_action is not None:
            idx = selected_action.data()
            if idx is not None and 0 <= idx < len(self.search_results):
                self._select_result_idx(idx)

    def _select_result_idx(self, idx: int):
        """Download and set selected result from popup menu"""
        if 0 <= idx < len(self.search_results):
            result = self.search_results[idx]
            banner_url = result.get('banner_url')
            if banner_url and self.sgdb_client:
                if self.downloader_thread and self.downloader_thread.isRunning():
                    self.downloader_thread.quit()
                    self.downloader_thread.wait(500)
                self.downloader_thread = BannerDownloader(banner_url, self.sgdb_client)
                self.downloader_thread.download_complete.connect(self._on_banner_downloaded)
                self.downloader_thread.start()
    
    def _on_banner_downloaded(self, image_path: str):
        """Update preview image when background download completes with smooth scaling"""
        if image_path and os.path.exists(image_path):
            self.banner_path = image_path
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                target_size = self.banner_label.size()
                scaled = pixmap.scaled(
                    target_size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation
                )
                crop_x = max(0, (scaled.width() - target_size.width()) // 2)
                crop_y = max(0, (scaled.height() - target_size.height()) // 2)
                cropped = scaled.copy(crop_x, crop_y, target_size.width(), target_size.height())
                self.banner_label.setPixmap(cropped)
    
    def _on_search_error(self, error_msg: str):
        """Handle search error"""
        QMessageBox.information(self, "Search Info", error_msg)
    
    def _reset_fetch_button(self):
        """Re-enable fetch button"""
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("🔍 Search for Cover Art (Steam Store)")
    
    def _skip_banner(self):
        """Clear cover art preview and mark as explicitly cleared."""
        self.banner_path = "none"
        pixmap = QPixmap(180, 270)
        pixmap.fill(QColor("#181818"))
        painter = QPainter(pixmap)
        painter.setPen(QColor("#777777"))
        painter.setFont(QFont("Monospace", 11, QFont.Weight.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "No Cover Art")
        painter.end()
        self.banner_label.setPixmap(pixmap)
    
    def get_values(self):
        """Extract entered form values cleanly."""
        mode = self.mode_combo.currentData() or self.mode_combo.currentText()
        return (
            self.name_input.text().strip(),
            self.path_input.text().strip(),
            self.exe_combo.currentText().strip(),
            mode,
            self.banner_path
        )


class EditGameDialog(AddGameDialog):
    """Dialog pre-populated with existing game details allowing editing name, path, exe, mode, and cover art."""
    def __init__(self, game_data: tuple, parent=None, sgdb_client: SteamGridDBClient = None):
        super().__init__(parent, sgdb_client)
        self.title_bar.title_label.setText("✏️ Edit Game Settings")
        
        # Unpack game data (game_id, name, path, exe, mode, banner_url, steam_id, ...)
        game_id, name, path, exe, mode, banner_url, steam_id, *_ = (*game_data, 0)
        self.game_id = game_id
        self.banner_path = banner_url  # Preserve existing banner URL/path
        
        # Populate pre-existing values
        self.name_input.setText(name or "")
        self.path_input.setText(path or "")
        
        if path and os.path.exists(path):
            self._scan_and_populate_exes(path)
            
        if exe:
            self.exe_combo.setEditText(exe)
            
        mode_idx = self.mode_combo.findData(mode)
        if mode_idx < 0:
            mode_idx = self.mode_combo.findText(mode)
        if mode_idx >= 0:
            self.mode_combo.setCurrentIndex(mode_idx)
            
        if banner_url and os.path.exists(banner_url):
            self._on_banner_downloaded(banner_url)
            
        # Customize main action button for Edit mode
        self.add_btn.setText("Save Changes")
        self.add_btn.setIcon(get_app_icon("export"))


class ToastNotification(QFrame):
    """Floating non-blocking toast overlay for smooth status updates."""
    def __init__(self, parent=None, message: str = "", is_error: bool = False, duration_ms: int = 3000):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.SubWindow)
        
        self.setStyleSheet("""
            QFrame {
                background-color: #141414;
                border: none;
                border-radius: 6px;
            }
            QLabel {
                color: #ffffff;
                font-weight: bold;
                font-size: 12px;
                background: transparent;
                border: none;
                padding: 0px;
            }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)
        
        icon_name = "shield" if is_error else "library"
        icon_label = QLabel()
        icon_label.setPixmap(get_app_icon(icon_name).pixmap(16, 16))
        layout.addWidget(icon_label)
        
        text_label = QLabel(message)
        layout.addWidget(text_label)
        
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._auto_close)
        self.duration_ms = duration_ms

    def show_toast(self, parent_widget: QWidget):
        self.adjustSize()
        px = parent_widget.width() - self.width() - 25
        py = parent_widget.height() - self.height() - 25
        self.move(max(10, px), max(10, py))
        self.raise_()
        self.show()
        self.timer.start(self.duration_ms)

    def _auto_close(self):
        self.hide()
        self.deleteLater()


class CustomRemoveDialog(QDialog):
    """Custom styled dark confirmation dialog for game removal."""
    def __init__(self, game_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Remove Game")
        self.setFixedWidth(440)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setStyleSheet("""
            QDialog {
                background-color: #181818;
                border: 1px solid #333333;
                border-radius: 8px;
            }
            QLabel {
                color: #ffffff;
            }
            QPushButton {
                padding: 10px 16px;
                border-radius: 6px;
                font-weight: bold;
                font-size: 12px;
                border: none;
            }
        """)

        self.choice = None  # 'library_only', 'delete_disk', or 'cancel'

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        title_label = QLabel("🗑️ Remove Game")
        title_label.setFont(QFont("Arial", 15, QFont.Weight.Bold))
        layout.addWidget(title_label)

        msg_label = QLabel(f"How would you like to remove '{game_name}'?")
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("color: #cccccc; font-size: 13px;")
        layout.addWidget(msg_label)

        btn_box = QVBoxLayout()
        btn_box.setSpacing(10)

        btn_lib = QPushButton("Library Only (Keep Files on Disk)")
        btn_lib.setStyleSheet("QPushButton { background: #1e293b; color: white; border: 1px solid #334155; } QPushButton:hover { background: #334155; }")
        btn_lib.clicked.connect(self._select_lib)

        btn_disk = QPushButton("Delete Game Files & Sandbox Data from Disk")
        btn_disk.setStyleSheet("QPushButton { background: #c62828; color: white; } QPushButton:hover { background: #e53935; }")
        btn_disk.clicked.connect(self._select_disk)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet("QPushButton { background: #333333; color: #aaaaaa; } QPushButton:hover { background: #444444; color: white; }")
        btn_cancel.clicked.connect(self.reject)

        btn_box.addWidget(btn_lib)
        btn_box.addWidget(btn_disk)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)

    def _select_lib(self):
        self.choice = 'library_only'
        self.accept()

    def _select_disk(self):
        self.choice = 'delete_disk'
        self.accept()


class CustomTitleBar(QFrame):
    """Custom top title bar containing navigation actions, window dragging, double-click maximize, and app control buttons."""
    def __init__(self, main_window: QMainWindow):
        super().__init__(main_window)
        self.main_window = main_window
        self.drag_pos = None
        self.setFixedHeight(44)
        self.setStyleSheet("""
            QFrame {
                background: #090909;
                border-bottom: 1px solid #222222;
            }
            QPushButton {
                background: transparent;
                color: #aaaaaa;
                padding: 5px 12px;
                border-radius: 5px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1e1e1e;
                color: #ffffff;
            }
            QPushButton:checked {
                background: #1e293b;
                color: #ffffff;
                border: 1px solid #334155;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(8)

        # App Logo on far left of top bar
        if os.path.exists(LOGO_PATH):
            logo_label = QLabel()
            logo_pix = QPixmap(LOGO_PATH).scaled(
                24, 24,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            logo_label.setPixmap(logo_pix)
            layout.addWidget(logo_label)

        # Top Bar Navigation Buttons
        self.nav_library = QPushButton(" My Library")
        self.nav_library.setIcon(get_app_icon("library"))
        self.nav_library.setCheckable(True)
        self.nav_library.setChecked(True)

        self.nav_sandbox = QPushButton(" Sandbox Folder")
        self.nav_sandbox.setIcon(get_app_icon("sandbox"))

        self.nav_install_zip = QPushButton(" Install Zip/7z")
        self.nav_install_zip.setIcon(get_icon("ph.archive-bold"))
        self.nav_install_zip.setToolTip("Extract and install game from ZIP or 7z archive")

        self.nav_sync = QPushButton()
        self.nav_sync.setIcon(get_app_icon("sync"))
        self.nav_sync.setToolTip("Sync Sandbox Library")
        self.nav_sync.setFixedSize(32, 28)
        self.nav_sync.setStyleSheet("""
            QPushButton {
                background: #181818;
                border-radius: 5px;
                padding: 0px;
            }
            QPushButton:hover {
                background: #252525;
            }
        """)

        self.btn_saves = QPushButton(" Saves")
        self.btn_saves.setIcon(get_app_icon("export"))
        
        saves_menu = QMenu(self)
        saves_menu.setStyleSheet("""
            QMenu {
                background-color: #1a1a1a;
                color: #ffffff;
                border: 1px solid #333333;
                border-radius: 6px;
                padding: 6px;
            }
            QMenu::item {
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 12px;
            }
            QMenu::item:selected {
                background-color: #1e293b;
                color: #ffffff;
            }
        """)
        self.act_export = saves_menu.addAction(get_app_icon("export"), " Export Selected Game Save")
        self.act_import = saves_menu.addAction(get_app_icon("import"), " Import Selected Game Save")
        self.btn_saves.setMenu(saves_menu)

        layout.addWidget(self.nav_library)
        layout.addWidget(self.nav_sandbox)
        layout.addWidget(self.nav_install_zip)
        layout.addWidget(self.nav_sync)
        layout.addWidget(self.btn_saves)

        layout.addStretch()

        # Installed Games Counter badge in top bar
        self.stat_label = QLabel("0 Games Installed")
        self.stat_label.setStyleSheet("color: #777777; font-size: 11px; font-weight: bold; margin-right: 10px;")
        layout.addWidget(self.stat_label)

        # Window Control Buttons (Minimize, Maximize/Restore, Close)
        btn_minimize = QPushButton()
        btn_minimize.setIcon(get_app_icon("minimize"))
        btn_minimize.setFixedSize(32, 28)
        btn_minimize.setToolTip("Minimize Window")
        btn_minimize.setStyleSheet("QPushButton { background: transparent; border-radius: 4px; padding: 0px; } QPushButton:hover { background: #222; }")
        btn_minimize.clicked.connect(self.main_window.showMinimized)

        self.btn_max = QPushButton()
        self.btn_max.setIcon(get_app_icon("maximize"))
        self.btn_max.setFixedSize(32, 28)
        self.btn_max.setToolTip("Maximize / Restore Window")
        self.btn_max.setStyleSheet("QPushButton { background: transparent; border-radius: 4px; padding: 0px; } QPushButton:hover { background: #222; }")
        self.btn_max.clicked.connect(self.main_window._toggle_maximize)

        btn_close = QPushButton()
        btn_close.setIcon(get_app_icon("close"))
        btn_close.setFixedSize(32, 28)
        btn_close.setToolTip("Close Window")
        btn_close.setStyleSheet("QPushButton { background: transparent; border-radius: 4px; padding: 0px; } QPushButton:hover { background: #c62828; }")
        btn_close.clicked.connect(self.main_window.close)

        layout.addWidget(btn_minimize)
        layout.addWidget(self.btn_max)
        layout.addWidget(btn_close)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            widget = self.childAt(event.position().toPoint())
            if not isinstance(widget, QPushButton):
                handle = self.main_window.windowHandle()
                if handle and hasattr(handle, "startSystemMove"):
                    handle.startSystemMove()
                else:
                    self.drag_pos = event.globalPosition().toPoint() - self.main_window.frameGeometry().topLeft()
                event.accept()

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.MouseButton.LeftButton) and getattr(self, 'drag_pos', None) is not None:
            if not self.main_window.isMaximized():
                self.main_window.move(event.globalPosition().toPoint() - self.drag_pos)
                event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            widget = self.childAt(event.position().toPoint())
            if not isinstance(widget, QPushButton):
                self.main_window._toggle_maximize()
                event.accept()

class MainWindow(QMainWindow):
    def __init__(self, db: GameDatabase, runner: ISandboxRunner, backup: IBackupManager):
        super().__init__()
        self.db = db
        self.runner = runner
        self.backup = backup
        self.sgdb_client = SteamGridDBClient()
        self.games = []
        self.selected_game = None
        self.banner_widgets = {}
        self.auto_fetchers = []
        self.playtime_trackers = []  # keep references so GC doesn't kill running threads

        self.setWindowTitle("🎮 MGLauncher - Game Sandbox Manager")
        self.resize(1180, 750)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        
        if os.path.exists(LOGO_PATH):
            self.setWindowIcon(QIcon(LOGO_PATH))

        # Root Layout: Top Title Bar + Content Panel
        root_widget = QWidget()
        root_widget.setStyleSheet("background: #141414;")
        root_vbox = QVBoxLayout(root_widget)
        root_vbox.setContentsMargins(0, 0, 0, 0)
        root_vbox.setSpacing(0)
        
        # Top Custom Draggable Title Bar
        self.title_bar = CustomTitleBar(self)
        root_vbox.addWidget(self.title_bar)
        
        self.nav_library = self.title_bar.nav_library
        self.nav_sandbox = self.title_bar.nav_sandbox
        self.nav_sandbox.clicked.connect(self._open_sandbox_dir)
        self.nav_install_zip = self.title_bar.nav_install_zip
        self.nav_install_zip.clicked.connect(self._on_install_zip_archive)
        self.nav_sync = self.title_bar.nav_sync
        self.nav_sync.clicked.connect(self._on_sync_sandbox)
        self.stat_label = self.title_bar.stat_label

        self.title_bar.act_export.triggered.connect(self._on_export)
        self.title_bar.act_import.triggered.connect(self._on_import)

        # Main Content Panel (Full Width Game Library Grid)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(20, 20, 20, 20)
        right_layout.setSpacing(15)
        root_vbox.addWidget(right_panel)
        
        # Right Header / Title
        header_layout = QHBoxLayout()
        header_title = QLabel("Game Library")
        header_title.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        header_title.setStyleSheet("color: #fff;")
        header_layout.addWidget(header_title)
        header_layout.addStretch()
        right_layout.addLayout(header_layout)

        # Games Grid in Scroll Area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { background: #141414; border: none; }")
        
        # Dynamic Responsive Grid Container (2:3 portrait cards, width 200px)
        self.grid_container = ResponsiveGridContainer(card_width=200, spacing=15)
        scroll_area.setWidget(self.grid_container)
        right_layout.addWidget(scroll_area)

        # Action Buttons Layout (Add Game on bottom-left, Launch on bottom-right)
        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 5, 0, 0)
        
        self.btn_add = QPushButton(" Add / Install Game")
        self.btn_add.setIcon(get_app_icon("add"))
        self.btn_add.clicked.connect(self._on_add)
        self.btn_add.setMinimumHeight(42)
        self.btn_add.setStyleSheet("QPushButton { background: #1e293b; color: white; font-weight: bold; border-radius: 6px; border: 1px solid #334155; padding: 10px 20px; } QPushButton:hover { background: #334155; }")
        action_layout.addWidget(self.btn_add)

        action_layout.addStretch()

        self.btn_launch = QPushButton(" Launch Selected")
        self.btn_launch.setIcon(get_app_icon("launch"))
        self.btn_launch.clicked.connect(self._on_launch)
        self.btn_launch.setMinimumHeight(42)
        self.btn_launch.setStyleSheet("QPushButton { background: #2e7d32; color: white; font-weight: bold; border-radius: 6px; padding: 10px 24px; font-size: 13px; } QPushButton:hover { background: #388e3c; }")
        action_layout.addWidget(self.btn_launch)
        
        right_layout.addLayout(action_layout)
        self.setCentralWidget(root_widget)
        
        self.setStyleSheet("""
            QMainWindow { background: #141414; }
            QPushButton { 
                background: #0d47a1; 
                color: white; 
                border: none; 
                padding: 8px 15px;
                border-radius: 6px; 
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover { background: #1565c0; }
            QLabel { color: #fff; }
        """)
        
        # 5-minute periodic drive check timer (300,000 ms)
        self.drive_check_timer = QTimer(self)
        self.drive_check_timer.setInterval(5 * 60 * 1000)
        self.drive_check_timer.timeout.connect(self._check_games_on_drive)
        self.drive_check_timer.start()

        # Auto sync sandbox games on startup
        self._on_sync_sandbox(quiet=True)
        self._refresh_library()

    def _toggle_maximize(self):
        """Toggle between maximized state and normal window size"""
        if self.isMaximized():
            self.showNormal()
            if hasattr(self, 'title_bar'):
                self.title_bar.btn_max.setIcon(get_app_icon("maximize"))
        else:
            self.showMaximized()
            if hasattr(self, 'title_bar'):
                self.title_bar.btn_max.setIcon(get_app_icon("restore"))

    def _open_sandbox_dir(self):
        """Open ~/Games/Sandbox in system file manager"""
        import subprocess
        path = ensure_sandbox_dir()
        try:
            subprocess.Popen(["xdg-open", path])
        except Exception as e:
            QMessageBox.information(self, "Sandbox Path", f"Sandbox directory:\n{path}")

    def _on_install_zip_archive(self):
        """Install game by picking a zip/7z archive directly from the top bar."""
        zip_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Game Zip/7z Archive",
            "",
            "Archive Files (*.zip *.7z *.tar.gz *.rar)"
        )
        if not zip_path or not os.path.exists(zip_path):
            return

        archive_name = os.path.splitext(os.path.basename(zip_path))[0]
        sandbox_dir = ensure_sandbox_dir()
        dest_dir = os.path.join(sandbox_dir, archive_name)

        thread = ExtractionThread(zip_path, dest_dir)
        thread.extraction_complete.connect(self._on_topbar_extraction_complete)
        self._show_toast(f"Extracting '{archive_name}' in background...")
        thread.start()
        self.topbar_extractor_thread = thread

    def _on_topbar_extraction_complete(self, game_name: str, dest_dir: str, success: bool):
        """Callback when topbar archive extraction completes"""
        if not success:
            self._show_toast(f"Failed to extract '{game_name}'.", is_error=True)
            return

        self._show_toast(f"✓ Extracted '{game_name}' successfully!")
        exes = find_executables(dest_dir)
        default_exe = exes[0] if exes else ""

        dialog = AddGameDialog(self, self.sgdb_client)
        dialog.name_input.setText(game_name)
        dialog.path_input.setText(dest_dir)
        dialog._scan_and_populate_exes(dest_dir)
        if default_exe:
            dialog.exe_combo.setEditText(default_exe)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, path, exe, mode, banner_path = dialog.get_values()
            if name and path and exe:
                save_sandbox_config(path, exe)
                self.db.add_game(name, path, exe, mode, banner_path)
                self._refresh_library()
                self._show_toast(f"✓ Game '{name}' added to library!")

    def _refresh_library(self):
        """Clear and reload game banners into dynamic responsive grid"""
        # Explicitly hide and destroy old child widgets to prevent layout overlap/stacking
        for old_w in list(self.banner_widgets.values()):
            old_w.hide()
            old_w.setParent(None)
            old_w.deleteLater()
        self.banner_widgets.clear()
        
        self.games = self.db.get_all_games()
        
        self.stat_label.setText(f"{len(self.games)} Game(s) Installed")
        
        if not self.games:
            label = QLabel("🎮 No games in library yet.\nClick 'Add / Install Game' or 'Sync Library' to get started!")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("color: #999; font-size: 14px; padding: 40px;")
            self.grid_container.set_banner_widgets([label])
            return
        
        widgets = []
        for game in self.games:
            game_id, name, path, executable, mode, banner_url, steam_id, playtime_seconds = (*game[:7], game[7] if len(game) > 7 else 0)
            
            widget = GameBannerWidget(game_id, name, banner_url, playtime_seconds or 0)
            widget.clicked.connect(self._select_game)
            widget.rightClicked.connect(self._on_game_right_clicked)
            
            widgets.append(widget)
            self.banner_widgets[game_id] = widget
            
            # Only auto-fetch if banner_url is None (never set), NOT if explicitly cleared ('none' or empty)!
            if banner_url is None:
                fetcher = BannerAutoFetcher(game_id, name, self.sgdb_client)
                fetcher.banner_auto_downloaded.connect(self._on_auto_banner_downloaded)
                fetcher.start()
                self.auto_fetchers.append(fetcher)
            
        self.grid_container.set_banner_widgets(widgets)
        self._check_games_on_drive()

    def _check_games_on_drive(self):
        """Check all games in library against disk and grey out missing ones"""
        for game in self.games:
            game_id, name, path, executable, mode, banner_url, steam_id, *_ = (*game, 0)
            
            folder_exists = os.path.exists(path) if path else False
            full_exe_path = os.path.join(path, executable) if (path and executable) else path
            exe_exists = os.path.exists(full_exe_path) if full_exe_path else False
            
            is_missing = not (folder_exists and (exe_exists or not executable))
            
            if game_id in self.banner_widgets:
                self.banner_widgets[game_id].set_missing(is_missing)

    def _on_auto_banner_downloaded(self, game_id: int, image_path: str):
        """Update DB and widget when background auto-fetch completes"""
        self.db.update_game_banner(game_id, image_path)
        if game_id in self.banner_widgets:
            self.banner_widgets[game_id].set_banner(image_path)

    def _select_game_by_id(self, game_id: int):
        """Select a game card visually without triggering launch popup menu"""
        for widget in self.banner_widgets.values():
            widget.set_selected(False)
        for game in self.games:
            if game[0] == game_id:
                self.selected_game = game
                if game_id in self.banner_widgets:
                    self.banner_widgets[game_id].set_selected(True)
                break

    def _select_game(self, game_id: int):
        """Single clicking a game banner selects it and opens launch menu directly"""
        self._select_game_by_id(game_id)
        self._on_launch()

    def _on_game_right_clicked(self, game_id: int, global_pos: QPoint):
        """Show full context menu when right-clicking a game card"""
        self._select_game_by_id(game_id)
        game = self._get_selected_game()
        if not game:
            return

        game_id, name, path, exe, mode, banner_url, steam_id, *_ = (*game, 0)

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1a1a1a;
                color: #ffffff;
                border: 1px solid #333333;
                border-radius: 6px;
                padding: 6px;
            }
            QMenu::item {
                padding: 8px 18px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 12px;
            }
            QMenu::item:selected {
                background-color: #1e293b;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: #333333;
                margin: 4px 6px;
            }
        """)

        # 1. Launch Options Sub-Menu
        launch_menu = menu.addMenu(get_app_icon("launch"), f" Launch {name}")
        launch_menu.setStyleSheet(menu.styleSheet())
        
        act_umu = launch_menu.addAction(get_app_icon("shield"), "Primary: UMU (Offline / No Net)")
        act_umu_net = launch_menu.addAction(get_app_icon("globe"), "Secondary: UMU (Network Enabled)")
        act_wine = launch_menu.addAction(get_app_icon("wine"), "3rd: Legacy Wine (Offline)")
        act_linux = None
        if mode == "linux":
            act_linux = launch_menu.addAction(get_app_icon("terminal"), "Native Linux Script/Binary")

        menu.addSeparator()

        # 2. Edit Settings
        act_edit = menu.addAction(get_app_icon("edit"), " Edit Game Settings")
        
        # 3. Export Save
        act_export = menu.addAction(get_app_icon("export"), " Export Save")
        
        # 4. Import Save
        act_import = menu.addAction(get_app_icon("import"), " Import Save")

        menu.addSeparator()

        # 5. Remove Game
        act_remove = menu.addAction(get_app_icon("remove"), " Remove Game")

        selected = menu.exec(global_pos)
        if not selected:
            return

        if selected == act_edit:
            self._on_edit()
        elif selected == act_export:
            self._on_export()
        elif selected == act_import:
            self._on_import()
        elif selected == act_remove:
            self._on_remove()
        elif selected == act_umu:
            self._launch_mode(game_id, path, exe, "umu")
        elif selected == act_umu_net:
            self._launch_mode(game_id, path, exe, "umu_net")
        elif selected == act_wine:
            self._launch_mode(game_id, path, exe, "wine")
        elif act_linux and selected == act_linux:
            self._launch_mode(game_id, path, exe, "linux")

    def _launch_mode(self, game_id: int, path: str, exe: str, selected_mode: str):
        """Helper to launch a game directly with the chosen mode"""
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "Missing Game", f"Cannot launch game. Path does not exist:\n{path}")
            return
        try:
            process = self.runner.launch(path, exe, selected_mode)
            if process:
                tracker = PlaytimeTrackerThread(game_id, process, parent=self)
                tracker.playtime_recorded.connect(self._on_playtime_recorded)
                tracker.finished.connect(lambda t=tracker: self._cleanup_tracker(t))
                tracker.start()
                self.playtime_trackers.append(tracker)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to launch game: {str(e)}")

    def _get_selected_game(self):
        """Get the currently selected game"""
        return self.selected_game

    def _on_launch(self):
        game = self._get_selected_game()
        if not game:
            QMessageBox.warning(self, "Warning", "Please select a game to launch.")
            return
        
        game_id, name, path, exe, mode, banner_url, steam_id, *_ = (*game, 0)
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "Missing Game", f"Cannot launch '{name}'. Game directory does not exist on disk:\n{path}")
            return
            
        # 3-Option Popup Menu for Launching
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #444444;
                border-radius: 6px;
                padding: 6px;
            }
            QMenu::item {
                padding: 10px 20px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 12px;
                font-family: 'Monospace', 'Courier New', monospace;
            }
            QMenu::item:selected {
                background-color: #1e293b;
                color: #ffffff;
            }
        """)

        action_umu = menu.addAction(get_app_icon("shield"), "Primary: UMU (Offline / No Net)")
        action_umu_net = menu.addAction(get_app_icon("globe"), "Secondary: UMU (Network Enabled)")
        action_wine = menu.addAction(get_app_icon("wine"), "3rd: Legacy Wine (Offline)")
        
        action_linux = None
        if mode == "linux":
            action_linux = menu.addAction(get_app_icon("terminal"), "Native Linux Script/Binary")

        action_edit_menu = menu.addAction(get_app_icon("edit"), "Edit Game Settings")

        # Position popup menu centered over the selected game's banner widget if available
        from PyQt6.QtCore import QPoint
        menu_size = menu.sizeHint()
        menu_half_w = menu_size.width() // 2
        menu_half_h = menu_size.height() // 2
        
        target_widget = self.banner_widgets.get(game_id)
        if target_widget and target_widget.isVisible():
            global_center = target_widget.mapToGlobal(target_widget.rect().center())
            pos = QPoint(global_center.x() - menu_half_w, global_center.y() - menu_half_h)
        else:
            global_center = self.mapToGlobal(self.rect().center())
            pos = QPoint(global_center.x() - menu_half_w, global_center.y() - menu_half_h)
        
        selected_action = menu.exec(pos)
        if not selected_action:
            return  # User cancelled menu

        if selected_action == action_edit_menu:
            self._on_edit()
            return
        elif selected_action == action_umu:
            selected_mode = "umu"
        elif selected_action == action_umu_net:
            selected_mode = "umu_net"
        elif selected_action == action_wine:
            selected_mode = "wine"
        elif action_linux and selected_action == action_linux:
            selected_mode = "linux"
        else:
            return

        try:
            process = self.runner.launch(path, exe, selected_mode)
            if process:
                tracker = PlaytimeTrackerThread(game_id, process, parent=self)
                tracker.playtime_recorded.connect(self._on_playtime_recorded)
                tracker.finished.connect(lambda t=tracker: self._cleanup_tracker(t))
                tracker.start()
                self.playtime_trackers.append(tracker)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to launch game: {str(e)}")

    def _on_playtime_recorded(self, game_id: int, elapsed_seconds: int):
        """Called (on main thread) when a game exits — persists and displays playtime."""
        self.db.add_playtime(game_id, elapsed_seconds)
        total = self.db.get_playtime(game_id)
        if game_id in self.banner_widgets:
            self.banner_widgets[game_id].set_playtime(total)

    def _cleanup_tracker(self, tracker: PlaytimeTrackerThread):
        """Remove finished tracker from the list so it can be garbage collected."""
        try:
            self.playtime_trackers.remove(tracker)
        except ValueError:
            pass

    def _on_add(self):
        dialog = AddGameDialog(self, self.sgdb_client)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, path, exe, mode, banner_path = dialog.get_values()
            if not name or not path or not exe:
                QMessageBox.warning(self, "Error", "All fields are required.")
                return
            if not os.path.isdir(path):
                QMessageBox.warning(self, "Error", "Invalid game path.")
                return
            
            path = os.path.abspath(os.path.expanduser(path))
            save_sandbox_config(path, exe)
            self.db.add_game(name, path, exe, mode, banner_path)
            self._refresh_library()
            self._show_toast(f"✓ Game '{name}' added to library!")

    def _show_toast(self, message: str, is_error: bool = False):
        """Show non-blocking toast overlay notification in bottom-right corner."""
        toast = ToastNotification(self, message, is_error=is_error)
        toast.show_toast(self)

    def _on_edit(self):
        """Edit details of the currently selected game."""
        game = self._get_selected_game()
        if not game:
            QMessageBox.warning(self, "Warning", "Please select a game to edit.")
            return

        dialog = EditGameDialog(game, self, self.sgdb_client)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, path, exe, mode, banner_path = dialog.get_values()
            if not name or not path or not exe:
                QMessageBox.warning(self, "Error", "All fields are required.")
                return
            if not os.path.isdir(path):
                QMessageBox.warning(self, "Error", "Invalid game path.")
                return

            game_id = game[0]
            path = os.path.abspath(os.path.expanduser(path))
            save_sandbox_config(path, exe)
            self.db.update_game(game_id, name, path, exe, mode, banner_path)
            self._refresh_library()
            self._show_toast(f"✓ Updated settings for '{name}'.")
    

    def _on_sync_sandbox(self, quiet: bool = False):
        """Auto-discover installed games in ~/Games/Sandbox"""
        found = scan_sandbox_games(DEFAULT_SANDBOX_DIR)
        existing_paths = {os.path.abspath(g[2]) for g in self.db.get_all_games() if g[2]}
        
        added_count = 0
        for game in found:
            norm_path = os.path.abspath(game['path'])
            if norm_path not in existing_paths:
                self.db.add_game(game['name'], norm_path, game['executable'], game['mode'])
                added_count += 1
                
        if added_count > 0:
            self._refresh_library()
            if not quiet:
                self._show_toast(f"✓ Found and added {added_count} game(s) from sandbox!")
        else:
            if not quiet:
                self._show_toast("✓ No new games found in sandbox.")

    def _on_remove(self):
        game = self._get_selected_game()
        if not game:
            self._show_toast("Please select a game to remove.", is_error=True)
            return
        
        dialog = CustomRemoveDialog(game[1], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            game_id = game[0]
            game_path = game[2]
            
            self.db.remove_game(game_id)
            
            if dialog.choice == 'delete_disk':
                if os.path.exists(game_path):
                    try:
                        shutil.rmtree(game_path)
                        self._show_toast(f"✓ Removed '{game[1]}' and deleted files.")
                    except Exception as e:
                        self._show_toast(f"Failed to delete files: {e}", is_error=True)
                else:
                    self._show_toast(f"✓ Removed '{game[1]}' from library.")
            else:
                self._show_toast(f"✓ Removed '{game[1]}' (files preserved).")
                
            self._refresh_library()
            self.selected_game = None

    def _on_export(self):
        game = self._get_selected_game()
        if not game:
            QMessageBox.warning(self, "Warning", "Please select a game.")
            return
        
        save_path = os.path.join(game[2], "prefix", "drive_c", "users")
        
        if not os.path.exists(save_path):
            QMessageBox.warning(self, "Warning", "Save directory not found.")
            return
        
        export_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Save",
            f"{game[1]}_save.zip",
            "ZIP Files (*.zip)"
        )
        
        if export_path:
            if self.backup.export_save(save_path, export_path):
                self._show_toast("✓ Save exported successfully.")
            else:
                self._show_toast("Failed to export save.", is_error=True)
    
    def _on_import(self):
        game = self._get_selected_game()
        if not game:
            self._show_toast("Please select a game to import save.", is_error=True)
            return
        
        import_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Save",
            "",
            "ZIP Files (*.zip)"
        )
        
        if import_path:
            dest_path = os.path.join(game[2], "prefix", "drive_c", "users")
            os.makedirs(dest_path, exist_ok=True)
            
            if self.backup.import_save(import_path, dest_path):
                self._show_toast("✓ Save imported successfully.")
            else:
                self._show_toast("Failed to import save.", is_error=True)
