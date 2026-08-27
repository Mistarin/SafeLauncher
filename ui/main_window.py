import os
import re
import shutil
import subprocess
import threading
from datetime import datetime
from html import escape
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QGridLayout, QFileDialog, QMessageBox, QDialog, QLabel, QLineEdit,
    QComboBox, QFormLayout, QScrollArea, QFrame, QListWidget, QListWidgetItem, QMenu,
    QApplication, QSystemTrayIcon, QCheckBox, QGraphicsOpacityEffect, QPlainTextEdit, QProgressBar,
    QStackedWidget, QSlider, QSplitter, QDialogButtonBox, QInputDialog, QSizePolicy,
    QProgressDialog
)
from PyQt6.QtCore import (
    Qt, QSize, QPoint, pyqtSignal, QVariantAnimation, QEasingCurve, QTimer,
    QUrl, QSettings, QAbstractAnimation, QEvent,
)
from PyQt6.QtGui import QPixmap, QFont, QColor, QIcon, QPainter, QMovie, QDesktopServices
from core.interfaces import ISandboxRunner, IBackupManager
from core.steamgriddb_client import SteamGridDBClient
from core.playtime_tracker import PlaytimeTrackerThread
from core.steam_tags import SteamTagsFetcher
from core.steam_build_tracker import SteamBuildFetcher, read_local_steam_build
from core.disk_utils import format_size, get_disk_usage, peek_dir_size, has_fresh_dir_size
from core.discord_rpc import DiscordRPC
from core.host_process import host_process_env
from core.archive_extractor import (
    DEFAULT_SANDBOX_DIR, ensure_sandbox_dir,
    find_executables, save_sandbox_config, scan_sandbox_games
)
from core.archive_installer import ArchiveInstaller
from core.proton_manager import GEProtonDownloader
from database import GameDatabase, _APP_DATA_DIR
from core.logger import get_logger
from core.launch_diagnostics import persist_diagnostics
from core.library_state import LibrarySelectionModel
from ui.library_list import LibraryListView
from ui.icons import (
    LOGO_PATH, GIF_PATH, CONFIRM_GIF_PATH, draw_custom_lock_pixmap,
    get_app_icon, get_icon,
)
from ui.maintenance_dialogs import PrefixMaintenanceDialog

logger = get_logger("UI")

from ui.threads import (
    BannerFetcher, BannerDownloader, BannerAutoFetcher, ArchiveExtractorThread,
    GitHubReleasesFetcherThread, UmuBootstrapWorker, SafeLaunchLogReader,
    DiskSizeFetcherThread, HeroFetcherThread, IconAutoFetcherThread,
    CloudSaveStatusFetcherThread
)
from core.archive_installer import find_executables
from core.host_process import host_process_env
from core.plugins.gpu_screen_recorder import (
    GpuRecorderService, GpuRecorderConfig,
    WlScreenrecService, WlScreenrecConfig,
    DEFAULT_RECORDINGS_DIR
)
from core.global_hotkeys import GlobalHotkeyListener
from ui.components.overlay_hud import show_ingame_notification
from ui.components.banner_card import GameBannerWidget
from ui.components.responsive_grid import ResponsiveGridContainer
from ui.components.hero_background import HeroBackgroundWidget
from ui.components.sidebar import LeftSidebarWidget, CustomTitleBar, DialogTitleBar, add_soft_shadow
from ui.dialogs.proton_dialogs import ProtonSetupWizard, ProtonManagerDialog, UmuRuntimeManagerDialog
from ui.dialogs.game_dialogs import (
    AddGameDialog, EditGameDialog, LaunchOptionsDialog, SafeLaunchDialog,
    MissingDependencyDialog, ToastNotification, CustomRemoveDialog,
    ManageCollectionGamesDialog, CreateCollectionDialog, RenameCollectionDialog
)
from ui.dialogs.settings_dialog import UserSettingsDialog, ScreenshotGalleryDialog, VideoGalleryDialog, DiskManagerDialog
from ui.dialogs.game_properties_dialog import GamePropertiesDialog
from ui.dialogs.save_manager_dialog import SaveManagerDialog
from ui.dialogs.save_conflict_dialog import SaveConflictDialog
from core.cloud_save_sync import CloudSaveSyncEngine, SyncStatus
from ui.theme import (
    get_application_stylesheet, btn_primary_style, btn_secondary_style,
    btn_tertiary_style, btn_destructive_style, BG_APP, SURFACE, SURFACE_ELEVATED,
    BORDER, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, ACCENT_PRIMARY
)


import getpass
from core.playtime_tracker import PlaytimeTrackerThread, _shutdown_firejail_sandbox


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
            elif any(x in id_str or x in like_str for x in ['debian', 'ubuntu', 'mint', 'pop']):
                return (name_str, 'sudo apt install firejail')
            return (name_str, 'sudo apt install firejail')
        except Exception:
            pass
    return (os_name, cmd)


class MainWindow(QMainWindow):
    # Queued-signal carriers for save-sync work performed off the GUI thread.
    _save_op_done = pyqtSignal(object)      # exit-upload payload dict
    _prelaunch_resolved = pyqtSignal(object)  # pre-launch sync payload dict
    _startup_sync_done = pyqtSignal(object)   # startup cloud sync sweep payload dict

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
        self._pending_auto_fetchers = []  # queued so first-load art fetch doesn't storm APIs
        self.max_concurrent_auto_fetchers = 3
        self._auto_fetch_attempted = set()
        self.metadata_fetchers = []
        self.metadata_attempted_builds = set()
        self.update_status_by_game_id = {}
        self.steam_check_results = {}
        self.cloud_save_status_cache = {}
        self.local_version_by_game_id = {}
        self.metadata_attempted_tags = set()
        self._hero_attempted = set()
        self._icon_attempted = set()
        self.playtime_trackers = []  # keep references so GC doesn't kill running threads
        self.running_game_ids = set()
        self.topbar_extractor_thread = None
        self._size_fetch_scheduled = set()  # game dirs queued for background sizing
        self._size_resort_timer = QTimer(self)
        self._size_resort_timer.setSingleShot(True)
        self._size_resort_timer.setInterval(400)
        self._size_resort_timer.timeout.connect(self._refresh_library)
        self.games_by_id = {}
        self.library_selection = LibrarySelectionModel()

        self.search_query = ""
        self.settings = QSettings("SafeLauncher", "SafeLauncher")
        self.library_view_mode = self.settings.value("library_view_mode", "grid", type=str)
        default_user = getpass.getuser().capitalize()
        self.user_name = self.settings.value("user_name", default_user, type=str).strip() or default_user
        self.proton_path = self.settings.value("proton_path", "", type=str).strip()
        if hasattr(self.runner, "set_proton_path"):
            self.runner.set_proton_path(self.proton_path)
        self.current_filter = "all"
        self.collection_filter = ""
        self.current_sort = 0  # 0: A-Z, 1: Playtime, 2: Recently Added

        # Load Screenshot and GPU Recorder configuration
        self.screenshot_screen = self.settings.value("screenshot_target_screen", "current", type=str)
        self.screenshot_hotkey = self.settings.value("screenshot_hotkey", "F12", type=str)
        self.gpu_recorder_config = GpuRecorderConfig(
            enabled=self.settings.value("gpu_recorder_enabled", self.settings.value("wl_screenrec_enabled", False, type=bool), type=bool),
            mode=self.settings.value("gpu_recorder_mode", self.settings.value("wl_screenrec_mode", "manual", type=str), type=str),
            codec=self.settings.value("gpu_recorder_codec", self.settings.value("wl_screenrec_codec", "auto", type=str), type=str),
            bitrate=self.settings.value("gpu_recorder_bitrate", self.settings.value("wl_screenrec_bitrate", "12M", type=str), type=str),
            target_screen=self.settings.value("gpu_recorder_target_screen", "screen", type=str),
            audio=self.settings.value("gpu_recorder_audio", self.settings.value("wl_screenrec_audio", True, type=bool), type=bool),
            audio_device=self.settings.value("gpu_recorder_audio_device", self.settings.value("wl_screenrec_audio_device", "default_output", type=str), type=str),
            microphone_device=self.settings.value("gpu_recorder_microphone_device", "", type=str),
            history_seconds=self.settings.value("gpu_recorder_history", self.settings.value("wl_screenrec_history", 60, type=int), type=int),
            output_dir=self.settings.value("gpu_recorder_output_dir", self.settings.value("wl_screenrec_output_dir", DEFAULT_RECORDINGS_DIR, type=str), type=str),
            capture_hotkey=self.settings.value("gpu_recorder_capture_hotkey", self.settings.value("wl_screenrec_capture_hotkey", "F9", type=str), type=str),
            replay_hotkey=self.settings.value("gpu_recorder_capture_hotkey", self.settings.value("wl_screenrec_capture_hotkey", "F9", type=str), type=str),
            in_game_overlay=self.settings.value("gpu_recorder_in_game_overlay", self.settings.value("wl_screenrec_in_game_overlay", True, type=bool), type=bool),
        )
        self.wl_recorder_config = self.gpu_recorder_config
        GpuRecorderService.instance().config = self.gpu_recorder_config

        # Initialize background global hotkey daemon for in-game shortcuts
        self.global_hotkeys = GlobalHotkeyListener(self)
        self._update_global_hotkeys()
        self.global_hotkeys.hotkey_triggered.connect(self._on_global_hotkey)
        self.global_hotkeys.start()

        self.setWindowTitle("SafeLauncher - Game Sandbox Manager")
        self.resize(1180, 750)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        
        if os.path.exists(LOGO_PATH):
            self.setWindowIcon(QIcon(LOGO_PATH))

        # Root Layout: Hero Background Canvas + Top Title Bar + Body (Left Sidebar + Center Grid & Right Inspector Splitter)
        self.hero_bg = HeroBackgroundWidget(self)
        self.setCentralWidget(self.hero_bg)
        
        self.setMouseTracking(True)
        self.hero_bg.setMouseTracking(True)
        self.installEventFilter(self)
        self.hero_bg.installEventFilter(self)
        
        root_vbox = QVBoxLayout(self.hero_bg)
        root_vbox.setContentsMargins(0, 0, 0, 0)
        root_vbox.setSpacing(0)
        
        # Top Custom Draggable Title Bar with Tools Dropdown and Search Bar
        self.title_bar = CustomTitleBar(self)
        root_vbox.addWidget(self.title_bar)
        self.title_bar.search_changed.connect(self._on_search_query_changed)
        self.title_bar.sync_requested.connect(self._on_sync_sandbox)
        self.title_bar.install_archive_requested.connect(self._on_install_zip_archive)
        self.title_bar.check_updates_requested.connect(self._check_all_steam_updates)
        self.title_bar.open_sandbox_requested.connect(self._open_sandbox_dir)
        self.title_bar.export_save_requested.connect(self._on_export)
        self.title_bar.import_save_requested.connect(self._on_import)
        self.title_bar.disk_manager_requested.connect(self._open_disk_manager)
        
        # Body Container Layout (Left Sidebar + Center/Right Splitter)
        body_widget = QWidget()
        body_layout = QHBoxLayout(body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        root_vbox.addWidget(body_widget)

        # 1. Left Navigation Sidebar
        self.sidebar = LeftSidebarWidget(self)
        body_layout.addWidget(self.sidebar)
        self.sidebar.set_compact(self.settings.value("sidebar_compact", False, type=bool))
        self.sidebar.compact_changed.connect(
            lambda compact: self.settings.setValue("sidebar_compact", compact)
        )
        self.sidebar.filter_selected.connect(self._set_filter)
        self.sidebar.collection_selected.connect(self._set_collection_filter)
        self.sidebar.add_collection_requested.connect(self._on_add_collection)
        self.sidebar.size_changed.connect(self._on_card_size_changed)
        self.sidebar.btn_settings.clicked.connect(self._open_settings)
        self.stat_label = QLabel()  # Keep hidden logic variable for tests

        # 2. Splitter Layout: Center Game Grid + Right Game Details Inspector Panel
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setStyleSheet("""
            QSplitter::handle {
                background: transparent;
                width: 0px;
            }
        """)
        body_layout.addWidget(self.splitter)

        # -------------------------------------------------------------
        # Right Game Detail Panel (Inspector)
        # -------------------------------------------------------------
        self.detail_panel = QFrame()
        self.detail_panel.setMinimumWidth(240)
        self.detail_panel.setMaximumWidth(550)
        self.detail_panel.setStyleSheet("""
            QFrame {
                background: #14171D;
                border: none;
            }
            QLabel {
                color: #F5F7FA;
            }
        """)

        self.detail_panel.setVisible(False)

        # Inspector panel slide & fade animation setup
        self.panel_opacity_effect = QGraphicsOpacityEffect(self.detail_panel)
        self.detail_panel.setGraphicsEffect(self.panel_opacity_effect)
        self.panel_opacity_effect.setOpacity(0.0)

        self.panel_anim = QVariantAnimation(self)
        self.panel_anim.setDuration(250)
        self.panel_anim.valueChanged.connect(self._on_panel_anim_step)
        self.panel_anim.finished.connect(self._on_panel_anim_finished)
        self._panel_expanding = False

        detail_layout = QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(18, 12, 18, 18)
        detail_layout.setSpacing(8)
        detail_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Top Bar with Close button
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 4)
        top_bar.addStretch()

        self.btn_hide_detail = QPushButton("✕")
        self.btn_hide_detail.setFixedSize(24, 24)
        self.btn_hide_detail.setToolTip("Close details panel")
        self.btn_hide_detail.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #6F7682;
                font-size: 11px;
                font-weight: bold;
                border: 1px solid transparent;
                border-radius: 4px;
                padding: 0;
                text-align: center;
            }
            QPushButton:hover {
                color: #F5F7FA;
                background: #1A1E26;
                border-color: #252A33;
            }
        """)
        self.btn_hide_detail.clicked.connect(lambda: self._animate_left_panel(False))
        top_bar.addWidget(self.btn_hide_detail)
        detail_layout.addLayout(top_bar)

        # Selected Game Cover Art Preview
        self.detail_cover = QLabel()
        self.detail_cover.setFixedSize(QSize(180, 270))
        self.detail_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_cover.setStyleSheet("border: 1px solid #252A33; border-radius: 8px; background: #14171D;")
        
        cover_row = QHBoxLayout()
        cover_row.setContentsMargins(0, 0, 0, 0)
        cover_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_row.addWidget(self.detail_cover)
        detail_layout.addLayout(cover_row)

        # Selected Game Title Header Row
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)

        self.detail_title = QLabel("Select a Game")
        self.detail_title.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        self.detail_title.setWordWrap(True)
        self.detail_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_title.setStyleSheet("color: #F5F7FA; background: transparent;")
        title_row.addWidget(self.detail_title, 1)

        detail_layout.addLayout(title_row)

        # Steam Tags Badge Container
        self.tags_widget = QWidget()
        self.tags_layout = QHBoxLayout(self.tags_widget)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setSpacing(6)
        self.tags_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail_layout.addWidget(self.tags_widget)

        # Selected Game Playtime
        self.detail_playtime = QLabel("")
        self.detail_playtime.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_playtime.setStyleSheet("color: #A7ADB8; font-size: 11px; font-weight: 500;")
        detail_layout.addWidget(self.detail_playtime)

        # Selected Game Last Played
        self.detail_last_played = QLabel("")
        self.detail_last_played.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_last_played.setStyleSheet("""
            QLabel {
                color: #A7ADB8;
                background: #1A1E26;
                border: 1px solid #252A33;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 11px;
                font-weight: 500;
            }
        """)
        detail_layout.addWidget(self.detail_last_played)

        # Selected Game Disk Size
        self.detail_disk_size = QLabel("")
        self.detail_disk_size.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_disk_size.setStyleSheet("color: #6F7682; font-size: 11px; font-weight: 500; padding: 2px 0;")
        detail_layout.addWidget(self.detail_disk_size)

        # Selected Game Cloud Save Status
        self.detail_cloud_status = QLabel("")
        self.detail_cloud_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_cloud_status.setStyleSheet("color: #6F7682; font-size: 11px; font-weight: 500; padding: 2px 0;")
        detail_layout.addWidget(self.detail_cloud_status)

        # Steam update status and version details
        self.detail_update_widget = QWidget()
        self.detail_update_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.detail_update_layout = QVBoxLayout(self.detail_update_widget)
        self.detail_update_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_update_layout.setSpacing(4)
        self.detail_update_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_detail_update = QLabel("")
        self.lbl_detail_update.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_detail_update.setFixedHeight(22)
        self.detail_update_layout.addWidget(self.lbl_detail_update)

        self.lbl_detail_versions = QLabel("")
        self.lbl_detail_versions.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_detail_versions.setWordWrap(True)
        self.lbl_detail_versions.setOpenExternalLinks(True)
        self.lbl_detail_versions.setStyleSheet("QLabel { color: #A7ADB8; background: #1A1E26; border: 1px solid #252A33; border-radius: 6px; font-size: 10px; padding: 3px 8px; }")
        self.detail_update_layout.addWidget(self.lbl_detail_versions)

        self.btn_retry_steam = QPushButton("Retry")
        self.btn_retry_steam.setVisible(False)
        self.btn_retry_steam.setToolTip("Retry the Steam build check")
        self.btn_retry_steam.clicked.connect(self._retry_steam_check)
        self.detail_update_layout.addWidget(self.btn_retry_steam)

        self.detail_update_widget.setVisible(False)
        detail_layout.addWidget(self.detail_update_widget)

        # Primary Launch Game Button
        detail_layout.addSpacing(6)
        self.btn_detail_launch = QPushButton("Launch Game")
        self.btn_detail_launch.setObjectName("detailLaunch")
        self.btn_detail_launch.setIcon(get_icon("ph.play-bold", color="#FFFFFF"))
        self.btn_detail_launch.setIconSize(QSize(15, 15))
        self.btn_detail_launch.setFixedHeight(40)
        self.btn_detail_launch.setStyleSheet("""
            QPushButton#detailLaunch {
                background-color: #3B9FE8;
                color: #FFFFFF;
                font-weight: 600;
                font-size: 12px;
                border: 1px solid #3B9FE8;
                border-radius: 6px;
                padding: 0 16px;
                text-align: center;
                letter-spacing: 0.2px;
            }
            QPushButton#detailLaunch:hover {
                background-color: #55ACED;
                border-color: #55ACED;
            }
            QPushButton#detailLaunch:pressed {
                background-color: #2789D0;
            }
        """)
        self.btn_detail_launch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_detail_launch.clicked.connect(self._on_launch)
        detail_layout.addWidget(self.btn_detail_launch)

        sec_btn_style = """
            QPushButton {
                background-color: #1A1E26;
                color: #F5F7FA;
                border: 1px solid #252A33;
                border-radius: 6px;
                padding: 0 14px;
                font-weight: 500;
                font-size: 12px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #252A33;
                border-color: #6F7682;
            }
            QPushButton:pressed {
                background-color: #14171D;
            }
        """

        # Secondary Action Buttons
        self.btn_detail_edit = QPushButton("Edit Game")
        self.btn_detail_edit.setIcon(get_icon("ph.pencil-simple-bold", color="#3B9FE8"))
        self.btn_detail_edit.setIconSize(QSize(15, 15))
        self.btn_detail_edit.setFixedHeight(32)
        self.btn_detail_edit.setStyleSheet(sec_btn_style)
        self.btn_detail_edit.clicked.connect(self._on_edit)
        detail_layout.addWidget(self.btn_detail_edit)

        self.btn_detail_screenshots = QPushButton("Screenshots")
        self.btn_detail_screenshots.setIcon(get_icon("ph.image-bold", color="#A7ADB8"))
        self.btn_detail_screenshots.setIconSize(QSize(15, 15))
        self.btn_detail_screenshots.setFixedHeight(32)
        self.btn_detail_screenshots.setStyleSheet(sec_btn_style)
        self.btn_detail_screenshots.clicked.connect(self._open_screenshot_gallery)
        detail_layout.addWidget(self.btn_detail_screenshots)

        self.btn_detail_videos = QPushButton("Videos")
        self.btn_detail_videos.setIcon(get_icon("ph.video-camera-bold", color="#A7ADB8"))
        self.btn_detail_videos.setIconSize(QSize(15, 15))
        self.btn_detail_videos.setFixedHeight(32)
        self.btn_detail_videos.setStyleSheet(sec_btn_style)
        self.btn_detail_videos.clicked.connect(self._open_video_gallery)
        detail_layout.addWidget(self.btn_detail_videos)

        self.btn_detail_properties = QPushButton("Properties")
        self.btn_detail_properties.setIcon(get_icon("ph.sliders-horizontal-bold", color="#A7ADB8"))
        self.btn_detail_properties.setIconSize(QSize(15, 15))
        self.btn_detail_properties.setFixedHeight(32)
        self.btn_detail_properties.setStyleSheet(sec_btn_style)
        self.btn_detail_properties.clicked.connect(self._open_game_properties)
        detail_layout.addWidget(self.btn_detail_properties)

        self.btn_detail_remove = QPushButton("Remove Game")
        self.btn_detail_remove.setIcon(get_icon("ph.trash-bold", color="#F05D6C"))
        self.btn_detail_remove.setIconSize(QSize(15, 15))
        self.btn_detail_remove.setFixedHeight(32)
        self.btn_detail_remove.setStyleSheet("""
            QPushButton {
                background-color: rgba(240, 93, 108, 0.08);
                color: #F05D6C;
                border: 1px solid rgba(240, 93, 108, 0.25);
                border-radius: 6px;
                padding: 0 14px;
                font-weight: 500;
                font-size: 12px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: rgba(240, 93, 108, 0.16);
                border-color: #F05D6C;
                color: #FFFFFF;
            }
            QPushButton:pressed {
                background-color: rgba(240, 93, 108, 0.24);
            }
        """)
        self.btn_detail_remove.clicked.connect(self._on_remove)
        detail_layout.addWidget(self.btn_detail_remove)

        detail_layout.addStretch()

        # Center Main Game Library Area
        self.right_panel = QWidget()
        self.right_panel.setObjectName("libraryCentralPanel")
        self.right_panel.setStyleSheet("QWidget#libraryCentralPanel { background: transparent; }")
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(18, 14, 18, 14)
        right_layout.setSpacing(12)

        # Add center game grid first, right detail panel second
        self.splitter.addWidget(self.right_panel)
        self.splitter.addWidget(self.detail_panel)

        saved_right_w = self.settings.value("right_inspector_width", 300, type=int)
        self.splitter.setSizes([880, saved_right_w])
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

        # Header Title with Sorting, View Toggle, and Inspector Reveal Button
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)
        
        # Header Title with Sorting, View Toggle, and Inspector Reveal Button
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)
        
        header_title = QLabel("Game Library")
        header_title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        header_title.setStyleSheet(f"color: {TEXT_PRIMARY}; background: transparent;")
        header_layout.addWidget(header_title)
        header_layout.addStretch()

        # Sorting ComboBox
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Sort: A–Z Title", "Sort: Most Played", "Sort: Recently Added", "Sort: Disk Size", "Sort: Runner"])
        self.sort_combo.setFixedHeight(30)
        self.sort_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {SURFACE};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 0 10px;
                font-size: 11px;
                font-weight: 500;
            }}
            QComboBox:hover {{
                border-color: {TEXT_MUTED};
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background-color: {SURFACE_ELEVATED};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                selection-background-color: {BORDER};
            }}
        """)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        header_layout.addWidget(self.sort_combo)

        self.btn_view_toggle = QPushButton("☷ List" if self.library_view_mode == "grid" else "▦ Grid")
        self.btn_view_toggle.setToolTip("Toggle grid/list library view")
        self.btn_view_toggle.clicked.connect(self._toggle_library_view)
        self.btn_view_toggle.setFixedHeight(30)
        self.btn_view_toggle.setStyleSheet(btn_secondary_style())
        header_layout.addWidget(self.btn_view_toggle)
        right_layout.addLayout(header_layout)

        # ── Rich Collection Header Banner (Shown when inside a Collection) ──
        self.collection_banner = QFrame(self.right_panel)
        self.collection_banner.setVisible(False)
        self.collection_banner.setStyleSheet(f"""
            QFrame {{
                background: {SURFACE};
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
        """)
        cb_layout = QHBoxLayout(self.collection_banner)
        cb_layout.setContentsMargins(14, 10, 14, 10)
        cb_layout.setSpacing(14)

        col_icon_lbl = QLabel()
        col_icon_lbl.setPixmap(get_icon("ph.folder-open-bold", color=ACCENT_PRIMARY).pixmap(24, 24))
        cb_layout.addWidget(col_icon_lbl)

        col_text_layout = QVBoxLayout()
        col_text_layout.setSpacing(2)
        self.lbl_col_banner_title = QLabel("Collection")
        self.lbl_col_banner_title.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        self.lbl_col_banner_title.setStyleSheet(f"color: {TEXT_PRIMARY}; background: transparent;")
        col_text_layout.addWidget(self.lbl_col_banner_title)

        self.lbl_col_banner_stats = QLabel("0 Games  •  0.0 hrs Total Playtime")
        self.lbl_col_banner_stats.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px; font-weight: 500; background: transparent;")
        col_text_layout.addWidget(self.lbl_col_banner_stats)
        cb_layout.addLayout(col_text_layout)

        cb_layout.addStretch()

        btn_col_manage = QPushButton("Manage Games")
        btn_col_manage.setIcon(get_icon("ph.plus-bold", color="#FFFFFF"))
        btn_col_manage.setIconSize(QSize(13, 13))
        btn_col_manage.setFixedHeight(28)
        btn_col_manage.setStyleSheet(btn_primary_style())
        btn_col_manage.clicked.connect(self._manage_current_collection_games)
        cb_layout.addWidget(btn_col_manage)

        btn_col_rename = QPushButton("Rename")
        btn_col_rename.setIcon(get_icon("ph.pencil-simple-bold", color=TEXT_SECONDARY))
        btn_col_rename.setIconSize(QSize(13, 13))
        btn_col_rename.setFixedHeight(28)
        btn_col_rename.setStyleSheet(btn_secondary_style())
        btn_col_rename.clicked.connect(self._rename_current_collection)
        cb_layout.addWidget(btn_col_rename)

        btn_col_delete = QPushButton("Delete")
        btn_col_delete.setIcon(get_icon("ph.trash-bold", color="#F05D6C"))
        btn_col_delete.setIconSize(QSize(13, 13))
        btn_col_delete.setFixedHeight(28)
        btn_col_delete.setStyleSheet(btn_destructive_style())
        btn_col_delete.clicked.connect(self._delete_current_collection)
        cb_layout.addWidget(btn_col_delete)

        btn_col_close = QPushButton("✕")
        btn_col_close.setFixedSize(24, 24)
        btn_col_close.setToolTip("Exit collection view")
        btn_col_close.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {TEXT_MUTED};
                border: 1px solid transparent;
                border-radius: 4px;
                font-size: 11px;
                font-weight: bold;
                padding: 0;
                text-align: center;
            }}
            QPushButton:hover {{
                color: {TEXT_PRIMARY};
                background: {SURFACE_ELEVATED};
                border-color: {BORDER};
            }}
        """)
        btn_col_close.clicked.connect(lambda: self._set_collection_filter(""))
        cb_layout.addWidget(btn_col_close)

        right_layout.addWidget(self.collection_banner)

        # Games Grid in Scroll Area
        self.scroll_area = QScrollArea(self.right_panel)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea, QWidget#qt_scrollarea_viewport { background: transparent; border: none; }")
        
        # Dynamic Responsive Grid Container (2:3 portrait cards, default width 200px)
        self.library_view_stack = QStackedWidget(self.scroll_area)
        self.grid_container = ResponsiveGridContainer(self.library_view_stack, card_width=200, spacing=15)
        self.grid_container.setStyleSheet("background: transparent;")
        self.list_view = LibraryListView(self.library_view_stack)
        self.list_view.game_clicked.connect(self._select_game_by_id)
        self.list_view.game_double_clicked.connect(self._on_double_click_game)
        self.list_view.game_launch_clicked.connect(self._launch_game_by_id)
        self.library_view_stack.addWidget(self.grid_container)
        self.library_view_stack.addWidget(self.list_view)
        self.library_view_stack.setCurrentIndex(1 if self.library_view_mode == "list" else 0)
        self.scroll_area.setWidget(self.library_view_stack)
        right_layout.addWidget(self.scroll_area)

        # Action Buttons Layout (Add Game on bottom-left)
        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 4, 0, 0)
        action_layout.setSpacing(12)
        
        self.btn_add = QPushButton("Add Game")
        self.btn_add.setObjectName("addGameButton")
        self.btn_add.setIcon(get_app_icon("add"))
        self.btn_add.clicked.connect(self._on_add)
        self.btn_add.setFixedHeight(34)
        self.btn_add.setStyleSheet(btn_primary_style())
        self.btn_add.setIconSize(QSize(15, 15))
        action_layout.addWidget(self.btn_add)

        action_layout.addStretch()

        # Details Button for opening the right panel (placed down in bottom bar)
        self.btn_reveal_detail = QPushButton(" Details")
        self.btn_reveal_detail.setIcon(get_icon("ph.caret-double-left-bold", color=TEXT_SECONDARY))
        self.btn_reveal_detail.setIconSize(QSize(14, 14))
        self.btn_reveal_detail.setToolTip("Open game details panel")
        self.btn_reveal_detail.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reveal_detail.setFixedHeight(34)
        self.btn_reveal_detail.setStyleSheet(btn_secondary_style())
        self.btn_reveal_detail.clicked.connect(lambda: self._animate_left_panel(True))
        action_layout.addWidget(self.btn_reveal_detail)

        right_layout.addLayout(action_layout)
        
        self.setStyleSheet(get_application_stylesheet())
        
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
        QTimer.singleShot(300, self._check_all_steam_updates)
        QTimer.singleShot(800, self._start_background_cloud_sync)

        show_wizard = self.settings.value("show_welcome_wizard", True, type=bool)
        if show_wizard:
            QTimer.singleShot(150, self._show_welcome_wizard)

    def _show_welcome_wizard(self):
        from ui.dialogs.welcome_wizard import WelcomeWizardDialog
        wizard = WelcomeWizardDialog(self.user_name, self.proton_path, self)
        if wizard.exec() == QDialog.DialogCode.Accepted:
            self.user_name = wizard.get_user_name()
            self.proton_path = wizard.get_proton_path()
            self.settings.setValue("user_name", self.user_name)
            self.settings.setValue("proton_path", self.proton_path)
            self.settings.setValue("show_welcome_wizard", wizard.should_show_on_startup())
            if hasattr(self.runner, "set_proton_path"):
                self.runner.set_proton_path(self.proton_path)

    def _toggle_maximize(self):
        """Toggle between maximized state and normal window size"""
        if self.isMaximized():
            self.showNormal()
            if hasattr(self, 'title_bar'):
                restore_icon = get_app_icon("maximize", color="#f4f4f5")
                self.title_bar.btn_max.setIcon(restore_icon)
                self.title_bar.btn_max.setText("[]" if restore_icon.isNull() else "")
                self.title_bar.btn_max.setToolTip("Maximize window")
        else:
            self.showMaximized()
            if hasattr(self, 'title_bar'):
                restore_icon = get_app_icon("restore", color="#f4f4f5")
                self.title_bar.btn_max.setIcon(restore_icon)
                self.title_bar.btn_max.setText("=" if restore_icon.isNull() else "")
                self.title_bar.btn_max.setToolTip("Restore window")

    def _open_settings(self):
        """Open launcher preferences and persist profile changes."""
        show_wizard = self.settings.value("show_welcome_wizard", True, type=bool)
        cloud_dir = self.settings.value("cloud_saves_dir", "", type=str)
        dialog = UserSettingsDialog(
            self.user_name,
            self.proton_path,
            show_welcome_wizard=show_wizard,
            gpu_config=self.gpu_recorder_config,
            screenshot_screen=self.screenshot_screen,
            screenshot_hotkey=self.screenshot_hotkey,
            cloud_saves_dir=cloud_dir,
            parent=self
        )
        dialog.runtime_manager_requested.connect(self._open_runtime_manager)
        dialog.proton_manager_requested.connect(self._open_proton_manager)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.user_name = dialog.get_user_name()
            self.proton_path = dialog.get_proton_path()
            self.settings.setValue("user_name", self.user_name)
            self.settings.setValue("proton_path", self.proton_path)
            self.settings.setValue("show_welcome_wizard", dialog.get_show_welcome_wizard())
            self.settings.setValue("cloud_saves_dir", dialog.get_cloud_saves_dir())
            if hasattr(self.runner, "set_proton_path"):
                self.runner.set_proton_path(self.proton_path)

            self.screenshot_screen = dialog.get_screenshot_target_screen()
            self.settings.setValue("screenshot_target_screen", self.screenshot_screen)

            self.screenshot_hotkey = dialog.get_screenshot_hotkey()
            self.settings.setValue("screenshot_hotkey", self.screenshot_hotkey)

            # Save GPU screen recorder addon preferences
            self.gpu_recorder_config = dialog.get_gpu_recorder_config()
            self.wl_recorder_config = self.gpu_recorder_config
            self.settings.setValue("gpu_recorder_enabled", self.gpu_recorder_config.enabled)
            self.settings.setValue("gpu_recorder_mode", self.gpu_recorder_config.mode)
            self.settings.setValue("gpu_recorder_codec", self.gpu_recorder_config.codec)
            self.settings.setValue("gpu_recorder_bitrate", self.gpu_recorder_config.bitrate)
            self.settings.setValue("gpu_recorder_target_screen", self.gpu_recorder_config.target_screen)
            self.settings.setValue("gpu_recorder_audio", self.gpu_recorder_config.audio)
            self.settings.setValue("gpu_recorder_audio_device", self.gpu_recorder_config.audio_device)
            self.settings.setValue("gpu_recorder_microphone_device", self.gpu_recorder_config.microphone_device)
            self.settings.setValue("gpu_recorder_history", self.gpu_recorder_config.history_seconds)
            self.settings.setValue("gpu_recorder_output_dir", self.gpu_recorder_config.output_dir)
            self.settings.setValue("gpu_recorder_capture_hotkey", self.gpu_recorder_config.capture_hotkey)
            self.settings.setValue("gpu_recorder_replay_hotkey", self.gpu_recorder_config.replay_hotkey)
            self.settings.setValue("gpu_recorder_in_game_overlay", self.gpu_recorder_config.in_game_overlay)
            GpuRecorderService.instance().config = self.gpu_recorder_config
            self._update_global_hotkeys()
            self._refresh_record_button_state()

            self._show_toast(f"Display name changed to {self.user_name}.")

    def _open_proton_manager(self):
        if isinstance(self.selected_game, tuple):
            game_name = self.selected_game[1] if len(self.selected_game) > 1 else str(self.selected_game[0])
        elif hasattr(self.selected_game, 'name'):
            game_name = self.selected_game.name
        elif self.selected_game:
            game_name = str(self.selected_game)
        else:
            game_name = ""
        manager = ProtonManagerDialog(self.proton_path, game_name, self)
        manager.proton_selected.connect(self._set_global_proton_path)
        manager.apply_to_game_requested.connect(self._apply_proton_to_selected_game)
        manager.exec()

    def _set_global_proton_path(self, proton_path: str):
        self.proton_path = proton_path
        self.settings.setValue("proton_path", self.proton_path)
        if hasattr(self.runner, "set_proton_path"):
            self.runner.set_proton_path(self.proton_path)
        display_name = os.path.basename(proton_path) if proton_path else "UMU Auto"
        self._show_toast(f"Global default Proton set to: {display_name}")

    def _apply_proton_to_selected_game(self, proton_path: str):
        if not self.selected_game:
            return
        game_id = self.selected_game.id if hasattr(self.selected_game, 'id') else self.selected_game[0]
        self.db.update_game_proton_path(game_id, proton_path)
        self._show_toast(f"Applied {os.path.basename(proton_path)} to '{self.selected_game.name}'.")

    def _open_runtime_manager(self):
        manager = UmuRuntimeManagerDialog(self.proton_path, self)
        manager.proton_path_selected.connect(self._set_global_proton_path)
        manager.exec()

    def _set_proton_path(self, proton_path: str):
        self._set_global_proton_path(proton_path)

    def _setup_tray_icon(self):
        """Setup system tray icon with quick launch context menu for favorites and recently played games."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray_icon = QSystemTrayIcon(self)
        if os.path.exists(LOGO_PATH):
            self.tray_icon.setIcon(QIcon(LOGO_PATH))
        else:
            self.tray_icon.setIcon(get_app_icon("library"))

        self.tray_icon.setToolTip("SafeLauncher - Game Sandbox Manager")
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
        act_show = self.tray_menu.addAction(get_app_icon("library"), "Open SafeLauncher Library")
        act_show.triggered.connect(self._show_and_raise)

        self.tray_menu.addSeparator()

        # Quick Launch Section: Favorites
        fav_games = [g for g in self.games if len(g) > 8 and g[8]]
        if fav_games:
            lbl_fav = self.tray_menu.addAction(get_app_icon("favorite"), "Favorites Quick Launch")
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
            lbl_rec = self.tray_menu.addAction(get_icon("ph.clock-bold", color="#A7ADB8"), "Recently Played")
            lbl_rec.setEnabled(False)
            for g in rec_games[:5]:
                game_id, name, path, exe, mode = g[0], g[1], g[2], g[3], g[4]
                act = self.tray_menu.addAction(get_app_icon("launch"), f"  Launch {name}")
                act.triggered.connect(lambda _, gid=game_id, p=path, e=exe, m=mode: self._launch_mode(gid, p, e, m or "umu"))
            self.tray_menu.addSeparator()

        # Disk Manager option
        act_disk = self.tray_menu.addAction(get_app_icon("search"), "Disk Space Manager")
        act_disk.triggered.connect(self._open_disk_manager)

        self.tray_menu.addSeparator()

        # Quit through closeEvent so all background workers, trackers, RPC and
        # hotkey grabs are shut down cleanly instead of being destroyed mid-run.
        act_quit = self.tray_menu.addAction(get_app_icon("close"), "Quit SafeLauncher")
        act_quit.triggered.connect(self.close)

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
        """Open ~/Games/Sandbox in the system file manager."""
        import subprocess
        path = ensure_sandbox_dir()

        # Qt handles desktop portals and desktop environments more reliably
        # than launching xdg-open directly, especially from an AppImage.
        if QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            return

        # Keep a few command-line fallbacks for minimal Linux installations.
        for opener in ("xdg-open", "gio", "kde-open5", "exo-open"):
            if not shutil.which(opener):
                continue
            command = [opener, "open", path] if opener == "gio" else [opener, path]
            try:
                subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    env=host_process_env(),
                )
                return
            except OSError:
                continue

        QMessageBox.information(
            self,
            "Sandbox Path",
            f"Could not open a file manager automatically.\n\nSandbox directory:\n{path}",
        )

    def _on_install_zip_archive(self):
        """Install game by picking a zip/7z archive directly from the top bar."""
        zip_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Game Archive",
            "",
            "Archive Files (*.zip *.7z *.tar.gz *.tgz)"
        )
        if not zip_path or not os.path.exists(zip_path):
            return

        archive_name = os.path.splitext(os.path.basename(zip_path))[0]
        sandbox_dir = ensure_sandbox_dir()
        dest_dir = os.path.join(sandbox_dir, archive_name)

        try:
            inspection = ArchiveInstaller().inspect(zip_path, sandbox_dir)
            if not inspection.enough_space:
                QMessageBox.warning(self, "Not enough disk space", f"The archive needs about {inspection.required_bytes / (1024**3):.2f} GB, but only {inspection.free_bytes / (1024**3):.2f} GB is free.")
                return
            if inspection.duplicate_path and QMessageBox.question(self, "Duplicate installation", f"{inspection.duplicate_path} already exists. Extract over it?") != QMessageBox.StandardButton.Yes:
                return
        except Exception as error:
            QMessageBox.critical(self, "Archive preflight failed", str(error))
            return

        # Reuse the archive worker used by AddGameDialog.  The old class name
        # here was never defined and caused a NameError when using the top-bar
        # install button.
        thread = ArchiveExtractorThread(zip_path, dest_dir)
        thread.extraction_complete.connect(self._on_topbar_extraction_complete)
        self._show_toast(f"Extracting '{archive_name}' in background...")
        thread.start()
        self.topbar_extractor_thread = thread

    def _on_topbar_extraction_complete(self, game_name: str, dest_dir: str, success: bool):
        """Callback when topbar archive extraction completes"""
        if not success:
            self._show_toast(f"Failed to extract '{game_name}'.", is_error=True)
            return

        self._show_toast(f"Extracted '{game_name}' successfully.")
        exes = find_executables(dest_dir)
        default_exe = exes[0] if exes else ""

        dialog = AddGameDialog(self, self.sgdb_client)
        dialog.name_input.setText(game_name)
        dialog.path_input.setText(dest_dir)
        dialog._scan_and_populate_exes(dest_dir)
        if default_exe:
            default_idx = dialog.exe_combo.findData(default_exe)
            if default_idx >= 0:
                dialog.exe_combo.setCurrentIndex(default_idx)
            else:
                dialog.exe_combo.setCurrentIndex(-1)
                dialog.exe_combo.setEditText(default_exe)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, path, exe, mode, banner_path = dialog.get_values()
            if name and path and exe:
                save_sandbox_config(path, exe)
                self.db.add_game(name, path, exe, mode, banner_path)
                self._refresh_library()
                self._show_toast(f"Game '{name}' added to library.")

    def _set_filter(self, filter_mode: str):
        """Set active filter mode (all, installed, favorites, archived) and refresh view."""
        self.current_filter = filter_mode
        self.collection_filter = ""
        self._refresh_library()

    def _set_collection_filter(self, col_name: str):
        """Filter library to a specific collection and update banner."""
        self.collection_filter = col_name.strip()
        self.current_filter = "" if self.collection_filter else "all"
        self._refresh_library()

    def _on_add_collection(self):
        """Prompt to create a new collection with custom styled modal."""
        dlg = CreateCollectionDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            col_name = dlg.get_collection_name()
            if col_name:
                self.db.add_collection(col_name)
                self._set_collection_filter(col_name)
                self._show_toast(f"Created collection '{col_name}'.")

    def _manage_current_collection_games(self):
        """Open modal to select games belonging to current collection."""
        if not self.collection_filter:
            return
        active_games = [g for g in self.games if not (len(g) > 17 and g[17])]
        current_members = {g[0] for g in active_games if len(g) > 13 and str(g[13]).strip() == self.collection_filter}
        dlg = ManageCollectionGamesDialog(self.collection_filter, active_games, current_members, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_members = dlg.get_selected_game_ids()
            for g in active_games:
                g_id = g[0]
                if g_id in new_members:
                    self.db.update_game_collection(g_id, self.collection_filter)
                elif len(g) > 13 and str(g[13]).strip() == self.collection_filter:
                    self.db.update_game_collection(g_id, "")
            self._show_toast(f"Updated collection '{self.collection_filter}'.")
            self._refresh_library()

    def _rename_current_collection(self):
        """Rename active collection across all games with custom styled modal."""
        if not self.collection_filter:
            return
        dlg = RenameCollectionDialog(self.collection_filter, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_col = dlg.get_collection_name()
            if new_col and new_col != self.collection_filter:
                self.db.rename_collection(self.collection_filter, new_col)
                self._set_collection_filter(new_col)
                self._show_toast(f"Renamed collection to '{new_col}'.")

    def _delete_current_collection(self):
        """Delete active collection tag from all games."""
        if not self.collection_filter:
            return
        reply = QMessageBox.question(
            self,
            "Delete Collection",
            f"Are you sure you want to delete collection '{self.collection_filter}'?\n(Games will remain in your library)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_collection(self.collection_filter)
            self._set_collection_filter("")
            self._show_toast("Collection deleted.")

    def _choose_collection_filter(self):
        collections = sorted({str(game[13]).strip() for game in self.games if len(game) > 13 and str(game[13]).strip()})
        choices = ["All collections"] + collections
        choice, accepted = QInputDialog.getItem(self, "Filter collection", "Collection:", choices, 0, False)
        if not accepted:
            return
        self._set_collection_filter("" if choice == "All collections" else choice)

    def _on_sort_changed(self, idx: int):
        """Sort games list by title, activity, install date, size, or runner."""
        self.current_sort = idx
        self._refresh_library()

    def _on_search_query_changed(self, query: str):
        """Filter games real-time as user types in top search box"""
        self.search_query = query.strip().lower()
        self._refresh_library()

    def _toggle_library_view(self):
        self.library_view_mode = "list" if self.library_view_mode == "grid" else "grid"
        self.settings.setValue("library_view_mode", self.library_view_mode)
        self.library_view_stack.setCurrentIndex(1 if self.library_view_mode == "list" else 0)
        self.btn_view_toggle.setText("▦ Grid" if self.library_view_mode == "list" else "☷ List")

    def _visible_library_ids(self) -> set[int]:
        if self.library_view_mode == "list":
            return {int(self.list_view.item(index).data(Qt.ItemDataRole.UserRole)) for index in range(self.list_view.count())}
        return set(self.banner_widgets.keys())

    def _select_all_visible(self):
        self.library_selection.replace(self._visible_library_ids())
        self._refresh_library()

    def _clear_library_selection(self):
        self.library_selection.clear()
        self._refresh_library()

    def _assign_selected_collection(self):
        selected = self.library_selection.ids
        if not selected:
            self._show_toast("Select one or more games first.", is_error=True)
            return
        collection, accepted = QInputDialog.getText(self, "Assign collection", "Collection name (empty removes it):")
        if not accepted:
            return
        for game_id in selected:
            self.db.update_game_collection(game_id, collection.strip())
        self._refresh_library()

    def _favorite_selected(self):
        selected = self.library_selection.ids
        if not selected:
            self._show_toast("Select one or more games first.", is_error=True)
            return
        for game_id in selected:
            self.db.toggle_favorite(game_id)
        self._refresh_library()

    def _on_toggle_favorite(self):
        """Toggle favorite status for currently selected game"""
        game = self.selected_game
        if not game:
            return
        game_id = game[0]
        new_fav = self.db.toggle_favorite(game_id)
        self._show_toast("Added to Favorites" if new_fav else "Removed from Favorites")
        self._refresh_library()
        self._select_game_by_id(game_id)

    def _on_card_favorite_clicked(self, game_id: int):
        """Toggle a game's favorite directly from its library card."""
        new_fav = self.db.toggle_favorite(game_id)
        self._show_toast("Added to Favorites" if new_fav else "Removed from Favorites")
        self._refresh_library()

    def _refresh_library(self):
        """Clear and reload game banners into dynamic responsive grid based on search, status filter, and sorting."""
        selected_game_id = self.selected_game[0] if self.selected_game else None
        # Explicitly hide and destroy old child widgets
        for old_w in list(self.banner_widgets.values()):
            try:
                old_w.hide()
                old_w.setParent(None)
                old_w.deleteLater()
            except (RuntimeError, AttributeError):
                pass
        self.banner_widgets.clear()
        
        self.games = self.db.get_all_games()
        self.games_by_id = {game[0]: game for game in self.games}
        if selected_game_id is not None:
            self.selected_game = self.games_by_id.get(selected_game_id)
        self.library_selection.replace(self.library_selection.ids.intersection(self.games_by_id))
        self.stat_label.setText(f"{len(self.games)} Game(s) Total")

        # Compute dynamic sidebar statistics
        self._update_sidebar_counts()

        all_cols = self.db.get_all_collections()
        collections_dict = {c: 0 for c in all_cols}
        for g in self.games:
            if len(g) > 17 and g[17]:
                continue
            c_name = str(g[13]).strip() if len(g) > 13 else ""
            if c_name:
                collections_dict[c_name] = collections_dict.get(c_name, 0) + 1
        sorted_cols = sorted(collections_dict.items(), key=lambda x: x[0].lower())
        self.sidebar.update_collections_list(sorted_cols)

        if not self.games:
            label = QLabel("No games in your library yet.\nClick 'Add Game' or 'Sync Library' to get started.")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("color: #999; font-size: 14px; padding: 40px;")
            self.grid_container.set_banner_widgets([label])
            self.collection_banner.setVisible(False)
            return

        # Filter & sort games list
        processed = []
        for g in self.games:
            game_id, name, path, executable, mode, banner_url, steam_id = g[:7]
            playtime = g[7] if len(g) > 7 and g[7] else 0
            is_fav = bool(g[8]) if len(g) > 8 and g[8] else False
            is_archived = bool(g[17]) if len(g) > 17 and g[17] else False

            # 1. Search Query Filter
            searchable = " ".join(str(value or "") for value in (name, g[10] if len(g) > 10 else "", executable, steam_id, mode, g[12] if len(g) > 12 else "" )).lower()
            if self.search_query and self.search_query not in searchable:
                continue

            # Disk check for status filter
            folder_exists = os.path.exists(path) if path else False
            full_exe = os.path.join(path, executable) if (path and executable) else path
            exe_exists = os.path.exists(full_exe) if full_exe else False
            is_missing = not (folder_exists and (exe_exists or not executable))

            # 2. Status & Archive Filtering
            if self.current_filter == "archived":
                if not is_archived:
                    continue
            else:
                if is_archived:
                    continue
                if self.current_filter == "installed" and is_missing:
                    continue
                elif self.current_filter == "favorites" and not is_fav:
                    continue
                if self.collection_filter and (len(g) <= 13 or str(g[13]).strip() != self.collection_filter):
                    continue

            processed.append((g, is_missing, playtime, is_fav))

        # 3. Update collection banner stats
        try:
            if self.collection_filter:
                self.collection_banner.setVisible(True)
                self.lbl_col_banner_title.setText(f"{self.collection_filter}")
                col_playtime = sum(item[2] for item in processed)
                col_hours = col_playtime / 3600.0
                self.lbl_col_banner_stats.setText(f"{len(processed)} Game(s)  •  {col_hours:.1f} hrs Total Playtime")
            else:
                self.collection_banner.setVisible(False)
        except (RuntimeError, AttributeError):
            pass

        # 4. Sorting
        if self.current_sort == 0:  # A-Z Title
            processed.sort(key=lambda x: x[0][1].lower())
        elif self.current_sort == 1:  # Most Played
            processed.sort(key=lambda x: x[2], reverse=True)
        elif self.current_sort == 2:  # Recently Added (id desc)
            processed.sort(key=lambda x: x[0][14] if len(x[0]) > 14 and x[0][14] else x[0][0], reverse=True)
        elif self.current_sort == 3:  # Disk size
            # Never walk multi-GB directories on the GUI thread here: sort by
            # whatever sizes are cached; missing ones are computed by workers
            # (_schedule_size_fetches below) and re-sorted when they arrive.
            processed.sort(key=lambda x: peek_dir_size(x[0][2]) or 0, reverse=True)
        elif self.current_sort == 4:  # Runner
            processed.sort(key=lambda x: x[0][4].lower())

        # Background size calculation for any game dir without a fresh cached
        # value (used by Disk Size sorting and list-view metadata rows).
        self._schedule_size_fetches({x[0][2] for x in processed if x[0][2]})

        if not processed:
            msg = f"No games matching '{self.search_query}'" if self.search_query else ("No archived games found." if self.current_filter == "archived" else "No games matching selected filter.")
            label = QLabel(msg)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("color: #777777; font-size: 14px; padding: 40px;")
            self.grid_container.set_banner_widgets([label])
            return

        widgets = []
        for g, is_missing, playtime_seconds, is_fav in processed:
            game_id, name, path, executable, mode, banner_url, steam_id = g[:7]
            version_override = g[15] if len(g) > 15 and g[15] else ""
            icon_url = g[18] if len(g) > 18 and g[18] else ""
            
            # Check local icons cache if not yet set in DB
            if not icon_url:
                cached_icon = os.path.join(self.sgdb_client.cache_dir.parent, "icons", f"icon_{game_id}.png")
                if os.path.exists(cached_icon):
                    icon_url = cached_icon
            
            widget = GameBannerWidget(
                game_id, name, banner_url, playtime_seconds or 0,
                version=version_override, icon_path=icon_url, parent=self.grid_container
            )
            widget.set_missing(is_missing)
            widget.set_update_available(self.update_status_by_game_id.get(game_id, False))
            widget.set_favorite(is_fav)
            widget.set_selected(game_id in self.library_selection.ids)
            widget.clicked.connect(self._select_game_by_id)
            widget.doubleClicked.connect(self._on_double_click_game)
            widget.favoriteClicked.connect(self._on_card_favorite_clicked)
            widget.launchClicked.connect(self._launch_game_by_id)
            
            widgets.append(widget)
            self.banner_widgets[game_id] = widget
            
            if (banner_url is None or not icon_url) and game_id not in self._auto_fetch_attempted:
                self._auto_fetch_attempted.add(game_id)
                full_exe = os.path.join(path, executable) if (path and executable) else ""
                fetcher = BannerAutoFetcher(game_id, name, self.sgdb_client, exe_path=full_exe, steam_id=str(steam_id or ""))
                fetcher.banner_auto_downloaded.connect(self._on_auto_banner_downloaded)
                fetcher.finished.connect(lambda f=fetcher: self._cleanup_auto_fetcher(f))
                # Throttle: start immediately only up to the concurrency cap,
                # queue the rest instead of firing N simultaneous request bursts.
                if len(self.auto_fetchers) < self.max_concurrent_auto_fetchers:
                    fetcher.start()
                    self.auto_fetchers.append(fetcher)
                else:
                    self._pending_auto_fetchers.append(fetcher)
            
        try:
            self.grid_container.set_banner_widgets(widgets)
        except (RuntimeError, AttributeError):
            pass
        try:
            self.list_view.set_games(
                processed,
                self.library_selection.ids,
                self.update_status_by_game_id,
                self.sgdb_client.cache_dir
            )
        except (RuntimeError, AttributeError):
            pass
        self._check_games_on_drive()
        self._update_tray_menu()

        # Pre-cache 16:9 hero background artwork and game icons in background threads
        for game in self.games:
            g_id, g_name, _, _, _, _, s_id = game[:7]
            hero_cache_file = os.path.join(self.sgdb_client.cache_dir, "heroes", f"hero_{g_id}.jpg")
            if not os.path.exists(hero_cache_file) and g_id not in self._hero_attempted:
                if not any(isinstance(f, HeroFetcherThread) and f.game_id == g_id for f in self.metadata_fetchers):
                    self._hero_attempted.add(g_id)
                    hero_thread = HeroFetcherThread(g_id, g_name, s_id, self.sgdb_client, parent=self)
                    hero_thread.hero_downloaded.connect(self._on_hero_downloaded)
                    self._track_metadata_fetcher(hero_thread)

            icon_url = game[18] if len(game) > 18 and game[18] else ""
            if not icon_url and g_id not in self._icon_attempted:
                self._icon_attempted.add(g_id)
                g_path = game[2] if len(game) > 2 else ""
                g_exe = game[3] if len(game) > 3 else ""
                full_exe = os.path.join(g_path, g_exe) if (g_path and g_exe) else ""
                icon_thread = IconAutoFetcherThread(g_id, g_name, str(s_id or ""), self.sgdb_client, exe_path=full_exe, parent=self)
                icon_thread.icon_downloaded.connect(self._on_icon_downloaded)
                self._track_metadata_fetcher(icon_thread)

    def _update_sidebar_counts(self):
        """Recompute sidebar stats; called on refresh and after drive re-checks."""
        active_games = [g for g in self.games if not (len(g) > 17 and g[17])]
        inst_games = []
        for g in active_games:
            p = g[2]
            exe = g[3]
            f_ex = os.path.exists(p) if p else False
            e_ex = os.path.exists(os.path.join(p, exe)) if (p and exe) else f_ex
            if f_ex and (e_ex or not exe):
                inst_games.append(g)
        fav_games = [g for g in active_games if (len(g) > 8 and g[8])]
        arch_games = [g for g in self.games if (len(g) > 17 and g[17])]
        self.sidebar.update_counts(len(active_games), len(inst_games), len(fav_games), len(arch_games))

    def _on_icon_downloaded(self, game_id: int, icon_path: str):
        """Save downloaded game icon path in DB and update card."""
        self.db.update_game_icon(game_id, icon_path)
        try:
            if game_id in self.banner_widgets:
                self.banner_widgets[game_id].set_icon(icon_path)
        except (RuntimeError, AttributeError):
            pass

    def _check_games_on_drive(self):
        """Check all games in library against disk and grey out missing ones"""
        for game in self.games:
            game_id, name, path, executable, mode, banner_url, steam_id, *_ = (*game, 0)
            
            folder_exists = os.path.exists(path) if path else False
            full_exe_path = os.path.join(path, executable) if (path and executable) else path
            exe_exists = os.path.exists(full_exe_path) if full_exe_path else False
            
            is_missing = not (folder_exists and (exe_exists or not executable))
            
            try:
                if game_id in self.banner_widgets:
                    self.banner_widgets[game_id].set_missing(is_missing)
            except (RuntimeError, AttributeError):
                pass

        # Keep sidebar counts consistent with the re-checked on-disk state.
        self._update_sidebar_counts()

    def _on_auto_banner_downloaded(self, game_id: int, image_path: str, steam_id: int = 0, icon_path: str = ""):
        """Update DB and widget when background auto-fetch completes"""
        if image_path:
            self.db.update_game_banner(game_id, image_path)
        if steam_id:
            self.db.update_game_steam_id(game_id, steam_id)
        if icon_path:
            self.db.update_game_icon(game_id, icon_path)
        try:
            if game_id in self.banner_widgets:
                if image_path:
                    self.banner_widgets[game_id].set_banner(image_path)
                if icon_path:
                    self.banner_widgets[game_id].set_icon(icon_path)
        except (RuntimeError, AttributeError):
            pass

    def _cleanup_auto_fetcher(self, fetcher):
        if fetcher in self.auto_fetchers:
            self.auto_fetchers.remove(fetcher)
        # Defer Qt-side destruction to the event loop: finished fires while the
        # OS thread is still terminating, and dropping the last Python reference
        # here can destroy a running QThread.
        try:
            fetcher.deleteLater()
        except RuntimeError:
            pass
        self._start_next_pending_fetcher()

    def _start_next_pending_fetcher(self):
        """Launch the next queued art fetch once a slot frees up."""
        while self._pending_auto_fetchers:
            if len(self.auto_fetchers) >= self.max_concurrent_auto_fetchers:
                return
            fetcher = self._pending_auto_fetchers.pop(0)
            if fetcher.isInterruptionRequested():
                continue
            fetcher.start()
            self.auto_fetchers.append(fetcher)
            return

    def _cancel_metadata_fetchers(self):
        for fetcher in list(self.metadata_fetchers):
            if fetcher.isRunning():
                fetcher.requestInterruption()

    def _track_metadata_fetcher(self, fetcher):
        fetcher.finished.connect(lambda f=fetcher: self._cleanup_metadata_fetcher(f))
        self.metadata_fetchers.append(fetcher)
        fetcher.start()

    def _cleanup_metadata_fetcher(self, fetcher):
        if fetcher in self.metadata_fetchers:
            self.metadata_fetchers.remove(fetcher)
        try:
            fetcher.deleteLater()
        except RuntimeError:
            pass

    def _schedule_size_fetches(self, paths):
        """Compute missing directory sizes on worker threads, never on the GUI."""
        for path in paths or []:
            if not path or path in self._size_fetch_scheduled or has_fresh_dir_size(path):
                continue
            self._size_fetch_scheduled.add(path)
            fetcher = DiskSizeFetcherThread(-1, path, parent=self)
            fetcher.disk_size_calculated.connect(
                lambda _gid, _sz, p=path: self._on_library_size_ready(p)
            )
            self._track_metadata_fetcher(fetcher)

    def _on_library_size_ready(self, path):
        """A background size landed in the cache; re-sort once things settle."""
        self._size_fetch_scheduled.discard(path)
        if self.current_sort == 3 and not self._size_resort_timer.isActive():
            self._size_resort_timer.start()

    def _select_game_by_id(self, game_id: int):
        """Select a game card visually and update the left detail panel"""
        additive = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
        self.library_selection.click(game_id, additive=additive)
        if not self.selected_game or self.selected_game[0] != game_id:
            self._cancel_metadata_fetchers()
        for widget in list(self.banner_widgets.values()):
            try:
                widget.set_selected(widget.game_id in self.library_selection.ids)
            except (RuntimeError, AttributeError):
                pass
        for game in self.games:
            if game[0] == game_id:
                self.selected_game = game
                try:
                    if game_id in self.banner_widgets:
                        self.banner_widgets[game_id].set_selected(True)
                except (RuntimeError, AttributeError):
                    pass
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

    def _on_splitter_moved(self, pos: int, index: int):
        sizes = self.splitter.sizes()
        if len(sizes) > 1 and sizes[1] > 150:
            self.settings.setValue("right_inspector_width", sizes[1])
        self._reposition_reveal_button()

    RESIZE_MARGIN = 8

    def _get_resize_edges_at_point(self, global_pos: QPoint) -> Qt.Edge:
        if self.isMaximized():
            return Qt.Edge(0)
        pos = self.mapFromGlobal(global_pos)
        x = pos.x()
        y = pos.y()
        w = self.width()
        h = self.height()
        if x < 0 or y < 0 or x > w or y > h:
            return Qt.Edge(0)
        edge = Qt.Edge(0)
        if x <= self.RESIZE_MARGIN:
            edge |= Qt.Edge.LeftEdge
        elif x >= w - self.RESIZE_MARGIN:
            edge |= Qt.Edge.RightEdge

        if y <= self.RESIZE_MARGIN:
            edge |= Qt.Edge.TopEdge
        elif y >= h - self.RESIZE_MARGIN:
            edge |= Qt.Edge.BottomEdge

        return edge

    def _update_edge_cursor(self, edges: Qt.Edge):
        if (edges == (Qt.Edge.TopEdge | Qt.Edge.LeftEdge)) or (edges == (Qt.Edge.BottomEdge | Qt.Edge.RightEdge)):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif (edges == (Qt.Edge.TopEdge | Qt.Edge.RightEdge)) or (edges == (Qt.Edge.BottomEdge | Qt.Edge.LeftEdge)):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseMove:
            if not self.isMaximized() and event.buttons() == Qt.MouseButton.NoButton:
                edges = self._get_resize_edges_at_point(event.globalPosition().toPoint())
                if edges:
                    self._update_edge_cursor(edges)
                elif self.cursor().shape() in (
                    Qt.CursorShape.SizeHorCursor,
                    Qt.CursorShape.SizeVerCursor,
                    Qt.CursorShape.SizeFDiagCursor,
                    Qt.CursorShape.SizeBDiagCursor,
                ):
                    self.unsetCursor()
        elif event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton and not self.isMaximized():
                edges = self._get_resize_edges_at_point(event.globalPosition().toPoint())
                if edges:
                    handle = self.windowHandle()
                    if handle is not None and handle.startSystemResize(edges):
                        return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_reveal_button()

    def _reposition_reveal_button(self):
        """No-op as reveal button is docked in the bottom action bar."""
        pass

    def _on_panel_anim_step(self, val: float):
        saved_w = self.settings.value("right_inspector_width", 300, type=int)
        normalized_val = min(1.0, max(0.0, val))
        current_inspector_w = max(1, int(saved_w * normalized_val))
        total_w = self.splitter.width() or self.width() or 1180
        self.splitter.setSizes([max(300, total_w - current_inspector_w), current_inspector_w])
        
        # Opacity fade + horizontal swipe translation
        self.panel_opacity_effect.setOpacity(normalized_val)
        
        # Physical horizontal swipe offset (glides in from 45px right edge)
        swipe_offset = int((1.0 - normalized_val) * 45)
        self.detail_panel.setContentsMargins(18 + swipe_offset, 18, max(0, 18 - swipe_offset), 18)

    def _on_panel_anim_finished(self):
        if not self._panel_expanding:
            self.detail_panel.setVisible(False)
            self.btn_reveal_detail.setVisible(True)
        else:
            self.detail_panel.setContentsMargins(18, 18, 18, 18)
            self.btn_reveal_detail.setVisible(False)

    def _animate_left_panel(self, expand: bool):
        """Smoothly swipe and fade in/out the right detail inspector panel from the right edge."""
        if expand:
            if not self.detail_panel.isVisible() or self.panel_anim.state() == QAbstractAnimation.State.Running:
                self._panel_expanding = True
                self.btn_reveal_detail.setVisible(False)
                self.detail_panel.setVisible(True)
                self.panel_anim.stop()
                self.panel_anim.setDuration(280)
                self.panel_anim.setStartValue(self.panel_opacity_effect.opacity())
                self.panel_anim.setEndValue(1.0)
                self.panel_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                self.panel_anim.start()
        else:
            if self.detail_panel.isVisible():
                self._panel_expanding = False
                self.panel_anim.stop()
                self.panel_anim.setDuration(220)
                self.panel_anim.setStartValue(self.panel_opacity_effect.opacity())
                self.panel_anim.setEndValue(0.0)
                self.panel_anim.setEasingCurve(QEasingCurve.Type.InCubic)
                self.panel_anim.start()

    def _update_global_hotkeys(self):
        """Refresh registered global hotkeys in background listener."""
        if not hasattr(self, "global_hotkeys"):
            return
        bindings = {
            self.screenshot_hotkey or "F12": "screenshot",
            self.gpu_recorder_config.capture_hotkey or "F9": "toggle_recording",
        }
        replay_key = self.gpu_recorder_config.replay_hotkey or "F10"
        # Only register when distinct — the listener binds one action per key.
        if all(key != replay_key for key in bindings):
            bindings[replay_key] = "save_replay"
        self.global_hotkeys.update_bindings(bindings)

    def _on_global_hotkey(self, action: str):
        """Dispatch global hotkey action emitted from background listener."""
        if action == "screenshot":
            self._take_screenshot()
        elif action == "toggle_recording":
            # In replay buffer mode the capture hotkey saves a clip
            if self.gpu_recorder_config.mode == "replay_buffer":
                self._trigger_replay_save()
            else:
                self._toggle_game_recording()
        elif action == "save_replay":
            self._trigger_replay_save()

    def _take_screenshot(self):
        """Capture screenshot of designated screen, display in-game HUD overlay and save to gallery."""
        game_id = 0
        game_name = "unknown"
        if self.selected_game:
            game_id = self.selected_game[0] if isinstance(self.selected_game, tuple) else getattr(self.selected_game, 'id', 0)
            game_name = self.selected_game[1] if isinstance(self.selected_game, tuple) else getattr(self.selected_game, 'name', 'unknown')
        elif self.games:
            game_id = self.games[0][0] if isinstance(self.games[0], tuple) else getattr(self.games[0], 'id', 0)
            game_name = self.games[0][1] if isinstance(self.games[0], tuple) else getattr(self.games[0], 'name', 'unknown')

        try:
            from core.screenshot_capture import capture_desktop_screenshot
            target_path = capture_desktop_screenshot(game_id, target_screen=self.screenshot_screen, game_name=game_name)
            if target_path:
                self._show_toast("Screenshot saved to gallery.")
                show_ingame_notification(
                    "Screenshot Captured",
                    os.path.basename(target_path),
                    icon_type="screenshot",
                    enabled=self.gpu_recorder_config.in_game_overlay,
                    play_sound=True,
                    target_screen=self.screenshot_screen
                )
                self._update_detail_panel()
            else:
                self._show_toast("Failed to capture screenshot.", is_error=True)
                show_ingame_notification("Screenshot Failed", "Could not capture screen", icon_type="warning", enabled=self.gpu_recorder_config.in_game_overlay, target_screen=self.screenshot_screen)
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

    def _open_video_gallery(self):
        """Open the video browser for the selected game."""
        game = self.selected_game
        if not game:
            return
        dialog = VideoGalleryDialog(game[0], game[1], self.gpu_recorder_config.output_dir, self)
        dialog.exec()
        self._update_detail_panel()

    def _open_game_properties(self):
        """Open consolidated GamePropertiesDialog for the selected game."""
        game = self.selected_game
        if not game:
            return
        dialog = GamePropertiesDialog(game, self)
        dialog.exec()
        self._update_detail_panel()

    def _refresh_record_button_state(self):
        """No-op kept for backwards compatibility."""
        pass

    def _set_detail_record_label(self, text: str, icon_key: str = None):
        """Update the detail-panel record button if one exists in the current layout."""
        button = getattr(self, "btn_detail_record", None)
        if not button:
            return
        button.setText(text)
        if icon_key:
            button.setIcon(get_icon(icon_key))

    def _toggle_game_recording(self):
        """Handle manual recording start/stop or instant replay buffer capture."""
        rec_svc = GpuRecorderService.instance()
        game_name = "unknown"
        if self.selected_game:
            game_name = self.selected_game[1] if isinstance(self.selected_game, tuple) else getattr(self.selected_game, 'name', 'unknown')

        if self.gpu_recorder_config.mode == "replay_buffer":
            # In replay buffer mode this button always means SAVE CLIP — never stop
            if not rec_svc.is_running():
                # Buffer hasn't started yet — start it silently
                if rec_svc.start_recording(game_name, is_replay=True):
                    self._show_toast("Replay buffer started. It will keep running until the game is closed.")
                    show_ingame_notification(
                        "Replay Buffer Active",
                        f"{self.gpu_recorder_config.replay_hotkey} → save clip",
                        icon_type="replay",
                        enabled=self.gpu_recorder_config.in_game_overlay,
                        play_sound=True,
                    )
                else:
                    self._show_toast("Failed to start replay buffer.", is_error=True)
                    show_ingame_notification("Replay Buffer Failed", "Could not start recorder", icon_type="warning", enabled=self.gpu_recorder_config.in_game_overlay)
            else:
                # Buffer is running — save a clip
                if rec_svc.save_replay_clip():
                    self._show_toast("Saved replay clip to videos folder.")
                    show_ingame_notification("Replay Clip Saved", "Clip written to Videos", icon_type="replay", enabled=self.gpu_recorder_config.in_game_overlay, play_sound=True)
                else:
                    self._show_toast("Failed to save replay clip.", is_error=True)
        else:
            if rec_svc.is_running():
                saved_path = rec_svc.stop_recording()
                self._set_detail_record_label(" Record Video", "ph.video-camera-bold")
                fn = os.path.basename(saved_path) if saved_path else "video.mp4"
                self._show_toast(f"Recording saved: {fn}")
                show_ingame_notification("Recording Saved", fn, icon_type="info", enabled=self.gpu_recorder_config.in_game_overlay, play_sound=True)
            else:
                if rec_svc.start_recording(game_name, is_replay=False):
                    self._set_detail_record_label(" Stop Recording")
                    self._show_toast(f"Recording started for '{game_name}'...")
                    show_ingame_notification(
                        "Recording Started",
                        f"{self.gpu_recorder_config.capture_hotkey} → stop",
                        icon_type="recording",
                        enabled=self.gpu_recorder_config.in_game_overlay,
                        play_sound=True,
                    )
                else:
                    self._show_toast("Failed to start recording.", is_error=True)
                    show_ingame_notification("Recording Failed", "Check GPU recorder in Settings", icon_type="warning", enabled=self.gpu_recorder_config.in_game_overlay)

    def _trigger_replay_save(self):
        """Dedicated action to capture instant replay clip."""
        rec_svc = GpuRecorderService.instance()
        if not rec_svc.is_installed():
            self._show_toast("Recorder engine not installed.", is_error=True)
            return

        game_name = "unknown"
        if self.selected_game:
            game_name = self.selected_game[1] if isinstance(self.selected_game, tuple) else getattr(self.selected_game, 'name', 'unknown')

        if not rec_svc.is_running():
            if rec_svc.start_recording(game_name, is_replay=True):
                self._show_toast("Started replay buffer. Press hotkey again to save clip.")
                self._set_detail_record_label(" Save Replay Clip")
                show_ingame_notification(
                    "Replay Buffer Active",
                    f"Press {self.gpu_recorder_config.replay_hotkey} to save clip",
                    icon_type="replay",
                    enabled=self.gpu_recorder_config.in_game_overlay,
                    play_sound=True,
                )
            else:
                self._show_toast("Failed to start replay buffer.", is_error=True)
        else:
            if rec_svc.save_replay_clip():
                self._show_toast("Saved replay clip to videos folder.")
                show_ingame_notification("Replay Clip Saved", "Clip written to Videos", icon_type="replay", enabled=self.gpu_recorder_config.in_game_overlay, play_sound=True)
            else:
                self._show_toast("Failed to save replay clip.", is_error=True)

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

    @staticmethod
    def _format_version_date(timestamp: int) -> str:
        if not timestamp:
            return "Unknown date"
        try:
            return datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            return "Unknown date"

    def _check_all_steam_updates(self):
        """Check every Steam-linked game once, used on startup and from the tools menu."""
        games = self.db.get_all_games()
        pending = [0]
        self._show_toast("Checking Steam for updates...")
        if hasattr(self, "nav_updates") and self.nav_updates is not None:
            self.nav_updates.setEnabled(False)
            self.nav_updates.setText(" Checking Steam…")

        def finished_one():
            pending[0] -= 1
            if pending[0] <= 0:
                if hasattr(self, "nav_updates") and self.nav_updates is not None:
                    self.nav_updates.setEnabled(True)
                    self.nav_updates.setText(" Check for Updates")
                self._show_toast("Steam update check complete.")

        for game in games:
            game_id, _, path, _, _, _, steam_id = game[:7]
            if not steam_id or str(steam_id) == "0":
                continue
            if any(isinstance(fetcher, SteamBuildFetcher) and fetcher.game_id == game_id for fetcher in self.metadata_fetchers):
                continue

            self.metadata_attempted_builds.discard(game_id)
            local_build_id = game[11] if len(game) > 11 and game[11] else ""
            local_build_date = game[14] if len(game) > 14 and game[14] else 0
            manifest_build, manifest_date = read_local_steam_build(path, str(steam_id))
            local_build_id = manifest_build or local_build_id
            local_build_date = manifest_date or local_build_date
            if not local_build_date and path and os.path.exists(path):
                try:
                    local_build_date = int(os.path.getmtime(path))
                except OSError:
                    local_build_date = 0
            self.local_version_by_game_id[game_id] = (local_build_id, local_build_date)

            fetcher = SteamBuildFetcher(game_id, steam_id, local_build_id, local_build_date, parent=self)
            fetcher.update_checked.connect(self._on_steam_build_checked)
            fetcher.check_failed.connect(self._on_steam_check_failed)
            fetcher.finished.connect(finished_one)
            self.metadata_fetchers.append(fetcher)
            self.metadata_attempted_builds.add(game_id)
            pending[0] += 1
            fetcher.finished.connect(lambda f=fetcher: self._cleanup_metadata_fetcher(f))
            fetcher.start()

        if pending[0] == 0:
            finished_one()

    def _capture_initial_steam_build(self, game_id: int, steam_id: str):
        """Record the Steam build present when a game is first added."""
        self.metadata_attempted_builds.add(game_id)
        fetcher = SteamBuildFetcher(game_id, steam_id, "", 0, parent=self)
        fetcher.update_checked.connect(self._on_initial_steam_build_checked)
        fetcher.check_failed.connect(lambda gid, reason: self.metadata_attempted_builds.discard(gid))
        fetcher.finished.connect(lambda f=fetcher: self._cleanup_metadata_fetcher(f))
        self.metadata_fetchers.append(fetcher)
        fetcher.start()

    def _on_initial_steam_build_checked(self, game_id: int, build_id: str, build_date: int, _needs_update: bool):
        if not build_id:
            return
        self.db.update_build_id(game_id, build_id)
        self.local_version_by_game_id[game_id] = (build_id, build_date)
        self.steam_check_results[game_id] = (build_id, build_date, False, "")
        self.update_status_by_game_id[game_id] = False
        self.metadata_attempted_builds.discard(game_id)
        try:
            if game_id in self.banner_widgets:
                self.banner_widgets[game_id].set_update_available(False)
        except (RuntimeError, AttributeError):
            pass

    def _on_steam_build_checked(self, game_id: int, latest_build_id: str, latest_build_date: int, is_update_available: bool):
        """Callback when background SteamBuildFetcher returns build info."""
        self.steam_check_results[game_id] = (latest_build_id, latest_build_date, is_update_available, "")
        self.update_status_by_game_id[game_id] = bool(is_update_available and latest_build_id)
        try:
            if game_id in self.banner_widgets:
                self.banner_widgets[game_id].set_update_available(is_update_available)
        except (RuntimeError, AttributeError):
            pass
        if not self.selected_game or self.selected_game[0] != game_id:
            return

        if self.selected_game and self.selected_game[0] == game_id:
            if not latest_build_id:
                local_build_id, local_date = self.local_version_by_game_id.get(game_id, ("", 0))
                if not local_build_id and len(self.selected_game) > 11:
                    local_build_id = self.selected_game[11] or ""
                if not local_date and len(self.selected_game) > 14:
                    local_date = self.selected_game[14] or 0
                local_build_id = local_build_id or "Not recorded"
                self.lbl_detail_update.setText("⚪ Steam check unavailable")
                self.lbl_detail_update.setStyleSheet("background: #3f3f46; color: #d4d4d8; border: 1px solid #71717a; border-radius: 6px; padding: 4px 8px; font-size: 10px; font-weight: bold;")
                self.lbl_detail_versions.setText(
                    f"Current: {local_build_id} · {self._format_version_date(local_date)}\n"
                    "Steam: unavailable"
                )
                self.detail_update_widget.setVisible(True)
                return
            local_build_id, local_date = self.local_version_by_game_id.get(game_id, ("", 0))
            if not local_build_id and len(self.selected_game) > 11:
                local_build_id = self.selected_game[11] or ""
            if not local_date and len(self.selected_game) > 14:
                local_date = self.selected_game[14] or 0
            local_build_id = local_build_id or "Not recorded"
            version_override = self.selected_game[15] if len(self.selected_game) > 15 and self.selected_game[15] else "Version unavailable"
            patch_notes_url = self.selected_game[16] if len(self.selected_game) > 16 and self.selected_game[16] else ""
            patch_link = f"<br><a href='{escape(patch_notes_url, quote=True)}'>Open patch notes</a>" if patch_notes_url else ""
            status = "Needs update" if is_update_available else "Up to date"
            status_color = ("rgba(229, 169, 61, 0.12)", "#E5A93D", "rgba(229, 169, 61, 0.3)") if is_update_available else ("rgba(53, 201, 138, 0.12)", "#35C98A", "rgba(53, 201, 138, 0.3)")
            self.lbl_detail_update.setText(status)
            self.lbl_detail_update.setStyleSheet(
                f"background: {status_color[0]}; color: {status_color[1]}; border: 1px solid {status_color[2]}; border-radius: 4px; padding: 2px 8px; font-size: 10px; font-weight: 600;"
            )
            self.lbl_detail_versions.setText(
                "<table width='100%' cellspacing='0' cellpadding='1' style='margin:0; padding:0; border-collapse:collapse;'>"
                "<tr><td></td><td align='center'><font color='#6F7682'>LOCAL</font></td>"
                "<td align='center'><font color='#6F7682'>STEAM</font></td></tr>"
                f"<tr><td><font color='#A7ADB8'>Version</font></td><td align='center'><b>{escape(str(version_override))}</b></td>"
                f"<td align='center'><font color='#6F7682'>—</font></td></tr>"
                f"<tr><td><font color='#A7ADB8'>Build</font></td><td align='center'><b>{escape(str(local_build_id))}</b></td>"
                f"<td align='center'><b>{escape(str(latest_build_id))}</b></td></tr>"
                f"<tr><td><font color='#A7ADB8'>Updated</font></td><td align='center'>{self._format_version_date(local_date)}</td>"
                f"<td align='center'>{self._format_version_date(latest_build_date)}</td></tr>"
                f"<tr><td colspan='3' align='center'>{patch_link.replace('<br>', '', 1) if patch_link else ''}</td></tr>"
                "</table>"
            )
            self.detail_update_widget.setVisible(True)
            self.btn_retry_steam.setVisible(False)
            self.latest_checked_build_id = latest_build_id
            self.latest_checked_build_date = latest_build_date

    def _on_steam_check_failed(self, game_id: int, reason: str):
        self.steam_check_results[game_id] = ("", 0, False, reason)
        if self.selected_game and self.selected_game[0] == game_id:
            self.lbl_detail_update.setText("Steam check failed")
            self.lbl_detail_update.setStyleSheet("background: #1A1E26; color: #A7ADB8; border: 1px solid #252A33; border-radius: 4px; padding: 2px 8px; font-size: 10px; font-weight: 500;")
            self.lbl_detail_versions.setText(reason)
            self.lbl_detail_versions.setToolTip(reason)
            self.detail_update_widget.setVisible(True)
            self.btn_retry_steam.setVisible(True)
        self.metadata_attempted_builds.discard(game_id)
        logger.warning(f"Steam build check failed for game {game_id}: {reason}")

    def _retry_steam_check(self):
        if not self.selected_game:
            return
        game_id = self.selected_game[0]
        self.metadata_attempted_builds.discard(game_id)
        self._update_detail_panel()

    def _mark_build_current_from_config(self, game_id: int):
        """Record a manually installed Steam build from the game settings dialog."""
        if not self.selected_game or self.selected_game[0] != game_id:
            return
        latest = getattr(self, 'latest_checked_build_id', "")
        if not latest:
            self._show_toast("Steam has not provided a build to record yet.", is_error=True)
            return
        self.db.update_build_id(game_id, latest)
        self.update_status_by_game_id[game_id] = False
        self._show_toast("Steam build marked as current. Game files were not changed.")
        self._refresh_library()
        self._select_game_by_id(game_id)

    def _apply_steam_update_to_game(self):
        """Allow users to mark local install build as matching Steam current release."""
        game = self.selected_game
        if not game:
            return
        game_id = game[0]
        if not self.update_status_by_game_id.get(game_id, False):
            self._show_toast("No Steam update detected for this game.", is_error=True)
            return
        latest = getattr(self, 'latest_checked_build_id', "")
        if not latest:
            self._show_toast("Steam has not provided a build to record yet.", is_error=True)
            return
        self.db.update_build_id(game_id, latest)
        self.update_status_by_game_id[game_id] = False
        self._show_toast("Steam build marked as current. Game files were not changed.")
        self._refresh_library()
        self._select_game_by_id(game_id)

    def _on_steam_tags_found(self, game_id: int, tags_list: list, steam_app_id: str = ""):
        """Callback when background SteamTagsFetcher returns genres/categories"""
        if steam_app_id and steam_app_id.isdigit() and int(steam_app_id) > 0:
            self.db.update_game_steam_id(game_id, steam_app_id)
        if tags_list:
            tags_str = ", ".join(tags_list)
            self.db.update_game_tags(game_id, tags_str)
        if not self.selected_game or self.selected_game[0] != game_id:
            return

        # Reload the row so the newly discovered AppID is used by future launches.
        self._refresh_library()
        self._select_game_by_id(game_id)

    def _update_tags_pills(self, tags_list: list):
        while self.tags_layout.count() > 0:
            item = self.tags_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for tag in tags_list[:3]:
            badge = QLabel(tag)
            badge.setStyleSheet("""
                QLabel {
                    background: #1A1E26;
                    color: #A7ADB8;
                    border: 1px solid #252A33;
                    border-radius: 4px;
                    padding: 2px 8px;
                    font-size: 10px;
                    font-weight: 500;
                }
            """)
            self.tags_layout.addWidget(badge)

    def _on_hero_downloaded(self, game_id: int, image_path: str):
        if self.selected_game and self.selected_game[0] == game_id:
            self.hero_bg.set_hero_image(image_path)

    def _on_disk_size_calculated(self, game_id: int, size_bytes: int):
        if self.selected_game and self.selected_game[0] == game_id:
            self.detail_disk_size.setText(f"Size: {format_size(size_bytes)}")

    def _render_cloud_status(self, game_id: int, status, local_stats=None):
        """Update both library card badge and left detail inspector panel."""
        if game_id in self.banner_widgets:
            self.banner_widgets[game_id].set_cloud_status(status)

        if self.selected_game and self.selected_game[0] == game_id:
            from core.cloud_save_sync import SyncStatus
            if status == SyncStatus.IN_SYNC:
                self.detail_cloud_status.setText("<font color='#35C98A'><b>● Cloud Save: Synced</b></font>")
                self.detail_cloud_status.setToolTip("Save files are fully backed up and synchronized with the cloud.")
            elif status == SyncStatus.LOCAL_NEWER:
                self.detail_cloud_status.setText("<font color='#3B9FE8'><b>▲ Cloud Save: Ready to Upload</b></font>")
                self.detail_cloud_status.setToolTip("Local save is newer than cloud. SafeLauncher will auto-upload on game exit.")
            elif status == SyncStatus.CLOUD_NEWER:
                self.detail_cloud_status.setText("<font color='#E5A93D'><b>▼ Cloud Save: Newer in Cloud</b></font>")
                self.detail_cloud_status.setToolTip("A newer save exists in the cloud. SafeLauncher will prompt to restore on launch.")
            elif status == SyncStatus.CLOUD_ONLY:
                self.detail_cloud_status.setText("<font color='#3B9FE8'><b>▼ Cloud Save: Available</b></font>")
                self.detail_cloud_status.setToolTip("Cloud save exists and will be auto-restored on launch.")
            elif status == SyncStatus.NO_SAVES or (local_stats is not None and not getattr(local_stats, "exists", False)):
                self.detail_cloud_status.setText("<font color='#F05D6C'><b>✕ Cloud Save: Save not found</b></font>")
                self.detail_cloud_status.setToolTip("Game save not found. SafeLauncher will not upload entire game files.")
            else:
                self.detail_cloud_status.setText("<font color='#6F7682'>Cloud Save: --</font>")
                self.detail_cloud_status.setToolTip("")

    def _on_cloud_save_status_calculated(self, game_id: int, status, local_stats, cloud_stats):
        self.cloud_save_status_cache[game_id] = (status, local_stats, cloud_stats)
        self._render_cloud_status(game_id, status, local_stats)

    def _update_detail_panel(self):
        """Update left panel with current selected game details and trigger smooth slide animation."""
        game = self.selected_game
        if not game:
            self._animate_left_panel(False)
            self.hero_bg.set_hero_image(None)
            return

        self._animate_left_panel(True)

        game_id, name, path, exe, mode, banner_url, steam_id = game[:7]
        playtime_seconds = game[7] if len(game) > 7 and game[7] else 0
        is_fav = bool(game[8]) if len(game) > 8 and game[8] else False
        last_played_ts = game[9] if len(game) > 9 and game[9] else 0
        tags_str = game[10] if len(game) > 10 and game[10] else ""

        # Update Inspector Cover Art Preview (2:3 Portrait Cover)
        if banner_url and os.path.exists(banner_url):
            pix = QPixmap(banner_url)
            if not pix.isNull():
                scaled_cover = pix.scaled(QSize(200, 300), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                self.detail_cover.setPixmap(scaled_cover)
            else:
                self.detail_cover.setPixmap(QPixmap())
                self.detail_cover.setText(name)
        else:
            self.detail_cover.setPixmap(QPixmap())
            self.detail_cover.setText(name)

        # Update Hero Blurred Background Image (strictly 16:9 widescreen artwork)
        hero_cache_path = os.path.join(self.sgdb_client.cache_dir, "heroes", f"hero_{game_id}.jpg")
        if os.path.exists(hero_cache_path):
            self.hero_bg.set_hero_image(hero_cache_path)
        else:
            self.hero_bg.set_hero_image(None)

        if not os.path.exists(hero_cache_path) and game_id not in self._hero_attempted:
            if not any(isinstance(f, HeroFetcherThread) and f.game_id == game_id for f in self.metadata_fetchers):
                self._hero_attempted.add(game_id)
                hero_thread = HeroFetcherThread(game_id, name, steam_id, self.sgdb_client, parent=self)
                hero_thread.hero_downloaded.connect(self._on_hero_downloaded)
                self._track_metadata_fetcher(hero_thread)

        self.detail_title.setText(name)
        self.detail_playtime.setText(GameBannerWidget._format_playtime(playtime_seconds))
        self.detail_last_played.setText(self._format_last_played(last_played_ts))

        # Disk Size calculation in background thread to prevent UI freezing
        self.detail_disk_size.setText("Size: Calculating...")
        if path and os.path.exists(path):
            disk_thread = DiskSizeFetcherThread(game_id, path, parent=self)
            disk_thread.disk_size_calculated.connect(self._on_disk_size_calculated)
            self._track_metadata_fetcher(disk_thread)
        else:
            self.detail_disk_size.setText("Size: --")

        # Cloud Save status: instant render from cache if available, background refresh
        cached_save = self.cloud_save_status_cache.get(game_id)
        if cached_save is not None:
            c_status, c_local, _ = cached_save
            self._render_cloud_status(game_id, c_status, c_local)
        else:
            self.detail_cloud_status.setText("Cloud Save: Checking...")
            self.detail_cloud_status.setToolTip("Checking save sync status...")

        save_thread = CloudSaveStatusFetcherThread(game_id, name, path or "", str(steam_id or ""), parent=self)
        save_thread.save_status_calculated.connect(self._on_cloud_save_status_calculated)
        self._track_metadata_fetcher(save_thread)

        local_build_id = game[11] if len(game) > 11 and game[11] else ""
        local_build_date = game[14] if len(game) > 14 and game[14] else 0
        manifest_build, manifest_date = read_local_steam_build(path, str(steam_id or ""))
        local_build_id = manifest_build or local_build_id
        local_build_date = manifest_date or local_build_date
        if not local_build_date and path and os.path.exists(path):
            try:
                local_build_date = int(os.path.getmtime(path))
            except OSError:
                local_build_date = 0
        self.local_version_by_game_id[game_id] = (local_build_id, local_build_date)
        self.lbl_detail_update.setText("Checking Steam…")
        self.lbl_detail_update.setStyleSheet("background: #1f2937; color: #d1d5db; border: 1px solid #4b5563; border-radius: 6px; padding: 4px 8px; font-size: 10px; font-weight: bold;")
        self.lbl_detail_versions.setText("Checking current and Steam versions…")
        self.lbl_detail_versions.setToolTip("")
        self.btn_retry_steam.setVisible(False)
        self.latest_checked_build_id = ""
        self.latest_checked_build_date = 0
        if steam_id and steam_id != "0" and game_id not in self.metadata_attempted_builds and not any(
            isinstance(fetcher, SteamBuildFetcher) and fetcher.game_id == game_id
            for fetcher in self.metadata_fetchers
        ):
            fetcher = SteamBuildFetcher(game_id, steam_id, local_build_id, local_build_date, parent=self)
            fetcher.update_checked.connect(self._on_steam_build_checked)
            fetcher.check_failed.connect(self._on_steam_check_failed)
            self._track_metadata_fetcher(fetcher)
            self.metadata_attempted_builds.add(game_id)
        else:
            cached_result = self.steam_check_results.get(game_id)
            if cached_result:
                cached_build, cached_date, cached_update, cached_error = cached_result
                if cached_build:
                    self._on_steam_build_checked(game_id, cached_build, cached_date, cached_update)
                else:
                    self._on_steam_check_failed(game_id, cached_error or "Steam check unavailable")
            else:
                self.detail_update_widget.setVisible(False)
                self.lbl_detail_versions.setText("")

        # Steam Tags Display & Auto Fetcher
        if tags_str:
            tags_list = [t.strip() for t in tags_str.split(",") if t.strip()]
            self._update_tags_pills(tags_list)
        # Existing cached tags must not prevent resolving the Steam AppID:
        # UMU needs GAMEID=umu-<appid> for Steamworks/protonfixes games.
        if not steam_id or str(steam_id) == "0":
            if game_id not in self.metadata_attempted_tags and not any(
                isinstance(fetcher, SteamTagsFetcher) and fetcher.game_id == game_id
                for fetcher in self.metadata_fetchers
            ):
                fetcher = SteamTagsFetcher(game_id, name, parent=self)
                fetcher.tags_found.connect(self._on_steam_tags_found)
                self._track_metadata_fetcher(fetcher)
                self.metadata_attempted_tags.add(game_id)
        elif not tags_str:
            self._update_tags_pills([])

        # Update Screenshot button badge count
        shots_dir = os.path.join(_APP_DATA_DIR, "screenshots", str(game_id))
        image_extensions = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        count = sum(
            1 for filename in os.listdir(shots_dir)
            if os.path.isfile(os.path.join(shots_dir, filename))
            and os.path.splitext(filename)[1].lower() in image_extensions
        ) if os.path.exists(shots_dir) else 0
        self.btn_detail_screenshots.setText(f"Screenshots ({count})")

        video_dir = os.path.abspath(os.path.expanduser(self.gpu_recorder_config.output_dir))
        video_prefix = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_") or "gameplay"
        video_extensions = (".mp4", ".mkv", ".webm", ".mov", ".avi")
        video_count = sum(
            1 for filename in os.listdir(video_dir)
            if os.path.isfile(os.path.join(video_dir, filename))
            and filename.lower().endswith(video_extensions)
            and filename.lower().startswith(video_prefix + "_")
        ) if os.path.exists(video_dir) else 0
        self.btn_detail_videos.setText(f"Videos ({video_count})")

        self._update_detail_launch_button(game_id)
        self.btn_detail_launch.setVisible(True)
        self.btn_detail_launch.raise_()
        self.btn_detail_edit.setEnabled(True)
        self.btn_detail_screenshots.setEnabled(True)
        self.btn_detail_videos.setEnabled(True)
        self.btn_detail_properties.setEnabled(True)
        self.btn_detail_remove.setEnabled(True)
        self._animate_left_panel(True)

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

    def _update_detail_launch_button(self, game_id: int):
        """Show a red actionable Stop Game button while running, green Launch Game, or blue Restore button if archived."""
        game = self.selected_game
        is_archived = bool(game[17]) if game and len(game) > 17 and game[17] else False

        if is_archived:
            self.btn_detail_launch.setText("Restore to Library")
            self.btn_detail_launch.setIcon(get_icon("ph.arrow-counter-clockwise-bold", color="#FFFFFF"))
            self.btn_detail_launch.setIconSize(QSize(15, 15))
            self.btn_detail_launch.setEnabled(True)
            self.btn_detail_launch.setStyleSheet("""
                QPushButton#detailLaunch {
                    background-color: #3B9FE8;
                    color: #FFFFFF;
                    border: 1px solid #3B9FE8;
                    border-radius: 6px;
                    font-weight: 600;
                    padding: 0 16px;
                    font-size: 12px;
                    text-align: center;
                }
                QPushButton#detailLaunch:hover {
                    background-color: #55ACED;
                    border-color: #55ACED;
                }
            """)
            self.btn_detail_remove.setText("Permanently Delete")
            return

        self.btn_detail_remove.setText("Remove Game")
        if game_id in self.running_game_ids:
            self.btn_detail_launch.setText("Stop Game")
            self.btn_detail_launch.setIcon(get_icon("ph.stop-circle-bold", color="#FFFFFF"))
            self.btn_detail_launch.setIconSize(QSize(15, 15))
            self.btn_detail_launch.setEnabled(True)
            self.btn_detail_launch.setStyleSheet("""
                QPushButton#detailLaunch {
                    background-color: #F05D6C;
                    color: #FFFFFF;
                    border: 1px solid #F05D6C;
                    border-radius: 6px;
                    font-weight: 600;
                    padding: 0 16px;
                    font-size: 12px;
                    text-align: center;
                }
                QPushButton#detailLaunch:hover {
                    background-color: #F87171;
                    border-color: #F87171;
                }
                QPushButton#detailLaunch:disabled {
                    background-color: #1A1E26;
                    color: #6F7682;
                    border-color: #252A33;
                }
            """)
        else:
            self.btn_detail_launch.setText("Launch Game")
            self.btn_detail_launch.setIcon(get_icon("ph.play-bold", color="#FFFFFF"))
            self.btn_detail_launch.setIconSize(QSize(15, 15))
            self.btn_detail_launch.setEnabled(True)
            self.btn_detail_launch.setStyleSheet("""
                QPushButton#detailLaunch {
                    background-color: #3B9FE8;
                    color: #FFFFFF;
                    border: 1px solid #3B9FE8;
                    border-radius: 6px;
                    font-weight: 600;
                    padding: 0 16px;
                    font-size: 12px;
                    text-align: center;
                }
                QPushButton#detailLaunch:hover {
                    background-color: #55ACED;
                    border-color: #55ACED;
                }
                QPushButton#detailLaunch:pressed {
                    background-color: #2789D0;
                }
                QPushButton#detailLaunch:disabled {
                    background-color: #1A1E26;
                    color: #6F7682;
                    border-color: #252A33;
                }
            """)

    def _stop_game(self, game_id: int):
        """Terminate the active game process and its sandbox container."""
        stopped = False
        for tracker in list(self.playtime_trackers):
            if tracker.game_id == game_id:
                if tracker.process and tracker.process.poll() is None:
                    try:
                        tracker.process.terminate()
                        stopped = True
                    except Exception as e:
                        logger.warning(f"Error terminating game process {game_id}: {e}")
                if hasattr(tracker, "sandbox_name") and tracker.sandbox_name:
                    _shutdown_firejail_sandbox(sandbox_name=tracker.sandbox_name)
                    stopped = True
        if stopped:
            self._show_toast("Stopping game container...")
            logger.info(f"Stop signal sent to Game ID {game_id}")
        else:
            self.running_game_ids.discard(game_id)
            self._update_detail_launch_button(game_id)

    def _launch_mode(self, game_id: int, path: str, exe: str, selected_mode: str, sandbox: bool = True):
        """Helper to launch a game directly with the chosen mode"""
        logger.info(f"Initiating launch for Game ID {game_id}: exe='{exe}', mode='{selected_mode}', path='{path}'")
        if not path or not os.path.exists(path):
            logger.error(f"Cannot launch Game ID {game_id}: Path does not exist on disk ({path})")
            QMessageBox.warning(self, "Missing Game", f"Cannot launch game. Path does not exist:\n{path}")
            return

        # Check dependencies first. If firejail is missing, present distro install warning popup
        deps = getattr(self.runner, 'check_dependencies', lambda: {})()
        if deps and not deps.get('firejail', True):
            logger.warning("Firejail dependency is missing. Prompting user with MissingDependencyDialog.")
            dialog = MissingDependencyDialog(parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return

        try:
            game_name = self.games_by_id.get(game_id, (None, "Game"))[1]

            game_data = self.games_by_id.get(game_id, ())
            steam_id = str(game_data[6]).strip() if len(game_data) > 6 and game_data[6] else ""
            selected_proton = game_data[12] if len(game_data) > 12 and game_data[12] else self.proton_path
            game_env_vars = self.db.get_game_env_vars(game_id)

            # Pre-launch Cloud Save Synchronization. The heavy zip/dir I/O runs
            # on a worker thread behind a modal progress indicator; the launch
            # itself continues in _continue_launch once resolution arrives.
            ctx = {
                "game_id": game_id,
                "game_name": game_name,
                "path": path,
                "exe": exe,
                "selected_mode": selected_mode,
                "selected_proton": selected_proton,
                "steam_id": steam_id,
                "sandbox": sandbox,
                "env_vars": game_env_vars,
            }
            self._schedule_prelaunch_sync(ctx)
            return
        except Exception as e:
            logger.error(f"Failed to launch game ID {game_id}: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to launch game: {str(e)}")

    def _schedule_prelaunch_sync(self, ctx: dict):
        """Resolve cloud-save state for a pending launch on a worker thread."""
        game_name = ctx["game_name"]
        path = ctx["path"]
        steam_id = ctx["steam_id"]

        progress = QProgressDialog(f"Checking cloud saves for '{game_name}'…", None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(150)
        progress.show()

        def _work():
            payload = {"proceed": True, "needs_conflict": False, "toast": "", "ctx": ctx}
            try:
                status, local_stats, cloud_stats = CloudSaveSyncEngine.check_sync_status(game_name, path, steam_id)
                if status == SyncStatus.CLOUD_ONLY:
                    ok = CloudSaveSyncEngine.sync_cloud_to_local(game_name, path)
                    if ok:
                        payload["toast"] = f"Restored cloud save for '{game_name}'."
                elif status == SyncStatus.CLOUD_NEWER:
                    auto_newer = self.settings.value("auto_prefer_newer_saves", False, type=bool)
                    if auto_newer:
                        ok = CloudSaveSyncEngine.sync_cloud_to_local(game_name, path)
                        if ok:
                            payload["toast"] = f"Updated to newer cloud save for '{game_name}'."
                    else:
                        payload["needs_conflict"] = True
                        payload["local_stats"] = local_stats
                        payload["cloud_stats"] = cloud_stats
            except Exception as sync_check_err:
                logger.warning(f"Pre-launch cloud sync check failed: {sync_check_err}")
            try:
                progress.deleteLater()
            except RuntimeError:
                pass
            self._prelaunch_resolved.emit(payload)

        self._prelaunch_resolved.connect(self._finish_prelaunch_sync)
        threading.Thread(target=_work, daemon=True, name="SafeLauncher-PrelaunchSync").start()

    def _finish_prelaunch_sync(self, payload: dict):
        """GUI-thread continuation after the pre-launch sync worker resolves."""
        try:
            self._prelaunch_resolved.disconnect(self._finish_prelaunch_sync)
        except TypeError:
            pass
        ctx = payload.get("ctx", {})

        if payload.get("needs_conflict"):
            conflict_dlg = SaveConflictDialog(ctx.get("game_name", ""), payload["local_stats"], payload["cloud_stats"], parent=self)
            if conflict_dlg.exec() == QDialog.DialogCode.Accepted:
                if conflict_dlg.cb_always_newer.isChecked():
                    self.settings.setValue("auto_prefer_newer_saves", True)
                if conflict_dlg.choice == "cloud":
                    if CloudSaveSyncEngine.sync_cloud_to_local(ctx["game_name"], ctx["path"]):
                        self._show_toast(f"Restored newer cloud save for '{ctx['game_name']}'.")
                else:
                    CloudSaveSyncEngine.sync_local_to_cloud(ctx["game_name"], ctx["path"], ctx["steam_id"])
                    self._show_toast("Overwrote cloud save with local version.")
            else:
                return
        elif payload.get("toast"):
            self._show_toast(payload["toast"])

        self._continue_launch(ctx)

    def _continue_launch(self, ctx: dict):
        """Perform the actual process launch and follow-up wiring (main thread)."""
        game_id = ctx["game_id"]
        game_name = ctx["game_name"]
        path = ctx["path"]
        exe = ctx["exe"]
        steam_id = ctx["steam_id"]
        sandbox = ctx["sandbox"]
        env_vars = ctx["env_vars"]
        selected_mode = ctx["selected_mode"]
        selected_proton = ctx["selected_proton"]
        try:
            if hasattr(self.runner, "set_proton_path"):
                self.runner.set_proton_path(selected_proton)
            process = self.runner.launch(path, exe, selected_mode, steam_id, sandbox=sandbox, env_vars=env_vars)
            if process:
                logger.info(f"Successfully launched '{game_name}' (PID: {process.pid})")
                self.running_game_ids.add(game_id)
                if self.selected_game and self.selected_game[0] == game_id:
                    self._update_detail_launch_button(game_id)
                # Update Discord Rich Presence
                if hasattr(self, 'discord_rpc') and self.discord_rpc:
                    import time
                    self.discord_rpc.set_activity(game_name, start_timestamp=int(time.time()), details="Playing in Sandbox")

                tracker = PlaytimeTrackerThread(game_id, process, parent=self)
                tracker.playtime_recorded.connect(self._on_playtime_recorded)
                tracker.finished.connect(lambda t=tracker: self._cleanup_tracker(t))
                tracker.start()
                self.playtime_trackers.append(tracker)

                # Auto-start GPU recorder / replay buffer on game launch if configured
                if getattr(self, "gpu_recorder_config", None) and self.gpu_recorder_config.enabled:
                    if self.gpu_recorder_config.mode in ("replay_buffer", "auto_game"):
                        rec_svc = GpuRecorderService.instance()
                        if not rec_svc.is_running():
                            is_rep = (self.gpu_recorder_config.mode == "replay_buffer")
                            rec_svc.start_recording(game_name, is_replay=is_rep)
                            if is_rep:
                                show_ingame_notification(
                                    "Replay Buffer Active",
                                    f"{self.gpu_recorder_config.capture_hotkey} → save clip",
                                    icon_type="replay",
                                    enabled=self.gpu_recorder_config.in_game_overlay,
                                    target_screen=self.gpu_recorder_config.target_screen
                                )

                # Show animated Safe Launch Popup with console log stream & greeting (non-blocking)
                popup = SafeLaunchDialog(game_name, user_name=self.user_name, process=process, parent=self)
                popup.retry_requested.connect(
                    lambda retry_mode: self._launch_mode(game_id, path, exe, retry_mode, sandbox=True)
                )
                popup.unsafe_launch_requested.connect(
                    lambda: self._launch_mode(game_id, path, exe, selected_mode, sandbox=False)
                )
                popup.show()
        except Exception as e:
            logger.error(f"Failed to launch game ID {game_id}: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to launch game: {str(e)}")

    def _get_selected_game(self):
        """Get the currently selected game"""
        return self.selected_game

    def _open_prefix_maintenance(self):
        game = self._get_selected_game()
        if not game:
            return
        PrefixMaintenanceDialog(game[2], self).exec()

    def _set_game_runtime(self):
        game = self._get_selected_game()
        if not game:
            return
        current = game[12] if len(game) > 12 and game[12] else os.path.expanduser("~/.local/share/umu")
        path = QFileDialog.getExistingDirectory(self, "Select Proton runtime for this game", current)
        if path:
            self.db.update_game_proton_path(game[0], os.path.realpath(path))
            self._refresh_library()
            self._select_game_by_id(game[0])

    def _on_restore_game(self):
        """Restore an archived game back to the active library."""
        game = self._get_selected_game()
        if not game:
            return
        game_id = game[0]
        self.db.restore_game(game_id)
        self._show_toast(f"Restored '{game[1]}' to library.")
        self._refresh_library()
        self._select_game_by_id(game_id)

    def _launch_game_by_id(self, game_id: int):
        """Directly select and launch game by its ID."""
        self._select_game_by_id(game_id)
        self._on_launch()

    def _on_launch(self):
        """Launch selected game directly using default mode, or stop if already running."""
        game = self._get_selected_game()
        if not game:
            self._show_toast("Please select a game to launch.", is_error=True)
            return

        is_archived = bool(game[17]) if len(game) > 17 and game[17] else False
        if is_archived:
            self._on_restore_game()
            return

        game_id, name, path, exe, mode, banner_url, steam_id, *_ = (*game, 0)

        if game_id in self.running_game_ids:
            self._stop_game(game_id)
            return

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

    def keyPressEvent(self, event):
        """Global keyboard shortcuts for library navigation and recorder fallbacks."""
        key = event.key()
        modifiers = event.modifiers()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.selected_game:
                self._on_launch()
                event.accept()
                return
        elif key == Qt.Key.Key_Delete:
            if self.selected_game:
                self._on_remove()
                event.accept()
                return
        elif key == Qt.Key.Key_F and (modifiers & Qt.KeyboardModifier.ControlModifier):
            if hasattr(self, "title_bar") and hasattr(self.title_bar, "search_input"):
                self.title_bar.search_input.setFocus()
                self.title_bar.search_input.selectAll()
                event.accept()
                return

        # Screenshot fallback for environments without X11 global grabs (e.g. Wayland).
        if key == Qt.Key.Key_F12:
            self._take_screenshot()

        # Local hotkey fallbacks for the wl-screenrec addon.
        if getattr(self, "wl_recorder_config", None) and self.wl_recorder_config.enabled:
            key_name = ""
            if key == Qt.Key.Key_F9:
                key_name = "F9"
            elif key == Qt.Key.Key_F10:
                key_name = "F10"
            elif key == Qt.Key.Key_F11:
                key_name = "F11"
            elif key == Qt.Key.Key_F12:
                key_name = "F12"

            if key_name:
                if key_name == self.wl_recorder_config.capture_hotkey:
                    self._toggle_game_recording()
                elif key_name == self.wl_recorder_config.replay_hotkey:
                    self._trigger_replay_save()

        super().keyPressEvent(event)

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
        self.running_game_ids.discard(tracker.game_id)
        if self.selected_game and self.selected_game[0] == tracker.game_id:
            self._update_detail_launch_button(tracker.game_id)
        if hasattr(self, 'discord_rpc') and self.discord_rpc and len(self.playtime_trackers) == 0:
            self.discord_rpc.clear_activity()

        # Standby: if no games are running, stop automatic recorder so launcher stays idle
        if not self.running_game_ids:
            if getattr(self, "gpu_recorder_config", None) and self.gpu_recorder_config.enabled:
                if self.gpu_recorder_config.mode in ("replay_buffer", "auto_game"):
                    rec_svc = GpuRecorderService.instance()
                    if rec_svc.is_running():
                        rec_svc.stop_recording()
                        logger.info("GPU recorder put on standby (all games closed)")

        # Auto Cloud Save Sync on Game Exit. Runs on a worker thread — zipping
        # multi-GB save trees must never freeze the GUI. Only uploads when
        # local state is genuinely newer; an unconditional upload would clobber
        # a cloud archive a newer session on another machine produced.
        try:
            game_rec = self.games_by_id.get(tracker.game_id)
            if game_rec:
                g_name = game_rec[1]
                g_path = game_rec[2]
                g_steam_id = str(game_rec[6]).strip() if len(game_rec) > 6 and game_rec[6] else ""

                def _exit_sync(name=g_name, gpath=g_path, sid=g_steam_id):
                    payload = {"game": name, "outcome": "skipped", "reason": ""}
                    try:
                        status, _, _ = CloudSaveSyncEngine.check_sync_status(name, gpath, sid)
                        if status == SyncStatus.LOCAL_NEWER:
                            payload["outcome"] = (
                                "uploaded" if CloudSaveSyncEngine.sync_local_to_cloud(name, gpath, sid)
                                else "failed"
                            )
                            payload["reason"] = status.value
                        else:
                            payload["outcome"] = "skipped"
                            payload["reason"] = status.value
                    except Exception as err:
                        logger.warning(f"Auto cloud save sync on game exit failed: {err}")
                        payload["outcome"] = "failed"
                        payload["reason"] = str(err)
                    return payload

                self._save_op_done.connect(self._on_exit_save_sync_done)
                threading.Thread(
                    target=lambda: self._save_op_done.emit(_exit_sync()),
                    daemon=True,
                    name="SafeLauncher-ExitSync",
                ).start()
        except Exception as sync_exit_err:
            logger.warning(f"Auto cloud save sync on game exit failed: {sync_exit_err}")

    def _on_exit_save_sync_done(self, payload: dict):
        """GUI-thread slot reporting the outcome of the background exit upload."""
        try:
            self._save_op_done.disconnect(self._on_exit_save_sync_done)
        except TypeError:
            pass
        name = payload.get("game", "")
        outcome = payload.get("outcome")
        if outcome == "uploaded":
            self._show_toast(f"Cloud save synced for '{name}'.")
            if self.selected_game and self.selected_game[1] == name:
                self._update_detail_panel()
        elif outcome == "failed":
            logger.warning(f"Exit cloud-save upload failed for '{name}'.")
        elif payload.get("reason") in ("cloud_newer", "cloud_only"):
            logger.info(
                f"Skipping exit upload for '{name}': cloud save is newer "
                f"({payload['reason']}); use the Save Manager to resolve manually."
            )

    def _start_background_cloud_sync(self):
        """Run startup cloud save check & sync sweep across library on a daemon thread."""
        games_snapshot = list(self.games)
        if not games_snapshot:
            return

        def _work():
            payload = {
                "uploaded": [],
                "newer_in_cloud": [],
                "checked_count": 0,
                "game_statuses": {},
            }
            try:
                for g in games_snapshot:
                    if len(g) < 5:
                        continue
                    game_id, name, path, exe, mode = g[0], g[1], g[2], g[3], g[4]
                    steam_id = str(g[6]).strip() if len(g) > 6 and g[6] else ""
                    try:
                        status, l_stat, c_stat = CloudSaveSyncEngine.check_sync_status(name, path, steam_id)
                        payload["checked_count"] += 1
                        if status == SyncStatus.LOCAL_NEWER:
                            # Auto-upload unsynced local saves to cloud
                            if CloudSaveSyncEngine.sync_local_to_cloud(name, path, steam_id):
                                payload["uploaded"].append(name)
                                status = SyncStatus.IN_SYNC
                        elif status in (SyncStatus.CLOUD_NEWER, SyncStatus.CLOUD_ONLY):
                            payload["newer_in_cloud"].append(name)
                        payload["game_statuses"][game_id] = status
                    except Exception as ge:
                        logger.debug(f"Startup cloud check failed for '{name}': {ge}")
            except Exception as e:
                logger.warning(f"Startup cloud sync sweep failed: {e}")

            return payload

        self._startup_sync_done.connect(self._on_startup_cloud_sync_done)
        threading.Thread(
            target=lambda: self._startup_sync_done.emit(_work()),
            daemon=True,
            name="SafeLauncher-StartupCloudSync",
        ).start()

    def _on_startup_cloud_sync_done(self, payload: dict):
        """GUI-thread handler for library startup cloud sweep results."""
        try:
            self._startup_sync_done.disconnect(self._on_startup_cloud_sync_done)
        except TypeError:
            pass

        # Update each visible card badge in library and cache
        for game_id, status in payload.get("game_statuses", {}).items():
            self.cloud_save_status_cache[game_id] = (status, None, None)
            if game_id in self.banner_widgets:
                self.banner_widgets[game_id].set_cloud_status(status)

        if self.selected_game and self.selected_game[0] in self.cloud_save_status_cache:
            sel_id = self.selected_game[0]
            self._render_cloud_status(sel_id, self.cloud_save_status_cache[sel_id][0])

        uploaded = payload.get("uploaded", [])
        newer_in_cloud = payload.get("newer_in_cloud", [])

        if uploaded:
            names = ", ".join(uploaded[:2])
            extra = f" (+{len(uploaded)-2} more)" if len(uploaded) > 2 else ""
            self._show_toast(f"Cloud Sync: Backed up {names}{extra}.")
        elif newer_in_cloud:
            names = ", ".join(newer_in_cloud[:2])
            extra = f" (+{len(newer_in_cloud)-2} more)" if len(newer_in_cloud) > 2 else ""
            self._show_toast(f"Newer cloud save(s) available for: {names}{extra}")

    def closeEvent(self, event):
        """Stop all background workers, then destroy the main window.

        Quit must ALWAYS succeed: the window hides immediately for feedback,
        every work source is stopped once, and past a hard deadline stubborn
        threads are terminated instead of being waited on forever.
        """
        import time as _time

        first_attempt = getattr(self, "_shutdown_deadline", 0.0) == 0.0
        if first_attempt:
            self._shutdown_deadline = _time.monotonic() + 12.0
            # Immediate visual feedback: quit looks like quit.
            self.hide()

            # Halt every source that schedules new background work while we
            # are trying to shut down.
            for timer_name in ("drive_check_timer", "_size_resort_timer"):
                timer = getattr(self, timer_name, None)
                if timer is not None:
                    try:
                        timer.stop()
                    except RuntimeError:
                        pass
            listener = getattr(self, "global_hotkeys", None)
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass

        self._pending_auto_fetchers = []  # queued fetchers were never started
        for fetcher in list(self.metadata_fetchers):
            if fetcher.isRunning():
                fetcher.requestInterruption()
        for fetcher in list(self.auto_fetchers):
            if fetcher.isRunning() and hasattr(fetcher, "requestInterruption"):
                fetcher.requestInterruption()
        for tracker in list(self.playtime_trackers):
            tracker.stop()

        workers = list(self.metadata_fetchers) + list(self.auto_fetchers) + list(self.playtime_trackers)
        for worker in workers:
            if worker.isRunning():
                # Network requests use short timeouts, but allow enough time
                # for the active request to return before Qt destroys QThread.
                worker.wait(1500)

        still_running = [w for w in workers if w.isRunning()]
        if still_running and _time.monotonic() < self._shutdown_deadline:
            QTimer.singleShot(100, self.close)
            event.ignore()
            return

        if still_running:
            logger.error(
                f"Forcing quit past shutdown deadline; terminating "
                f"{len(still_running)} unresponsive worker(s)."
            )
            for worker in still_running:
                worker.terminate()
                worker.wait(1000)

        if hasattr(self, "tray_icon") and self.tray_icon:
            try:
                self.tray_icon.hide()
            except Exception:
                pass

        try:
            from core.plugins.gpu_screen_recorder import GpuRecorderService
            GpuRecorderService.instance().stop_recording()
        except Exception:
            pass

        if hasattr(self, "discord_rpc") and self.discord_rpc:
            try:
                self.discord_rpc.clear_activity()
            except Exception:
                pass

        super().closeEvent(event)

        app = QApplication.instance()
        if app:
            app.quit()

    def _on_add(self):
        dialog = AddGameDialog(self, self.sgdb_client)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, path, exe, mode, banner_path = dialog.get_values()
            steam_id = dialog.get_steam_id()
            version_override, patch_notes_url = dialog.get_version_metadata()
            if not name or not path or not exe:
                QMessageBox.warning(self, "Error", "All fields are required.")
                return
            if not os.path.isdir(path):
                QMessageBox.warning(self, "Error", "Invalid game path.")
                return
            
            path = os.path.abspath(os.path.expanduser(path))
            save_sandbox_config(path, exe)
            game_id = self.db.add_game(name, path, exe, mode, banner_path, steam_id or None)
            if game_id:
                self.db.update_game_version_metadata(game_id, version_override, patch_notes_url)
            self._refresh_library()
            if game_id and steam_id:
                self._capture_initial_steam_build(game_id, steam_id)
            self._show_toast(f"Game '{name}' added to library.")

    def _on_card_size_changed(self, value: int):
        """Update card banner size dynamically when user moves bottom size slider."""
        if hasattr(self, 'grid_container') and self.grid_container:
            self.grid_container.set_card_width(value)

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
        dialog.mark_current_requested.connect(self._mark_build_current_from_config)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, path, exe, mode, banner_path = dialog.get_values()
            version_override, patch_notes_url = dialog.get_version_metadata()
            manual_build_id = dialog.get_build_id()
            if not name or not path or not exe:
                QMessageBox.warning(self, "Error", "All fields are required.")
                return
            if not os.path.isdir(path):
                QMessageBox.warning(self, "Error", "Invalid game path.")
                return

            game_id = game[0]
            path = os.path.abspath(os.path.expanduser(path))
            if mode not in ("umu", "umu_net", "wine", "linux"):
                logger.warning(f"Invalid runner mode '{mode}' for game {game_id}; keeping existing mode.")
                mode = game[4] if game[4] in ("umu", "umu_net", "wine", "linux") else "umu"
            save_sandbox_config(path, exe)
            self.db.update_game(game_id, name, path, exe, mode, banner_path)
            self.db.update_game_mode(game_id, mode)
            logger.info(f"Saved game settings for {game_id}: executable='{exe}', mode='{mode}'")
            self.db.update_game_version_metadata(game_id, version_override, patch_notes_url)
            if manual_build_id is not None:
                self.db.update_build_id(game_id, manual_build_id)
                self.local_version_by_game_id[game_id] = (manual_build_id, 0)
                # Clear cached update status so it re-checks against new manual build
                self.metadata_attempted_builds.discard(game_id)
                self.steam_check_results.pop(game_id, None)
            self._refresh_library()
            self._show_toast(f"Updated settings for '{name}'.")
    

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
                self._show_toast(f"Found and added {added_count} new game(s) from sandbox.")
        else:
            if not quiet:
                self._show_toast("Sandbox synced (no new games found).")

    def _on_remove(self):
        game = self._get_selected_game()
        if not game:
            self._show_toast("Please select a game to remove.", is_error=True)
            return
        
        game_id = game[0]
        is_archived = bool(game[17]) if len(game) > 17 and game[17] else False

        def _force_delete_game_path(target_path: str):
            if not target_path or not os.path.exists(target_path):
                return
            try:
                def _handle_readonly(func, subpath, exc_info):
                    try:
                        os.chmod(subpath, 0o777)
                        func(subpath)
                    except Exception:
                        pass

                if os.path.isfile(target_path) or os.path.islink(target_path):
                    try:
                        os.chmod(target_path, 0o777)
                    except Exception:
                        pass
                    os.unlink(target_path)
                elif os.path.isdir(target_path):
                    try:
                        shutil.rmtree(target_path, on_exc=_handle_readonly)
                    except TypeError:
                        shutil.rmtree(target_path, onerror=_handle_readonly)
                logger.info(f"Force deleted game files at: {target_path}")
            except Exception as e:
                logger.warning(f"Could not force delete game files at '{target_path}': {e}")

        if is_archived:
            reply = QMessageBox.question(
                self,
                "Permanently Delete",
                f"Permanently delete '{game[1]}' and all its recorded history and files from SafeLauncher?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                game_path = game[2]
                resolved_path = os.path.realpath(os.path.expanduser(game_path)) if game_path else ""
                _force_delete_game_path(resolved_path)
                self.db.remove_game(game_id)
                self._show_toast(f"Permanently removed '{game[1]}'.")
                self.selected_game = None
                self._refresh_library()
            return

        dialog = CustomRemoveDialog(game[1], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            game_path = game[2]
            resolved_path = os.path.realpath(os.path.expanduser(game_path)) if game_path else ""

            if dialog.choice == 'archive_delete_disk':
                sandbox_root = os.path.realpath(os.path.expanduser(DEFAULT_SANDBOX_DIR))
                try:
                    inside_sandbox = os.path.commonpath([sandbox_root, resolved_path]) == sandbox_root
                except ValueError:
                    inside_sandbox = False
                if inside_sandbox and resolved_path != sandbox_root:
                    _force_delete_game_path(resolved_path)
                elif resolved_path and os.path.exists(resolved_path):
                    _force_delete_game_path(resolved_path)

                self.db.archive_game(game_id, True)
                self._show_toast(f"Archived '{game[1]}' and force deleted files from disk.")
            elif dialog.choice == 'archive_keep':
                self.db.archive_game(game_id, True)
                self._show_toast(f"Archived '{game[1]}' (files preserved on disk).")
            elif dialog.choice == 'purge_permanently':
                # Force delete all game files from disk without going to trash
                _force_delete_game_path(resolved_path)
                self.db.remove_game(game_id)
                self._show_toast(f"Permanently removed '{game[1]}' and force deleted files.")

            self._refresh_library()
            self.selected_game = None

    def _on_export(self):
        game = self._get_selected_game()
        if not game:
            QMessageBox.warning(self, "Warning", "Please select a game.")
            return
        steam_id = str(game[6]).strip() if len(game) > 6 and game[6] else ""
        SaveManagerDialog(game[0], game[1], game[2], steam_id, self).exec()
    
    def _on_import(self):
        game = self._get_selected_game()
        if not game:
            self._show_toast("Please select a game to import save.", is_error=True)
            return
        steam_id = str(game[6]).strip() if len(game) > 6 and game[6] else ""
        dlg = SaveManagerDialog(game[0], game[1], game[2], steam_id, self)
        dlg._import_snapshot()
