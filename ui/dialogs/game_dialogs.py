import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFormLayout,
    QFileDialog, QMessageBox, QComboBox, QProgressBar, QWidget, QFrame, QMenu,
    QCheckBox, QStackedWidget, QPlainTextEdit, QGraphicsOpacityEffect, QApplication
)
from PyQt6.QtCore import Qt, QSize, QPoint, pyqtSignal, QVariantAnimation, QEasingCurve, QTimer, QUrl
from PyQt6.QtGui import QFont, QPixmap, QColor, QPainter, QIcon, QMovie, QDesktopServices

from core.steamgriddb_client import SteamGridDBClient
from core.archive_extractor import executable_sort_key
from core.launch_diagnostics import persist_diagnostics
from ui.icons import get_app_icon, get_icon, LOGO_PATH, GIF_PATH, CONFIRM_GIF_PATH, draw_custom_lock_pixmap
from ui.threads import BannerFetcher, BannerDownloader, ArchiveExtractorThread, SafeLaunchLogReader
from ui.components.sidebar import DialogTitleBar, add_soft_shadow

DEFAULT_SANDBOX_DIR = os.path.expanduser("~/Games/Sandbox")


def ensure_sandbox_dir() -> str:
    """Guarantee ~/Games/Sandbox folder exists."""
    os.makedirs(DEFAULT_SANDBOX_DIR, mode=0o700, exist_ok=True)
    return DEFAULT_SANDBOX_DIR


def find_executables(dir_path: str) -> list[str]:
    """Find potential executable files in directory."""
    if not dir_path or not os.path.isdir(dir_path):
        return []
    
    exes = []
    for root, _, files in os.walk(dir_path):
        # Skip wine prefix directory to avoid listing wine exes
        if "prefix" in root.split(os.sep):
            continue
            
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in ('.exe', '.bat', '.sh') or os.access(os.path.join(root, file), os.X_OK):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, start=dir_path)
                exes.append(rel_path)
                
    # Prefer the actual game binary over installers and redistributables.
    exes.sort(key=executable_sort_key)
    return exes


def load_sandbox_config(dir_path: str) -> str | None:
    """Load executable relative path from .sandbox-config if present."""
    cfg_file = os.path.join(dir_path, ".sandbox-config")
    if os.path.isfile(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("EXE="):
                        return line[4:].strip()
        except Exception:
            pass
    return None


class AddGameDialog(QDialog):
    def __init__(self, parent=None, sgdb_client: SteamGridDBClient = None):
        super().__init__(parent)
        self.setWindowTitle("Add Game")
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
        self.selected_steam_id = ""
        
        ensure_sandbox_dir()

        # Root vertical layout (Title bar + Main body + Bottom action bar)
        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Custom Draggable Title Bar
        self.title_bar = DialogTitleBar(self, "➕ Add Game")
        root_layout.addWidget(self.title_bar)

        # Main Body Widget (2-Column Grid Layout)
        body_widget = QWidget()
        body_layout = QHBoxLayout(body_widget)
        body_layout.setContentsMargins(25, 20, 25, 20)
        body_layout.setSpacing(25)

        # LEFT COLUMN: Game Configuration Form (~500px width)
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
        form_layout.addRow("Game Name:", self.name_input)

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
        self._exe_user_edited = False
        self.exe_combo.setPlaceholderText("e.g., game.exe, bin/game.exe, start.sh")
        self.exe_combo.setMinimumHeight(36)
        # An editable combo keeps the previously selected item's userData
        # after the user types a different executable.  Clear the selection
        # on manual edits so get_values() cannot silently save the stale path.
        self.exe_combo.lineEdit().textEdited.connect(
            self._on_executable_edited
        )
        
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
        self.mode_combo.addItem(get_app_icon("shield"), "UMU – Offline", "umu")
        self.mode_combo.addItem(get_app_icon("globe"), "UMU – Network Enabled", "umu_net")
        self.mode_combo.addItem(get_app_icon("wine"), "Wine – Legacy", "wine")
        self.mode_combo.addItem(get_app_icon("terminal"), "Native Linux", "linux")
        form_layout.addRow("Runner Mode:", self.mode_combo)

        self.version_input = QLineEdit()
        self.version_input.setPlaceholderText("e.g., V 0.33.7.2")
        self.version_input.setMinimumHeight(36)
        form_layout.addRow("Current Version:", self.version_input)

        self.patch_notes_input = QLineEdit()
        self.patch_notes_input.setPlaceholderText("https://...")
        self.patch_notes_input.setMinimumHeight(36)
        form_layout.addRow("Patch Notes URL:", self.patch_notes_input)

        left_box.addLayout(form_layout)

        # Status Label / Banner
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #4ade80; font-weight: bold; font-size: 11px; padding: 6px 0px;")
        left_box.addWidget(self.status_label)

        self.install_progress = QProgressBar()
        self.install_progress.setRange(0, 0)
        self.install_progress.setFixedHeight(6)
        self.install_progress.setVisible(False)
        left_box.addWidget(self.install_progress)
        self.cancel_install_btn = QPushButton("Cancel extraction")
        self.cancel_install_btn.setVisible(False)
        self.cancel_install_btn.clicked.connect(self._cancel_extraction)
        left_box.addWidget(self.cancel_install_btn)

        self.left_box = left_box
        left_box.addStretch()
        body_layout.addLayout(left_box, stretch=3)

        # RIGHT COLUMN: Cover Art & Steam Grid DB Search (~280px width)
        right_box = QVBoxLayout()
        right_box.setSpacing(12)
        right_box.setAlignment(Qt.AlignmentFlag.AlignTop)

        sec_cover = QLabel("Cover Art")
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

        skip_btn = QPushButton(" Clear Cover Art")
        skip_btn.setIcon(get_icon("ph.x-circle-bold"))
        skip_btn.setMinimumHeight(32)
        skip_btn.clicked.connect(self._skip_banner)
        right_box.addWidget(skip_btn)

        body_layout.addLayout(right_box, stretch=2)
        root_layout.addWidget(body_widget)

        # BOTTOM ACTION TOOLBAR
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
            QLineEdit:focus { border: 1px solid #737780; }
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
                thread.requestInterruption()
                thread.quit()
                thread.wait(7000)
                if thread.isRunning():
                    thread.wait()
        super().closeEvent(event)
    
    def _scan_and_populate_exes(self, path: str):
        """Scan directory for executables and populate dropdown"""
        exes = find_executables(path)
        self.exe_combo.clear()
        
        cfg_exe = load_sandbox_config(path)
        if cfg_exe:
            if cfg_exe not in exes:
                exes.insert(0, cfg_exe)
            else:
                exes.remove(cfg_exe)
                exes.insert(0, cfg_exe)
                
        if exes:
            from core.archive_installer import ArchiveInstaller
            candidates = {candidate.relative_path: candidate for candidate in ArchiveInstaller().candidates(path)}
            for relative in exes:
                candidate = candidates.get(relative)
                from core.disk_utils import format_size
                size = format_size(candidate.size_bytes) if candidate else "unknown size"
                kind = candidate.kind if candidate else "Executable"
                icon = get_icon("ph.terminal-window-bold") if relative.lower().endswith(".sh") else get_app_icon("wine")
                self.exe_combo.addItem(icon, f"{relative}  ·  {kind}  ·  {size}", relative)
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
                self._exe_user_edited = True
                self.exe_combo.setCurrentIndex(-1)
                self.exe_combo.setEditText(rel)
            else:
                filename = os.path.basename(path)
                self._exe_user_edited = True
                self.exe_combo.setCurrentIndex(-1)
                self.exe_combo.setEditText(filename)

    def _on_executable_edited(self, _text: str):
        self._exe_user_edited = True
        self.exe_combo.setCurrentIndex(-1)
    
    def _fetch_banner(self):
        game_name = self.name_input.text().strip()
        if not game_name:
            QMessageBox.warning(self, "Error", "Please enter a game name first.")
            return
        
        if not self.sgdb_client:
            QMessageBox.warning(self, "Error", "Banner fetcher not available.")
            return
        
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("🔄 Searching...")
        
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
            self.selected_steam_id = str(result.get('appid') or "").strip()
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

    def _cancel_extraction(self):
        if self.extractor_thread and self.extractor_thread.isRunning():
            self.extractor_thread.requestInterruption()
            self.status_label.setText("⏹ Cancelling extraction…")
    
    def get_values(self):
        """Extract entered form values cleanly."""
        mode = self.mode_combo.currentData()
        if mode not in ("umu", "umu_net", "wine", "linux"):
            mode = {
                "UMU – Offline": "umu",
                "UMU – Network Enabled": "umu_net",
                "Wine – Legacy": "wine",
                "Native Linux": "linux",
            }.get(self.mode_combo.currentText().strip(), "umu")
        executable = (
            self.exe_combo.currentText()
            if self._exe_user_edited or self.exe_combo.currentIndex() < 0
            else self.exe_combo.currentData()
        )
        return (
            self.name_input.text().strip(),
            self.path_input.text().strip(),
            (executable or "").strip(),
            mode,
            self.banner_path
        )

    def get_steam_id(self) -> str:
        """Return the Steam AppID selected with the cover art, when available."""
        return self.selected_steam_id

    def get_version_metadata(self) -> tuple[str, str]:
        return self.version_input.text().strip(), self.patch_notes_input.text().strip()


class EditGameDialog(AddGameDialog):
    """Dialog pre-populated with existing game details allowing editing name, path, exe, mode, and cover art."""
    mark_current_requested = pyqtSignal(int)

    def __init__(self, game_data: tuple, parent=None, sgdb_client: SteamGridDBClient = None):
        super().__init__(parent, sgdb_client)
        self.title_bar.title_label.setText("✏️ Edit Game Settings")
        
        game_id, name, path, exe, mode, banner_url, steam_id, *_ = (*game_data, 0)
        self.game_id = game_id
        self.banner_path = banner_url
        
        self.name_input.setText(name or "")
        if len(game_data) > 15:
            self.version_input.setText(game_data[15] or "")
        if len(game_data) > 16:
            self.patch_notes_input.setText(game_data[16] or "")
        self.path_input.setText(path or "")
        
        if path and os.path.exists(path):
            self._scan_and_populate_exes(path)
            
        # The database value is the user's current setting.  The sidecar file
        # is only a compatibility export; using it here could resurrect an
        # older auto-detected installer and overwrite a newer database value.
        effective_exe = exe
        if effective_exe:
            # Select the existing item by its userData when possible.  Using
            # setEditText() alone leaves currentData() pointing at the first
            # auto-detected executable (often Redist/DXWebSetup.exe).
            exe_idx = self.exe_combo.findData(effective_exe)
            if exe_idx >= 0:
                self.exe_combo.setCurrentIndex(exe_idx)
            else:
                self.exe_combo.setCurrentIndex(-1)
                self.exe_combo.setEditText(effective_exe)
            
        mode_idx = self.mode_combo.findData(mode)
        if mode_idx < 0:
            mode_idx = self.mode_combo.findText(mode)
        if mode_idx >= 0:
            self.mode_combo.setCurrentIndex(mode_idx)
            
        if banner_url and os.path.exists(banner_url):
            self._on_banner_downloaded(banner_url)
            
        self.add_btn.setText("Save Changes")
        self.add_btn.setIcon(get_app_icon("export"))

        update_note = QLabel("Steam update tracking only records a build as installed.\nIt does not download or update game files.")
        update_note.setWordWrap(True)
        update_note.setStyleSheet("color: #a1a1aa; font-size: 10px; padding-top: 8px;")
        self.left_box.insertWidget(self.left_box.count() - 1, update_note)

        self.mark_current_btn = QPushButton("Mark Steam Build as Current")
        self.mark_current_btn.setToolTip("Use only after updating the game manually through Steam or by replacing its files.")
        self.mark_current_btn.clicked.connect(lambda: self.mark_current_requested.emit(self.game_id))
        self.left_box.insertWidget(self.left_box.count() - 1, self.mark_current_btn)


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

        btn_umu = self._create_option_button(
            "UMU – Offline",
            "Recommended for Windows games that do not need internet access",
            "shield"
        )
        btn_umu.clicked.connect(lambda: self._select("umu"))
        body_layout.addWidget(btn_umu)

        btn_umu_net = self._create_option_button(
            "UMU – Network Enabled",
            "Allows internet access for online features",
            "globe"
        )
        btn_umu_net.clicked.connect(lambda: self._select("umu_net"))
        body_layout.addWidget(btn_umu_net)

        btn_wine = self._create_option_button(
            "Wine – Legacy",
            "Runs directly with system Wine without the Proton wrapper",
            "wine"
        )
        btn_wine.clicked.connect(lambda: self._select("wine"))
        body_layout.addWidget(btn_wine)

        if mode == "linux":
            btn_linux = self._create_option_button(
                "Native Linux",
                "Runs directly as a native Linux executable in Firejail",
                "terminal"
            )
            btn_linux.clicked.connect(lambda: self._select("linux"))
            body_layout.addWidget(btn_linux)

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
                background: #52565e;
                border-color: #737780;
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
                border-color: #737780;
                color: #ffffff;
            }
        """)
        btn.setText(f"{title}\n{subtitle}")
        return btn

    def _select(self, mode: str):
        self.selected_mode = mode
        self.accept()


class SafeLaunchDialog(QDialog):
    """Sleek, non-blocking animated dark card diagnostic popup for game launches."""
    retry_requested = pyqtSignal(str)
    unsafe_launch_requested = pyqtSignal()

    def __init__(self, game_name: str, user_name: str = None, process=None, parent=None):
        super().__init__(parent)
        self.game_name = game_name
        self.user_name = user_name or getpass.getuser().capitalize()
        self.process = process
        self.diagnostics = getattr(process, "safelauncher_diagnostics", None)
        if self.diagnostics:
            self.diagnostics.game_name = game_name
        self.log_lines = []
        self.launch_finished = False
        self.handoff_shown = False
        self.requires_proton_setup = False
        import time
        self.startup_started_at = time.monotonic()
        self.startup_grace_seconds = 15.0

        self.setWindowTitle(f"Safe Launch Log - {game_name}")
        self.setMinimumSize(620, 440)
        self.resize(760, 600)

        if parent:
            p_geo = parent.geometry()
            self.move(
                p_geo.x() + (p_geo.width() - self.width()) // 2,
                p_geo.y() + (p_geo.height() - self.height()) // 2
            )

        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
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

        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack)

        self.close_log_button = QPushButton("Close log window")
        self.close_log_button.setMinimumHeight(34)
        self.close_log_button.setStyleSheet("""
            QPushButton {
                background: #27272a; color: #e4e4e7; border: 1px solid #52525b;
                border-radius: 6px; font-weight: bold; padding: 6px 14px;
            }
            QPushButton:hover { background: #3f3f46; color: #ffffff; }
        """)
        self.close_log_button.clicked.connect(self.reject)
        root_layout.addWidget(self.close_log_button)

        # PAGE 0: Pudgy Penguin GIF Intro Stage
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

        # PAGE 1: Virtual Environment Console Stage
        self.page_console = QWidget()
        console_layout = QVBoxLayout(self.page_console)
        console_layout.setContentsMargins(0, 0, 0, 0)
        console_layout.setSpacing(10)

        self.header_title = QLabel("Preparing Virtual Environment...")
        self.header_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_title.setStyleSheet("color: #ffffff; font-size: 17px; font-weight: bold;")
        console_layout.addWidget(self.header_title)

        self.header_sub = QLabel(f"Initializing Firejail & UMU sandbox for '{game_name}'")
        self.header_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_sub.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        console_layout.addWidget(self.header_sub)

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

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.document().setMaximumBlockCount(4000)
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

        # PAGE 3: Launch failure screen
        self.page_error = QWidget()
        error_layout = QVBoxLayout(self.page_error)
        error_layout.setContentsMargins(10, 16, 10, 10)
        error_layout.setSpacing(12)

        self.error_title = QLabel("Game launch failed")
        self.error_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_title.setStyleSheet("color: #fca5a5; font-size: 19px; font-weight: bold;")
        error_layout.addWidget(self.error_title)

        self.error_summary = QLabel()
        self.error_summary.setWordWrap(True)
        self.error_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_summary.setStyleSheet("color: #d4d4d8; font-size: 12px;")
        error_layout.addWidget(self.error_summary)

        self.error_details = QPlainTextEdit()
        self.error_details.setReadOnly(True)
        self.error_details.document().setMaximumBlockCount(4000)
        self.error_details.setStyleSheet("""
            QPlainTextEdit {
                background: #09090b; color: #fca5a5; border: 1px solid #7f1d1d;
                border-radius: 8px; font-family: monospace; font-size: 10px; padding: 8px;
            }
        """)
        error_layout.addWidget(self.error_details)

        diagnostics_buttons = QHBoxLayout()
        copy_diagnostics = QPushButton("Copy diagnostics")
        copy_diagnostics.clicked.connect(self._copy_diagnostics)
        diagnostics_buttons.addWidget(copy_diagnostics)
        open_logs = QPushButton("Open log folder")
        open_logs.clicked.connect(self._open_log_folder)
        diagnostics_buttons.addWidget(open_logs)
        diagnostics_buttons.addStretch()
        error_layout.addLayout(diagnostics_buttons)

        recovery_buttons = QHBoxLayout()
        retry_safe = QPushButton("Retry with safe fallback")
        retry_safe.setToolTip("Retry using the sandboxed Wine fallback.")
        retry_safe.clicked.connect(lambda: self._request_retry("wine"))
        recovery_buttons.addWidget(retry_safe)
        unsafe = QPushButton("⚠ Launch without sandbox (UNSAFE)")
        unsafe.setStyleSheet("QPushButton { background: #7f1d1d; color: #fecaca; border: 1px solid #ef4444; font-weight: bold; }")
        unsafe.clicked.connect(self._request_unsafe_launch)
        recovery_buttons.addWidget(unsafe)
        error_layout.addLayout(recovery_buttons)

        close_error = QPushButton("Close")
        close_error.setMinimumHeight(36)
        close_error.setStyleSheet("QPushButton { background: #991b1b; color: #ffffff; border-radius: 6px; font-weight: bold; } QPushButton:hover { background: #b91c1c; }")
        close_error.clicked.connect(self.reject)
        error_layout.addWidget(close_error)
        self.stack.addWidget(self.page_error)

        # PAGE 2: Confirmation Greeting Screen
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

        self.dialog_opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.dialog_opacity)
        self.dialog_opacity.setOpacity(1.0)

        import time, shutil
        t_str = time.strftime("%H:%M:%S")
        if shutil.which("firejail"):
            self.append_log(f"[{t_str}] 🛡️ [SECURITY] Initializing Firejail namespace isolation...")
            self.append_log(f"[{t_str}] 🔒 [SECURITY] Applying filesystem whitelist & Firejail isolation...")
        else:
            self.append_log(f"[{t_str}] ⚠️ [WARNING] Firejail is not installed on this system.")
            self.append_log(f"[{t_str}] ⚡ [FALLBACK] Running game in direct unsandboxed execution mode.")
        self.append_log(f"[{t_str}] 🍷 [RUNNER] Loading Proton / Wine runtime container...")
        self.append_log(f"[{t_str}] 🚀 [EXEC] Launching process for '{game_name}'...")

        self.process_log_path = getattr(self.process, "safelauncher_log_path", None)
        self._process_log_offset = 0
        self._extra_log_offsets = {}
        if self.process_log_path:
            self.log_poll_timer = QTimer(self)
            self.log_poll_timer.setInterval(250)
            self.log_poll_timer.timeout.connect(self._poll_process_log)
            self.log_poll_timer.start()
        elif self.process and getattr(self.process, 'stdout', None):
            self.reader_thread = SafeLaunchLogReader(self.process, self)
            self.reader_thread.log_line.connect(self.append_log)
            self.reader_thread.start()

        self.process_timer = QTimer(self)
        self.process_timer.setInterval(200)
        self.process_timer.timeout.connect(self._check_process_state)
        self.process_timer.start()

        self.stack.setCurrentIndex(0)

        self.gif_timer = QTimer(self)
        self.gif_timer.setSingleShot(True)
        self.gif_timer.timeout.connect(self._goto_console_stage)
        self.gif_timer.start(2200)

    def _goto_console_stage(self):
        self.stack.setCurrentIndex(1)
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
            self.log_lines.append(text)
            if self.diagnostics:
                self.diagnostics.output.append(text)
            self.console.appendPlainText(text)
            if hasattr(self, "error_details") and self.stack.currentWidget() is self.page_error:
                self.error_details.appendPlainText(text)
            sb = self.console.verticalScrollBar()
            if sb:
                sb.setValue(sb.maximum())

            if "parent is shutting down" in text.lower() and not self.launch_finished:
                QTimer.singleShot(350, self._check_process_state)

    def _poll_process_log(self):
        path = getattr(self, "process_log_path", None)
        paths = []
        if path:
            paths.append((path, ""))
        for extra_path in getattr(self.process, "safelauncher_extra_log_paths", []):
            paths.append((extra_path, f"[PROTON LOG] {extra_path}"))

        for log_path, marker in paths:
            if not os.path.exists(log_path):
                continue
            try:
                if marker and log_path not in self._extra_log_offsets:
                    self._extra_log_offsets[log_path] = 0
                    self.append_log(marker)
                offset = self._process_log_offset if not marker else self._extra_log_offsets[log_path]
                with open(log_path, "r", encoding="utf-8", errors="replace") as stream:
                    stream.seek(offset)
                    new_text = stream.read()
                    new_offset = stream.tell()
                if marker:
                    self._extra_log_offsets[log_path] = new_offset
                else:
                    self._process_log_offset = new_offset
                if new_text:
                    lines = new_text.splitlines()
                    live_limit = 250
                    if len(lines) > live_limit:
                        omitted = len(lines) - live_limit
                        omitted_lines = lines[:omitted]
                        self.log_lines.extend(omitted_lines)
                        if self.diagnostics:
                            self.diagnostics.output.extend(omitted_lines)
                        self.append_log(
                            f"[diagnostics] {omitted} lines kept in the file log; "
                            "live display was throttled to keep the window responsive."
                        )
                        lines = lines[-live_limit:]
                    for line in lines:
                        self.append_log(line)
            except OSError:
                continue

    def _goto_confirmation_stage(self):
        if self.launch_finished:
            return
        if self.process and self.process.poll() is not None:
            self._check_process_state()
            return
        self.handoff_shown = True
        import time
        t_str = time.strftime("%H:%M:%S")
        self.append_log(f"[{t_str}] ✔️ [SUCCESS] Sandbox container initialized cleanly.")
        self.append_log(f"[{t_str}] ✨ [STATUS] Handing off control to {self.game_name}. Have fun!")

        self.header_title.setText("Game running — launch log")
        self.header_sub.setText(f"'{self.game_name}' is running in the sandbox container")
        self.progress_bar.setValue(100)
        self.stack.setCurrentWidget(self.page_console)

    def _check_process_state(self):
        if not self.process or self.launch_finished:
            return

        return_code = self.process.poll()
        if return_code is None:
            return

        import time
        startup_elapsed = time.monotonic() - self.startup_started_at
        if startup_elapsed > self.startup_grace_seconds:
            self.launch_finished = True
            self.process_timer.stop()
            return

        self.launch_finished = True
        self.process_timer.stop()
        if hasattr(self, "progress_anim"):
            self.progress_anim.stop()
        if hasattr(self, "gif_timer"):
            self.gif_timer.stop()

        if getattr(self, "process_log_path", None):
            self._poll_process_log()
        details = "\n".join(self.log_lines)
        if return_code < 0:
            reason = f"The launcher was terminated by signal {-return_code}."
        elif return_code == 0:
            reason = "Proton/UMU exited before the game reached the running state."
        else:
            reason = f"Proton/UMU exited with code {return_code}."

        lower_details = details.lower()
        steam_api_failure = any(token in lower_details for token in (
            "steam_api64.dll", "steam_api.dll", "steamapi_", "steam api",
            "steamapps_v", "steamapps", "unimplemented function steam",
        ))
        if self.diagnostics:
            self.diagnostics.return_code = return_code
            self.diagnostics.output = list(self.log_lines)
            persist_diagnostics(self.diagnostics)
            reason = self.diagnostics.actionable_explanation()
        self.requires_proton_setup = any(marker in lower_details for marker in (
            "protonpath",
            "proton not found",
            "umu has not been setup",
            "steamrt4 validation failed",
            "could not find steamrt4",
            "an internet connection is required to setup umu",
        ))
        if steam_api_failure:
            # Keep the Steam/API disclaimer as the primary explanation even
            # when Wine also emitted generic library or exit-code warnings.
            reason = self.diagnostics.actionable_explanation() if self.diagnostics else (
                "Disclaimer: the sandbox initialized, but the game requires a Steam API/client unavailable in this launch mode."
            )
        elif "libcrypto.so" in lower_details or "openssl_" in lower_details:
            reason += " A packaged launcher library was loaded by a host runtime tool. Restart using the updated SafeLauncher build."
        elif "no such file" in lower_details or "cannot open" in lower_details:
            reason += " Check that the selected executable path is correct."
        elif "no permissions to create a new namespace" in lower_details or "unprivileged_userns_clone" in lower_details:
            reason += " The kernel has disabled unprivileged user namespaces; enable kernel.unprivileged_userns_clone=1 or use a compatible kernel/container configuration."
        elif ("proton" in lower_details or "umu" in lower_details) and not any(token in lower_details for token in ("steam_api64.dll", "steam_api.dll", "steamapi_", "steam api")):
            reason += " Check the Proton/UMU runtime and the game prefix."

        self.error_summary.setText(reason)
        self.error_details.setPlainText(self.diagnostics.as_text() if self.diagnostics else (details or "No diagnostic output was produced."))
        self.stack.setCurrentWidget(self.page_error)

    def _copy_diagnostics(self):
        text = self.diagnostics.as_text() if self.diagnostics else self.error_details.toPlainText()
        QApplication.clipboard().setText(text)

    def _open_log_folder(self):
        path = getattr(self.diagnostics, "log_path", "") if self.diagnostics else ""
        folder = os.path.dirname(path) if path else os.path.expanduser("~/.local/state/safelauncher/diagnostics")
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _request_retry(self, mode: str):
        self.retry_requested.emit(mode)
        self.accept()

    def _request_unsafe_launch(self):
        self.unsafe_launch_requested.emit()
        self.accept()

    def _fade_out_dialog(self):
        if self.process and self.process.poll() is not None and not self.launch_finished:
            self._check_process_state()
            if self.stack.currentWidget() is self.page_error:
                return
        fade_dialog = QVariantAnimation(self)
        fade_dialog.setStartValue(1.0)
        fade_dialog.setEndValue(0.0)
        fade_dialog.setDuration(500)
        fade_dialog.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade_dialog.valueChanged.connect(self.dialog_opacity.setOpacity)
        fade_dialog.finished.connect(self.accept)
        fade_dialog.start()
        self._fade_dialog = fade_dialog

    def _cleanup_resources(self):
        for timer_name in ("gif_timer", "process_timer", "close_timer"):
            timer = getattr(self, timer_name, None)
            if timer:
                timer.stop()
        log_poll_timer = getattr(self, "log_poll_timer", None)
        if log_poll_timer:
            log_poll_timer.stop()
        progress_anim = getattr(self, "progress_anim", None)
        if progress_anim:
            progress_anim.stop()
        fade_anim = getattr(self, "_fade_dialog", None)
        if fade_anim:
            fade_anim.stop()
        reader = getattr(self, "reader_thread", None)
        if reader and reader.isRunning():
            reader.stop()

    def accept(self):
        self._cleanup_resources()
        super().accept()

    def reject(self):
        self._cleanup_resources()
        super().reject()

    def closeEvent(self, event):
        self._cleanup_resources()
        super().closeEvent(event)


def detect_linux_distro() -> tuple[str, str]:
    """Detect Linux distribution and return (os_name, install_command)."""
    os_name = "Linux"
    cmd = "sudo apt install firejail"
    if os.path.exists('/etc/os-release'):
        try:
            info = {}
            with open('/etc/os-release') as f:
                for line in f:
                    if '=' in line:
                        k, v = line.strip().split('=', 1)
                        info[k] = v.strip('\"\'')
            id_str = info.get('ID', '').lower()
            like_str = info.get('ID_LIKE', '').lower()
            name_str = info.get('NAME', 'Linux')

            if 'arch' in id_str or 'arch' in like_str or 'manjaro' in id_str or 'endeavour' in id_str or 'cachy' in id_str:
                return (name_str, 'sudo pacman -S firejail umu-launcher')
            elif 'fedora' in id_str or 'fedora' in like_str or 'rhel' in id_str:
                return (name_str, 'sudo dnf install firejail')
            elif 'suse' in id_str or 'suse' in like_str:
                return (name_str, 'sudo zypper install firejail')
            elif 'ubuntu' in id_str or 'debian' in id_str or 'mint' in id_str or 'pop' in id_str:
                return (name_str, 'sudo apt update && sudo apt install firejail')
            return (name_str, cmd)
        except Exception:
            pass
    return (os_name, cmd)


class MissingDependencyDialog(QDialog):
    """Warning modal shown only when Firejail sandboxing dependencies are missing."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sandboxing Dependencies Missing")
        self.setFixedSize(520, 320)

        if parent:
            p_geo = parent.geometry()
            self.move(
                p_geo.x() + (p_geo.width() - 520) // 2,
                p_geo.y() + (p_geo.height() - 320) // 2
            )

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        distro_name, install_cmd = detect_linux_distro()
        self.install_cmd = install_cmd

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(24, 24, 24, 24)
        root_layout.setSpacing(14)

        self.setStyleSheet("""
            QDialog {
                background-color: #141417;
                border: 2px solid #ef4444;
                border-radius: 14px;
            }
            QLabel {
                color: #ffffff;
            }
        """)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)

        icon_label = QLabel()
        icon_pix = QIcon.fromTheme("dialog-warning").pixmap(36, 36)
        if not icon_pix.isNull():
            icon_label.setPixmap(icon_pix)
        header_layout.addWidget(icon_label)

        title_label = QLabel("Sandboxing Dependencies Missing")
        title_label.setStyleSheet("color: #f87171; font-size: 18px; font-weight: bold;")
        header_layout.addWidget(title_label, 1)
        root_layout.addLayout(header_layout)

        body_text = (
            f"<b>Firejail</b> is not installed on your system (<b>{distro_name}</b>).<br>"
            "Without Firejail, the game will run in <b>direct unsandboxed mode</b>.<br><br>"
            "Quick install command for your system:"
        )
        body_label = QLabel(body_text)
        body_label.setWordWrap(True)
        body_label.setStyleSheet("color: #d4d4d8; font-size: 13px;")
        root_layout.addWidget(body_label)

        cmd_layout = QHBoxLayout()
        cmd_box = QLineEdit(install_cmd)
        cmd_box.setReadOnly(True)
        cmd_box.setStyleSheet("""
            QLineEdit {
                background-color: #09090b;
                color: #34d399;
                border: 1px solid #27272a;
                border-radius: 6px;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 12px;
                padding: 8px;
            }
        """)
        cmd_layout.addWidget(cmd_box)

        copy_btn = QPushButton("Copy")
        copy_icon = QIcon.fromTheme("edit-copy")
        if not copy_icon.isNull():
            copy_btn.setIcon(copy_icon)
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #ffffff;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 8px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3f3f46;
            }
        """)
        copy_btn.clicked.connect(self._copy_command)
        cmd_layout.addWidget(copy_btn)
        root_layout.addLayout(cmd_layout)

        root_layout.addStretch(1)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        cancel_btn = QPushButton("Cancel Launch")
        cancel_icon = QIcon.fromTheme("process-stop")
        if not cancel_icon.isNull():
            cancel_btn.setIcon(cancel_icon)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #a1a1aa;
                border: none;
                border-radius: 6px;
                padding: 10px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3f3f46;
                color: #ffffff;
            }
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        launch_btn = QPushButton("Launch Unsandboxed")
        run_icon = QIcon.fromTheme("system-run")
        if not run_icon.isNull():
            launch_btn.setIcon(run_icon)
        launch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        launch_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc2626;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 10px 18px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #b91c1c;
            }
        """)
        launch_btn.clicked.connect(self.accept)
        btn_layout.addWidget(launch_btn)

        root_layout.addLayout(btn_layout)

    def _copy_command(self):
        cb = QApplication.clipboard()
        if cb:
            cb.setText(self.install_cmd)


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
        
        icon_name = "library" if is_error else "shield"
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
