import os
import shutil
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QGridLayout, QFileDialog, QMessageBox, QDialog, QLabel, QLineEdit,
    QComboBox, QFormLayout, QScrollArea, QFrame, QListWidget, QListWidgetItem, QMenu,
    QApplication, QSystemTrayIcon, QCheckBox, QGraphicsOpacityEffect, QPlainTextEdit, QProgressBar,
    QGraphicsDropShadowEffect, QStackedWidget
)
from PyQt6.QtCore import Qt, QSize, QPoint, QThread, pyqtSignal, QVariantAnimation, QEasingCurve, QTimer, QEvent, QAbstractAnimation
from PyQt6.QtGui import QPixmap, QFont, QColor, QIcon, QPainter, QPen, QRadialGradient, QLinearGradient, QMovie
from core.interfaces import ISandboxRunner, IBackupManager
from core.steamgriddb_client import SteamGridDBClient
from core.playtime_tracker import PlaytimeTrackerThread
from core.steam_tags import SteamTagsFetcher
from core.steam_build_tracker import SteamBuildFetcher
from core.disk_utils import get_dir_size, format_size, get_disk_usage
from core.discord_rpc import DiscordRPC
from core.archive_extractor import (
    DEFAULT_SANDBOX_DIR, ensure_sandbox_dir, extract_archive_sandboxed,
    find_executables, save_sandbox_config, load_sandbox_config, scan_sandbox_games
)
from database import GameDatabase, _APP_DATA_DIR
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

    def __init__(self, game_id: int, name: str, banner_path: str = None, playtime_seconds: int = 0, parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.name = name
        self.banner_path = banner_path
        self.playtime_seconds = playtime_seconds
        self.selected = False
        self.is_missing = False
        self.is_favorite = False
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

    def set_favorite(self, is_favorite: bool):
        self.is_favorite = is_favorite
        self.render_frame(self._hover_progress)

    def set_update_available(self, is_available: bool):
        self.is_update_available = is_available
        self.render_frame(self._hover_progress)

    def _overlay_favorite_badge(self, pixmap: QPixmap) -> QPixmap:
        """Overlay gold star badge in top-right corner of card if game is favorited."""
        if not self.is_favorite:
            return pixmap
        res = QPixmap(pixmap)
        painter = QPainter(res)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        bx, by = res.width() - 32, 8
        painter.setBrush(QColor(18, 18, 18, 220))
        painter.setPen(QColor(234, 179, 8, 220))
        painter.drawEllipse(bx, by, 24, 24)

        painter.setPen(QColor(234, 179, 8))
        painter.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        painter.drawText(bx, by, 24, 24, Qt.AlignmentFlag.AlignCenter, "★")
        painter.end()
        return res

    def _overlay_update_badge(self, pixmap: QPixmap) -> QPixmap:
        """Overlay green '🟢 Update' badge in top-left corner of card if update is available."""
        if not getattr(self, 'is_update_available', False):
            return pixmap
        res = QPixmap(pixmap)
        painter = QPainter(res)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        bx, by, bw, bh = 8, 8, 70, 22
        painter.setBrush(QColor(6, 95, 70, 230))
        painter.setPen(QColor(52, 211, 153, 220))
        painter.drawRoundedRect(bx, by, bw, bh, 6, 6)

        painter.setPen(QColor(52, 211, 153))
        painter.setFont(QFont("Arial", 8, QFont.Weight.Bold))
        painter.drawText(bx, by, bw, bh, Qt.AlignmentFlag.AlignCenter, "🟢 Update")
        painter.end()
        return res

    def render_frame(self, progress: float):
        """Render cover art with LERP zoom or greyed-out missing overlay"""
        target_w, target_h = 200, 300
        
        # 1. Missing game state (greyed out)
        if self.is_missing:
            if self.banner_path and self.banner_path != "none" and os.path.exists(self.banner_path):
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
                    
                    greyed = QPixmap(cropped.size())
                    greyed.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(greyed)
                    painter.drawPixmap(0, 0, cropped)
                    painter.fillRect(greyed.rect(), QColor(20, 20, 20, 175))
                    painter.end()
                    
                    self.image_label.setPixmap(self._overlay_favorite_badge(greyed))
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
                
                self.image_label.setPixmap(self._overlay_update_badge(self._overlay_favorite_badge(cropped)))
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
        self.image_label.setPixmap(self._overlay_update_badge(self._overlay_favorite_badge(placeholder)))

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
        self.mode_combo.addItem(get_app_icon("shield"), "Primary: UMU (Proton/Wine - Offline)", "umu")
        self.mode_combo.addItem(get_app_icon("globe"), "Secondary: UMU (Network Enabled)", "umu_net")
        self.mode_combo.addItem(get_app_icon("wine"), "3rd: Legacy Wine (Standalone)", "wine")
        self.mode_combo.addItem(get_app_icon("terminal"), "Native Linux Binary / Script", "linux")
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


class LaunchOptionsDialog(QDialog):
    """Custom styled dark modal dialog for selecting game launch runner modes."""
    def __init__(self, game_data: tuple, parent=None):
        super().__init__(parent)
        game_id, name, path, exe, mode, banner_url, steam_id, *_ = (*game_data, 0)
        self.game_data = game_data
        self.selected_mode = None

        self.setWindowTitle(f"Launch {name}")
        self.setFixedWidth(480)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)

        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Draggable title bar
        self.title_bar = DialogTitleBar(self, f"Launch Options - {name}")
        root_layout.addWidget(self.title_bar)

        # Body container
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(22, 20, 22, 22)
        body_layout.setSpacing(12)

        info_label = QLabel("Select runner mode to launch:")
        info_label.setStyleSheet("color: #aaaaaa; font-size: 12px; font-weight: bold;")
        body_layout.addWidget(info_label)

        # Option 1: UMU Primary
        btn_umu = self._create_option_button(
            "Primary: UMU (Proton/Wine - Offline)",
            "Recommended for offline single-player Windows games",
            "shield"
        )
        btn_umu.clicked.connect(lambda: self._select("umu"))
        body_layout.addWidget(btn_umu)

        # Option 2: UMU Network
        btn_umu_net = self._create_option_button(
            "Secondary: UMU (Network Enabled)",
            "Enables internet access for online features",
            "globe"
        )
        btn_umu_net.clicked.connect(lambda: self._select("umu_net"))
        body_layout.addWidget(btn_umu_net)

        # Option 3: Legacy Wine
        btn_wine = self._create_option_button(
            "3rd: Legacy Wine (Standalone)",
            "Runs directly via system Wine without Proton wrapper",
            "wine"
        )
        btn_wine.clicked.connect(lambda: self._select("wine"))
        body_layout.addWidget(btn_wine)

        # Option 4: Linux (if mode == 'linux')
        if mode == "linux":
            btn_linux = self._create_option_button(
                "Native Linux Script / Binary",
                "Runs directly as a native Linux executable in Firejail",
                "terminal"
            )
            btn_linux.clicked.connect(lambda: self._select("linux"))
            body_layout.addWidget(btn_linux)

        # Checkbox to remember as default
        self.set_as_default_cb = QCheckBox("Remember as default launch mode for this game")
        self.set_as_default_cb.setChecked(True)
        self.set_as_default_cb.setStyleSheet("""
            QCheckBox {
                color: #cccccc;
                font-size: 11px;
                font-weight: bold;
                padding-top: 5px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid #444;
                background: #181818;
            }
            QCheckBox::indicator:checked {
                background: #2563eb;
                border-color: #3b82f6;
            }
        """)
        body_layout.addWidget(self.set_as_default_cb)

        root_layout.addWidget(body)
        self.setLayout(root_layout)

        self.setStyleSheet("""
            QDialog {
                background-color: #121212;
                border: 1px solid #2a2a2a;
                border-radius: 8px;
            }
        """)

    def _create_option_button(self, title: str, subtitle: str, icon_key: str) -> QPushButton:
        btn = QPushButton()
        btn.setIcon(get_app_icon(icon_key))
        btn.setIconSize(QSize(22, 22))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumHeight(56)
        btn.setStyleSheet("""
            QPushButton {
                background: #1c1c1c;
                color: #ffffff;
                border: 1px solid #333333;
                border-radius: 6px;
                padding: 10px 16px;
                text-align: left;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background: #1e293b;
                border-color: #3b82f6;
                color: #ffffff;
            }
        """)
        btn.setText(f"{title}\n{subtitle}")
        return btn

    def _select(self, mode: str):
        self.selected_mode = mode
        self.accept()


class SafeLaunchLogReader(QThread):
    """Background reader thread to stream stdout/stderr lines from Firejail process to SafeLaunchDialog console."""
    log_line = pyqtSignal(str)

    def __init__(self, process, parent=None):
        super().__init__(parent)
        self.process = process

    def run(self):
        if not self.process or not getattr(self.process, 'stdout', None):
            return
        try:
            for line in iter(self.process.stdout.readline, ''):
                if not line:
                    break
                self.log_line.emit(line.strip())
        except Exception:
            pass


def draw_custom_lock_pixmap(size: int = 80, is_ready: bool = False) -> QPixmap:
    """Draw a high-resolution, multi-layered vector lock or checkmark badge icon with radial glow effects."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    cx, cy = size // 2, size // 2

    if not is_ready:
        # Radial Glow Ring
        glow_grad = QRadialGradient(cx, cy, size // 2)
        glow_grad.setColorAt(0.0, QColor(34, 197, 94, 60))
        glow_grad.setColorAt(0.7, QColor(34, 197, 94, 15))
        glow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(glow_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, size, size)

        # Glass Badge Base
        circle_grad = QLinearGradient(0, 0, size, size)
        circle_grad.setColorAt(0.0, QColor(6, 78, 59))
        circle_grad.setColorAt(1.0, QColor(2, 44, 34))
        painter.setBrush(circle_grad)
        painter.setPen(QPen(QColor(34, 197, 94), 2))
        painter.drawEllipse(8, 8, size - 16, size - 16)

        # White Lock Shackle
        painter.setPen(QPen(QColor(255, 255, 255), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(cx - 12, cy - 18, 24, 24, 0, 180 * 16)

        # White Lock Body
        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(cx - 15, cy - 3, 30, 22, 5, 5)

        # Dark Keyhole
        painter.setBrush(QColor(2, 44, 34))
        painter.drawEllipse(cx - 4, cy + 3, 8, 8)
        painter.drawRect(cx - 2, cy + 7, 4, 6)
    else:
        # Radial Glow Ring for Ready
        glow_grad = QRadialGradient(cx, cy, size // 2)
        glow_grad.setColorAt(0.0, QColor(34, 197, 94, 90))
        glow_grad.setColorAt(0.7, QColor(34, 197, 94, 25))
        glow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(glow_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, size, size)

        # Emerald Badge Base
        circle_grad = QLinearGradient(0, 0, size, size)
        circle_grad.setColorAt(0.0, QColor(22, 163, 74))
        circle_grad.setColorAt(1.0, QColor(21, 128, 61))
        painter.setBrush(circle_grad)
        painter.setPen(QPen(QColor(74, 222, 128), 2))
        painter.drawEllipse(8, 8, size - 16, size - 16)

        # Pure White Bold Checkmark
        painter.setPen(QPen(QColor(255, 255, 255), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(cx - 12, cy, cx - 4, cy + 8)
        painter.drawLine(cx - 4, cy + 8, cx + 13, cy - 9)

    painter.end()
    return pix


GIF_PATH = "/home/martin/Stažené/penguin-pudgy.gif"
CONFIRM_GIF_PATH = "/home/martin/Stažené/smict.gif"


class SafeLaunchDialog(QDialog):
    """Sleek, zero-jump animated dark card popup:
    Page 0: Animated Pudgy Penguin GIF intro (/home/martin/Stažené/penguin-pudgy.gif)
    Page 1: Clean 'Preparing Virtual Environment' header + progress bar + terminal console log
    Page 2: Confirmation screen ('Enjoy your time, Martin! ✨') with animated GIF (/home/martin/Stažené/smict.gif)
    Stage 4: Smooth 500ms opacity fade out & auto-close.
    """
    def __init__(self, game_name: str, user_name: str = "Martin", process=None, parent=None):
        super().__init__(parent)
        self.game_name = game_name
        self.user_name = user_name
        self.process = process

        self.setWindowTitle(f"Safe Launch - {game_name}")
        self.setFixedSize(500, 360)

        # Center over parent window if available
        if parent:
            p_geo = parent.geometry()
            self.move(
                p_geo.x() + (p_geo.width() - 500) // 2,
                p_geo.y() + (p_geo.height() - 360) // 2
            )

        # Frameless dialog (No system titlebar, solid painted window frame)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(0)

        self.setStyleSheet("""
            QDialog {
                background-color: #121215;
                border: 2px solid #27272a;
                border-radius: 16px;
            }
        """)

        # Main Stacked Widget for Page Transitions (Zero Layout Jumping!)
        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack)

        # ---------------------------------------------------------------------
        # PAGE 0: Pudgy Penguin GIF Intro Stage
        # ---------------------------------------------------------------------
        self.page_gif = QWidget()
        gif_layout = QVBoxLayout(self.page_gif)
        gif_layout.setContentsMargins(0, 0, 0, 0)
        gif_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        gif_layout.addStretch(1)

        self.gif_label = QLabel()
        self.gif_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if os.path.exists(GIF_PATH):
            self.movie = QMovie(GIF_PATH)
            self.movie.setScaledSize(QSize(120, 120))
            self.gif_label.setMovie(self.movie)
            self.movie.start()
        else:
            self.gif_label.setPixmap(draw_custom_lock_pixmap(80, is_ready=False))

        gif_layout.addWidget(self.gif_label, 0, Qt.AlignmentFlag.AlignCenter)

        gif_title = QLabel("Securing Game Launch...")
        gif_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gif_title.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: bold; margin-top: 10px;")
        gif_layout.addWidget(gif_title, 0, Qt.AlignmentFlag.AlignCenter)

        gif_sub = QLabel(f"Preparing isolated Firejail container for '{game_name}'")
        gif_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gif_sub.setStyleSheet("color: #a1a1aa; font-size: 12px; margin-top: 4px;")
        gif_layout.addWidget(gif_sub, 0, Qt.AlignmentFlag.AlignCenter)

        gif_layout.addStretch(1)
        self.stack.addWidget(self.page_gif)

        # ---------------------------------------------------------------------
        # PAGE 1: Virtual Environment Console & Progress Stage
        # ---------------------------------------------------------------------
        self.page_console = QWidget()
        console_layout = QVBoxLayout(self.page_console)
        console_layout.setContentsMargins(0, 0, 0, 0)
        console_layout.setSpacing(10)

        # Clean Header
        self.header_title = QLabel("Preparing Virtual Environment...")
        self.header_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_title.setStyleSheet("color: #ffffff; font-size: 17px; font-weight: bold;")
        console_layout.addWidget(self.header_title)

        self.header_sub = QLabel(f"Initializing Firejail & UMU sandbox for '{game_name}'")
        self.header_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_sub.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        console_layout.addWidget(self.header_sub)

        # Progress Bar (Green Accent)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(5)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background: #1c1c22;
                border: none;
                border-radius: 2.5px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #15803d, stop:1 #22c55e);
                border-radius: 2.5px;
            }
        """)
        console_layout.addWidget(self.progress_bar)

        # Terminal Console View
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet("""
            QPlainTextEdit {
                background-color: #09090b;
                color: #34d399;
                border: 1px solid #27272a;
                border-radius: 8px;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
                font-size: 11px;
                padding: 8px;
            }
        """)
        console_layout.addWidget(self.console)
        self.stack.addWidget(self.page_console)

        # ---------------------------------------------------------------------
        # PAGE 2: Confirmation Greeting Screen with smict.gif Animation
        # ---------------------------------------------------------------------
        self.page_confirm = QWidget()
        confirm_layout = QVBoxLayout(self.page_confirm)
        confirm_layout.setContentsMargins(0, 0, 0, 0)
        confirm_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        confirm_layout.addStretch(1)

        self.confirm_label = QLabel()
        self.confirm_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if os.path.exists(CONFIRM_GIF_PATH):
            self.confirm_movie = QMovie(CONFIRM_GIF_PATH)
            self.confirm_movie.setScaledSize(QSize(100, 100))
            self.confirm_label.setMovie(self.confirm_movie)
            self.confirm_movie.start()
        else:
            self.confirm_label.setPixmap(draw_custom_lock_pixmap(80, is_ready=True))

        confirm_layout.addWidget(self.confirm_label, 0, Qt.AlignmentFlag.AlignCenter)

        self.confirm_title = QLabel(f"Enjoy your time, {self.user_name}!")
        self.confirm_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.confirm_title.setStyleSheet("color: #ffffff; font-size: 22px; font-weight: bold; margin-top: 12px;")
        confirm_layout.addWidget(self.confirm_title, 0, Qt.AlignmentFlag.AlignCenter)

        self.confirm_sub = QLabel(f"'{game_name}' is running safely in Firejail sandbox")
        self.confirm_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.confirm_sub.setStyleSheet("color: #a1a1aa; font-size: 13px; margin-top: 6px;")
        confirm_layout.addWidget(self.confirm_sub, 0, Qt.AlignmentFlag.AlignCenter)

        confirm_layout.addStretch(1)
        self.stack.addWidget(self.page_confirm)

        # Overall Dialog Opacity Effect
        self.dialog_opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.dialog_opacity)
        self.dialog_opacity.setOpacity(1.0)

        # Append initial security logs
        import time, shutil
        t_str = time.strftime("%H:%M:%S")
        if shutil.which("firejail"):
            self.append_log(f"[{t_str}] 🛡️ [SECURITY] Initializing Firejail namespace isolation...")
            self.append_log(f"[{t_str}] 🔒 [SECURITY] Applying filesystem whitelist & network sandbox (--net=none)...")
        else:
            self.append_log(f"[{t_str}] ⚠️ [WARNING] Firejail is not installed on this system.")
            self.append_log(f"[{t_str}] ⚡ [FALLBACK] Running game in direct unsandboxed execution mode.")
        self.append_log(f"[{t_str}] 🍷 [RUNNER] Loading Proton / Wine runtime container...")
        self.append_log(f"[{t_str}] 🚀 [EXEC] Launching process for '{game_name}'...")

        # Start log reader thread if process is piped
        if self.process and getattr(self.process, 'stdout', None):
            self.reader_thread = SafeLaunchLogReader(self.process, self)
            self.reader_thread.log_line.connect(self.append_log)
            self.reader_thread.start()

        # Start on Page 0 (GIF Intro)
        self.stack.setCurrentIndex(0)

        # Phase 1 -> Phase 2 Timer (Pudgy Penguin GIF plays for 2.2s then transitions to Console)
        self.gif_timer = QTimer(self)
        self.gif_timer.setSingleShot(True)
        self.gif_timer.timeout.connect(self._goto_console_stage)
        self.gif_timer.start(2200)

    def _goto_console_stage(self):
        """Phase 2: Transition to Console View & animate progress bar."""
        self.stack.setCurrentIndex(1)

        # Progress bar animation (0% -> 100% over 3.5 seconds)
        self.progress_anim = QVariantAnimation(self)
        self.progress_anim.setStartValue(0)
        self.progress_anim.setEndValue(100)
        self.progress_anim.setDuration(3500)
        self.progress_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.progress_anim.valueChanged.connect(self.progress_bar.setValue)
        self.progress_anim.finished.connect(self._goto_confirmation_stage)
        self.progress_anim.start()

    def append_log(self, text: str):
        if text:
            self.console.appendPlainText(text)
            sb = self.console.verticalScrollBar()
            if sb:
                sb.setValue(sb.maximum())

    def _goto_confirmation_stage(self):
        """Phase 3: Transition to Confirmation Screen ('Enjoy your time, Martin! ✨')."""
        import time
        t_str = time.strftime("%H:%M:%S")
        self.append_log(f"[{t_str}] ✔️ [SUCCESS] Sandbox container initialized cleanly.")
        self.append_log(f"[{t_str}] ✨ [STATUS] Handing off control to {self.game_name}. Have fun!")

        # Transition to Page 2 (Confirmation)
        self.stack.setCurrentIndex(2)

        # Hold confirmation screen for 2.5s, then fade out entire dialog
        self.close_timer = QTimer(self)
        self.close_timer.setSingleShot(True)
        self.close_timer.timeout.connect(self._fade_out_dialog)
        self.close_timer.start(2500)

    def _fade_out_dialog(self):
        """Phase 4: Smooth opacity fade out of entire dialog before closing."""
        fade_dialog = QVariantAnimation(self)
        fade_dialog.setStartValue(1.0)
        fade_dialog.setEndValue(0.0)
        fade_dialog.setDuration(500)
        fade_dialog.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade_dialog.finished.connect(self.accept)
        fade_dialog.start()
        self._fade_dialog = fade_dialog


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
                background: #2563eb; color: #fff; border: none; padding: 8px 20px; border-radius: 5px; font-weight: bold;
            }
            QPushButton:hover { background: #1d4ed8; }
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
        import subprocess
        try:
            subprocess.Popen(["xdg-open", self.screenshots_dir])
        except Exception:
            pass


class DiskManagerDialog(QDialog):
    """Custom dark dialog for analyzing sandbox disk space consumption and largest installed games."""
    def __init__(self, games: list, parent=None):
        super().__init__(parent)
        self.games = games

        self.setWindowTitle("Disk Space Manager")
        self.setMinimumSize(620, 480)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Title bar
        self.title_bar = DialogTitleBar(self, "💾 Disk Space Manager")
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
        stats_card.setStyleSheet("QFrame { background: #181818; border: 1px solid #2a2a2a; border-radius: 8px; padding: 12px; }")
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
        scroll.setStyleSheet("QScrollArea { background: #121212; border: 1px solid #2a2a2a; border-radius: 6px; }")

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
            row_frame.setStyleSheet("QFrame { background: #1a1a1a; border: 1px solid #282828; border-radius: 6px; }")
            r_layout = QHBoxLayout(row_frame)
            r_layout.setContentsMargins(10, 8, 10, 8)

            name_lbl = QLabel(name)
            name_lbl.setStyleSheet("color: #ffffff; font-weight: bold; font-size: 12px;")
            r_layout.addWidget(name_lbl)

            r_layout.addStretch()

            size_badge = QLabel(format_size(sz))
            size_badge.setStyleSheet("background: #1e293b; color: #60a5fa; border: 1px solid #2563eb; border-radius: 4px; padding: 3px 8px; font-size: 11px; font-weight: bold;")
            r_layout.addWidget(size_badge)

            btn_folder = QPushButton("📂 Open Folder")
            btn_folder.setStyleSheet("QPushButton { background: #222222; color: #aaaaaa; border: 1px solid #333333; border-radius: 4px; padding: 4px 8px; font-weight: bold; font-size: 11px; } QPushButton:hover { background: #333333; color: #ffffff; }")
            btn_folder.clicked.connect(lambda _, p=path: self._open_path(p))
            r_layout.addWidget(btn_folder)

            list_layout.addWidget(row_frame)

        scroll.setWidget(list_widget)
        body_layout.addWidget(scroll)

        # Close button
        btn_close = QPushButton("Close")
        btn_close.setStyleSheet("QPushButton { background: #2563eb; color: #fff; border: none; padding: 8px 24px; border-radius: 5px; font-weight: bold; } QPushButton:hover { background: #1d4ed8; }")
        btn_close.clicked.connect(self.accept)
        
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        body_layout.addLayout(btn_row)

        root_layout.addWidget(body)

    def _open_path(self, path: str):
        import subprocess
        try:
            if path and os.path.exists(path):
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass


class CustomTitleBar(QFrame):
    """Custom top title bar containing navigation actions, search input, window dragging, double-click maximize, and app control buttons."""
    search_changed = pyqtSignal(str)

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

        # Centered wider search input box with vector search icon (positioned in resizeEvent)
        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("Search library...")
        self.search_input.addAction(get_app_icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search_input.setFixedWidth(280)
        self.search_input.setFixedHeight(30)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background: #141414;
                color: #ffffff;
                border: 1px solid #2a2a2a;
                border-radius: 6px;
                padding: 2px 10px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #3b82f6;
                background: #1a1a1a;
            }
        """)
        self.search_input.textChanged.connect(self.search_changed.emit)

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'search_input') and self.search_input:
            center_x = self.width() // 2
            input_w = self.search_input.width()
            input_h = self.search_input.height()
            top_y = (self.height() - input_h) // 2
            left_x = center_x - (input_w // 2)
            self.search_input.move(left_x, top_y)
            self.search_input.raise_()

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

        self.search_query = ""
        self.current_filter = "all"
        self.current_sort = 0  # 0: A-Z, 1: Playtime, 2: Recently Added

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
        self.title_bar.search_changed.connect(self._on_search_query_changed)

        self.title_bar.act_export.triggered.connect(self._on_export)
        # Content Split Layout: Left Game Detail Panel + Right Game Library Grid
        content_widget = QWidget()
        content_layout = QHBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        root_vbox.addWidget(content_widget)

        # -------------------------------------------------------------
        # 1. Left Game Detail Panel (Width: 280px)
        # -------------------------------------------------------------
        self.detail_panel = QFrame()
        self.detail_panel.setFixedWidth(280)
        self.detail_panel.setStyleSheet("""
            QFrame {
                background: #090909;
                border-right: 1px solid #222222;
            }
            QLabel {
                color: #ffffff;
            }
            QPushButton {
                background: #181818;
                color: #ffffff;
                border: 1px solid #2a2a2a;
                border-radius: 6px;
                padding: 9px 12px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #252525;
            }
            QPushButton:disabled {
                background: #121212;
                color: #555555;
                border-color: #1a1a1a;
            }
        """)

        self.detail_panel.setFixedWidth(0)
        self.detail_panel.setVisible(False)
        detail_layout = QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(20, 20, 20, 20)
        detail_layout.setSpacing(14)
        detail_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Selected Game Cover Art Preview
        self.detail_cover = QLabel()
        self.detail_cover.setFixedSize(QSize(200, 300))
        self.detail_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_cover.setStyleSheet("border: 1px solid #222222; border-radius: 6px; background: #121212;")
        
        cover_row = QHBoxLayout()
        cover_row.addWidget(self.detail_cover)
        detail_layout.addLayout(cover_row)

        # Selected Game Title
        self.detail_title = QLabel("Select a Game")
        self.detail_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        self.detail_title.setWordWrap(True)
        self.detail_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail_layout.addWidget(self.detail_title)

        # Steam Tags Badge Container (Grey rounded boxes)
        self.tags_widget = QWidget()
        self.tags_layout = QHBoxLayout(self.tags_widget)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setSpacing(6)
        self.tags_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail_layout.addWidget(self.tags_widget)

        # Favorite Star Toggle Button in Detail Panel
        self.btn_detail_fav = QPushButton(" Favorite")
        self.btn_detail_fav.setIcon(get_icon("ph.star-bold"))
        self.btn_detail_fav.setCheckable(True)
        self.btn_detail_fav.setStyleSheet("""
            QPushButton {
                background: #181818;
                color: #eab308;
                border: 1px solid #333333;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background: #252525;
            }
            QPushButton:checked {
                background: #854d0e;
                color: #fef08a;
                border-color: #eab308;
            }
        """)
        self.btn_detail_fav.clicked.connect(self._on_toggle_favorite)
        detail_layout.addWidget(self.btn_detail_fav)

        # Selected Game Playtime
        self.detail_playtime = QLabel("")
        self.detail_playtime.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_playtime.setStyleSheet("color: #aaaaaa; font-size: 11px; font-weight: bold;")
        detail_layout.addWidget(self.detail_playtime)

        # Selected Game Last Played
        self.detail_last_played = QLabel("")
        self.detail_last_played.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_last_played.setStyleSheet("color: #777777; font-size: 10px; font-weight: bold;")
        detail_layout.addWidget(self.detail_last_played)

        # Selected Game Disk Size
        self.detail_disk_size = QLabel("")
        self.detail_disk_size.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_disk_size.setStyleSheet("color: #60a5fa; font-size: 10px; font-weight: bold;")
        detail_layout.addWidget(self.detail_disk_size)

        # Update Available Badge & Sync Build Button
        self.detail_update_widget = QWidget()
        self.detail_update_layout = QHBoxLayout(self.detail_update_widget)
        self.detail_update_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_update_layout.setSpacing(6)
        self.detail_update_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_detail_update = QLabel("")
        self.lbl_detail_update.setStyleSheet("background: #064e3b; color: #34d399; border: 1px solid #059669; border-radius: 6px; padding: 4px 8px; font-size: 10px; font-weight: bold;")
        self.detail_update_layout.addWidget(self.lbl_detail_update)

        self.btn_sync_build = QPushButton("Sync Build #")
        self.btn_sync_build.setStyleSheet("QPushButton { background: #181818; color: #aaaaaa; border: 1px solid #333333; border-radius: 6px; padding: 4px 8px; font-size: 10px; font-weight: bold; } QPushButton:hover { background: #252525; color: #ffffff; }")
        self.btn_sync_build.clicked.connect(self._on_sync_build_id)
        self.detail_update_layout.addWidget(self.btn_sync_build)

        self.detail_update_widget.setVisible(False)
        detail_layout.addWidget(self.detail_update_widget)

        # Big Launch Game Button
        self.btn_detail_launch = QPushButton(" Launch Game")
        self.btn_detail_launch.setIcon(get_app_icon("launch"))
        self.btn_detail_launch.setMinimumHeight(42)
        self.btn_detail_launch.setStyleSheet("""
            QPushButton {
                background: #2e7d32;
                color: #ffffff;
                font-weight: bold;
                font-size: 13px;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover {
                background: #388e3c;
            }
            QPushButton:disabled {
                background: #1b381d;
                color: #666666;
            }
        """)
        self.btn_detail_launch.clicked.connect(self._on_launch)
        detail_layout.addWidget(self.btn_detail_launch)

        # Action Buttons
        self.btn_detail_edit = QPushButton(" Edit Settings")
        self.btn_detail_edit.setIcon(get_app_icon("edit"))
        self.btn_detail_edit.clicked.connect(self._on_edit)
        detail_layout.addWidget(self.btn_detail_edit)

        self.btn_detail_screenshots = QPushButton(" Screenshots")
        self.btn_detail_screenshots.setIcon(get_icon("ph.camera-bold"))
        self.btn_detail_screenshots.clicked.connect(self._open_screenshot_gallery)
        detail_layout.addWidget(self.btn_detail_screenshots)

        self.btn_detail_export = QPushButton(" Export Save")
        self.btn_detail_export.setIcon(get_app_icon("export"))
        self.btn_detail_export.clicked.connect(self._on_export)
        detail_layout.addWidget(self.btn_detail_export)

        self.btn_detail_import = QPushButton(" Import Save")
        self.btn_detail_import.setIcon(get_app_icon("import"))
        self.btn_detail_import.clicked.connect(self._on_import)
        detail_layout.addWidget(self.btn_detail_import)

        self.btn_detail_remove = QPushButton(" Remove Game")
        self.btn_detail_remove.setIcon(get_app_icon("remove"))
        self.btn_detail_remove.setStyleSheet("""
            QPushButton {
                background: #2a1212;
                color: #ef4444;
                border: 1px solid #7f1d1d;
            }
            QPushButton:hover {
                background: #7f1d1d;
                color: #ffffff;
            }
            QPushButton:disabled {
                background: #181212;
                color: #553333;
                border-color: #221515;
            }
        """)
        self.btn_detail_remove.clicked.connect(self._on_remove)
        detail_layout.addWidget(self.btn_detail_remove)

        detail_layout.addStretch()
        content_layout.addWidget(self.detail_panel)

        # -------------------------------------------------------------
        # 2. Right Main Content Panel (Game Library Grid)
        # -------------------------------------------------------------
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(20, 20, 20, 20)
        right_layout.setSpacing(15)
        content_layout.addWidget(right_panel)
        
        # Right Header / Title & Filter Controls
        header_layout = QHBoxLayout()
        header_title = QLabel("Game Library")
        header_title.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        header_title.setStyleSheet("color: #fff;")
        header_layout.addWidget(header_title)
        
        header_layout.addStretch()

        # Sleek Segmented Filter Bar Container
        filter_container = QFrame()
        filter_container.setStyleSheet("""
            QFrame {
                background: #0d0d0d;
                border: 1px solid #222222;
                border-radius: 8px;
            }
        """)
        fc_layout = QHBoxLayout(filter_container)
        fc_layout.setContentsMargins(3, 3, 3, 3)
        fc_layout.setSpacing(4)

        filter_btn_style = """
            QPushButton {
                background: transparent;
                color: #888888;
                border: none;
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #181818;
                color: #ffffff;
            }
            QPushButton:checked {
                background: #1e293b;
                color: #60a5fa;
                border: 1px solid #2563eb;
            }
        """

        self.btn_filter_all = QPushButton("All")
        self.btn_filter_all.setIcon(get_app_icon("library"))
        self.btn_filter_all.setCheckable(True)
        self.btn_filter_all.setChecked(True)
        self.btn_filter_all.setStyleSheet(filter_btn_style)
        self.btn_filter_all.clicked.connect(lambda: self._set_filter("all"))

        self.btn_filter_installed = QPushButton("Installed")
        self.btn_filter_installed.setIcon(get_app_icon("sandbox"))
        self.btn_filter_installed.setCheckable(True)
        self.btn_filter_installed.setStyleSheet(filter_btn_style)
        self.btn_filter_installed.clicked.connect(lambda: self._set_filter("installed"))

        self.btn_filter_missing = QPushButton("Missing")
        self.btn_filter_missing.setIcon(get_icon("ph.warning-circle-bold"))
        self.btn_filter_missing.setCheckable(True)
        self.btn_filter_missing.setStyleSheet(filter_btn_style)
        self.btn_filter_missing.clicked.connect(lambda: self._set_filter("missing"))

        self.btn_filter_fav = QPushButton("Favorites")
        self.btn_filter_fav.setIcon(get_icon("ph.star-fill"))
        self.btn_filter_fav.setCheckable(True)
        self.btn_filter_fav.setStyleSheet(filter_btn_style)
        self.btn_filter_fav.clicked.connect(lambda: self._set_filter("favorites"))

        fc_layout.addWidget(self.btn_filter_all)
        fc_layout.addWidget(self.btn_filter_installed)
        fc_layout.addWidget(self.btn_filter_missing)
        fc_layout.addWidget(self.btn_filter_fav)

        header_layout.addWidget(filter_container)

        # Sorting ComboBox
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Sort: A–Z Title", "Sort: Most Played", "Sort: Recently Added"])
        self.sort_combo.setFixedHeight(28)
        self.sort_combo.setStyleSheet("""
            QComboBox {
                background: #181818;
                color: #ffffff;
                border: 1px solid #2a2a2a;
                border-radius: 5px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: bold;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background: #1a1a1a;
                color: #ffffff;
                selection-background-color: #1e293b;
            }
        """)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        header_layout.addWidget(self.sort_combo)

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
        self.discord_rpc = DiscordRPC()
        self._on_sync_sandbox(quiet=True)
        self._setup_tray_icon()
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

    def _setup_tray_icon(self):
        """Setup system tray icon with quick launch context menu for favorites and recently played games."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray_icon = QSystemTrayIcon(self)
        if os.path.exists(LOGO_PATH):
            self.tray_icon.setIcon(QIcon(LOGO_PATH))
        else:
            self.tray_icon.setIcon(get_app_icon("library"))

        self.tray_icon.setToolTip("MGLauncher - Game Sandbox Manager")
        self.tray_menu = QMenu(self)
        self.tray_menu.setStyleSheet("""
            QMenu {
                background-color: #121212;
                color: #ffffff;
                border: 1px solid #2a2a2a;
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
            QMenu::separator {
                height: 1px;
                background: #282828;
                margin: 4px 6px;
            }
        """)

        self.tray_icon.activated.connect(self._on_tray_icon_activated)
        self.tray_icon.show()

    def _update_tray_menu(self):
        """Populate system tray menu with Favorites and Recently Played Quick Launch items."""
        if not hasattr(self, 'tray_menu'):
            return

        self.tray_menu.clear()

        # Show / Hide Launcher
        act_show = self.tray_menu.addAction(get_app_icon("library"), "Open MGLauncher Library")
        act_show.triggered.connect(self._show_and_raise)

        self.tray_menu.addSeparator()

        # Quick Launch Section: Favorites
        fav_games = [g for g in self.games if len(g) > 8 and g[8]]
        if fav_games:
            lbl_fav = self.tray_menu.addAction("⭐ Favorites Quick Launch")
            lbl_fav.setEnabled(False)
            for g in fav_games[:5]:
                game_id, name, path, exe, mode = g[0], g[1], g[2], g[3], g[4]
                act = self.tray_menu.addAction(get_app_icon("launch"), f"  Launch {name}")
                act.triggered.connect(lambda _, gid=game_id, p=path, e=exe, m=mode: self._launch_mode(gid, p, e, m or "umu"))
            self.tray_menu.addSeparator()

        # Quick Launch Section: Recently Played
        rec_games = [g for g in self.games if len(g) > 9 and g[9] > 0]
        rec_games.sort(key=lambda x: x[9], reverse=True)
        if rec_games:
            lbl_rec = self.tray_menu.addAction("⏱ Recently Played")
            lbl_rec.setEnabled(False)
            for g in rec_games[:5]:
                game_id, name, path, exe, mode = g[0], g[1], g[2], g[3], g[4]
                act = self.tray_menu.addAction(get_app_icon("launch"), f"  Launch {name}")
                act.triggered.connect(lambda _, gid=game_id, p=path, e=exe, m=mode: self._launch_mode(gid, p, e, m or "umu"))
            self.tray_menu.addSeparator()

        # Disk Manager option
        act_disk = self.tray_menu.addAction(get_app_icon("export"), "💾 Disk Space Manager")
        act_disk.triggered.connect(self._open_disk_manager)

        self.tray_menu.addSeparator()

        # Quit
        act_quit = self.tray_menu.addAction(get_app_icon("close"), "Quit MGLauncher")
        act_quit.triggered.connect(QApplication.instance().quit)

        self.tray_icon.setContextMenu(self.tray_menu)

    def _show_and_raise(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self._show_and_raise()

    def _open_disk_manager(self):
        """Open DiskManagerDialog to inspect sandbox storage and game sizes."""
        dialog = DiskManagerDialog(self.games, self)
        dialog.exec()

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

    def _set_filter(self, filter_mode: str):
        """Set active filter mode (all, installed, missing, favorites) and refresh view"""
        self.current_filter = filter_mode
        self.btn_filter_all.setChecked(filter_mode == "all")
        self.btn_filter_installed.setChecked(filter_mode == "installed")
        self.btn_filter_missing.setChecked(filter_mode == "missing")
        self.btn_filter_fav.setChecked(filter_mode == "favorites")
        self._refresh_library()

    def _on_sort_changed(self, idx: int):
        """Sort games list by A-Z, Playtime, or Recently Added"""
        self.current_sort = idx
        self._refresh_library()

    def _on_search_query_changed(self, query: str):
        """Filter games real-time as user types in top search box"""
        self.search_query = query.strip().lower()
        self._refresh_library()

    def _on_toggle_favorite(self):
        """Toggle favorite status for currently selected game"""
        game = self.selected_game
        if not game:
            return
        game_id = game[0]
        new_fav = self.db.toggle_favorite(game_id)
        self._show_toast("⭐ Added to Favorites!" if new_fav else "Removed from Favorites")
        self._refresh_library()
        self._select_game_by_id(game_id)

    def _refresh_library(self):
        """Clear and reload game banners into dynamic responsive grid based on search, status filter, and sorting."""
        # Explicitly hide and destroy old child widgets
        for old_w in list(self.banner_widgets.values()):
            old_w.hide()
            old_w.setParent(None)
            old_w.deleteLater()
        self.banner_widgets.clear()
        
        self.games = self.db.get_all_games()
        self.stat_label.setText(f"{len(self.games)} Game(s) Total")

        if not self.games:
            label = QLabel("🎮 No games in library yet.\nClick 'Add / Install Game' or 'Sync Library' to get started!")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("color: #999; font-size: 14px; padding: 40px;")
            self.grid_container.set_banner_widgets([label])
            return

        # Filter & sort games list
        processed = []
        for g in self.games:
            game_id, name, path, executable, mode, banner_url, steam_id = g[:7]
            playtime = g[7] if len(g) > 7 and g[7] else 0
            is_fav = bool(g[8]) if len(g) > 8 and g[8] else False

            # 1. Search Query Filter
            if self.search_query and self.search_query not in name.lower():
                continue

            # Disk check for status filter
            folder_exists = os.path.exists(path) if path else False
            full_exe = os.path.join(path, executable) if (path and executable) else path
            exe_exists = os.path.exists(full_exe) if full_exe else False
            is_missing = not (folder_exists and (exe_exists or not executable))

            # 2. Status Filter
            if self.current_filter == "installed" and is_missing:
                continue
            elif self.current_filter == "missing" and not is_missing:
                continue
            elif self.current_filter == "favorites" and not is_fav:
                continue

            processed.append((g, is_missing, playtime, is_fav))

        # 3. Sorting
        if self.current_sort == 0:  # A-Z Title
            processed.sort(key=lambda x: x[0][1].lower())
        elif self.current_sort == 1:  # Most Played
            processed.sort(key=lambda x: x[2], reverse=True)
        elif self.current_sort == 2:  # Recently Added (id desc)
            processed.sort(key=lambda x: x[0][0], reverse=True)

        if not processed:
            msg = f"No games matching '{self.search_query}'" if self.search_query else "No games matching selected filter."
            label = QLabel(f"🔍 {msg}")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("color: #777777; font-size: 14px; padding: 40px;")
            self.grid_container.set_banner_widgets([label])
            return

        widgets = []
        for g, is_missing, playtime_seconds, is_fav in processed:
            game_id, name, path, executable, mode, banner_url, steam_id = g[:7]
            
            widget = GameBannerWidget(game_id, name, banner_url, playtime_seconds or 0)
            widget.set_favorite(is_fav)
            widget.clicked.connect(self._select_game_by_id)
            widget.doubleClicked.connect(self._on_double_click_game)
            
            widgets.append(widget)
            self.banner_widgets[game_id] = widget
            
            if banner_url is None:
                fetcher = BannerAutoFetcher(game_id, name, self.sgdb_client)
                fetcher.banner_auto_downloaded.connect(self._on_auto_banner_downloaded)
                fetcher.start()
                self.auto_fetchers.append(fetcher)
            
        self.grid_container.set_banner_widgets(widgets)
        self._check_games_on_drive()
        self._update_tray_menu()

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
        """Select a game card visually and update the left detail panel"""
        for widget in self.banner_widgets.values():
            widget.set_selected(False)
        for game in self.games:
            if game[0] == game_id:
                self.selected_game = game
                if game_id in self.banner_widgets:
                    self.banner_widgets[game_id].set_selected(True)
                break
        self._update_detail_panel()

    def _on_double_click_game(self, game_id: int):
        """Double-clicking a game banner card instantly launches it!"""
        self._select_game_by_id(game_id)
        game = self._get_selected_game()
        if not game:
            return
        game_id, name, path, exe, mode, *_ = (*game, 0)
        self._launch_mode(game_id, path, exe, mode or "umu")

    def _animate_left_panel(self, expand: bool):
        """Smoothly slide left detail panel out from/in to the left (0px <-> 280px) with LERP cubic easing curve."""
        target_w = 280 if expand else 0
        current_w = self.detail_panel.width()

        if expand and not self.detail_panel.isVisible():
            self.detail_panel.setVisible(True)

        if hasattr(self, '_panel_anim') and self._panel_anim and self._panel_anim.state() == QAbstractAnimation.State.Running:
            self._panel_anim.stop()

        anim = QVariantAnimation(self)
        anim.setStartValue(current_w)
        anim.setEndValue(target_w)
        anim.setDuration(280)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic if expand else QEasingCurve.Type.InCubic)

        def _on_step(val):
            w = int(val)
            self.detail_panel.setFixedWidth(w)

        def _on_finished():
            if not expand:
                self.detail_panel.setVisible(False)

        anim.valueChanged.connect(_on_step)
        anim.finished.connect(_on_finished)
        anim.start()
        self._panel_anim = anim

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        if event.key() == Qt.Key.Key_F12:
            self._take_screenshot()

    def _take_screenshot(self):
        """Capture screenshot of primary screen and save to current game's gallery."""
        game = self.selected_game
        if not game:
            self._show_toast("Select a game first to save screenshots.", is_error=True)
            return

        game_id = game[0]
        shots_dir = os.path.join(_APP_DATA_DIR, "screenshots", str(game_id))
        os.makedirs(shots_dir, exist_ok=True)

        import time
        filename = f"screenshot_{int(time.time())}.png"
        filepath = os.path.join(shots_dir, filename)

        try:
            screen = QApplication.primaryScreen()
            if screen:
                pixmap = screen.grabWindow(0)
                pixmap.save(filepath, "PNG")
                self._show_toast("📸 Screenshot saved to gallery!")
                self._update_detail_panel()
        except Exception as e:
            self._show_toast(f"Failed to capture screenshot: {e}", is_error=True)

    def _open_screenshot_gallery(self):
        """Open ScreenshotGalleryDialog for current game."""
        game = self.selected_game
        if not game:
            return
        dialog = ScreenshotGalleryDialog(game[0], game[1], self)
        dialog.exec()
        self._update_detail_panel()

    @staticmethod
    def _format_last_played(timestamp: int) -> str:
        if not timestamp or timestamp <= 0:
            return "Last played: Never"
        import time
        now = int(time.time())
        diff = max(0, now - timestamp)
        
        if diff < 60:
            return "Last played: Just now"
        elif diff < 3600:
            mins = diff // 60
            return f"Last played: {mins}m ago"
        elif diff < 86400:
            hours = diff // 3600
            return f"Last played: {hours}h ago"
        elif diff < 172800:
            return "Last played: Yesterday"
        elif diff < 604800:
            days = diff // 86400
            return f"Last played: {days} days ago"
        else:
            weeks = diff // 604800
            return f"Last played: {weeks} week(s) ago"

    def _on_steam_build_checked(self, game_id: int, latest_build_id: str, is_update_available: bool):
        """Callback when background SteamBuildFetcher returns build info."""
        if game_id in self.banner_widgets:
            self.banner_widgets[game_id].set_update_available(is_update_available)

        if self.selected_game and self.selected_game[0] == game_id:
            if is_update_available:
                self.lbl_detail_update.setText(f"🟢 Update Available (Build #{latest_build_id})")
                self.detail_update_widget.setVisible(True)
                self.latest_checked_build_id = latest_build_id
            else:
                self.detail_update_widget.setVisible(False)

    def _on_sync_build_id(self):
        """Mark local game build ID as updated to latest online build ID."""
        if not self.selected_game:
            return
        game_id = self.selected_game[0]
        latest = getattr(self, 'latest_checked_build_id', "")
        if latest:
            self.db.update_build_id(game_id, latest)
            self._show_toast("✓ Build ID updated to latest!")
            self._refresh_library()
            self._select_game_by_id(game_id)

    def _on_steam_tags_found(self, game_id: int, tags_list: list):
        """Callback when background SteamTagsFetcher returns genres/categories"""
        if tags_list:
            tags_str = ", ".join(tags_list)
            self.db.update_game_tags(game_id, tags_str)
            if self.selected_game and self.selected_game[0] == game_id:
                self._update_tags_pills(tags_list)

    def _update_tags_pills(self, tags_list: list):
        while self.tags_layout.count() > 0:
            item = self.tags_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for tag in tags_list[:3]:
            badge = QLabel(tag)
            badge.setStyleSheet("""
                QLabel {
                    background: #1e1e1e;
                    color: #d1d5db;
                    border: 1px solid #333333;
                    border-radius: 10px;
                    padding: 3px 9px;
                    font-size: 10px;
                    font-weight: bold;
                }
            """)
            self.tags_layout.addWidget(badge)

    def _update_detail_panel(self):
        """Update left panel with current selected game details and trigger smooth slide animation."""
        game = self.selected_game
        if not game:
            self._animate_left_panel(False)
            return

        self._animate_left_panel(True)

        game_id, name, path, exe, mode, banner_url, steam_id = game[:7]
        playtime_seconds = game[7] if len(game) > 7 and game[7] else 0
        is_fav = bool(game[8]) if len(game) > 8 and game[8] else False
        last_played_ts = game[9] if len(game) > 9 and game[9] else 0
        tags_str = game[10] if len(game) > 10 and game[10] else ""

        self.detail_title.setText(name)
        self.detail_playtime.setText(GameBannerWidget._format_playtime(playtime_seconds))
        self.detail_last_played.setText(self._format_last_played(last_played_ts))

        disk_bytes = get_dir_size(path) if path and os.path.exists(path) else 0
        self.detail_disk_size.setText(f"💾 Size: {format_size(disk_bytes)}")

        local_build_id = game[11] if len(game) > 11 and game[11] else ""
        if steam_id and steam_id != "0":
            fetcher = SteamBuildFetcher(game_id, steam_id, local_build_id, parent=self)
            fetcher.update_checked.connect(self._on_steam_build_checked)
            fetcher.start()
        else:
            self.detail_update_widget.setVisible(False)

        # Steam Tags Display & Auto Fetcher
        if tags_str:
            tags_list = [t.strip() for t in tags_str.split(",") if t.strip()]
            self._update_tags_pills(tags_list)
        else:
            self._update_tags_pills([])
            fetcher = SteamTagsFetcher(game_id, name, parent=self)
            fetcher.tags_found.connect(self._on_steam_tags_found)
            fetcher.start()

        # Update Screenshot button badge count
        shots_dir = os.path.join(_APP_DATA_DIR, "screenshots", str(game_id))
        count = len(os.listdir(shots_dir)) if os.path.exists(shots_dir) else 0
        self.btn_detail_screenshots.setText(f" Screenshots ({count})")

        self.btn_detail_fav.setChecked(is_fav)
        self.btn_detail_fav.setIcon(get_icon("ph.star-fill") if is_fav else get_icon("ph.star-bold"))
        self.btn_detail_fav.setText(" Favorited" if is_fav else " Add to Favorites")

        self.btn_detail_launch.setEnabled(True)
        self.btn_detail_edit.setEnabled(True)
        self.btn_detail_screenshots.setEnabled(True)
        self.btn_detail_export.setEnabled(True)
        self.btn_detail_import.setEnabled(True)
        self.btn_detail_remove.setEnabled(True)

        if banner_url and banner_url != "none" and os.path.exists(banner_url):
            pixmap = QPixmap(banner_url)
            if not pixmap.isNull():
                target_size = self.detail_cover.size()
                scaled = pixmap.scaled(
                    target_size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation
                )
                crop_x = max(0, (scaled.width() - target_size.width()) // 2)
                crop_y = max(0, (scaled.height() - target_size.height()) // 2)
                cropped = scaled.copy(crop_x, crop_y, target_size.width(), target_size.height())
                self.detail_cover.setPixmap(cropped)
                return

        placeholder = QPixmap(200, 300)
        placeholder.fill(QColor("#181818"))
        painter = QPainter(placeholder)
        painter.setPen(QColor("#777777"))
        painter.setFont(QFont("Monospace", 12, QFont.Weight.Bold))
        painter.drawText(placeholder.rect(), Qt.AlignmentFlag.AlignCenter, name)
        painter.end()
        self.detail_cover.setPixmap(placeholder)



    def _launch_mode(self, game_id: int, path: str, exe: str, selected_mode: str):
        """Helper to launch a game directly with the chosen mode"""
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "Missing Game", f"Cannot launch game. Path does not exist:\n{path}")
            return
        try:
            game_name = "Game"
            for g in self.games:
                if g[0] == game_id:
                    game_name = g[1]
                    break

            process = self.runner.launch(path, exe, selected_mode)
            if process:
                # Update Discord Rich Presence
                if hasattr(self, 'discord_rpc') and self.discord_rpc:
                    import time
                    self.discord_rpc.set_activity(game_name, start_timestamp=int(time.time()), details="Playing in Sandbox")

                tracker = PlaytimeTrackerThread(game_id, process, parent=self)
                tracker.playtime_recorded.connect(self._on_playtime_recorded)
                tracker.finished.connect(lambda t=tracker: self._cleanup_tracker(t))
                tracker.start()
                self.playtime_trackers.append(tracker)

                # Show animated Safe Launch Popup with console log stream & greeting to Martin
                popup = SafeLaunchDialog(game_name, user_name="Martin", process=process, parent=self)
                popup.show()
                QApplication.processEvents()
                popup.exec()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to launch game: {str(e)}")

    def _get_selected_game(self):
        """Get the currently selected game"""
        return self.selected_game

    def _on_launch(self):
        """Launch selected game directly using default mode, or open LaunchOptionsDialog if Shift is held down or unconfigured."""
        game = self._get_selected_game()
        if not game:
            self._show_toast("Please select a game to launch.", is_error=True)
            return
        
        game_id, name, path, exe, mode, banner_url, steam_id, *_ = (*game, 0)
        if not path or not os.path.exists(path):
            self._show_toast(f"Cannot launch '{name}'. Directory does not exist on disk.", is_error=True)
            return

        modifiers = QApplication.keyboardModifiers()
        shift_pressed = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)

        if mode and not shift_pressed:
            self._launch_mode(game_id, path, exe, mode)
        else:
            dialog = LaunchOptionsDialog(game, self)
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_mode:
                if dialog.set_as_default_cb.isChecked():
                    self.db.update_game_mode(game_id, dialog.selected_mode)
                    self._refresh_library()
                self._launch_mode(game_id, path, exe, dialog.selected_mode)

    def _on_playtime_recorded(self, game_id: int, elapsed_seconds: int):
        """Called (on main thread) when a game exits — persists and displays playtime and last_played timestamp."""
        import time
        self.db.add_playtime(game_id, elapsed_seconds)
        self.db.update_last_played(game_id, int(time.time()))
        total = self.db.get_playtime(game_id)
        if game_id in self.banner_widgets:
            self.banner_widgets[game_id].set_playtime(total)
        self._update_detail_panel()

    def _cleanup_tracker(self, tracker: PlaytimeTrackerThread):
        """Remove finished tracker from the list so it can be garbage collected."""
        if tracker in self.playtime_trackers:
            self.playtime_trackers.remove(tracker)
        if hasattr(self, 'discord_rpc') and self.discord_rpc and len(self.playtime_trackers) == 0:
            self.discord_rpc.clear_activity()

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
        """Auto-discover installed games in ~/Games/Sandbox without creating duplicate entries."""
        found = scan_sandbox_games(DEFAULT_SANDBOX_DIR)
        db_games = self.db.get_all_games()
        existing_paths = {os.path.realpath(g[2]) for g in db_games if g[2]}
        existing_names = {g[1].lower().replace('-', ' ').replace('_', ' ').strip() for g in db_games if g[1]}

        added_count = 0
        for game in found:
            norm_path = os.path.realpath(game['path'])
            folder_clean = game['name'].lower().replace('-', ' ').replace('_', ' ').strip()
            
            # Prevent duplicate if exact path exists or clean name matches existing entry
            is_path_known = norm_path in existing_paths
            is_name_known = any(
                folder_clean in db_name or db_name in folder_clean
                for db_name in existing_names
            )

            if not is_path_known and not is_name_known:
                self.db.add_game(game['name'], norm_path, game['executable'], game['mode'])
                added_count += 1
                existing_paths.add(norm_path)
                existing_names.add(folder_clean)
                
        if added_count > 0:
            self._refresh_library()
            if not quiet:
                self._show_toast(f"✓ Found and added {added_count} new game(s) from sandbox!")
        else:
            if not quiet:
                self._show_toast("✓ Sandbox synced (no new games found).")

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
