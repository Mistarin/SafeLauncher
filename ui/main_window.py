import os
import shutil
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QGridLayout, QFileDialog, QMessageBox, QDialog, QLabel, QLineEdit,
    QComboBox, QFormLayout, QScrollArea, QFrame, QListWidget, QListWidgetItem, QMenu
)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QVariantAnimation, QEasingCurve, QTimer
from PyQt6.QtGui import QPixmap, QFont, QColor, QIcon, QPainter
from core.interfaces import ISandboxRunner, IBackupManager
from core.steamgriddb_client import SteamGridDBClient
from core.archive_extractor import (
    DEFAULT_SANDBOX_DIR, ensure_sandbox_dir, extract_archive_sandboxed,
    find_executables, save_sandbox_config, load_sandbox_config, scan_sandbox_games
)
from database import GameDatabase

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

    def __init__(self, game_id: int, name: str, banner_path: str = None):
        super().__init__()
        self.game_id = game_id
        self.name = name
        self.banner_path = banner_path
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
        self.setFixedSize(QSize(200, 340))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        
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
        
        self.update_appearance()

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
        if self.banner_path and os.path.exists(self.banner_path):
            pixmap = QPixmap(self.banner_path)
            if not pixmap.isNull():
                # Smooth LERP scale: 1.0x -> 1.08x
                scale_factor = 1.0 + (0.08 * progress)
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
                
                # Smooth LERP dark overlay alpha: 0 -> 75 (30% max dark tint)
                alpha = int(75 * progress)
                if alpha > 0:
                    darkened = QPixmap(cropped.size())
                    darkened.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(darkened)
                    painter.drawPixmap(0, 0, cropped)
                    painter.fillRect(darkened.rect(), QColor(0, 0, 0, alpha))
                    painter.end()
                    self.image_label.setPixmap(darkened)
                else:
                    self.image_label.setPixmap(cropped)
                    
                self.image_label.setText("")
                return
        
        # Fallback card if banner is missing
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText(f"🎮\n\n{self.name}")
        bg = f"stop:0 #0d47a1, stop:1 #1565c0" if progress > 0.5 else "stop:0 #1e3c72, stop:1 #2a5298"
        self.image_label.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, {bg});"
            "color: #ffffff; font-weight: bold; font-size: 13px; padding: 10px; border-radius: 6px;"
        )

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
        self.widgets = widgets
        self.reflow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def reflow(self):
        # Clear items without destroying child widgets
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

class AddGameDialog(QDialog):
    def __init__(self, parent=None, sgdb_client: SteamGridDBClient = None):
        super().__init__(parent)
        self.setWindowTitle("Add / Install Game")
        self.setGeometry(100, 100, 620, 640)
        if os.path.exists(LOGO_PATH):
            self.setWindowIcon(QIcon(LOGO_PATH))
            
        self.sgdb_client = sgdb_client
        self.banner_path = None
        self.fetcher_thread = None
        self.downloader_thread = None
        self.extractor_thread = None
        self.search_results = []
        
        ensure_sandbox_dir()
        
        layout = QFormLayout()
        
        # Name
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g., Portal 2, Baldur's Gate 3")
        layout.addRow("Game Name:", self.name_input)
        
        # Game path with Browse Folder / Install Archive options
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText(f"e.g., {DEFAULT_SANDBOX_DIR}/MyGame")
        browse_folder_btn = QPushButton("📁 Folder...")
        browse_folder_btn.clicked.connect(self._browse_path)
        install_archive_btn = QPushButton("📦 Install Zip/7z...")
        install_archive_btn.clicked.connect(self._install_archive)
        
        path_layout = QHBoxLayout()
        path_layout.addWidget(self.path_input)
        path_layout.addWidget(browse_folder_btn)
        path_layout.addWidget(install_archive_btn)
        layout.addRow("Game Path:", path_layout)
        
        # Executable editable dropdown
        self.exe_combo = QComboBox()
        self.exe_combo.setEditable(True)
        self.exe_combo.setPlaceholderText("e.g., game.exe, bin/game.exe, start.sh")
        exe_browse_btn = QPushButton("Browse...")
        exe_browse_btn.clicked.connect(self._browse_exe)
        exe_layout = QHBoxLayout()
        exe_layout.addWidget(self.exe_combo)
        exe_layout.addWidget(exe_browse_btn)
        layout.addRow("Executable:", exe_layout)
        
        # Launch Mode
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([
            "umu",
            "umu_net",
            "wine",
            "linux"
        ])
        layout.addRow("Launch Mode:", self.mode_combo)
        
        # Status message label
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #00e676; font-style: italic;")
        layout.addRow("", self.status_label)
        
        # Banner section
        banner_label = QLabel("Cover Art (optional):")
        banner_label.setStyleSheet("font-weight: bold; color: #fff;")
        layout.addRow(banner_label)
        
        # Banner preview (2:3 vertical portrait aspect ratio)
        self.banner_label = QLabel()
        self.banner_label.setFixedSize(QSize(180, 270))
        self.banner_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner_label.setStyleSheet("border: 2px solid #666; border-radius: 3px; background: #111;")
        pixmap = QPixmap(180, 270)
        pixmap.fill(QColor("#333333"))
        self.banner_label.setPixmap(pixmap)
        layout.addRow("Preview:", self.banner_label)
        
        # Search button and results
        search_layout = QHBoxLayout()
        self.fetch_btn = QPushButton("🔍 Search for Cover Art (Steam Store)")
        self.fetch_btn.clicked.connect(self._fetch_banner)
        search_layout.addWidget(self.fetch_btn)
        layout.addRow(search_layout)
        
        # Results list
        results_label = QLabel("Search Results:")
        results_label.setStyleSheet("font-weight: bold; color: #fff; margin-top: 5px;")
        layout.addRow(results_label)
        
        self.results_list = QListWidget()
        self.results_list.setMaximumHeight(120)
        self.results_list.itemClicked.connect(self._on_result_selected)
        layout.addRow(self.results_list)
        
        # Buttons
        buttons_layout = QHBoxLayout()
        add_btn = QPushButton("✓ Add Game")
        cancel_btn = QPushButton("✗ Cancel")
        skip_btn = QPushButton("⊘ Skip Banner")
        add_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        skip_btn.clicked.connect(self._skip_banner)
        buttons_layout.addWidget(skip_btn)
        buttons_layout.addWidget(add_btn)
        buttons_layout.addWidget(cancel_btn)
        layout.addRow(buttons_layout)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        container.setLayout(layout)
        scroll.setWidget(container)
        
        main_layout = QVBoxLayout()
        main_layout.addWidget(scroll)
        self.setLayout(main_layout)
        
        self.setStyleSheet("""
            QDialog { background: #1a1a1a; }
            QLabel { color: #fff; }
            QLineEdit { background: #2a2a2a; color: #fff; border: 1px solid #666; padding: 5px; border-radius: 3px; }
            QPushButton { background: #0d47a1; color: white; border: none; padding: 8px; border-radius: 5px; font-weight: bold; }
            QPushButton:hover { background: #1565c0; }
            QComboBox { background: #2a2a2a; color: #fff; border: 1px solid #666; padding: 5px; border-radius: 3px; }
            QListWidget { background: #2a2a2a; color: #fff; border: 1px solid #666; border-radius: 3px; }
            QListWidget::item:selected { background: #0d47a1; }
            QListWidget::item:hover { background: #1a1a2a; }
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
        self.results_list.clear()
        
        # Create and start fetcher thread
        self.fetcher_thread = BannerFetcher(game_name, self.sgdb_client)
        self.fetcher_thread.results_found.connect(self._on_results_found)
        self.fetcher_thread.error_occurred.connect(self._on_search_error)
        self.fetcher_thread.finished.connect(self._reset_fetch_button)
        self.fetcher_thread.start()
    
    def _on_results_found(self, results: list):
        """Handle search results (called from main thread via signal)"""
        self.search_results = results
        self.results_list.clear()
        
        for i, result in enumerate(results):
            name = result.get('name', 'Unknown')
            released = result.get('released', 'Unknown')
            item_text = f"🎮 {name} ({released})"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.results_list.addItem(item)
        
        # Auto-select first result
        if results:
            self.results_list.setCurrentRow(0)
            self._on_result_selected(self.results_list.item(0))
    
    def _on_result_selected(self, item):
        """Handle result selection asynchronously"""
        idx = item.data(Qt.ItemDataRole.UserRole)
        if 0 <= idx < len(self.search_results):
            result = self.search_results[idx]
            banner_url = result.get('banner_url')
            
            if banner_url and self.sgdb_client:
                # Stop existing downloader thread if running
                if self.downloader_thread and self.downloader_thread.isRunning():
                    self.downloader_thread.quit()
                    self.downloader_thread.wait(500)
                
                # Start non-blocking downloader thread
                self.downloader_thread = BannerDownloader(banner_url, self.sgdb_client)
                self.downloader_thread.download_complete.connect(self._on_banner_downloaded)
                self.downloader_thread.download_failed.connect(lambda msg: None)
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
        """Skip banner selection and continue"""
        self.banner_path = None
        self.accept()
    
    def get_values(self):
        return (
            self.name_input.text().strip(),
            self.path_input.text().strip(),
            self.exe_combo.currentText().strip(),
            self.mode_combo.currentText(),
            self.banner_path
        )

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

        self.setWindowTitle("🎮 MGLauncher - Game Sandbox Manager")
        self.resize(1180, 750)
        
        if os.path.exists(LOGO_PATH):
            self.setWindowIcon(QIcon(LOGO_PATH))

        # Main Root Layout (Horizontal: Left Sidebar + Right Main Panel)
        root_widget = QWidget()
        root_widget.setStyleSheet("background: #141414;")
        root_layout = QHBoxLayout(root_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        
        # 1. Left Sidebar Bar Panel
        sidebar = QFrame()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet("""
            QFrame {
                background: #0d0d0d;
                border-right: 1px solid #282828;
            }
            QLabel {
                color: #fff;
            }
            QPushButton {
                background: transparent;
                color: #aaa;
                text-align: left;
                padding: 10px 15px;
                border-radius: 6px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1e1e1e;
                color: #fff;
            }
            QPushButton:checked {
                background: #0d47a1;
                color: #fff;
            }
        """)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(15, 20, 15, 20)
        sidebar_layout.setSpacing(12)
        
        # App Header in Sidebar with App Logo
        header_box = QHBoxLayout()
        header_box.setSpacing(10)
        
        logo_label = QLabel()
        if os.path.exists(LOGO_PATH):
            logo_pix = QPixmap(LOGO_PATH).scaled(
                40, 40,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            logo_label.setPixmap(logo_pix)
        header_box.addWidget(logo_label)
        
        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        app_title = QLabel("MGLauncher")
        app_title.setFont(QFont("Monospace", 14, QFont.Weight.Bold))
        app_sub = QLabel("Firejail Sandbox")
        app_sub.setStyleSheet("color: #777; font-size: 10px;")
        title_vbox.addWidget(app_title)
        title_vbox.addWidget(app_sub)
        header_box.addLayout(title_vbox)
        
        sidebar_layout.addLayout(header_box)
        sidebar_layout.addSpacing(10)
        
        # Sidebar Navigation Items
        self.nav_library = QPushButton("🎮 My Library")
        self.nav_library.setChecked(True)
        self.nav_sandbox = QPushButton("📁 Sandbox Folder")
        self.nav_sandbox.clicked.connect(self._open_sandbox_dir)
        self.nav_sync = QPushButton("🔄 Sync Library")
        self.nav_sync.clicked.connect(self._on_sync_sandbox)
        
        sidebar_layout.addWidget(self.nav_library)
        sidebar_layout.addWidget(self.nav_sandbox)
        sidebar_layout.addWidget(self.nav_sync)
        
        sidebar_layout.addStretch()
        
        # Sidebar Footer Badge
        self.stat_label = QLabel("0 Games Installed")
        self.stat_label.setStyleSheet("color: #666; font-size: 11px;")
        sidebar_layout.addWidget(self.stat_label)
        
        root_layout.addWidget(sidebar)

        # 2. Right Main Content Panel
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(20, 20, 20, 20)
        right_layout.setSpacing(15)
        
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

        # Action Buttons Layout (Launch, Add, Remove, Export, Import)
        action_layout = QHBoxLayout()
        action_layout.setSpacing(10)
        
        self.btn_launch = QPushButton("▶ Launch Selected")
        self.btn_launch.clicked.connect(self._on_launch)
        self.btn_launch.setMinimumHeight(40)
        self.btn_launch.setStyleSheet("background: #2e7d32; color: white; font-weight: bold; border-radius: 6px;")
        action_layout.addWidget(self.btn_launch)
        
        self.btn_add = QPushButton("➕ Add / Install Game")
        self.btn_add.clicked.connect(self._on_add)
        self.btn_add.setMinimumHeight(40)
        action_layout.addWidget(self.btn_add)
        
        self.btn_remove = QPushButton("🗑️ Remove")
        self.btn_remove.clicked.connect(self._on_remove)
        self.btn_remove.setMinimumHeight(40)
        self.btn_remove.setStyleSheet("background: #c62828; color: white; font-weight: bold; border-radius: 6px;")
        action_layout.addWidget(self.btn_remove)
        
        self.btn_export = QPushButton("💾 Export Save")
        self.btn_export.clicked.connect(self._on_export)
        self.btn_export.setMinimumHeight(40)
        action_layout.addWidget(self.btn_export)
        
        self.btn_import = QPushButton("📂 Import Save")
        self.btn_import.clicked.connect(self._on_import)
        self.btn_import.setMinimumHeight(40)
        action_layout.addWidget(self.btn_import)
        
        right_layout.addLayout(action_layout)
        
        root_layout.addWidget(right_panel)
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

    def _open_sandbox_dir(self):
        """Open ~/Games/Sandbox in system file manager"""
        import subprocess
        path = ensure_sandbox_dir()
        try:
            subprocess.Popen(["xdg-open", path])
        except Exception as e:
            QMessageBox.information(self, "Sandbox Path", f"Sandbox directory:\n{path}")

    def _refresh_library(self):
        """Clear and reload game banners into dynamic responsive grid"""
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
            game_id, name, path, executable, mode, banner_url, steam_id = game
            
            widget = GameBannerWidget(game_id, name, banner_url)
            widget.clicked.connect(self._select_game)
            
            widgets.append(widget)
            self.banner_widgets[game_id] = widget
            
            # If banner_url is missing or file does not exist on disk, auto-fetch in background!
            if not banner_url or not os.path.exists(banner_url):
                fetcher = BannerAutoFetcher(game_id, name, self.sgdb_client)
                fetcher.banner_auto_downloaded.connect(self._on_auto_banner_downloaded)
                fetcher.start()
                self.auto_fetchers.append(fetcher)
            
        self.grid_container.set_banner_widgets(widgets)
        self._check_games_on_drive()

    def _check_games_on_drive(self):
        """Check all games in library against disk and grey out missing ones"""
        for game in self.games:
            game_id, name, path, executable, mode, banner_url, steam_id = game
            
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

    def _select_game(self, game_id: int):
        """Single clicking a game banner selects it and opens launch menu directly"""
        # Deselect all
        for widget in self.banner_widgets.values():
            widget.set_selected(False)
        
        # Select clicked game
        for game in self.games:
            if game[0] == game_id:
                self.selected_game = game
                if game_id in self.banner_widgets:
                    self.banner_widgets[game_id].set_selected(True)
                break
        
        self._on_launch()

    def _get_selected_game(self):
        """Get the currently selected game"""
        return self.selected_game

    def _on_launch(self):
        game = self._get_selected_game()
        if not game:
            QMessageBox.warning(self, "Warning", "Please select a game to launch.")
            return
        
        game_id, name, path, exe, mode, banner_url, steam_id = game
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
                background-color: #0d47a1;
                color: #ffffff;
            }
        """)

        action_umu = menu.addAction("🛡️ Primary: UMU (Offline / No Net)")
        action_umu_net = menu.addAction("🌐 Secondary: UMU (Network Enabled)")
        action_wine = menu.addAction("🍷 3rd: Legacy Wine (Offline)")
        
        action_linux = None
        if mode == "linux":
            action_linux = menu.addAction("🐧 Native Linux Script/Binary")

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

        if selected_action == action_umu:
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
            self.runner.launch(path, exe, selected_mode)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to launch game: {str(e)}")
    
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
            
            save_sandbox_config(path, exe)
            self.db.add_game(name, path, exe, mode, banner_path)
            self._refresh_library()
            QMessageBox.information(self, "Success", f"Game '{name}' added to library.")
    
    def _on_sync_sandbox(self, quiet: bool = False):
        """Auto-discover installed games in ~/Games/Sandbox"""
        found = scan_sandbox_games(DEFAULT_SANDBOX_DIR)
        existing_paths = {g[2] for g in self.db.get_all_games()}
        
        added_count = 0
        for game in found:
            if game['path'] not in existing_paths:
                self.db.add_game(game['name'], game['path'], game['executable'], game['mode'])
                added_count += 1
                
        if added_count > 0:
            self._refresh_library()
            if not quiet:
                QMessageBox.information(self, "Sync Complete", f"Found and added {added_count} game(s) from {DEFAULT_SANDBOX_DIR}.")
        else:
            if not quiet:
                QMessageBox.information(self, "Sync Complete", f"No new games found in {DEFAULT_SANDBOX_DIR}.")

    def _on_remove(self):
        game = self._get_selected_game()
        if not game:
            QMessageBox.warning(self, "Warning", "Please select a game to remove.")
            return
        
        box = QMessageBox(self)
        box.setWindowTitle("Remove Game")
        box.setText(f"How would you like to remove '{game[1]}'?")
        box.setIcon(QMessageBox.Icon.Question)
        
        btn_remove_db = box.addButton("Remove from Library Only", QMessageBox.ButtonRole.AcceptRole)
        btn_delete_disk = box.addButton("Delete Game Files & Sandbox Data from Disk", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        
        box.exec()
        clicked = box.clickedButton()
        
        if clicked == btn_cancel or clicked is None:
            return
            
        game_id = game[0]
        game_path = game[2]
        
        self.db.remove_game(game_id)
        
        if clicked == btn_delete_disk:
            if os.path.exists(game_path):
                try:
                    shutil.rmtree(game_path)
                    QMessageBox.information(self, "Success", f"Removed '{game[1]}' from library and deleted game files from disk.")
                except Exception as e:
                    QMessageBox.warning(self, "Warning", f"Removed from library, but failed to delete files: {e}")
            else:
                QMessageBox.information(self, "Success", f"Removed '{game[1]}' from library (game directory not found).")
        else:
            QMessageBox.information(self, "Success", f"Removed '{game[1]}' from library (game files preserved).")
            
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
                QMessageBox.information(self, "Success", "Save exported successfully.")
            else:
                QMessageBox.critical(self, "Error", "Failed to export save.")
    
    def _on_import(self):
        game = self._get_selected_game()
        if not game:
            QMessageBox.warning(self, "Warning", "Please select a game.")
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
                QMessageBox.information(self, "Success", "Save imported successfully.")
            else:
                QMessageBox.critical(self, "Error", "Failed to import save.")
