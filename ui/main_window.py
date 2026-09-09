import os
import re
import time
import shutil
import subprocess
from dataclasses import replace
from datetime import datetime
from html import escape
from typing import Optional, List, Dict, Tuple, Any, Set

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QGridLayout, QFileDialog, QMessageBox, QDialog, QLabel, QLineEdit,
    QComboBox, QFormLayout, QScrollArea, QFrame, QListWidget, QListWidgetItem, QMenu,
    QApplication, QSystemTrayIcon, QCheckBox, QPlainTextEdit, QProgressBar,
    QSlider, QSplitter, QDialogButtonBox, QInputDialog, QSizePolicy,
    QProgressDialog
)
from PyQt6.QtCore import (
    Qt, QSize, QPoint, pyqtSignal, QVariantAnimation, QEasingCurve, QTimer,
    QUrl, QSettings, QAbstractAnimation, QEvent,
)
from PyQt6.QtGui import QPixmap, QFont, QColor, QIcon, QPainter, QMovie, QDesktopServices, QKeySequence, QShortcut
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
from core.library_state import LibraryStateStore
from core.library_controller import LibraryController, LibraryQuery, LibrarySnapshot
from core.game_status import GameStatusState, cloud_indicator
from core.save_state import SaveStateStore
from core.cloud_operations import CloudSyncCoordinator
from core.game_status import GameStatusState, cloud_indicator
from ui.icons import (
    LOGO_PATH, GIF_PATH, CONFIRM_GIF_PATH, draw_custom_lock_pixmap,
    get_app_icon, get_icon,
)
from ui.maintenance_dialogs import PrefixMaintenanceDialog
from ui.dialogs.achievements_dialog import create_rounded_pixmap
import html

logger = get_logger("UI")

from ui.threads import (
    BannerFetcher, BannerDownloader, BannerAutoFetcher, ArchiveExtractorThread,
    GitHubReleasesFetcherThread, UmuBootstrapWorker, SafeLaunchLogReader,
    DiskSizeFetcherThread, HeroFetcherThread, IconAutoFetcherThread,
    CloudSaveStatusFetcherThread, CloudSaveBatchQueueWorker,
    AchievementStatusFetcherThread, AchievementBatchQueueWorker
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
from ui.components.virtual_grid import BannerProxy
from ui.components.library_view_host import LibraryViewHost
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
from core.cloud_save_sync import SyncStatus
from core.performance_env import MANAGED_ENV_KEYS
from ui.theme import (
    get_application_stylesheet, btn_primary_style, btn_secondary_style,
    btn_tertiary_style, btn_destructive_style, BG_APP, SURFACE, SURFACE_ELEVATED,
    BORDER, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, ACCENT_PRIMARY
)


import getpass
from core.playtime_tracker import PlaytimeTrackerThread, _shutdown_firejail_sandbox
from core.game_session import GameSessionManager
from core.safe_thread import FunctionWorker, WorkerSupervisor
from core.operation_registry import OperationRegistry
from ui.components.activity_drawer import ActivityDrawer


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
    _cloud_poll_changed = pyqtSignal(list)    # games whose cloud save changed mid-session
    _save_restore_finished = pyqtSignal(object)  # structured manual restore result
    _prelaunch_restore_done = pyqtSignal(object)          # {"ctx", "ok", "toast"} after conflict restore
    _startup_backend_health_ready = pyqtSignal(object)


    def __init__(self, db: GameDatabase, runner: ISandboxRunner, backup: IBackupManager):
        super().__init__()
        self.db = db
        self.runner = runner
        self.backup = backup
        self.sgdb_client = SteamGridDBClient()
        # Keep the cache path available to every library presentation,
        # including the empty-filter branch used by compact view.
        self.cache_dir = self.sgdb_client.cache_dir
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
        self.game_status_by_id = {}
        self.game_status_by_id = {}
        self.steam_check_results = {}
        self.save_state_store = SaveStateStore()
        # Compatibility mapping view for existing library rendering code.
        self.cloud_save_status_cache = self.save_state_store
        self.cloud_sync_coordinator = CloudSyncCoordinator()
        # Incremented whenever the configured cloud identity changes. Every
        # asynchronous status result is bound to the generation that created it.
        # Compatibility alias; the coordinator is the authoritative owner of
        # cloud configuration generations.
        self._cloud_context_generation = self.cloud_sync_coordinator.generation
        self.achievement_status_cache = {}
        self.achievement_resolution_cache = {}
        self._achievement_checked_ts = {}
        self._achievement_poll_timer = None
        # A watcher can observe an unlock before its schema worker finishes.
        # Keep it in memory until the schema gives the API name a durable row;
        # otherwise the one-shot live event would be silently lost.
        self._pending_achievement_unlocks = {}
        self.local_version_by_game_id = {}
        self.metadata_attempted_tags = set()
        self._steam_build_checked_ts = {}
        self._hero_attempted = set()
        self._icon_attempted = set()
        self.playtime_trackers = []  # keep references so GC doesn't kill running threads
        self.game_sessions = GameSessionManager(self)
        self.game_sessions.session_state_changed.connect(self._on_game_session_state_changed)
        self._stopping_game_ids = set()  # game IDs transitioning from running to stopped
        self._background_workers = []  # authoritative registry for shutdown (see _register_worker)
        self._retiring_workers = []  # retain retiring threads until completely stopped to avoid GC destroying running QThread
        self.worker_supervisor = WorkerSupervisor(self)
        self.worker_supervisor.worker_finished.connect(self._on_supervised_worker_finished)
        # running_game_ids is derived from the session supervisor, not from
        # UI widgets or the playtime tracker feature list.
        self.topbar_extractor_thread = None
        self._size_fetch_scheduled = set()  # game dirs queued for background sizing
        self._size_resort_timer = QTimer(self)
        self._size_resort_timer.setSingleShot(True)
        self._size_resort_timer.setInterval(400)
        self._size_resort_timer.timeout.connect(self._refresh_library)
        # Coalesce asynchronous Steam results: every presentation is rebuilt
        # from the same update-status map, without one network result causing
        # three separate full library renders.
        self._update_status_refresh_timer = QTimer(self)
        self._update_status_refresh_timer.setSingleShot(True)
        self._update_status_refresh_timer.setInterval(80)
        self._update_status_refresh_timer.timeout.connect(self._refresh_library)
        self.games_by_id = {}
        self.library_controller = LibraryController()
        self.library_state = LibraryStateStore(self.library_controller)
        # Compatibility alias retained for existing selection actions; the
        # store is now the owner rather than MainWindow.
        self.library_selection = self.library_state.selection
        self.library_snapshot = self.library_state.snapshot
        self.operation_registry = OperationRegistry(self)
        self.achievement_watchers = {}
        self.active_toasts = []
        self._load_persistent_cache()

        # Always-connected signal carriers. A connect/disconnect dance around
        # each use mis-orders or drops payloads when two events overlap (two
        # games exiting at once, rapid consecutive launches).
        self._save_op_done.connect(self._on_exit_save_sync_done)
        self._prelaunch_resolved.connect(self._finish_prelaunch_sync)
        self._save_restore_finished.connect(self._on_save_restore_finished)
        self._prelaunch_restore_done.connect(self._on_prelaunch_restore_done)
        self._startup_backend_health_ready.connect(self._on_startup_backend_health_ready)


        # Background maintenance: prune orphaned temp files
        try:
            from core.prefix_sanitizer import cleanup_global_temp_files
            self._start_managed_task(
                "SafeLauncher-TempPrune",
                cleanup_global_temp_files,
                lambda result: logger.debug("Temporary-file cleanup finished: %s", result),
            )
        except Exception:
            pass

        self.search_query = ""
        self.settings = QSettings("SafeLauncher", "SafeLauncher")
        # CI/UI smoke tests must not depend on DNS or third-party response
        # timing.  This only disables *automatic* background network work;
        # explicit user actions continue to use their normal code paths.
        self._offline_test_mode = os.environ.get("SAFELAUNCHER_OFFLINE_TEST_MODE") == "1"
        # Compact is the product default.  Older releases persisted Grid/List
        # even though Compact became the primary unified library experience,
        # so migrate that stale preference once rather than surprising every
        # existing user on each later launch.
        if not self.settings.value("compact_view_default_migrated", False, type=bool):
            self.library_view_mode = "compact"
            self.settings.setValue("library_view_mode", self.library_view_mode)
            self.settings.setValue("compact_view_default_migrated", True)
        else:
            self.library_view_mode = self.settings.value("library_view_mode", "compact", type=str)
        if self.library_view_mode in ("steam", ""):
            self.library_view_mode = "compact"
        self.virtualization_threshold = self.settings.value("virtualization_threshold", 200, type=int)
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
        GpuRecorderService.instance().apply_config(self.gpu_recorder_config)

        # Headless smoke tests exercise widget wiring, not OS-wide input.
        # Starting an X11 listener there can leave a non-daemon thread blocked
        # on a runner display after Qt has finished, preventing CI from ever
        # exiting. Normal application runs retain the owned listener.
        self.global_hotkeys = GlobalHotkeyListener(self)
        self._update_global_hotkeys()
        self.global_hotkeys.hotkey_triggered.connect(self._on_global_hotkey)
        if not self._offline_test_mode:
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
        self.title_bar.filter_requested.connect(self._set_filter)
        self.title_bar.settings_requested.connect(self._open_settings)
        self.title_bar.toggle_collections_requested.connect(self._toggle_collections_panel)
        self.title_bar.sync_requested.connect(self._on_sync_sandbox)
        self.title_bar.install_archive_requested.connect(self._on_install_zip_archive)
        self.title_bar.check_updates_requested.connect(self._check_all_steam_updates)
        self.title_bar.open_sandbox_requested.connect(self._open_sandbox_dir)
        self.title_bar.export_save_requested.connect(self._on_export)
        self.title_bar.import_save_requested.connect(self._on_import)
        self.title_bar.disk_manager_requested.connect(self._open_disk_manager)

        # Non-intrusive Update Notification Banner (Hidden by default)
        self.update_banner = QFrame()
        self.update_banner.setVisible(False)
        self.update_banner.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1E3A8A, stop:1 #1D4ED8);
                border-bottom: 1px solid #3B82F6;
            }
        """)
        ub_layout = QHBoxLayout(self.update_banner)
        ub_layout.setContentsMargins(16, 6, 16, 6)
        ub_layout.setSpacing(10)

        self.lbl_update_banner_msg = QLabel("SafeLauncher update available")
        self.lbl_update_banner_msg.setStyleSheet("color: #FFFFFF; font-size: 12px; font-weight: bold;")
        ub_layout.addWidget(self.lbl_update_banner_msg)

        ub_layout.addStretch()

        self.btn_update_banner_action = QPushButton("Download & Apply")
        self.btn_update_banner_action.setStyleSheet("""
            QPushButton {
                background: #2563EB;
                color: #FFFFFF;
                border: 1px solid #60A5FA;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1D4ED8;
            }
        """)
        ub_layout.addWidget(self.btn_update_banner_action)

        self.btn_update_banner_dismiss = QPushButton("✕")
        self.btn_update_banner_dismiss.setFixedSize(22, 22)
        self.btn_update_banner_dismiss.setToolTip("Dismiss notification")
        self.btn_update_banner_dismiss.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #93C5FD;
                border: none;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #FFFFFF;
            }
        """)
        self.btn_update_banner_dismiss.clicked.connect(lambda: self.update_banner.setVisible(False))
        ub_layout.addWidget(self.btn_update_banner_dismiss)

        root_vbox.addWidget(self.update_banner)
        
        # Body Container Layout (Left Sidebar + Center/Right Splitter)
        body_widget = QWidget()
        body_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        body_widget.setStyleSheet("background: transparent;")
        body_layout = QHBoxLayout(body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        root_vbox.addWidget(body_widget)

        # 1. Left Collections Sidebar (On by default, collapsed)
        self.sidebar = LeftSidebarWidget(self)
        self.sidebar.setVisible(True)
        default_compact = self.settings.value("collections_collapsed", True, type=bool)
        self.sidebar.set_compact(default_compact)
        body_layout.addWidget(self.sidebar)
        self.sidebar.compact_changed.connect(
            lambda compact: self.settings.setValue("collections_collapsed", compact)
        )
        self.sidebar.filter_selected.connect(self._set_filter)
        self.sidebar.collection_selected.connect(self._set_collection_filter)
        self.sidebar.add_collection_requested.connect(self._on_add_collection)
        self.sidebar.size_changed.connect(self._on_card_size_changed)
        self.sidebar.profile_requested.connect(self._open_achievement_profile)
        self.sidebar.btn_settings.clicked.connect(self._open_settings)
        self.stat_label = QLabel()  # Keep hidden logic variable for tests

        # 2. Splitter Layout: Center Game Grid + Right Game Details Inspector Panel
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.splitter.setStyleSheet("""
            QSplitter {
                background: transparent;
            }
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
        self.detail_panel.setObjectName("detailPanel")
        self.detail_panel.setMinimumWidth(260)
        self.detail_panel.setMaximumWidth(480)
        self.detail_panel.setStyleSheet("""
            QFrame#detailPanel {
                background-color: #161618;
                border: none;
                border-left: 1px solid rgba(255, 255, 255, 0.06);
            }
            QLabel {
                color: #F5F7FA;
            }
        """)
        # Keep the inspector as a solid docked surface.  The library behind it
        # can remain translucent, but the right-side edit panel should not
        # reveal that background while it is opening or closing.
        self.detail_panel.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        self.detail_panel.setVisible(False)

        # Inspector panel slide animation setup. The panel itself remains
        # opaque throughout the animation.
        self.panel_anim = QVariantAnimation(self)
        self.panel_anim.setDuration(250)
        self.panel_anim.valueChanged.connect(self._on_panel_anim_step)
        self.panel_anim.finished.connect(self._on_panel_anim_finished)
        self._panel_expanding = False

        # Internal scrollable container for seamless scaling
        panel_outer_layout = QVBoxLayout(self.detail_panel)
        panel_outer_layout.setContentsMargins(0, 0, 0, 0)
        panel_outer_layout.setSpacing(0)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        
        detail_content = QWidget()
        detail_content.setStyleSheet("background: transparent;")
        detail_layout = QVBoxLayout(detail_content)
        # Keep the glassmorphism breathing room balanced vertically.
        detail_layout.setContentsMargins(16, 16, 16, 16)
        detail_layout.setSpacing(10)
        detail_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.detail_scroll.setWidget(detail_content)
        panel_outer_layout.addWidget(self.detail_scroll)

        # Top Bar with Inspector Header and Close button
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(2, 0, 0, 2)
        
        lbl_inspector_hdr = QLabel("INSPECTOR")
        lbl_inspector_hdr.setStyleSheet("color: #636366; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; background: transparent;")
        top_bar.addWidget(lbl_inspector_hdr)
        top_bar.addStretch()

        self.btn_hide_detail = QPushButton()
        self.btn_hide_detail.setIcon(get_icon("ph.x-bold", color="#8E8E93"))
        self.btn_hide_detail.setIconSize(QSize(12, 12))
        self.btn_hide_detail.setFixedSize(24, 24)
        self.btn_hide_detail.setToolTip("Close inspector")
        self.btn_hide_detail.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 12px;
            }
            QPushButton:hover {
                background: #202633;
            }
        """)
        self.btn_hide_detail.clicked.connect(lambda: self._animate_left_panel(False))
        top_bar.addWidget(self.btn_hide_detail)
        detail_layout.addLayout(top_bar)

        # Selected Game Cover Art Preview
        self.detail_cover = QLabel()
        self.detail_cover.setFixedSize(QSize(180, 270))
        self.detail_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_cover.setStyleSheet("""
            QLabel {
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
                background-color: #1C1C20;
            }
        """)
        
        cover_row = QHBoxLayout()
        cover_row.setContentsMargins(0, 2, 0, 4)
        cover_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_row.addWidget(self.detail_cover)
        detail_layout.addLayout(cover_row)

        # Selected Game Title Header Row
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)

        self.detail_title = QLabel("Select a Game")
        self.detail_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        self.detail_title.setWordWrap(True)
        self.detail_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_title.setStyleSheet("color: #FFFFFF; background: transparent; letter-spacing: -0.2px;")
        title_row.addWidget(self.detail_title, 1)

        detail_layout.addLayout(title_row)

        # Steam Tags Badge Container
        self.tags_widget = QWidget()
        self.tags_layout = QHBoxLayout(self.tags_widget)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setSpacing(6)
        self.tags_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail_layout.addWidget(self.tags_widget)

        # ── Unified Game Specs Card (Playtime, Last Played, Size, Cloud Save) ──
        self.detail_spec_card = QFrame()
        self.detail_spec_card.setObjectName("detailSpecCard")
        self.detail_spec_card.setStyleSheet("""
            QFrame#detailSpecCard {
                background-color: #11141A;
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 10px;
            }
        """)
        spec_layout = QGridLayout(self.detail_spec_card)
        spec_layout.setContentsMargins(12, 10, 12, 10)
        spec_layout.setHorizontalSpacing(14)
        spec_layout.setVerticalSpacing(6)

        # Col 0: Playtime
        lbl_pt_h = QLabel("PLAYTIME")
        lbl_pt_h.setStyleSheet("color: #636366; font-size: 9px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        spec_layout.addWidget(lbl_pt_h, 0, 0)
        self.detail_playtime = QLabel("--")
        self.detail_playtime.setStyleSheet("color: #F5F7FA; font-size: 11px; font-weight: 600; background: transparent;")
        spec_layout.addWidget(self.detail_playtime, 1, 0)

        # Col 1: Last Played
        lbl_lp_h = QLabel("LAST PLAYED")
        lbl_lp_h.setStyleSheet("color: #636366; font-size: 9px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        spec_layout.addWidget(lbl_lp_h, 0, 1)
        self.detail_last_played = QLabel("--")
        self.detail_last_played.setStyleSheet("color: #F5F7FA; font-size: 11px; font-weight: 600; background: transparent;")
        spec_layout.addWidget(self.detail_last_played, 1, 1)

        # Row 2, Col 0: Disk Size
        lbl_ds_h = QLabel("DISK SIZE")
        lbl_ds_h.setStyleSheet("color: #636366; font-size: 9px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        spec_layout.addWidget(lbl_ds_h, 2, 0)
        self.detail_disk_size = QLabel("--")
        self.detail_disk_size.setStyleSheet("color: #A1A1A6; font-size: 11px; font-weight: 500; background: transparent;")
        spec_layout.addWidget(self.detail_disk_size, 3, 0)

        # Row 2, Col 1: Cloud Sync
        lbl_cs_h = QLabel("CLOUD SAVE")
        lbl_cs_h.setStyleSheet("color: #636366; font-size: 9px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        spec_layout.addWidget(lbl_cs_h, 2, 1)

        cloud_box = QWidget()
        cloud_box.setStyleSheet("background: transparent;")
        cloud_box_layout = QHBoxLayout(cloud_box)
        cloud_box_layout.setContentsMargins(0, 0, 0, 0)
        cloud_box_layout.setSpacing(6)

        self.detail_cloud_status = QLabel("--")
        self.detail_cloud_status.setStyleSheet("color: #A1A1A6; font-size: 11px; font-weight: 500; background: transparent;")
        cloud_box_layout.addWidget(self.detail_cloud_status)

        self.btn_detail_cloud_restore = QPushButton("Restore")
        self.btn_detail_cloud_restore.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_detail_cloud_restore.setToolTip("Restore cloud save for this game")
        self.btn_detail_cloud_restore.setStyleSheet(
            "QPushButton { background: #2563EB; color: #FFFFFF; border: none; border-radius: 4px; "
            "padding: 2px 8px; font-size: 10px; font-weight: bold; } "
            "QPushButton:hover { background: #3B82F6; }"
        )
        self.btn_detail_cloud_restore.hide()
        self.btn_detail_cloud_restore.clicked.connect(self._restore_selected_game_cloud_save)
        cloud_box_layout.addWidget(self.btn_detail_cloud_restore)
        cloud_box_layout.addStretch()

        spec_layout.addWidget(cloud_box, 3, 1)

        detail_layout.addWidget(self.detail_spec_card)

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
        self.lbl_detail_versions.setStyleSheet("QLabel { color: #A1A1A6; background: #161A22; border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 6px; font-size: 10px; padding: 3px 8px; }")
        self.detail_update_layout.addWidget(self.lbl_detail_versions)

        self.btn_retry_steam = QPushButton("Retry")
        self.btn_retry_steam.setVisible(False)
        self.btn_retry_steam.setToolTip("Retry the Steam build check")
        self.btn_retry_steam.clicked.connect(self._retry_steam_check)
        self.detail_update_layout.addWidget(self.btn_retry_steam)

        self.detail_update_widget.setVisible(False)
        detail_layout.addWidget(self.detail_update_widget)

        # Primary Launch Game Button
        detail_layout.addSpacing(2)
        self.btn_detail_launch = QPushButton("Launch Game")
        self.btn_detail_launch.setObjectName("detailLaunch")
        self.btn_detail_launch.setIcon(get_icon("ph.play-bold", color="#FFFFFF"))
        self.btn_detail_launch.setIconSize(QSize(15, 15))
        self.btn_detail_launch.setFixedHeight(40)
        self.btn_detail_launch.setStyleSheet("""
            QPushButton#detailLaunch {
                background-color: #0A84FF;
                color: #FFFFFF;
                font-weight: 600;
                font-size: 13px;
                border: none;
                border-radius: 8px;
                padding: 0 16px;
                text-align: center;
                letter-spacing: 0.2px;
            }
            QPushButton#detailLaunch:hover {
                background-color: #0071E3;
            }
            QPushButton#detailLaunch:pressed {
                background-color: #005BB5;
            }
        """)
        self.btn_detail_launch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_detail_launch.clicked.connect(self._on_launch)
        detail_layout.addWidget(self.btn_detail_launch)

        sec_btn_style = """
            QPushButton {
                background-color: #161A22;
                color: #D1D5DB;
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
                padding: 0 10px;
                font-weight: 500;
                font-size: 11px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #202633;
                border-color: rgba(255, 255, 255, 0.12);
                color: #FFFFFF;
            }
            QPushButton:pressed {
                background-color: #10141B;
            }
        """

        # ── Secondary Action Buttons (2x2 Grid) ──
        actions_grid = QGridLayout()
        actions_grid.setContentsMargins(0, 0, 0, 0)
        actions_grid.setSpacing(6)

        self.btn_detail_edit = QPushButton("Edit Game")
        self.btn_detail_edit.setIcon(get_icon("ph.pencil-simple-bold", color="#0A84FF"))
        self.btn_detail_edit.setIconSize(QSize(14, 14))
        self.btn_detail_edit.setFixedHeight(32)
        self.btn_detail_edit.setStyleSheet(sec_btn_style)
        self.btn_detail_edit.clicked.connect(self._on_edit)
        actions_grid.addWidget(self.btn_detail_edit, 0, 0)

        self.btn_detail_properties = QPushButton("Properties")
        self.btn_detail_properties.setIcon(get_icon("ph.sliders-horizontal-bold", color="#8E8E93"))
        self.btn_detail_properties.setIconSize(QSize(14, 14))
        self.btn_detail_properties.setFixedHeight(32)
        self.btn_detail_properties.setStyleSheet(sec_btn_style)
        self.btn_detail_properties.clicked.connect(self._open_game_properties)
        actions_grid.addWidget(self.btn_detail_properties, 0, 1)

        self.btn_detail_screenshots = QPushButton("Screenshots")
        self.btn_detail_screenshots.setIcon(get_icon("ph.image-bold", color="#8E8E93"))
        self.btn_detail_screenshots.setIconSize(QSize(14, 14))
        self.btn_detail_screenshots.setFixedHeight(32)
        self.btn_detail_screenshots.setStyleSheet(sec_btn_style)
        self.btn_detail_screenshots.clicked.connect(self._open_screenshot_gallery)
        actions_grid.addWidget(self.btn_detail_screenshots, 1, 0)

        self.btn_detail_videos = QPushButton("Videos")
        self.btn_detail_videos.setIcon(get_icon("ph.video-camera-bold", color="#8E8E93"))
        self.btn_detail_videos.setIconSize(QSize(14, 14))
        self.btn_detail_videos.setFixedHeight(32)
        self.btn_detail_videos.setStyleSheet(sec_btn_style)
        self.btn_detail_videos.clicked.connect(self._open_video_gallery)
        actions_grid.addWidget(self.btn_detail_videos, 1, 1)

        detail_layout.addLayout(actions_grid)

        self.btn_detail_achievements = QPushButton("Achievements")
        self.btn_detail_achievements.setIcon(get_icon("ph.trophy-bold", color="#30D158"))
        self.btn_detail_achievements.setIconSize(QSize(14, 14))
        self.btn_detail_achievements.setFixedHeight(32)
        self.btn_detail_achievements.setStyleSheet(sec_btn_style)
        self.btn_detail_achievements.clicked.connect(self._open_achievements_dialog)
        self.btn_detail_achievements.setVisible(False)
        detail_layout.addWidget(self.btn_detail_achievements)

        # Apple-styled Achievement Preview Card in Inspector Detail Panel
        self.detail_ach_card = QFrame()
        self.detail_ach_card.setObjectName("detailAchCard")
        self.detail_ach_card.setStyleSheet("""
            QFrame#detailAchCard {
                background-color: #11141A;
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 10px;
                padding: 10px;
            }
            QFrame#detailAchCard:hover {
                background-color: #171B23;
                border-color: rgba(48, 209, 88, 0.3);
            }
        """)
        self.detail_ach_card.setCursor(Qt.CursorShape.PointingHandCursor)
        self.detail_ach_card.mousePressEvent = lambda e: self._open_achievements_dialog()

        ach_card_layout = QVBoxLayout(self.detail_ach_card)
        ach_card_layout.setContentsMargins(10, 8, 10, 8)
        ach_card_layout.setSpacing(6)

        ach_hdr_row = QHBoxLayout()
        ach_hdr_row.setContentsMargins(0, 0, 0, 0)
        ach_hdr_row.setSpacing(6)

        ach_title = QLabel("ACHIEVEMENTS")
        ach_title.setStyleSheet("color: #636366; font-size: 9px; font-weight: 700; letter-spacing: 0.8px; background: transparent;")
        ach_hdr_row.addWidget(ach_title)
        ach_hdr_row.addStretch()

        self.lbl_detail_ach_count = QLabel("0 / 0 (0%)")
        self.lbl_detail_ach_count.setStyleSheet("color: #30D158; font-size: 11px; font-weight: 700; background: transparent;")
        ach_hdr_row.addWidget(self.lbl_detail_ach_count)
        ach_card_layout.addLayout(ach_hdr_row)

        self.detail_ach_progress = QProgressBar()
        self.detail_ach_progress.setFixedHeight(4)
        self.detail_ach_progress.setTextVisible(False)
        self.detail_ach_progress.setRange(0, 100)
        self.detail_ach_progress.setValue(0)
        self.detail_ach_progress.setStyleSheet("""
            QProgressBar {
                background-color: #1A1F28;
                border: none;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #30D158, stop:1 #34C759);
                border-radius: 2px;
            }
        """)
        ach_card_layout.addWidget(self.detail_ach_progress)

        # Mini badge icons preview container
        self.detail_ach_badges_container = QWidget()
        self.detail_ach_badges_layout = QHBoxLayout(self.detail_ach_badges_container)
        self.detail_ach_badges_layout.setContentsMargins(0, 2, 0, 0)
        self.detail_ach_badges_layout.setSpacing(6)
        ach_card_layout.addWidget(self.detail_ach_badges_container)

        self.detail_ach_card.setVisible(False)
        detail_layout.addWidget(self.detail_ach_card)

        # Destructive Remove Game Button
        self.btn_detail_remove = QPushButton("Remove Game")
        self.btn_detail_remove.setIcon(get_icon("ph.trash-bold", color="#FF453A"))
        self.btn_detail_remove.setIconSize(QSize(13, 13))
        self.btn_detail_remove.setFixedHeight(28)
        self.btn_detail_remove.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #FF453A;
                border: none;
                border-radius: 6px;
                padding: 0 12px;
                font-weight: 500;
                font-size: 11px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: rgba(255, 69, 58, 0.1);
                color: #FF6961;
            }
            QPushButton:pressed {
                background-color: rgba(255, 69, 58, 0.18);
            }
        """)
        self.btn_detail_remove.clicked.connect(self._on_remove)
        detail_layout.addWidget(self.btn_detail_remove)

        detail_layout.addStretch()

        # Center Main Game Library Area
        self.right_panel = QWidget()
        self.right_panel.setObjectName("libraryCentralPanel")
        self.right_panel.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.right_panel.setStyleSheet("QWidget#libraryCentralPanel { background: transparent; }")
        self.right_layout = QVBoxLayout(self.right_panel)
        self.right_layout.setContentsMargins(18, 14, 18, 14)
        self.right_layout.setSpacing(12)
        right_layout = self.right_layout

        # Add center game grid first, right detail panel second
        self.splitter.addWidget(self.right_panel)
        self.splitter.addWidget(self.detail_panel)

        saved_right_w = self.settings.value("right_inspector_width", 300, type=int)
        self.splitter.setSizes([880, saved_right_w])
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

        # Sorting and Search Controls for Grid / List views (hidden in Compact view)
        self.library_header_bar = QWidget(self.right_panel)
        self.library_header_bar.setObjectName("libraryHeaderBar")
        self.library_header_bar.setStyleSheet("QWidget#libraryHeaderBar { background: transparent; }")
        header_layout = QHBoxLayout(self.library_header_bar)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        header_layout.addStretch()

        # Search Bar for Grid & List views
        self.grid_search_input = QLineEdit()
        self.grid_search_input.setPlaceholderText("Search library...")
        self.grid_search_input.setFixedWidth(200)
        self.grid_search_input.setFixedHeight(30)
        self.grid_search_input.setClearButtonEnabled(True)
        self.grid_search_input.addAction(get_icon("ph.magnifying-glass-bold", color="#8E8E93"), QLineEdit.ActionPosition.LeadingPosition)
        self.grid_search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {SURFACE};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 0 10px 0 28px;
                font-size: 11px;
            }}
            QLineEdit:focus {{
                border-color: {TEXT_MUTED};
                background-color: {SURFACE_ELEVATED};
            }}
            QLineEdit::placeholder {{
                color: {TEXT_MUTED};
            }}
        """)
        self.grid_search_input.textChanged.connect(self._on_search_query_changed)
        header_layout.addWidget(self.grid_search_input)

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

        right_layout.addWidget(self.library_header_bar)

        # ── Rich Collection Header Banner (Shown when inside a Collection) ──
        self.collection_banner = QFrame(self.right_panel)
        self.collection_banner.setVisible(False)
        self.collection_banner.setStyleSheet(f"""
            QFrame {{
                background: {SURFACE};
                border: none;
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
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
                background-color: transparent;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
                background-color: transparent;
            }
        """)
        if self.scroll_area.viewport():
            self.scroll_area.viewport().setStyleSheet("background: transparent; background-color: transparent; border: none;")
            self.scroll_area.viewport().setAutoFillBackground(False)
            self.scroll_area.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.scroll_area.viewport().installEventFilter(self)
        self.scroll_area.installEventFilter(self)
        
        # All library presentations are owned by one host and render the same
        # LibrarySnapshot.  Keep these aliases for existing detail/update code
        # while removing stack and signal ownership from MainWindow.
        self.library_view_host = LibraryViewHost(self.scroll_area)
        self.library_view_stack = self.library_view_host
        self.grid_container = self.library_view_host.grid_container
        self.list_view = self.library_view_host.list_view
        self.virtual_grid = self.library_view_host.virtual_grid
        self.compact_container = self.library_view_host.compact_container
        self.steam_container = self.compact_container

        self.library_view_host.game_selected.connect(self._select_game_by_id)
        self.library_view_host.game_double_clicked.connect(self._on_double_click_game)
        self.library_view_host.game_launch_requested.connect(self._launch_game_by_id)
        self.library_view_host.favorite_requested.connect(self._on_card_favorite_clicked)
        self.library_view_host.edit_requested.connect(self._on_edit)
        self.library_view_host.properties_requested.connect(self._open_game_properties)
        self.library_view_host.save_manager_requested.connect(self._on_export)
        self.library_view_host.open_folder_requested.connect(self._open_game_dir_by_id)
        self.library_view_host.prefix_maintenance_requested.connect(self._open_prefix_maintenance)
        self.library_view_host.achievements_requested.connect(self._open_achievements_dialog)
        self.library_view_host.steam_page_requested.connect(self._open_steam_page_by_id)
        self.library_view_host.filter_changed.connect(self._set_filter)
        self.library_view_host.screenshots_requested.connect(self._open_screenshot_gallery)
        self.library_view_host.videos_requested.connect(self._open_video_gallery)
        self.library_view_host.settings_requested.connect(self._open_settings)
        self.library_view_host.add_game_requested.connect(lambda: self._on_add(self.collection_filter))
        self.library_view_host.sort_changed.connect(self._on_sort_changed)
        self.library_view_host.search_changed.connect(self._on_search_query_changed)
        if self.library_view_mode == "list":
            self.library_view_host.set_mode("list")
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.library_header_bar.setVisible(True)
            self.right_layout.setContentsMargins(18, 14, 18, 14)
            self.right_layout.setSpacing(12)
        elif self.library_view_mode in ("compact", "steam"):
            self.library_view_host.set_mode("compact")
            # Compact view owns scrolling so the list and game page stay
            # independent from the outer library container.
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.detail_panel.setVisible(False)
            self.library_header_bar.setVisible(False)
            self.right_layout.setContentsMargins(0, 0, 0, 0)
            self.right_layout.setSpacing(0)
        else:
            self.library_view_host.set_mode("grid")
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.library_header_bar.setVisible(True)
            self.right_layout.setContentsMargins(18, 14, 18, 14)
            self.right_layout.setSpacing(12)
        self.scroll_area.setWidget(self.library_view_host)
        right_layout.addWidget(self.scroll_area)

        # ── Dedicated Darker Footer Bar (#0E0E10, 36px) with Add Game and View Toggle on bottom-left ──
        self.footer_bar = QFrame(self)
        self.footer_bar.setFixedHeight(36)
        self.footer_bar.setStyleSheet("""
            QFrame {
                background-color: #0E0E10;
                border: none;
                border-top: 1px solid rgba(255, 255, 255, 0.05);
            }
        """)
        footer_layout = QHBoxLayout(self.footer_bar)
        footer_layout.setContentsMargins(12, 0, 12, 0)
        footer_layout.setSpacing(8)

        self.btn_add = QPushButton("Add Game")
        self.btn_add.setObjectName("addGameButton")
        self.btn_add.setIcon(get_icon("ph.plus-bold", color="#FFFFFF"))
        self.btn_add.setIconSize(QSize(13, 13))
        self.btn_add.setFixedHeight(26)
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add.setStyleSheet("""
            QPushButton#addGameButton {
                background: transparent;
                color: #A1A1AA;
                border: none;
                border-radius: 5px;
                padding: 0 8px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton#addGameButton:hover {
                background: rgba(255, 255, 255, 0.06);
                color: #FFFFFF;
            }
        """)
        self.btn_add.clicked.connect(self._on_add)
        footer_layout.addWidget(self.btn_add)

        footer_divider = QFrame()
        footer_divider.setFixedSize(1, 14)
        footer_divider.setStyleSheet("background-color: rgba(255, 255, 255, 0.1); border: none;")
        footer_layout.addWidget(footer_divider)

        btn_labels = {"compact": "▦ Grid", "grid": "☷ List", "list": "≡ Compact", "steam": "▦ Grid"}
        self.btn_view_toggle = QPushButton(btn_labels.get(self.library_view_mode, "▦ Grid"))
        self.btn_view_toggle.setObjectName("viewToggleButton")
        self.btn_view_toggle.setToolTip("Toggle Compact, Grid, or List library view")
        self.btn_view_toggle.clicked.connect(self._toggle_library_view)
        self.btn_view_toggle.setFixedHeight(26)
        self.btn_view_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_view_toggle.setStyleSheet("""
            QPushButton#viewToggleButton {
                background: transparent;
                color: #A1A1AA;
                border: none;
                border-radius: 5px;
                padding: 0 8px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton#viewToggleButton:hover {
                background: rgba(255, 255, 255, 0.06);
                color: #FFFFFF;
            }
        """)
        footer_layout.addWidget(self.btn_view_toggle)

        self.btn_activity = QPushButton("Activity")
        self.btn_activity.setObjectName("activityButton")
        self.btn_activity.setIcon(get_icon("ph.arrows-clockwise-bold", color="#A1A1AA"))
        self.btn_activity.setIconSize(QSize(13, 13))
        self.btn_activity.setFixedHeight(26)
        self.btn_activity.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_activity.setToolTip("Show background operations")
        self.btn_activity.setStyleSheet("""
            QPushButton#activityButton {
                background: transparent;
                color: #A1A1AA;
                border: none;
                border-radius: 5px;
                padding: 0 8px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton#activityButton:hover {
                background: rgba(255, 255, 255, 0.06);
                color: #FFFFFF;
            }
        """)
        footer_layout.addWidget(self.btn_activity)

        footer_layout.addStretch()

        # Kept for test and event compatibility; hidden from footer
        self.btn_toggle_collections = QPushButton("Collections")
        self.btn_toggle_collections.setVisible(False)
        self.btn_toggle_collections.clicked.connect(self._toggle_collections_panel)

        self.btn_reveal_detail = QPushButton(" Details")
        self.btn_reveal_detail.setIcon(get_icon("ph.caret-double-left-bold", color=TEXT_SECONDARY))
        self.btn_reveal_detail.setIconSize(QSize(13, 13))
        self.btn_reveal_detail.setToolTip("Open game details panel")
        self.btn_reveal_detail.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reveal_detail.setFixedHeight(26)
        self.btn_reveal_detail.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #8E8E93;
                border: none;
                border-radius: 5px;
                padding: 0 8px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.06);
                color: #FFFFFF;
            }
        """)
        self.btn_reveal_detail.clicked.connect(lambda: self._animate_left_panel(True))
        if self.library_view_mode in ("compact", "steam"):
            self.btn_reveal_detail.setVisible(False)
        footer_layout.addWidget(self.btn_reveal_detail)

        root_vbox.addWidget(self.footer_bar)
        self.activity_drawer = ActivityDrawer(self.operation_registry, self)
        self.btn_activity.clicked.connect(self._toggle_activity_drawer)
        self.operation_registry.operation_failed.connect(self._on_operation_failed)
        self.operation_registry.unread_changed.connect(self._update_activity_button)
        self._update_activity_button(self.operation_registry.unread_count())
        self._setup_library_shortcuts()
        self._apply_accessibility_metadata()
        
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
        if not self._offline_test_mode:
            QTimer.singleShot(300, self._check_all_steam_updates)
            QTimer.singleShot(800, self._start_background_cloud_sync)
            QTimer.singleShot(1200, self._start_background_achievement_sync)
        if os.environ.get("SAFELAUNCHER_DISABLE_UPDATE_CHECK") != "1":
            self._update_check_timer = QTimer(self)
            self._update_check_timer.setSingleShot(True)
            self._update_check_timer.timeout.connect(self._check_app_updates)
            self._update_check_timer.start(3000)
        else:
            self._update_check_timer = None
        self._startup_app_update_info = None
        self._startup_backend_health = None
        self._startup_update_notice_shown = False
        if (
            not self._offline_test_mode
            and os.environ.get("SAFELAUNCHER_DISABLE_UPDATE_CHECK") != "1"
        ):
            # Let the main window and welcome wizard finish opening before the
            # optional network probe can present a notification.
            QTimer.singleShot(3200, self._check_backend_update_on_startup)
        self._start_cloud_poll_timer()

        show_wizard = self.settings.value("show_welcome_wizard", True, type=bool)
        if show_wizard:
            QTimer.singleShot(150, self._show_welcome_wizard)

    def _apply_accessibility_metadata(self) -> None:
        """Give the primary shell controls stable names for assistive tech.

        Most of the visual UI uses icons and compact labels, so tooltips alone
        are not sufficient for screen readers or automated accessibility
        tooling.  Keep this metadata alongside the shell wiring so it remains
        synchronized when the presentation changes.
        """
        named_controls = {
            self.btn_add: "Add game",
            self.btn_view_toggle: "Change library view",
            self.btn_activity: "Show background activity",
            self.btn_hide_detail: "Close game details",
            self.btn_reveal_detail: "Open game details",
            self.btn_detail_launch: "Launch or stop selected game",
            self.btn_detail_edit: "Edit selected game",
            self.btn_detail_properties: "Open selected game properties",
            self.btn_detail_screenshots: "Open selected game screenshots",
            self.btn_detail_videos: "Open selected game videos",
            self.btn_detail_achievements: "Open selected game achievements",
            self.btn_detail_remove: "Archive or remove selected game",
            self.btn_detail_cloud_restore: "Restore selected game cloud save",
            self.btn_update_banner_action: "Download and apply SafeLauncher update",
            self.btn_update_banner_dismiss: "Dismiss update notification",
        }
        for widget, name in named_controls.items():
            widget.setAccessibleName(name)
            if not widget.toolTip():
                widget.setAccessibleDescription(name)

        self.grid_search_input.setAccessibleName("Search library")
        self.sort_combo.setAccessibleName("Sort library games")
        self.detail_cloud_status.setAccessibleName("Cloud save status")
        self.lbl_detail_update.setAccessibleName("Game update status")

    def _check_app_updates(self):
        """Check GitHub Releases for new SafeLauncher versions in background."""
        if os.environ.get("SAFELAUNCHER_DISABLE_UPDATE_CHECK") == "1":
            return
        try:
            from core.updater import UpdateCheckWorker
            self._update_worker = UpdateCheckWorker(parent=self)
            self._update_worker.check_finished.connect(self._on_app_update_check_finished)
            self._register_worker(self._update_worker)
            self._update_worker.start()
        except Exception as e:
            logger.debug(f"Failed to start update worker: {e}")
            self._startup_app_update_info = {}
            self._maybe_show_startup_update_notice()

    def _check_backend_update_on_startup(self):
        """Probe the configured cloud backend without delaying window startup."""
        if self._startup_backend_health is not None:
            return
        try:
            from core.cloud_backend import get_site_url

            site_url = get_site_url()
            if not site_url:
                self._startup_backend_health = {}
                self._maybe_show_startup_update_notice()
                return

            secret_key = str(self.settings.value("cloud_secret_key", "") or "").strip()

            def _probe():
                try:
                    from core.cloud_backend import check_backend_health

                    return check_backend_health(site_url, secret_key, timeout=5.0)
                except Exception as exc:
                    return {"healthy": False, "is_outdated": False, "error": str(exc)}

            self._start_managed_task(
                "SafeLauncher-StartupBackendProbe",
                _probe,
                self._startup_backend_health_ready.emit,
            )
        except Exception as exc:
            logger.debug(f"Failed to start startup backend probe: {exc}")
            self._startup_backend_health = {}
            self._maybe_show_startup_update_notice()

    def _on_startup_backend_health_ready(self, health: dict):
        self._startup_backend_health = health or {}
        self._maybe_show_startup_update_notice()

    def _maybe_show_startup_update_notice(self):
        """Show one non-blocking launch notification once both checks complete."""
        if self._startup_update_notice_shown:
            return
        if self._startup_app_update_info is None or self._startup_backend_health is None:
            return

        notices = []
        app_info = self._startup_app_update_info
        if app_info.get("update_available"):
            latest = app_info.get("latest_version") or "a newer version"
            notices.append(f"SafeLauncher {latest} is available.")

        backend = self._startup_backend_health
        if backend.get("is_outdated"):
            version = backend.get("version") or "an older version"
            minimum = backend.get("min_version") or "the latest"
            notices.append(
                f"Your cloud backend is {version}; SafeLauncher requires {minimum} or newer."
            )

        if not notices:
            return

        self._startup_update_notice_shown = True
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Updates available")
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setText("\n\n".join(notices))
        dialog.setInformativeText(
            "Use the update banner to update SafeLauncher. "
            "For the cloud backend, open Settings → Cloud and choose Redeploy."
        )
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
        self._startup_update_notice = dialog
        dialog.open()

    def _on_app_update_check_finished(self, info: dict):
        """Display non-intrusive update banner if a new release is detected."""
        info = info or {}
        self._startup_app_update_info = info or {}
        self._maybe_show_startup_update_notice()
        if not info.get("update_available"):
            return

        self._latest_update_info = info
        latest = info.get("latest_version", "")

        try:
            self.btn_update_banner_action.clicked.disconnect()
        except Exception:
            pass

        if info.get("is_appimage") and info.get("appimage_asset"):
            self.lbl_update_banner_msg.setText(f"SafeLauncher {latest} is available!")
            self.btn_update_banner_action.setText("Download & Apply")
            self.btn_update_banner_action.clicked.connect(self._on_banner_download_clicked)
            self.update_banner.setVisible(True)
        else:
            self.lbl_update_banner_msg.setText(f"SafeLauncher {latest} is available on GitHub (source checkout).")
            self.btn_update_banner_action.setText("View Release ↗")
            rel_url = info.get("release_url") or "https://github.com/Mistarin/SafeLauncher/releases"
            self.btn_update_banner_action.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(rel_url)))
            self.update_banner.setVisible(True)

    def _on_banner_download_clicked(self):
        """Initiate AppImage download from banner."""
        info = getattr(self, "_latest_update_info", None)
        if not info or not info.get("appimage_asset"):
            return

        asset_url = info["appimage_asset"]["download_url"]
        self.btn_update_banner_action.setEnabled(False)
        self.btn_update_banner_action.setText("Downloading…")

        from core.updater import UpdateDownloadWorker
        self._dl_worker = UpdateDownloadWorker(asset_url, parent=self)
        self._dl_worker.progress.connect(self._on_banner_download_progress)
        self._dl_worker.finished.connect(self._on_banner_download_finished)
        self._dl_worker.failed.connect(self._on_banner_download_failed)
        self._dl_worker.start()

    def _on_banner_download_progress(self, downloaded: int, total: int):
        if total > 0:
            pct = int((downloaded / total) * 100)
            self.lbl_update_banner_msg.setText(f"Downloading SafeLauncher update: {pct}%…")

    def _on_banner_download_finished(self, target_path: str):
        self.lbl_update_banner_msg.setText("Update verified and ready to apply!")
        self.btn_update_banner_action.setText("Restart Now")
        self.btn_update_banner_action.setEnabled(True)
        try:
            self.btn_update_banner_action.clicked.disconnect()
        except Exception:
            pass
        self.btn_update_banner_action.clicked.connect(self._on_banner_restart_clicked)

        reply = QMessageBox.question(
            self, "Update Ready",
            "SafeLauncher has been successfully updated.\n\nRestart now to apply?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._on_banner_restart_clicked()

    def _on_banner_download_failed(self, error: str):
        self.lbl_update_banner_msg.setText(f"Update failed: {error}")
        self.btn_update_banner_action.setText("Retry")
        self.btn_update_banner_action.setEnabled(True)
        try:
            self.btn_update_banner_action.clicked.disconnect()
        except Exception:
            pass
        self.btn_update_banner_action.clicked.connect(self._on_banner_download_clicked)

    def _on_banner_restart_clicked(self):
        # exec() replaces this process: playtime tracking and exit-uploads for
        # running games would be lost (the games themselves keep running).
        if self.running_game_ids:
            names = ", ".join(
                self.games_by_id.get(gid, (None, f"Game {gid}"))[1]
                for gid in self.running_game_ids)
            QMessageBox.warning(
                self, "Games Still Running",
                "Restarting SafeLauncher now would lose playtime tracking and "
                f"the exit save-upload for: {names}.\n\nClose the game(s) first, "
                "then restart. The update is already applied and survives the restart.")
            return
        from core.updater import restart_application
        restart_application()

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
        else:
            self.showMaximized()
        self._sync_window_controls()

    def _sync_window_controls(self, *_args):
        """Keep the custom maximize control synchronized with the real state."""
        if not hasattr(self, "title_bar"):
            return
        maximized = self.isMaximized()
        self.title_bar.btn_max.setIcon(get_app_icon("restore" if maximized else "maximize", color="#F4F4F5"))
        self.title_bar.btn_max.setToolTip("Restore window" if maximized else "Maximize window")

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
        cloud_before = (
            self.settings.value("cloud_mode", "local", type=str),
            self.settings.value("convex_site_url", "", type=str),
            self.settings.value("cloud_secret_key", "", type=str),
            self.settings.value("cloud_saves_dir", "", type=str),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            previous_name = self.user_name
            self.user_name = dialog.get_user_name()
            self.proton_path = dialog.get_proton_path()
            self.settings.setValue("user_name", self.user_name)
            self.settings.setValue("proton_path", self.proton_path)
            self.settings.setValue("show_welcome_wizard", dialog.get_show_welcome_wizard())
            self.settings.setValue("cloud_saves_dir", dialog.get_cloud_saves_dir())
            if hasattr(dialog, "get_card_size"):
                card_size = dialog.get_card_size()
                self.settings.setValue("card_size", card_size)
                self._on_card_size_changed(card_size)
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
            GpuRecorderService.instance().apply_config(self.gpu_recorder_config)
            self._update_global_hotkeys()
            self._refresh_record_button_state()
            if hasattr(self, "compact_container"):
                self.compact_container.game_page.refresh_media_state()

            # Only announce an actual rename — this handler also runs when the
            # user only touched recorder/screenshot settings.
            if self.user_name != previous_name:
                self._show_toast(f"Display name changed to {self.user_name}.")

        # Runs for accept AND reject: the Cloud tab writes backend settings the
        # moment they are edited (mode combo) or via the embedded account
        # dialog, so a rejected session may still have changed the config.
        self._maybe_refresh_cloud_config(cloud_before)

    def _maybe_refresh_cloud_config(self, before: tuple):
        current = (
            self.settings.value("cloud_mode", "local", type=str),
            self.settings.value("convex_site_url", "", type=str),
            self.settings.value("cloud_secret_key", "", type=str),
            self.settings.value("cloud_saves_dir", "", type=str),
        )
        if current != before:
            self._refresh_cloud_after_config_change()

    def _refresh_cloud_after_config_change(self):
        """Cloud backend settings changed: no cached verdict may survive it.

        Drops the listing cache, the backend singleton (its data key belongs
        to the previous deployment) and every per-game status. Badges go
        neutral and the batch worker re-derives everything against the new
        configuration — the UI must never keep claiming the old
        connected/disconnected state.
        """
        from core.cloud_save_sync import reset_cloud_backend
        reset_cloud_backend()
        self._cloud_context_generation = self.cloud_sync_coordinator.invalidate_context()
        # A diff started for the retired backend must not prevent the new
        # backend from being checked immediately. Its completion is ignored
        # below by generation, and this flag belongs to the current context.
        self._cloud_poll_in_flight = False
        self.cloud_save_status_cache.clear()
        for widget in self.banner_widgets.values():
            widget.set_cloud_status(None)
        if hasattr(self, "library_view_host"):
            for game_id in self.games_by_id:
                self.library_view_host.update_cloud_status(game_id, None)
        if self.selected_game:
            self.detail_cloud_status.setText("<font color='#6F7682'>Cloud Save: checking…</font>")
            self.detail_cloud_status.setToolTip("Cloud settings changed — re-checking.")
        self._save_persistent_cache()
        self.request_cloud_recheck(None, "config-change")

    def refresh_cloud_status_for_game(self, game_id: int):
        """Re-check one game's cloud status and update its badge when done."""
        self.request_cloud_recheck([game_id], "explicit")

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

        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray_icon = QSystemTrayIcon(self)
        if os.path.exists(LOGO_PATH):
            self.tray_icon.setIcon(QIcon(LOGO_PATH))
        else:
            self.tray_icon.setIcon(get_app_icon("library"))

        self.tray_icon.setToolTip("SafeLauncher - Game Sandbox Manager")
        self.tray_icon.activated.connect(self._on_tray_icon_activated)
        self.tray_icon.show()

    def _update_tray_menu(self):
        """Populate system tray menu with pure text items: Recent Games - Library - Settings - Quit."""
        if not hasattr(self, 'tray_menu'):
            return

        self.tray_menu.clear()

        # 1. Recent Games (last 5 games visible normally, not inside a dropdown)
        rec_games = [g for g in self.games if len(g) > 9 and g[9] and g[9] > 0]
        rec_games.sort(key=lambda x: x[9], reverse=True)
        if not rec_games:
            rec_games = [g for g in self.games if not (len(g) > 17 and g[17])]

        recent_to_show = rec_games[:5]
        if recent_to_show:
            for g in recent_to_show:
                game_id, name, path, exe, mode = g[0], g[1], g[2], g[3], g[4]
                act = self.tray_menu.addAction(name)
                act.triggered.connect(lambda _, gid=game_id, p=path, e=exe, m=mode: self._launch_mode(gid, p, e, m or "umu"))
            self.tray_menu.addSeparator()

        # 2. Library
        act_show = self.tray_menu.addAction("Library")
        act_show.triggered.connect(self._show_and_raise)

        self.tray_menu.addSeparator()

        # 3. Settings (separated category)
        act_settings = self.tray_menu.addAction("Settings")
        act_settings.triggered.connect(self._open_settings)

        self.tray_menu.addSeparator()

        # 4. Quit
        act_quit = self.tray_menu.addAction("Quit")
        act_quit.triggered.connect(self.close)

        if hasattr(self, 'tray_icon') and self.tray_icon:
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
        thread = ArchiveExtractorThread(zip_path, dest_dir, parent=self)
        thread.extraction_complete.connect(self._on_topbar_extraction_complete)
        self._show_toast(f"Extracting '{archive_name}' in background...")
        self._register_worker(thread)
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

    def _toggle_collections_panel(self):
        """Toggle left collections panel between collapsed (small) and expanded."""
        if self.sidebar.isHidden():
            self.sidebar.setVisible(True)
            self.sidebar.set_compact(False)
        else:
            self.sidebar.toggle_compact()

    def _set_filter(self, filter_mode: str):
        """Set active filter mode (all, installed, favorites, archived) and refresh view."""
        self.current_filter = filter_mode
        if hasattr(self, "compact_container"):
            self.compact_container.set_filter(filter_mode)
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
        if hasattr(self, "sort_combo") and self.sort_combo.currentIndex() != idx:
            self.sort_combo.blockSignals(True)
            self.sort_combo.setCurrentIndex(idx)
            self.sort_combo.blockSignals(False)
        if hasattr(self, "compact_container") and hasattr(self.compact_container, "sidebar_list") and hasattr(self.compact_container.sidebar_list, "sort_combo"):
            combo = self.compact_container.sidebar_list.sort_combo
            if combo.currentIndex() != idx:
                combo.blockSignals(True)
                combo.setCurrentIndex(idx)
                combo.blockSignals(False)
        self._refresh_library()

    def _on_search_query_changed(self, query: str):
        """Update the single shared library search state from any view."""
        normalized = query.strip()
        self.search_query = normalized.lower()
        # Grid/list and compact no longer keep separate, conflicting search
        # fields. Block signals so typing in either one causes one refresh.
        if hasattr(self, "grid_search_input") and self.grid_search_input.text() != normalized:
            self.grid_search_input.blockSignals(True)
            self.grid_search_input.setText(normalized)
            self.grid_search_input.blockSignals(False)
        compact_search = getattr(
            getattr(getattr(self, "compact_container", None), "sidebar_list", None),
            "search_edit", None,
        )
        if compact_search is not None and compact_search.text() != normalized:
            compact_search.blockSignals(True)
            compact_search.setText(normalized)
            compact_search.blockSignals(False)
        self._refresh_library()

    def _setup_library_shortcuts(self) -> None:
        """Install safe, non-destructive shortcuts for everyday library work."""
        self._library_shortcuts = []
        bindings = (
            ("Ctrl+K", self._focus_library_search),
            ("/", self._focus_library_search),
            ("Return", self._shortcut_launch_selected),
            ("Space", self._shortcut_toggle_favorite),
            ("R", self._shortcut_rescan),
            ("Ctrl+R", self._refresh_library),
            ("Ctrl+1", lambda: self._set_library_view_mode("compact")),
            ("Ctrl+2", lambda: self._set_library_view_mode("grid")),
            ("Ctrl+3", lambda: self._set_library_view_mode("list")),
        )
        for sequence, callback in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(callback)
            self._library_shortcuts.append(shortcut)

    def _shortcut_allowed(self, allow_text_focus: bool = False) -> bool:
        if allow_text_focus:
            return True
        focus = QApplication.focusWidget()
        return not isinstance(focus, (QLineEdit, QPlainTextEdit))

    def _focus_library_search(self) -> None:
        search = getattr(self, "grid_search_input", None)
        if self.library_view_mode in ("compact", "steam"):
            search = getattr(getattr(self.compact_container, "sidebar_list", None), "search_edit", search)
        if search is not None:
            search.setFocus(Qt.FocusReason.ShortcutFocusReason)
            search.selectAll()

    def _shortcut_launch_selected(self) -> None:
        if self._shortcut_allowed() and self.selected_game:
            self._launch_game_by_id(self.selected_game[0])

    def _shortcut_toggle_favorite(self) -> None:
        if self._shortcut_allowed() and self.selected_game:
            self._on_toggle_favorite()

    def _shortcut_rescan(self) -> None:
        if self._shortcut_allowed():
            self._check_games_on_drive()
            self._show_toast("Game files rescanned.")

    def _set_library_view_mode(self, mode: str) -> None:
        if mode not in {"compact", "grid", "list"} or self.library_view_mode == mode:
            return
        self.library_view_mode = mode
        self.settings.setValue("library_view_mode", mode)
        # Reuse the existing view transition logic without changing the public
        # cycle behavior of the footer button.
        current = self.library_view_mode
        previous = {"compact": "list", "grid": "compact", "list": "grid"}[current]
        self.library_view_mode = previous
        self._toggle_library_view()

    def _toggle_library_view(self):
        cycle = {"compact": "grid", "grid": "list", "list": "compact", "steam": "grid"}
        self.library_view_mode = cycle.get(self.library_view_mode, "compact")
        self.settings.setValue("library_view_mode", self.library_view_mode)
        use_virtual = len(self.banner_widgets) >= getattr(self, "virtualization_threshold", 200)
        if self.library_view_mode == "list":
            self.library_view_host.set_mode("list")
            if hasattr(self, "library_header_bar"):
                self.library_header_bar.setVisible(True)
            if hasattr(self, "right_layout"):
                self.right_layout.setContentsMargins(18, 14, 18, 14)
                self.right_layout.setSpacing(12)
            if self.selected_game:
                self._animate_left_panel(True)
                self._update_detail_panel()
            else:
                self.btn_reveal_detail.setVisible(True)
        elif self.library_view_mode in ("compact", "steam"):
            self.library_view_host.set_mode("compact")
            self.detail_panel.setVisible(False)
            self.btn_reveal_detail.setVisible(False)
            if hasattr(self, "library_header_bar"):
                self.library_header_bar.setVisible(False)
            if hasattr(self, "right_layout"):
                self.right_layout.setContentsMargins(0, 0, 0, 0)
                self.right_layout.setSpacing(0)
            self._update_compact_game_page()
        elif use_virtual:
            self.library_view_host.set_mode("grid", use_virtual=True)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            if hasattr(self, "library_header_bar"):
                self.library_header_bar.setVisible(True)
            if hasattr(self, "right_layout"):
                self.right_layout.setContentsMargins(18, 14, 18, 14)
                self.right_layout.setSpacing(12)
            if self.selected_game:
                self._animate_left_panel(True)
                self._update_detail_panel()
            else:
                self.btn_reveal_detail.setVisible(True)
        else:
            self.library_view_host.set_mode("grid")
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            if hasattr(self, "library_header_bar"):
                self.library_header_bar.setVisible(True)
            if hasattr(self, "right_layout"):
                self.right_layout.setContentsMargins(18, 14, 18, 14)
                self.right_layout.setSpacing(12)
            if self.selected_game:
                self._animate_left_panel(True)
                self._update_detail_panel()
            else:
                self.btn_reveal_detail.setVisible(True)
        btn_labels = {"compact": "▦ Grid", "grid": "☷ List", "list": "≡ Compact", "steam": "▦ Grid"}
        self.btn_view_toggle.setText(btn_labels.get(self.library_view_mode, "▦ Grid"))

    def _visible_library_ids(self) -> set[int]:
        return self.library_view_host.visible_ids(self.library_view_mode)

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
            empty_snapshot = LibrarySnapshot(
                query=LibraryQuery(
                    search=self.search_query,
                    filter_mode=self.current_filter,
                    collection=self.collection_filter,
                    sort_index=self.current_sort,
                ),
                total_games=0,
                empty_message="No games in your library yet.\nClick 'Add Game' or 'Sync Library' to get started.",
            )
            self.library_view_host.render_snapshot(empty_snapshot, self.sgdb_client.cache_dir, set())
            self.library_view_host.set_empty_grid_message(empty_snapshot.empty_message)
            self.collection_banner.setVisible(False)
            if hasattr(self, "compact_container"):
                self.library_selection.clear()
                self.selected_game = None
            return

        # Reconcile persisted/legacy maps into the single status model before
        # any view receives the snapshot.
        for game in self.games:
            gid = int(game[0])
            current = self.game_status_by_id.get(gid, GameStatusState())
            cached_cloud = self.cloud_save_status_cache.get(gid)
            build_result = self.steam_check_results.get(gid)
            self.game_status_by_id[gid] = replace(
                current,
                update_available=bool(self.update_status_by_game_id.get(gid, False)),
                update_error=(str(build_result[3] or "") if build_result and len(build_result) > 3 else current.update_error),
                update_build_id=(str(build_result[0] or "") if build_result else current.update_build_id),
                update_build_date=(int(build_result[1] or 0) if build_result else current.update_build_date),
                cloud_status=(cached_cloud[0] if cached_cloud else current.cloud_status),
                local_stats=(cached_cloud[1] if cached_cloud else current.local_stats),
                cloud_stats=(cached_cloud[2] if cached_cloud else current.cloud_stats),
            )

        # One authoritative query/snapshot feeds every renderer. Views no
        # longer independently decide which games are visible.
        self.library_snapshot = self.library_state.set_inputs(
            self.games,
            LibraryQuery(
                search=self.search_query,
                filter_mode=self.current_filter,
                collection=self.collection_filter,
                sort_index=self.current_sort,
            ),
            update_status=self.update_status_by_game_id,
            cloud_status=self.cloud_save_status_cache,
            status=self.game_status_by_id,
        )
        processed = self.library_snapshot.legacy_items

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

        # Background size calculation for any game dir without a fresh cached
        # value (used by Disk Size sorting and list-view metadata rows).
        self._schedule_size_fetches({x[0][2] for x in processed if x[0][2]})

        if not processed:
            msg = self.library_snapshot.empty_message
            self.selected_game = None
            self.library_selection.clear()
            self.library_view_host.render_snapshot(
                self.library_snapshot,
                self.sgdb_client.cache_dir,
                self.library_selection.ids,
            )
            self.library_view_host.set_empty_grid_message(msg, show_add=True)
            return

        # A selected game can disappear when a collection/status filter changes.
        # Keep compact detail bound to a visible game, never to a stale record.
        visible_ids = {item[0][0] for item in processed}
        if not self.selected_game or self.selected_game[0] not in visible_ids:
            self.selected_game = processed[0][0]
            self.library_selection.replace({processed[0][0][0]})

        use_virtual = len(processed) >= getattr(self, "virtualization_threshold", 200)

        if use_virtual:
            # Virtualized grid presentation for large libraries (500+ games)
            for item_data in processed:
                g = item_data[0] if isinstance(item_data, tuple) and len(item_data) == 4 and not hasattr(item_data, "id") and hasattr(item_data[0], "__getitem__") else item_data
                raw_id = g[0] if hasattr(g, "__getitem__") else getattr(g, "id", 0)
                if hasattr(raw_id, "id"):
                    raw_id = raw_id.id
                if isinstance(raw_id, (tuple, list)) and len(raw_id) > 0:
                    raw_id = raw_id[0]
                game_id = int(raw_id)

                self.banner_widgets[game_id] = BannerProxy(game_id, self.virtual_grid)

                # Background banner/icon auto-fetch
                name = g[1] if len(g) > 1 and g[1] else ""
                path = g[2] if len(g) > 2 and g[2] else ""
                executable = g[3] if len(g) > 3 and g[3] else ""
                banner_url = g[5] if len(g) > 5 and g[5] else ""
                steam_id = g[6] if len(g) > 6 and g[6] else ""
                icon_url = g[18] if len(g) > 18 and g[18] else ""
                banner_missing = not banner_url or not os.path.exists(banner_url)
                icon_missing = not icon_url or not os.path.exists(icon_url)
                if (not self._offline_test_mode and (banner_missing or icon_missing)) and game_id not in self._auto_fetch_attempted:
                    self._auto_fetch_attempted.add(game_id)
                    full_exe = os.path.join(path, executable) if (path and executable) else ""
                    fetcher = BannerAutoFetcher(game_id, name, self.sgdb_client, exe_path=full_exe, steam_id=str(steam_id or ""))
                    fetcher.banner_auto_downloaded.connect(self._on_auto_banner_downloaded)
                    fetcher.finished.connect(lambda f=fetcher: self._cleanup_auto_fetcher(f))
                    if len(self.auto_fetchers) < self.max_concurrent_auto_fetchers:
                        fetcher.start()
                        self.auto_fetchers.append(fetcher)
                        self._register_worker(fetcher)
                    else:
                        self._pending_auto_fetchers.append(fetcher)

            try:
                self.library_view_host.set_grid_widgets([])
            except (RuntimeError, AttributeError):
                pass
        else:
            widgets = []
            for g, is_missing, playtime_seconds, is_fav in processed:
                game_id, name, path, executable, mode, banner_url, steam_id = g[:7]
                version_override = g[15] if len(g) > 15 and g[15] else ""
                icon_url = g[18] if len(g) > 18 and g[18] else ""

                if banner_url and not os.path.exists(banner_url):
                    banner_url = None

                if not icon_url or not os.path.exists(icon_url):
                    full_exe = os.path.join(path, executable) if (path and executable) else ""
                    cached_icon = self.sgdb_client.get_icon_cached_path(steam_id=steam_id, game_name=name, exe_path=full_exe, game_id=game_id)
                    if cached_icon and os.path.exists(cached_icon):
                        icon_url = cached_icon
                    else:
                        icon_url = ""
                
                widget = GameBannerWidget(
                    game_id, name, banner_url, playtime_seconds or 0,
                    version=version_override, icon_path=icon_url, parent=self.grid_container
                )
                widget.set_missing(is_missing)
                widget.set_update_available(self.update_status_by_game_id.get(game_id, False))
                widget.set_favorite(is_fav)
                widget.set_selected(game_id in self.library_selection.ids)
                cached_cloud = self.cloud_save_status_cache.get(game_id)
                if cached_cloud:
                    widget.set_cloud_status(cached_cloud[0])
                widget.clicked.connect(self._select_game_by_id)
                widget.doubleClicked.connect(self._on_double_click_game)
                widget.favoriteClicked.connect(self._on_card_favorite_clicked)
                widget.launchClicked.connect(self._launch_game_by_id)
                
                widgets.append(widget)
                self.banner_widgets[game_id] = widget
                
                banner_missing = not banner_url or not os.path.exists(banner_url)
                icon_missing = not icon_url or not os.path.exists(icon_url)
                if (not self._offline_test_mode and (banner_missing or icon_missing)) and game_id not in self._auto_fetch_attempted:
                    self._auto_fetch_attempted.add(game_id)
                    full_exe = os.path.join(path, executable) if (path and executable) else ""
                    fetcher = BannerAutoFetcher(game_id, name, self.sgdb_client, exe_path=full_exe, steam_id=str(steam_id or ""))
                    fetcher.banner_auto_downloaded.connect(self._on_auto_banner_downloaded)
                    fetcher.finished.connect(lambda f=fetcher: self._cleanup_auto_fetcher(f))
                    if len(self.auto_fetchers) < self.max_concurrent_auto_fetchers:
                        fetcher.start()
                        self.auto_fetchers.append(fetcher)
                        self._register_worker(fetcher)
                    else:
                        self._pending_auto_fetchers.append(fetcher)
                
            try:
                self.library_view_host.set_grid_widgets(widgets)
            except (RuntimeError, AttributeError):
                pass

        try:
            self.library_view_host.render_snapshot(self.library_snapshot, self.sgdb_client.cache_dir, self.library_selection.ids)
        except (RuntimeError, AttributeError):
            pass

        if self.library_view_mode == "list":
            self.library_view_host.set_mode("list")
            if self.selected_game:
                self._update_detail_panel()
        elif self.library_view_mode in ("compact", "steam"):
            self.library_view_host.set_mode("compact")
            self.detail_panel.setVisible(False)
            self.btn_reveal_detail.setVisible(False)
            self._update_compact_game_page()
        elif use_virtual:
            self.library_view_host.set_mode("grid", use_virtual=True)
            if self.selected_game:
                self._update_detail_panel()
        else:
            self.library_view_host.set_mode("grid")
            if self.selected_game:
                self._update_detail_panel()
        self._check_games_on_drive()
        self._update_tray_menu()

        # Pre-cache 16:9 hero background artwork and game icons in background threads
        for game in self.games:
            g_id = game[0]
            g_name = game[1] if len(game) > 1 else ""
            g_path = game[2] if len(game) > 2 else ""
            g_exe = game[3] if len(game) > 3 else ""
            s_id = game[6] if len(game) > 6 else ""
            full_exe = os.path.join(g_path, g_exe) if (g_path and g_exe) else ""

            hero_cache_file = self.sgdb_client.get_hero_cached_path(steam_id=s_id, game_name=g_name, exe_path=full_exe, game_id=g_id)
            if not self._offline_test_mode and not hero_cache_file and g_id not in self._hero_attempted:
                if not any(isinstance(f, HeroFetcherThread) and f.game_id == g_id for f in self.metadata_fetchers):
                    self._hero_attempted.add(g_id)
                    hero_thread = HeroFetcherThread(g_id, g_name, s_id, self.sgdb_client, exe_path=full_exe, parent=self)
                    hero_thread.hero_downloaded.connect(self._on_hero_downloaded)
                    self._track_metadata_fetcher(hero_thread)

            icon_url = game[18] if len(game) > 18 and game[18] else ""
            if (not self._offline_test_mode and (not icon_url or not os.path.exists(icon_url))) and g_id not in self._icon_attempted:
                self._icon_attempted.add(g_id)
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
        """Save downloaded game icon path in DB and update card and compact list."""
        self.db.update_game_icon(game_id, icon_path)
        for idx, g in enumerate(self.games):
            if g[0] == game_id:
                g_list = list(g)
                while len(g_list) <= 18:
                    g_list.append("")
                g_list[18] = icon_path
                self.games[idx] = tuple(g_list)
                break

        try:
            if game_id in self.banner_widgets:
                self.banner_widgets[game_id].set_icon(icon_path)
        except (RuntimeError, AttributeError):
            pass

        self._update_library_item("update_game_icon", game_id, icon_path)

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
            self._update_library_item("update_missing", game_id, is_missing)

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

        for idx, g in enumerate(self.games):
            if g[0] == game_id:
                g_list = list(g)
                while len(g_list) <= 18:
                    g_list.append("")
                if icon_path:
                    g_list[18] = icon_path
                if image_path:
                    g_list[5] = image_path
                if steam_id:
                    g_list[6] = str(steam_id)
                self.games[idx] = tuple(g_list)
                break

        try:
            if game_id in self.banner_widgets:
                if image_path:
                    self.banner_widgets[game_id].set_banner(image_path)
                if icon_path:
                    self.banner_widgets[game_id].set_icon(icon_path)
        except (RuntimeError, AttributeError):
            pass

        if icon_path:
            self._update_library_item("update_game_icon", game_id, icon_path)

    def _cleanup_auto_fetcher(self, fetcher):
        if fetcher in self.auto_fetchers:
            self.auto_fetchers.remove(fetcher)
        if fetcher in self._background_workers:
            self._background_workers.remove(fetcher)
        self._retiring_workers.append(fetcher)
        if len(self._retiring_workers) > 80:
            self._retiring_workers = [w for w in self._retiring_workers if w.isRunning()]
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
            self._register_worker(fetcher)
            return

    def _cancel_metadata_fetchers(self):
        for fetcher in list(self.metadata_fetchers):
            if fetcher.isRunning():
                fetcher.requestInterruption()

    def _track_metadata_fetcher(self, fetcher):
        fetcher.finished.connect(lambda f=fetcher: self._cleanup_metadata_fetcher(f))
        self.metadata_fetchers.append(fetcher)
        self._register_worker(fetcher)
        fetcher.start()

    def _register_worker(self, worker):
        """Register an application-owned worker with the shutdown supervisor."""
        if self.worker_supervisor.register(worker):
            self._background_workers.append(worker)

    def _on_supervised_worker_finished(self, worker):
        """Retain finished QThreads briefly, then let Qt reclaim them safely."""
        if worker in self._background_workers:
            self._background_workers.remove(worker)
        if worker not in self._retiring_workers:
            self._retiring_workers.append(worker)
        if len(self._retiring_workers) > 80:
            self._retiring_workers = [w for w in self._retiring_workers if w.isRunning()]

    def _start_managed_task(self, name: str, work, on_complete=None):
        """Start a one-shot task owned by this window and shut it down safely."""
        worker = FunctionWorker(work, parent=self)
        worker.setObjectName(name)
        operation = self.operation_registry.start(
            name.replace("_", " ").strip().title(),
            category="Background",
            cancel=getattr(worker, "request_cancel", worker.requestInterruption),
        )
        operation.retry = lambda: self._start_managed_task(name, work, on_complete)
        if on_complete is not None:
            def _complete(result, callback=on_complete, op_id=operation.operation_id):
                self.operation_registry.finish_result(op_id, result)
                callback(result)
            worker.completed.connect(_complete)
        else:
            worker.completed.connect(
                lambda result, op_id=operation.operation_id: self.operation_registry.finish_result(op_id, result)
            )
        worker.error_occurred.connect(
            lambda error, task=name, op_id=operation.operation_id: self._on_managed_task_error(task, op_id, error)
        )

        def _retire(w=worker):
            if operation.active:
                self.operation_registry.finish(operation.operation_id, state="cancelled")
            if w in self._background_workers:
                self._background_workers.remove(w)
            self._retiring_workers.append(w)

        worker.finished.connect(_retire)
        self._register_worker(worker)
        worker.start()
        return worker

    def _on_managed_task_error(self, task: str, operation_id: str, error: str) -> None:
        logger.warning("Background task %s failed: %s", task, error)
        self.operation_registry.fail(operation_id, error)

    def _on_operation_failed(self, operation) -> None:
        """Surface background failures without interrupting the current task."""
        message = operation.error or f"{operation.label} failed."
        if len(message) > 180:
            message = message[:177] + "..."
        self._show_toast(message, is_error=True)

    def _update_activity_button(self, failed_count: int = 0) -> None:
        """Keep the activity affordance useful without adding a permanent panel."""
        button = getattr(self, "btn_activity", None)
        if button is None:
            return
        label = "Activity"
        if failed_count:
            label += f" · {failed_count} failed"
        button.setText(label)
        button.setToolTip(
            "Show background operations"
            if not failed_count else f"Show {failed_count} failed background operation(s)"
        )

    def _toggle_activity_drawer(self) -> None:
        drawer = getattr(self, "activity_drawer", None)
        if drawer is None:
            return
        if drawer.isVisible():
            drawer.hide()
            return
        self.operation_registry.mark_all_read()
        self._position_activity_drawer()
        drawer.show()
        drawer.raise_()

    def _position_activity_drawer(self) -> None:
        drawer = getattr(self, "activity_drawer", None)
        if drawer is None:
            return
        drawer.adjustSize()
        margin = 14
        drawer.move(
            max(margin, self.width() - drawer.width() - margin),
            margin + self.title_bar.height(),
        )

    def _cleanup_metadata_fetcher(self, fetcher):
        if fetcher in self.metadata_fetchers:
            self.metadata_fetchers.remove(fetcher)
        if fetcher in self._background_workers:
            self._background_workers.remove(fetcher)
        self._retiring_workers.append(fetcher)
        if len(self._retiring_workers) > 80:
            self._retiring_workers = [w for w in self._retiring_workers if w.isRunning()]

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
        # Selection is application state, not a property of whichever view
        # happened to receive the click. Keep every presentation synchronized
        # so changing view never appears to lose the current selection.
        selected_ids = self.library_selection.ids
        for presentation in self._library_presentations():
            try:
                if presentation is not None and hasattr(presentation, "set_selected_game_ids"):
                    presentation.set_selected_game_ids(selected_ids)
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
        self._update_compact_game_page()

    def _library_presentations(self) -> tuple:
        """Return the single renderer boundary for library updates."""
        host = getattr(self, "library_view_host", None)
        return (host,) if host is not None else ()

    def _update_library_item(self, method: str, game_id: int, *args) -> None:
        """Fan out one derived-state change to every compatible renderer."""
        for presentation in self._library_presentations():
            callback = getattr(presentation, method, None)
            if callback is None:
                continue
            try:
                callback(game_id, *args)
            except (RuntimeError, AttributeError):
                pass

    def _update_compact_game_page(self):
        """Update the Compact Game Page widget with the selected game's details."""
        if not hasattr(self, "compact_container"):
            return
        game = self.selected_game
        if not game and self.games:
            game = self.games[0]
            self.selected_game = game
        if not game:
            self.hero_bg.set_hero_image(None)
            return
        g_id = game[0]

        try:
            ach_stats = self.db.get_achievement_stats(g_id)
            recent_achs = self.db.get_recent_unlocked_achievements(g_id, limit=6)
            all_achs = self.db.get_game_achievements(g_id)
            locked_achs = [a for a in all_achs if not a.get("unlocked")]
        except Exception as e:
            logger.debug(f"Error fetching achievements for Compact page: {e}")
            ach_stats = (0, 0, 0.0)
            recent_achs = []
            locked_achs = []

        cached_cloud = self.cloud_save_status_cache.get(g_id)
        if cached_cloud:
            c_status = cached_cloud[0] if isinstance(cached_cloud, tuple) else cached_cloud
        else:
            c_status = SyncStatus.NO_SAVES

        g_name = game[1] if len(game) > 1 else ""
        g_path = game[2] if len(game) > 2 else ""
        g_exe = game[3] if len(game) > 3 else ""
        s_id = game[6] if len(game) > 6 else ""
        full_exe = os.path.join(g_path, g_exe) if (g_path and g_exe) else ""
        banner_url = game[5] if len(game) > 5 else None

        hero_cache_path = self.sgdb_client.get_hero_cached_path(steam_id=s_id, game_name=g_name, exe_path=full_exe, game_id=g_id)
        if hero_cache_path and os.path.exists(hero_cache_path):
            hero_file = hero_cache_path
        else:
            if banner_url and os.path.exists(banner_url):
                hero_file = banner_url
            else:
                hero_file = None

        if not self._offline_test_mode and not hero_cache_path and g_id not in self._hero_attempted:
            if not any(isinstance(f, HeroFetcherThread) and f.game_id == g_id for f in self.metadata_fetchers):
                self._hero_attempted.add(g_id)
                hero_thread = HeroFetcherThread(g_id, g_name, s_id, self.sgdb_client, exe_path=full_exe, parent=self)
                hero_thread.hero_downloaded.connect(self._on_hero_downloaded)
                self._track_metadata_fetcher(hero_thread)

        self.hero_bg.set_hero_image(hero_file)

        is_running = g_id in self.running_game_ids
        full_game_exe = os.path.join(g_path, g_exe) if (g_path and g_exe) else g_path
        is_missing = not bool(g_path and os.path.exists(g_path) and (
            not g_exe or os.path.exists(full_game_exe)
        ))

        self.compact_container.game_page.set_game(
            game,
            ach_stats,
            recent_achs,
            locked_achs,
            cloud_status=c_status,
            hero_image_path=hero_file,
            is_running=is_running,
            is_missing=is_missing,
            is_update_available=bool(self.update_status_by_game_id.get(g_id, False)),
        )
        self.compact_container.select_game(g_id)

    _update_steam_game_page = _update_compact_game_page

    def _open_game_dir_by_id(self, game_id: int):
        """Open the installation folder for the specified game."""
        for g in self.games:
            if g[0] == game_id:
                path = g[2] if len(g) > 2 else ""
                if path and os.path.exists(path):
                    QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.realpath(path)))
                break

    def _open_steam_page_by_id(self, steam_id: str):
        """Open the Steam store page for the given Steam AppID."""
        if steam_id:
            QDesktopServices.openUrl(QUrl(f"https://store.steampowered.com/app/{steam_id}"))

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
        if (
            event.type() == QEvent.Type.Wheel
            and self.library_view_mode in ("compact", "steam")
            and obj in (self.scroll_area, self.scroll_area.viewport())
        ):
            # Compact child panes own scrolling. Prevent wheel events that
            # bubble up at their edges from moving the outer library surface.
            event.accept()
            return True
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
        self._position_activity_drawer()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_window_controls()

    def _reposition_reveal_button(self):
        """No-op as reveal button is docked in the bottom action bar."""
        pass

    def _on_panel_anim_step(self, val: float):
        saved_w = self.settings.value("right_inspector_width", 300, type=int)
        normalized_val = min(1.0, max(0.0, val))
        current_inspector_w = max(1, int(saved_w * normalized_val))
        total_w = self.splitter.width() or self.width() or 1180
        self.splitter.setSizes([max(300, total_w - current_inspector_w), current_inspector_w])
        
        # Keep the dock flush with the right edge while it resizes.
        self.detail_panel.setContentsMargins(0, 0, 0, 0)

    def _on_panel_anim_finished(self):
        if self.library_view_mode in ("compact", "steam"):
            self.detail_panel.setVisible(False)
            self.btn_reveal_detail.setVisible(False)
            return
        if not self._panel_expanding:
            self.detail_panel.setVisible(False)
            self.btn_reveal_detail.setVisible(True)
        else:
            self.detail_panel.setContentsMargins(0, 0, 0, 0)
            self.btn_reveal_detail.setVisible(False)

    def _animate_left_panel(self, expand: bool):
        """Smoothly swipe and fade in/out the right detail inspector panel from the right edge."""
        if self.library_view_mode in ("compact", "steam"):
            self.detail_panel.setVisible(False)
            self.btn_reveal_detail.setVisible(False)
            return
        if expand:
            if not self.detail_panel.isVisible() or self.panel_anim.state() == QAbstractAnimation.State.Running:
                self._panel_expanding = True
                self.btn_reveal_detail.setVisible(False)
                self.detail_panel.setVisible(True)
                self.panel_anim.stop()
                self.panel_anim.setDuration(280)
                self.panel_anim.setStartValue(0.0)
                self.panel_anim.setEndValue(1.0)
                self.panel_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                self.panel_anim.start()
        else:
            if self.detail_panel.isVisible():
                self._panel_expanding = False
                self.panel_anim.stop()
                self.panel_anim.setDuration(220)
                self.panel_anim.setStartValue(1.0)
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

    def _open_screenshot_gallery(self, game_id: Optional[int] = None):
        """Open ScreenshotGalleryDialog for current game."""
        if game_id is not None:
            self._select_game_by_id(game_id)
        game = self.selected_game
        if not game:
            return
        dialog = ScreenshotGalleryDialog(game[0], game[1], self)
        dialog.exec()
        self._update_detail_panel()

    def _open_video_gallery(self, game_id: Optional[int] = None):
        """Open the video browser for the selected game."""
        if game_id is not None:
            self._select_game_by_id(game_id)
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
        # Edits here can change the cloud verdict (manual upload/download,
        # generation rollback, rename → different cloud key): re-check.
        self.refresh_cloud_status_for_game(game[0])
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
        self._updates_offline = False
        self._show_toast("Checking Steam for updates...")
        if hasattr(self, "nav_updates") and self.nav_updates is not None:
            self.nav_updates.setEnabled(False)
            self.nav_updates.setText(" Checking Steam…")

        def finished_one():
            pending[0] -= 1
            if pending[0] <= 0:
                if hasattr(self, "nav_updates") and self.nav_updates is not None:
                    self.nav_updates.setEnabled(True)
                    if getattr(self, "_updates_offline", False):
                        self.nav_updates.setText(" Check for Updates (offline)")
                        self._show_toast("Update check finished — offline, results unavailable.")
                    else:
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
            fetcher.offline_detected.connect(self._on_update_check_offline)
            fetcher.finished.connect(finished_one)
            self.metadata_fetchers.append(fetcher)
            self._register_worker(fetcher)
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
        self._register_worker(fetcher)
        fetcher.start()

    def _set_game_update_status(self, game_id: int, is_available: bool) -> None:
        """Commit one game-version fact and fan it out to every library view.

        Grid, List, Virtual Grid, and Compact are presentations of the same
        library state.  A Steam worker must never update only whichever view
        happened to create a banner widget first.
        """
        is_available = bool(is_available)
        self.update_status_by_game_id[game_id] = is_available
        current = self.game_status_by_id.get(game_id, GameStatusState())
        self.game_status_by_id[game_id] = replace(
            current, update_available=is_available, update_error=""
        )

        # Every presentation receives the same derived state immediately.
        # The coalesced refresh below still rebuilds the shared snapshot so
        # view switches and persisted state remain correct.
        try:
            if game_id in self.banner_widgets:
                self.banner_widgets[game_id].set_update_available(is_available)
        except (RuntimeError, AttributeError):
            pass
        self._update_library_item("update_update_available", game_id, is_available)

        if hasattr(self, "_update_status_refresh_timer"):
            self._update_status_refresh_timer.start()

    def _on_initial_steam_build_checked(self, game_id: int, build_id: str, build_date: int, _needs_update: bool):
        if not build_id:
            return
        self.db.update_build_id(game_id, build_id)
        self.local_version_by_game_id[game_id] = (build_id, build_date)
        self.steam_check_results[game_id] = (build_id, build_date, False, "")
        self._set_game_update_status(game_id, False)
        self.metadata_attempted_builds.discard(game_id)

    def _on_steam_build_checked(self, game_id: int, latest_build_id: str, latest_build_date: int, is_update_available: bool):
        """Callback when background SteamBuildFetcher returns build info."""
        import time
        self.steam_check_results[game_id] = (latest_build_id, latest_build_date, is_update_available, "")
        self._set_game_update_status(game_id, bool(is_update_available and latest_build_id))
        is_update_available = self.update_status_by_game_id[game_id]
        if not hasattr(self, "_steam_build_checked_ts"):
            self._steam_build_checked_ts = {}
        self._steam_build_checked_ts[game_id] = time.time()
        self._save_persistent_cache()
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
                steam_app_id = str(self.selected_game[6]).strip() if len(self.selected_game) > 6 and self.selected_game[6] else "Not linked"
                self.lbl_detail_update.setText("Steam check unavailable")
                self.lbl_detail_update.setStyleSheet("background: #3f3f46; color: #d4d4d8; border: 1px solid #71717a; border-radius: 6px; padding: 4px 8px; font-size: 10px; font-weight: bold;")
                self.lbl_detail_versions.setText(
                    "<table width='100%' cellspacing='0' cellpadding='1' style='margin:0; padding:0; border-collapse:collapse;'>"
                    f"<tr><td align='left'><font color='#A7ADB8'>Version</font></td><td align='right'><b>{escape(str(self.selected_game[15] or 'Not set'))}</b></td></tr>"
                    f"<tr><td align='left'><font color='#A7ADB8'>Steam AppID</font></td><td align='right'><b>{escape(steam_app_id)}</b></td></tr>"
                    f"<tr><td align='left'><font color='#A7ADB8'>Installed build</font></td><td align='right'><b>{escape(str(local_build_id))}</b></td></tr>"
                    f"<tr><td align='left'><font color='#A7ADB8'>Updated</font></td><td align='right'>{self._format_version_date(local_date)}</td></tr>"
                    "<tr><td colspan='2' align='right'><font color='#6F7682'>Steam build unavailable</font></td></tr>"
                    "</table>"
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
            steam_app_id = str(self.selected_game[6]).strip() if len(self.selected_game) > 6 and self.selected_game[6] else "Not linked"
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
                f"<tr><td><font color='#A7ADB8'>Steam AppID</font></td><td colspan='2' align='center'><b>{escape(steam_app_id)}</b></td></tr>"
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
        current = self.game_status_by_id.get(game_id, GameStatusState())
        self.game_status_by_id[game_id] = replace(
            current,
            update_available=False,
            update_error=str(reason or "Update check failed"),
        )
        self.update_status_by_game_id[game_id] = False
        self._update_library_item("update_update_available", game_id, False)
        if self.selected_game and self.selected_game[0] == game_id:
            self.lbl_detail_update.setText("Steam check failed")
            steam_app_id = str(self.selected_game[6]).strip() if len(self.selected_game) > 6 and self.selected_game[6] else "Not linked"
            version_override = self.selected_game[15] if len(self.selected_game) > 15 and self.selected_game[15] else "Not set"
            self.lbl_detail_versions.setText(
                f"<b>Version:</b> {escape(str(version_override))} &nbsp;·&nbsp; "
                f"<b>Steam AppID:</b> {escape(steam_app_id)}<br>"
                f"{escape(str(reason or 'Steam build check failed'))}"
            )
            self.detail_update_widget.setVisible(True)
            self.btn_retry_steam.setVisible(True)

    @property
    def running_game_ids(self) -> set:
        """Game ids with an active authoritative game session."""
        return self.game_sessions.active_game_ids()

    def _on_game_session_state_changed(self, session) -> None:
        """Keep every presentation bound to the same session state."""
        self._update_detail_launch_button(session.game_id)
        if hasattr(self, "compact_container") and self.compact_container:
            if self.selected_game and self.selected_game[0] == session.game_id:
                if session.state == "stopping":
                    self.compact_container.set_play_state("stopping")
                elif session.state == "running" and session.is_live:
                    self.compact_container.set_play_state("running")
                else:
                    self.compact_container.set_play_state("play")

    def _on_update_check_offline(self, game_id: int):
        """Network unreachable: show an explicit offline state, never a
        false 'up to date' or a generic failure."""
        if not getattr(self, "_updates_offline", False):
            self._updates_offline = True
            self._show_toast("No internet connection — update checks are unavailable.")
            if hasattr(self, "nav_updates") and self.nav_updates is not None:
                self.nav_updates.setText(" Check for Updates (offline)")
        reason = "Offline — update check not performed"
        self.steam_check_results[game_id] = ("", 0, False, "offline")
        if self.selected_game and self.selected_game[0] == game_id:
            self.lbl_detail_update.setText("<font color='#6F7682'>Offline — update check not performed</font>")
            self.lbl_detail_update.setStyleSheet("background: #1A1E26; color: #A7ADB8; border: 1px solid #252A33; border-radius: 4px; padding: 2px 8px; font-size: 10px; font-weight: 500;")
            steam_app_id = str(self.selected_game[6]).strip() if len(self.selected_game) > 6 and self.selected_game[6] else "Not linked"
            version_override = self.selected_game[15] if len(self.selected_game) > 15 and self.selected_game[15] else "Not set"
            self.lbl_detail_versions.setText(
                f"<b>Version:</b> {escape(str(version_override))} &nbsp;·&nbsp; "
                f"<b>Steam AppID:</b> {escape(steam_app_id)}<br>{escape(reason)}"
            )
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

    def _load_persistent_cache(self):
        """Load cached cloud save statuses, Steam check results, and attempted tag lookups from disk."""
        import json
        import time
        from core.cloud_save_sync import SyncStatus, SaveStats
        cache_file = os.path.join(os.path.expanduser("~/.cache/safelauncher"), "metadata_cache.json")
        self._steam_build_checked_ts = {}
        self._achievement_checked_ts = {}
        if not os.path.isfile(cache_file):
            return
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            from core.cloud_save_sync import cloud_context_fingerprint
            # A status cache belongs to one endpoint/account. Old entries are
            # not useful hints after a credential or backend switch.
            if data.get("cloud_context") == cloud_context_fingerprint():
                cloud_cache = data.get("cloud_save_status", {})
                for gid_str, entry in cloud_cache.items():
                    try:
                        gid = int(gid_str)
                        stat_val = entry.get("status")
                        if stat_val:
                            l_stats = SaveStats(
                                exists=entry.get("local_exists", False),
                                last_modified=entry.get("local_mtime", 0.0),
                                size_bytes=entry.get("local_size", 0),
                                file_count=entry.get("local_count", 0),
                                display_path=entry.get("display_path", "")
                            )
                            c_stats = SaveStats(
                                exists=entry.get("cloud_exists", False),
                                last_modified=entry.get("cloud_mtime", 0.0),
                                size_bytes=entry.get("cloud_size", 0)
                            )
                            self.cloud_save_status_cache[gid] = (SyncStatus(stat_val), l_stats, c_stats)
                            self.save_state_store.state(gid).checked_at = entry.get("checked_at", time.time())
                    except Exception:
                        continue

            attempted_tags = data.get("attempted_tags", [])
            self.metadata_attempted_tags = set(int(x) for x in attempted_tags if str(x).isdigit())

            steam_builds = data.get("steam_builds", {})
            for gid_str, entry in steam_builds.items():
                try:
                    gid = int(gid_str)
                    self.steam_check_results[gid] = (
                        entry.get("latest_build_id", ""),
                        entry.get("latest_build_date", 0),
                        entry.get("is_update", False),
                        entry.get("error", "")
                    )
                    self._steam_build_checked_ts[gid] = entry.get("checked_at", time.time())
                    self.metadata_attempted_builds.add(gid)
                except Exception:
                    continue

            ach_cache = data.get("achievements", {})
            for gid_str, entry in ach_cache.items():
                try:
                    gid = int(gid_str)
                    self.achievement_status_cache[gid] = (
                        entry.get("unlocked_count", 0),
                        entry.get("total_count", 0),
                        float(entry.get("pct", 0.0)),
                        entry.get("recent", [])
                    )
                    self._achievement_checked_ts[gid] = entry.get("checked_at", time.time())
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"Could not load metadata cache: {e}")

    def _save_persistent_cache(self):
        """Atomically persist cloud save status, Steam lookup, and achievement cache to ~/.cache/safelauncher/metadata_cache.json."""
        import json
        import time
        from core.cloud_save_sync import cloud_context_fingerprint
        cache_dir = os.path.expanduser("~/.cache/safelauncher")
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, "metadata_cache.json")
        try:
            cloud_dict = {}
            for gid, val in self.cloud_save_status_cache.items():
                status, l_stats, c_stats = val
                cloud_dict[str(gid)] = {
                    "status": status.value if hasattr(status, "value") else str(status),
                    "local_exists": getattr(l_stats, "exists", False) if l_stats else False,
                    "local_mtime": getattr(l_stats, "last_modified", 0.0) if l_stats else 0.0,
                    "local_size": getattr(l_stats, "size_bytes", 0) if l_stats else 0,
                    "local_count": getattr(l_stats, "file_count", 0) if l_stats else 0,
                    "display_path": getattr(l_stats, "display_path", "") if l_stats else "",
                    "cloud_exists": getattr(c_stats, "exists", False) if c_stats else False,
                    "cloud_mtime": getattr(c_stats, "last_modified", 0.0) if c_stats else 0.0,
                    "cloud_size": getattr(c_stats, "size_bytes", 0) if c_stats else 0,
                    "checked_at": self.save_state_store.checked_at(gid) or time.time()
                }

            builds_dict = {}
            for gid, res in self.steam_check_results.items():
                build_id, b_date, is_update, err = res
                builds_dict[str(gid)] = {
                    "latest_build_id": build_id,
                    "latest_build_date": b_date,
                    "is_update": is_update,
                    "error": err,
                    "checked_at": getattr(self, "_steam_build_checked_ts", {}).get(gid, time.time())
                }

            achievements_dict = {}
            for gid, val in self.achievement_status_cache.items():
                unlocked_cnt, total_cnt, pct, recent = val
                achievements_dict[str(gid)] = {
                    "unlocked_count": unlocked_cnt,
                    "total_count": total_cnt,
                    "pct": pct,
                    "recent": recent if isinstance(recent, list) else [],
                    "checked_at": getattr(self, "_achievement_checked_ts", {}).get(gid, time.time())
                }

            payload = {
                "cloud_save_status": cloud_dict,
                "cloud_context": cloud_context_fingerprint(),
                "attempted_tags": list(self.metadata_attempted_tags),
                "steam_builds": builds_dict,
                "achievements": achievements_dict,
                "saved_at": time.time()
            }
            tmp_path = cache_file + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, cache_file)
        except Exception as e:
            logger.warning(f"Could not persist metadata cache: {e}")

    def _on_steam_tags_found(self, game_id: int, tags_list: list, steam_app_id: str = ""):
        """Callback when background SteamTagsFetcher returns genres/categories"""
        if steam_app_id and steam_app_id.isdigit() and int(steam_app_id) > 0:
            self.db.update_game_steam_id(game_id, steam_app_id)
        if tags_list:
            tags_str = ", ".join(tags_list)
            self.db.update_game_tags(game_id, tags_str)
        self.metadata_attempted_tags.add(game_id)
        self._save_persistent_cache()
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
                    background: rgba(255, 255, 255, 0.05);
                    color: #98989D;
                    border: none;
                    border-radius: 5px;
                    padding: 3px 8px;
                    font-size: 10px;
                    font-weight: 500;
                }
            """)
            self.tags_layout.addWidget(badge)

    def _on_hero_downloaded(self, game_id: int, image_path: str):
        if self.selected_game and self.selected_game[0] == game_id:
            self.hero_bg.set_hero_image(image_path)
            self._update_compact_game_page()

    def _on_disk_size_calculated(self, game_id: int, size_bytes: int):
        if self.selected_game and self.selected_game[0] == game_id:
            self.detail_disk_size.setText(f"Size: {format_size(size_bytes)}")

    def _render_cloud_status(self, game_id: int, status, local_stats=None, cloud_stats=None):
        """Update both library card badge and left detail inspector panel."""
        indicator = cloud_indicator(status)
        if game_id in self.banner_widgets:
            self.banner_widgets[game_id].set_cloud_status(status)

        self._update_library_item("update_cloud_status", game_id, status)


        if self.selected_game and self.selected_game[0] == game_id:
            self.detail_cloud_status.setText(
                f"<font color='{indicator.color}'><b>{indicator.label}</b></font>"
            )
            self.detail_cloud_status.setToolTip(indicator.tooltip)

            if hasattr(self, "btn_detail_cloud_restore"):
                c_stats = cloud_stats
                if c_stats is None:
                    cached_entry = self.cloud_save_status_cache.get(game_id)
                    if cached_entry:
                        c_stats = cached_entry[2]
                if c_stats and getattr(c_stats, "exists", False):
                    self.btn_detail_cloud_restore.show()
                else:
                    self.btn_detail_cloud_restore.hide()

    def _restore_selected_game_cloud_save(self):
        """Restore cloud save for the currently selected library game.

        Shows a progress dialog immediately (before any network I/O) so the
        user gets instant feedback.  The status check and restore both run on
        a daemon worker thread; the result comes back via _save_restore_finished.
        """
        game = self.selected_game
        if not game:
            return
        game_id, game_name, game_path = game[0], game[1], game[2]
        steam_id = str(game[6]).strip() if len(game) > 6 and game[6] else ""

        # L-5: Disable the button immediately to prevent double-click races.
        if hasattr(self, "btn_detail_cloud_restore"):
            self.btn_detail_cloud_restore.setEnabled(False)

        progress = QProgressDialog(f"Checking cloud save for '{game_name}'…", None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.show()
        self._active_restore_progress = progress

        def _work():
            from core.cloud_operations import CloudOperationResult
            preflight = self.cloud_sync_coordinator.preflight(game_id, game_name, game_path, steam_id)
            if preflight.error is not None:
                return {
                    "game_id": game_id,
                    "game_name": game_name,
                    "result": preflight.error,
                    "error": preflight.error.error,
                    "guidance": preflight.error.guidance,
                }
            if not preflight.cloud_stats or not preflight.cloud_stats.exists:
                result = CloudOperationResult(
                    False, "Cloud restore", game_name,
                    error="No cloud save is available to restore.",
                    category="cloud_missing",
                    guidance="Upload a local save first, then try restoring again.",
                )
                return {
                    "game_id": game_id,
                    "game_name": "__no_cloud_save__",
                    "result": result,
                    "error": result.error,
                    "guidance": result.guidance,
                }
            result = self.cloud_sync_coordinator.restore_cloud_save(
                game_id, game_name, game_path, steam_id=steam_id
            )
            payload = {"game_id": game_id, "game_name": game_name, "result": result}
            if not result.success:
                payload["error"] = result.error
                payload["guidance"] = result.guidance
            return payload

        self._start_managed_task(
            "SafeLauncher-ManualRestore",
            _work,
            self._save_restore_finished.emit,
        )

    def _on_save_restore_finished(self, payload: dict):
        if hasattr(self, "_active_restore_progress") and self._active_restore_progress:
            try:
                self._active_restore_progress.close()
                self._active_restore_progress.deleteLater()
            except Exception:
                pass
            self._active_restore_progress = None
        game_id = payload.get("game_id")
        game_name = payload.get("game_name", "")
        result = payload.get("result")
        ok = bool(getattr(result, "success", False))
        # Re-enable the restore button regardless of outcome (L-5 companion)
        if hasattr(self, "btn_detail_cloud_restore"):
            self.btn_detail_cloud_restore.setEnabled(True)
        # Handle the "no cloud save found" sentinel from the worker
        if game_name == "__no_cloud_save__":
            game = self.selected_game
            display_name = game[1] if game else "this game"
            QMessageBox.information(
                self, "No Cloud Save",
                f"No cloud save found for '{display_name}'."
            )
            return
        if ok:
            self._show_toast(f"Successfully restored cloud save for '{game_name}'.")
            self.request_cloud_recheck([game_id], "manual_restore")
        else:
            message = getattr(result, "error", "Failed to restore cloud save.")
            guidance = getattr(result, "guidance", "")
            self._show_toast(f"{message} {guidance}".strip(), is_error=True)


    def _on_cloud_save_status_calculated(self, game_id: int, status, local_stats, cloud_stats):
        import time
        checked_at = time.time()
        self.save_state_store.set_cloud_status(
            game_id,
            status,
            local_stats,
            cloud_stats,
            checked_at=checked_at,
            context_generation=self.cloud_sync_coordinator.generation,
        )
        current = self.game_status_by_id.get(game_id, GameStatusState())
        self.game_status_by_id[game_id] = replace(
            current,
            cloud_status=status,
            local_stats=local_stats,
            cloud_stats=cloud_stats,
            cloud_checked_at=checked_at,
        )
        self._render_cloud_status(game_id, status, local_stats, cloud_stats)
        if hasattr(self, "_update_status_refresh_timer"):
            self._update_status_refresh_timer.start()
        self._save_persistent_cache()

    def _accept_cloud_status_for_context(self, generation: int, game_id: int, status, local_stats, cloud_stats):
        """Discard an asynchronous cloud result from a retired configuration."""
        if not self.cloud_sync_coordinator.accepts(generation):
            logger.debug("Discarded cloud status for game %s from retired context %s", game_id, generation)
            return
        self._on_cloud_save_status_calculated(game_id, status, local_stats, cloud_stats)

    def _update_detail_panel(self):
        """Update left panel with current selected game details and trigger smooth slide animation."""
        game = self.selected_game
        if not game:
            if self.library_view_mode not in ("compact", "steam"):
                self._animate_left_panel(False)
            self.hero_bg.set_hero_image(None)
            return

        game_id, name, path, exe, mode, banner_url, steam_id = game[:7]
        playtime_seconds = game[7] if len(game) > 7 and game[7] else 0
        is_fav = bool(game[8]) if len(game) > 8 and game[8] else False
        last_played_ts = game[9] if len(game) > 9 and game[9] else 0
        tags_str = game[10] if len(game) > 10 and game[10] else ""

        # Update Hero Blurred Background Image (strictly 16:9 widescreen artwork with banner fallback)
        full_exe = os.path.join(path, exe) if (path and exe) else ""
        hero_cache_path = self.sgdb_client.get_hero_cached_path(steam_id=steam_id, game_name=name, exe_path=full_exe, game_id=game_id)
        if hero_cache_path and os.path.exists(hero_cache_path):
            self.hero_bg.set_hero_image(hero_cache_path)
        elif banner_url and os.path.exists(banner_url):
            self.hero_bg.set_hero_image(banner_url)
        else:
            self.hero_bg.set_hero_image(None)

        if not hero_cache_path and game_id not in self._hero_attempted:
            if not any(isinstance(f, HeroFetcherThread) and f.game_id == game_id for f in self.metadata_fetchers):
                self._hero_attempted.add(game_id)
                hero_thread = HeroFetcherThread(game_id, name, steam_id, self.sgdb_client, exe_path=full_exe, parent=self)
                hero_thread.hero_downloaded.connect(self._on_hero_downloaded)
                self._track_metadata_fetcher(hero_thread)

        if self.library_view_mode in ("compact", "steam"):
            self.detail_panel.setVisible(False)
            self.btn_reveal_detail.setVisible(False)
            return

        self._animate_left_panel(True)

        # Update Inspector Cover Art Preview (2:3 Portrait Cover)
        if banner_url and os.path.exists(banner_url):
            pix = QPixmap(banner_url)
            if not pix.isNull():
                scaled_cover = pix.scaled(QSize(180, 270), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                rounded_cover = create_rounded_pixmap(scaled_cover, QSize(180, 270), radius=12)
                self.detail_cover.setPixmap(rounded_cover)
            else:
                self.detail_cover.setPixmap(QPixmap())
                self.detail_cover.setText(name)
        else:
            self.detail_cover.setPixmap(QPixmap())
            self.detail_cover.setText(name)

        self.detail_title.setText(name)
        self.detail_playtime.setText(GameBannerWidget._format_playtime(playtime_seconds))
        self.detail_last_played.setText(self._format_last_played(last_played_ts))

        # Disk Size: check in-memory LRU cache first to avoid re-scanning multi-GB folders
        cached_size = peek_dir_size(path) if (path and os.path.exists(path)) else None
        if cached_size is not None:
            self.detail_disk_size.setText(f"Size: {format_size(cached_size)}")
        elif path and os.path.exists(path):
            self.detail_disk_size.setText("Size: Calculating...")
            disk_thread = DiskSizeFetcherThread(game_id, path, parent=self)
            disk_thread.disk_size_calculated.connect(self._on_disk_size_calculated)
            self._track_metadata_fetcher(disk_thread)
        else:
            self.detail_disk_size.setText("Size: --")

        # Cloud Save status: instant render from cache if available, background refresh only if stale (> 30 min)
        import time
        cached_save = self.cloud_save_status_cache.get(game_id)
        now = time.time()
        last_checked = self.save_state_store.checked_at(game_id)
        is_stale = (now - last_checked) > 1800  # 30 mins

        if cached_save is not None:
            c_status, c_local, c_cloud = cached_save
            self._render_cloud_status(game_id, c_status, c_local, c_cloud)
        else:
            self.detail_cloud_status.setText("Cloud Save: Checking...")
            self.detail_cloud_status.setToolTip("Checking save sync status...")
            if hasattr(self, "btn_detail_cloud_restore"):
                self.btn_detail_cloud_restore.hide()

        if cached_save is None or is_stale:
            self._spawn_status_fetchers(
                [(game_id, name, path or "", str(steam_id or ""))],
                self._on_cloud_save_status_calculated,
                "detail",
                generation=self.cloud_sync_coordinator.generation,
            )

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
        steam_last_checked = getattr(self, "_steam_build_checked_ts", {}).get(game_id, 0)
        steam_is_stale = (now - steam_last_checked) > 7200  # 2 hours

        if steam_id and steam_id != "0" and (game_id not in self.metadata_attempted_builds or steam_is_stale) and not any(
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
        try:
            count = sum(
                1 for filename in os.listdir(shots_dir)
                if os.path.isfile(os.path.join(shots_dir, filename))
                and os.path.splitext(filename)[1].lower() in image_extensions
            ) if os.path.isdir(shots_dir) else 0
        except OSError:
            count = 0
        self.btn_detail_screenshots.setText(f"Screenshots ({count})")

        video_dir = os.path.abspath(os.path.expanduser(self.gpu_recorder_config.output_dir))
        video_prefix = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_") or "gameplay"
        video_extensions = (".mp4", ".mkv", ".webm", ".mov", ".avi")
        try:
            video_count = sum(
                1 for filename in os.listdir(video_dir)
                if os.path.isfile(os.path.join(video_dir, filename))
                and filename.lower().endswith(video_extensions)
                and filename.lower().startswith(video_prefix + "_")
            ) if os.path.isdir(video_dir) else 0
        except OSError:
            video_count = 0
        self.btn_detail_videos.setText(f"Videos ({video_count})")

        # Achievements card & badges update
        self._update_achievement_inspector(game_id, steam_id)
        if steam_id and str(steam_id).strip() not in ("", "0"):
            now = time.time()
            last_checked = getattr(self, "_achievement_checked_ts", {}).get(game_id, 0)
            if game_id not in self.achievement_status_cache or (now - last_checked) > 900:
                self.request_achievement_recheck([game_id], tag="selection")

        self._update_detail_launch_button(game_id)
        self.btn_detail_launch.setVisible(True)
        self.btn_detail_launch.raise_()
        self.btn_detail_edit.setEnabled(True)
        self.btn_detail_screenshots.setEnabled(True)
        self.btn_detail_videos.setEnabled(True)
        self.btn_detail_properties.setEnabled(True)
        self.btn_detail_remove.setEnabled(True)
        self._animate_left_panel(True)

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

        # Synchronize compact container play button if compact view is active
        if hasattr(self, "compact_container") and self.compact_container:
            if self.selected_game and self.selected_game[0] == game_id:
                if game_id in self._stopping_game_ids:
                    self.compact_container.set_play_state("stopping")
                elif game_id in self.running_game_ids:
                    self.compact_container.set_play_state("running")
                else:
                    self.compact_container.set_play_state("play")

    def _stop_game(self, game_id: int):
        """Terminate the active game process and its sandbox container."""
        self._stopping_game_ids.add(game_id)
        if hasattr(self, "compact_container") and self.compact_container:
            if self.selected_game and self.selected_game[0] == game_id:
                self.compact_container.set_play_state("stopping")
        stopped = False
        self.game_sessions.mark_stopping(game_id)
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
            self._update_detail_launch_button(game_id)

    def _launch_mode(self, game_id: int, path: str, exe: str, selected_mode: str, sandbox: bool = True, disable_performance: bool = False):
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
            if disable_performance:
                game_env_vars = {
                    key: value for key, value in game_env_vars.items()
                    if key not in MANAGED_ENV_KEYS
                }

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
                "disable_performance": disable_performance,
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
        self._show_toast(f"Checking cloud saves for '{game_name}'…")

        def _work():
            payload = {"proceed": True, "needs_conflict": False, "toast": "", "ctx": ctx}
            preflight = self.cloud_sync_coordinator.preflight(ctx["game_id"], game_name, path, steam_id)
            if preflight.error is not None:
                payload["cloud_error"] = preflight.error
                payload["toast"] = (
                    f"Cloud check failed — launching '{game_name}' with local saves."
                )
                payload["error"] = preflight.error.error
                payload["guidance"] = preflight.error.guidance
                return payload

            status = preflight.status
            local_stats = preflight.local_stats
            cloud_stats = preflight.cloud_stats
            if status == SyncStatus.CLOUD_OFFLINE:
                payload["toast"] = f"Cloud not connected — launching '{game_name}' with local saves."
            elif status == SyncStatus.CLOUD_ONLY:
                auto_newer = self.settings.value("auto_prefer_newer_saves", False, type=bool)
                if auto_newer:
                    result = self.cloud_sync_coordinator.restore_cloud_save(ctx["game_id"], game_name, path, steam_id=steam_id)
                    payload["cloud_result"] = result
                    payload["toast"] = (
                        f"Restored cloud save for '{game_name}'." if result.success else
                        f"Cloud restore failed — launching '{game_name}' with local saves."
                    )
                    if not result.success:
                        payload["error"] = result.error
                        payload["guidance"] = result.guidance
                else:
                    payload["needs_cloud_only_prompt"] = True
                    payload["cloud_stats"] = cloud_stats
            elif status == SyncStatus.CLOUD_NEWER:
                auto_newer = self.settings.value("auto_prefer_newer_saves", False, type=bool)
                if auto_newer:
                    result = self.cloud_sync_coordinator.restore_cloud_save(ctx["game_id"], game_name, path, steam_id=steam_id)
                    payload["cloud_result"] = result
                    payload["toast"] = (
                        f"Updated to newer cloud save for '{game_name}'." if result.success else
                        f"Cloud restore failed — launching '{game_name}' with local saves."
                    )
                    if not result.success:
                        payload["error"] = result.error
                        payload["guidance"] = result.guidance
                else:
                    payload["needs_conflict"] = True
                    payload["local_stats"] = local_stats
                    payload["cloud_stats"] = cloud_stats
            elif status == SyncStatus.LOCAL_NEWER:
                if not cloud_stats.exists:
                    payload["toast"] = f"Local saves ready for '{game_name}'."
                else:
                    auto_local = self.settings.value("auto_prefer_local_saves", False, type=bool)
                    if auto_local:
                        payload["toast"] = f"Local saves preferred for '{game_name}'."
                    else:
                        payload["needs_conflict"] = True
                        payload["local_stats"] = local_stats
                        payload["cloud_stats"] = cloud_stats
            return payload

        self._start_managed_task(
            "SafeLauncher-PrelaunchSync",
            _work,
            self._prelaunch_resolved.emit,
        )

    def _finish_prelaunch_sync(self, payload: dict):
        """GUI-thread continuation after the pre-launch sync worker resolves.

        Dialogs (conflict chooser, CLOUD_ONLY prompt) are shown here on the
        main thread.  Any resulting I/O (cloud download / local upload) is
        dispatched to a daemon thread via _prelaunch_restore_done so the Qt
        main thread never blocks on network or disk work.
        """
        if hasattr(self, "_active_prelaunch_progress") and self._active_prelaunch_progress:
            try:
                self._active_prelaunch_progress.close()
                self._active_prelaunch_progress.deleteLater()
            except Exception:
                pass
            self._active_prelaunch_progress = None

        ctx = payload.get("ctx", {})
        game_name = ctx.get("game_name", "")

        if payload.get("needs_conflict"):
            conflict_dlg = SaveConflictDialog(game_name, payload["local_stats"], payload["cloud_stats"], parent=self)
            if conflict_dlg.exec() == QDialog.DialogCode.Accepted:
                if conflict_dlg.always_newer:
                    if conflict_dlg.choice == "cloud":
                        self.settings.setValue("auto_prefer_newer_saves", True)
                    else:
                        self.settings.setValue("auto_prefer_local_saves", True)

                if conflict_dlg.choice == "cloud":
                    def _do_cloud_restore(ctx=ctx):
                        result = self.cloud_sync_coordinator.restore_cloud_save(
                            ctx["game_id"], ctx["game_name"], ctx["path"], steam_id=ctx.get("steam_id", "")
                        )
                        toast = (
                            f"Restored cloud save for '{ctx['game_name']}' — your previous save was kept as a local backup."
                            if result.success else
                            f"Could not restore the cloud save for '{ctx['game_name']}' — launched with local saves."
                        )
                        payload = {"ctx": ctx, "ok": result.success, "toast": toast, "cloud_result": result}
                        if not result.success:
                            payload["error"] = result.error
                            payload["guidance"] = result.guidance
                        return payload

                    self._start_managed_task(
                        "SafeLauncher-ConflictRestore",
                        _do_cloud_restore,
                        self._prelaunch_restore_done.emit,
                    )
                    return  # resume in _on_prelaunch_restore_done
                else:
                    # Keep local: upload in background, launch immediately
                    def _do_local_upload(ctx=ctx):
                        result = self.cloud_sync_coordinator.upload_local_save(
                            ctx["game_id"], ctx["game_name"], ctx["path"], steam_id=ctx["steam_id"]
                        )
                        payload = {
                            "ctx": ctx, "ok": result.success, "cloud_result": result,
                            "toast": (
                                "Overwrote cloud save with local version."
                                if result.success else
                                "Could not upload the local save — launching with local state."
                            )
                        }
                        if not result.success:
                            payload["error"] = result.error
                            payload["guidance"] = result.guidance
                        return payload

                    self._start_managed_task(
                        "SafeLauncher-ConflictUpload",
                        _do_local_upload,
                        self._prelaunch_restore_done.emit,
                    )
                    return  # resume in _on_prelaunch_restore_done
            else:
                # Closing the conflict dialog cancels the launch — say so
                # instead of silently dropping the user's Play click.
                self._show_toast(f"Launch cancelled — resolve the save conflict for '{game_name}' first.")
                return

        elif payload.get("needs_cloud_only_prompt"):
            c_stats = payload.get("cloud_stats")
            ans = QMessageBox.question(
                self, "Restore Cloud Save",
                f"A cloud save is available for '{game_name}':\n\n"
                f"{c_stats.display_path}\n\n"
                "Would you like to restore this cloud save to the game before launching?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ans == QMessageBox.StandardButton.Yes:
                def _do_cloud_only_restore(ctx=ctx):
                    result = self.cloud_sync_coordinator.restore_cloud_save(
                        ctx["game_id"], ctx["game_name"], ctx["path"], steam_id=ctx.get("steam_id", "")
                    )
                    toast = (
                        f"Restored cloud save for '{ctx.get('game_name', '')}'."
                        if result.success else
                        f"Failed to restore cloud save for '{ctx.get('game_name', '')}'."
                    )
                    payload = {"ctx": ctx, "ok": result.success, "toast": toast, "cloud_result": result}
                    if not result.success:
                        payload["error"] = result.error
                        payload["guidance"] = result.guidance
                    return payload

                self._start_managed_task(
                    "SafeLauncher-CloudOnlyRestore",
                    _do_cloud_only_restore,
                    self._prelaunch_restore_done.emit,
                )
                return  # resume in _on_prelaunch_restore_done
            else:
                self._show_toast(f"Launching '{game_name}' without restoring cloud save.")
                # Fall through to _continue_launch below

        elif payload.get("toast"):
            # Informational toast only — no save conflict, launch proceeds.
            # NOTE: If adding an abort-toast path in future (e.g. preflight error
            # that should cancel the launch), return early here instead of
            # reaching _continue_launch.
            toast = payload["toast"]
            if payload.get("guidance"):
                toast = f"{toast} {payload['guidance']}"
            self._show_toast(toast, is_error=bool(payload.get("error")))

        # All non-async, non-abort paths reach here and proceed to launch.
        self._continue_launch(ctx)

    def _on_prelaunch_restore_done(self, result: dict):
        """Main-thread slot: close the progress dialog and continue the launch."""
        if hasattr(self, "_active_prelaunch_progress") and self._active_prelaunch_progress:
            try:
                self._active_prelaunch_progress.close()
                self._active_prelaunch_progress.deleteLater()
            except Exception:
                pass
            self._active_prelaunch_progress = None

        toast = result.get("toast", "")
        if result.get("guidance"):
            toast = f"{toast} {result['guidance']}".strip()
        if toast:
            self._show_toast(toast, is_error=bool(result.get("error")))

        ctx = result.get("ctx", {})
        if result.get("ok") and ctx.get("game_id") is not None:
            self.refresh_cloud_status_for_game(ctx["game_id"])

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
            existing_session = self.game_sessions.get(game_id)
            if existing_session and existing_session.is_live:
                self._stop_game(game_id)
                return
            if hasattr(self.runner, "set_proton_path"):
                self.runner.set_proton_path(selected_proton)
            process = self.runner.launch(path, exe, selected_mode, steam_id, sandbox=sandbox, env_vars=env_vars)
            if process:
                logger.info(f"Successfully launched '{game_name}' (PID: {process.pid})")
                # Register the tracker before refreshing UI so the derived
                # running_game_ids already contains this game.
                session_id = self.db.create_playtime_session(game_id, started_at=int(time.time()))
                session = self.game_sessions.start(game_id, game_name, process, session_id=session_id)
                tracker = PlaytimeTrackerThread(game_id, process, session_id=session_id, parent=self)
                self.game_sessions.attach_tracker(game_id, tracker)
                tracker.playtime_recorded.connect(self._on_playtime_recorded)
                tracker.playtime_checkpoint.connect(self._on_playtime_checkpoint)
                tracker.playtime_session_recorded.connect(self._on_playtime_session_recorded)
                tracker.finished.connect(lambda t=tracker: self._cleanup_tracker(t))
                self.playtime_trackers.append(tracker)
                self._register_worker(tracker)
                tracker.start()
                if self.selected_game and self.selected_game[0] == game_id:
                    self._update_detail_launch_button(game_id)
                # Update Discord Rich Presence
                if hasattr(self, 'discord_rpc') and self.discord_rpc:
                    self.discord_rpc.set_activity(game_name, start_timestamp=int(time.time()), details="Playing in Sandbox")

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

                # Real-time achievement monitoring
                if steam_id and str(steam_id).strip() not in ("", "0"):
                    try:
                        from core.achievement_watcher import AchievementWatcher
                        from core.achievement_schema import SteamAchievementFetcherWorker

                        cached_achs = self.db.get_game_achievements(game_id)
                        # Launch is a bounded backfill point: reconcile local
                        # state even when the schema is already cached.  Only
                        # request icons when the schema itself is missing.
                        fetcher = SteamAchievementFetcherWorker(
                            game_id,
                            str(steam_id).strip(),
                            game_path=path,
                            proton_path=selected_proton or "",
                            download_icons=not bool(cached_achs),
                            parent=self
                        )
                        fetcher.resolution_ready.connect(self._on_achievement_resolution_ready)
                        fetcher.schema_fetched.connect(self._on_achievement_schema_fetched)
                        self._track_metadata_fetcher(fetcher)

                        if game_id in self.achievement_watchers:
                            try:
                                self.achievement_watchers[game_id].stop()
                            except Exception:
                                pass

                        watcher = AchievementWatcher(game_id, str(steam_id).strip(), selected_proton or "", path or "", parent=self)
                        watcher.achievement_unlocked.connect(self._on_achievement_unlocked)
                        watcher.start()
                        self.achievement_watchers[game_id] = watcher
                    except Exception as ach_err:
                        logger.warning(f"Could not initialize achievement watcher for {game_name}: {ach_err}")

                # Show animated Safe Launch Popup with console log stream & greeting (non-blocking)
                popup = SafeLaunchDialog(
                    game_name,
                    user_name=self.user_name,
                    process=process,
                    parent=self,
                    session_manager=self.game_sessions,
                    game_id=game_id,
                )
                popup.retry_requested.connect(
                    lambda retry_mode: self._launch_mode(game_id, path, exe, retry_mode, sandbox=True)
                )
                popup.performance_retry_requested.connect(
                    lambda: self._launch_mode(
                        game_id, path, exe, selected_mode, sandbox=sandbox, disable_performance=True
                    )
                )
                popup.unsafe_launch_requested.connect(
                    lambda: self._launch_mode(game_id, path, exe, selected_mode, sandbox=False)
                )
                popup.edit_game_requested.connect(lambda: self._on_edit(game_id))
                popup.prefix_maintenance_requested.connect(
                    lambda: (self._select_game_by_id(game_id), self._open_prefix_maintenance())
                )
                popup.settings_requested.connect(self._open_settings)
                popup.runtime_manager_requested.connect(self._open_runtime_manager)
                popup.show()
        except Exception as e:
            logger.error(f"Failed to launch game ID {game_id}: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to launch game: {str(e)}")

    def _get_selected_game(self):
        """Get the currently selected game"""
        return self.selected_game

    def _update_achievement_inspector(self, game_id: int, steam_id: str):
        """Update achievements card, badges, and button in the inspector panel."""
        if not steam_id or str(steam_id).strip() in ("", "0"):
            self.btn_detail_achievements.setVisible(False)
            self.detail_ach_card.setVisible(False)
            return

        try:
            cached = self.achievement_status_cache.get(game_id)
            if cached:
                unlocked_count, total_count, pct, recent = cached
            else:
                unlocked_count, total_count, pct = self.db.get_achievement_stats(game_id)
                recent = self.db.get_recent_unlocked_achievements(game_id, limit=5)
                self.achievement_status_cache[game_id] = (unlocked_count, total_count, pct, recent)

            if total_count > 0:
                self.lbl_detail_ach_count.setText(f"{unlocked_count} / {total_count} ({int(pct)}%)")
                self.detail_ach_progress.setValue(int(pct))
                self.btn_detail_achievements.setText(f"Achievements ({unlocked_count}/{total_count})")
                self.btn_detail_achievements.setVisible(True)
                self.detail_ach_card.setVisible(True)

                # Update mini badge strip
                while self.detail_ach_badges_layout.count() > 0:
                    item = self.detail_ach_badges_layout.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()

                if recent:
                    for ach in recent:
                        b_lbl = QLabel()
                        b_lbl.setFixedSize(30, 30)
                        icon_p = ach.get("icon_path", "")
                        if icon_p and os.path.isfile(icon_p):
                            r_pix = create_rounded_pixmap(QPixmap(icon_p), QSize(30, 30), radius=6)
                            b_lbl.setPixmap(r_pix)
                        else:
                            b_lbl.setStyleSheet("background-color: rgba(48, 209, 88, 0.2); border-radius: 6px;")

                        d_name = html.escape(ach.get("display_name", ""))
                        d_desc = html.escape(ach.get("description", ""))
                        b_lbl.setToolTip(f"<div style='background: #1C1C1E; color: #FFF; padding: 3px;'><b>{d_name}</b><br/><span style='color: #A1A1A6; font-size: 11px;'>{d_desc}</span></div>")
                        self.detail_ach_badges_layout.addWidget(b_lbl)
                    self.detail_ach_badges_layout.addStretch()
                else:
                    lbl_no_yet = QLabel("No badges unlocked yet")
                    lbl_no_yet.setStyleSheet("color: #636366; font-size: 10px; background: transparent;")
                    self.detail_ach_badges_layout.addWidget(lbl_no_yet)
                    self.detail_ach_badges_layout.addStretch()
            else:
                resolution = self.achievement_resolution_cache.get(game_id)
                availability = getattr(getattr(resolution, "availability", None), "value", "") if resolution is not None else ""
                if resolution is not None and availability == "missing":
                    self.lbl_detail_ach_count.setText("Unavailable")
                    self.detail_ach_progress.setValue(0)
                    self.btn_detail_achievements.setText("Achievements · unavailable")
                    self.btn_detail_achievements.setVisible(True)
                    self.detail_ach_card.setVisible(True)
                    while self.detail_ach_badges_layout.count() > 0:
                        item = self.detail_ach_badges_layout.takeAt(0)
                        if item.widget():
                            item.widget().deleteLater()
                    unavailable = QLabel("Achievement schema is not available yet. Local unlock state will be kept and reconciled when a schema is found.")
                    unavailable.setWordWrap(True)
                    unavailable.setStyleSheet("color: #FF9F0A; font-size: 10px; background: transparent;")
                    self.detail_ach_badges_layout.addWidget(unavailable)
                    self.detail_ach_badges_layout.addStretch()
                else:
                    self.btn_detail_achievements.setText("Achievements")
                    self.btn_detail_achievements.setVisible(True)
                    self.detail_ach_card.setVisible(False)
        except Exception as ach_stat_err:
            logger.debug(f"Failed getting achievement stats for game {game_id}: {ach_stat_err}")
            self.btn_detail_achievements.setVisible(False)
            self.detail_ach_card.setVisible(False)

    def _open_achievements_dialog(self):
        """Open the achievements dialog for the currently selected game."""
        game = self._get_selected_game()
        if not game:
            return
        from ui.dialogs.achievements_dialog import AchievementsDialog
        dialog = AchievementsDialog(game, self.db, parent=self)
        dialog.exec()
        self.request_achievement_recheck([game[0]], tag="dialog_close")
        self._update_detail_panel()
        self._update_compact_game_page()

    def _open_achievement_profile(self):
        from ui.dialogs.achievement_profile_dialog import AchievementProfileDialog
        dialog = AchievementProfileDialog(self.db, parent=self)
        dialog.exec()
        self._refresh_library()
        self._update_detail_panel()

    def _on_achievement_unlocked(self, game_id: int, app_id: str, data: dict):
        """Handle real-time achievement unlock event from watcher."""
        api_name = data.get("api_name", "")
        unlock_time = float(data.get("unlock_time", 0.0) or 0.0)
        if not api_name:
            return

        schema = self.db.get_game_achievements(game_id)
        schema_has_api = any(a.get("api_name") == api_name for a in schema)
        if not schema_has_api:
            self._pending_achievement_unlocks.setdefault(game_id, {})[api_name] = unlock_time
            logger.info(
                "Queued achievement %s for game %s until its schema is available.", api_name, game_id
            )
            self.request_achievement_recheck([game_id], tag="realtime_schema")
            return

        # Database transition is the deduplication authority.  A duplicate
        # inotify/poll event must not emit a second toast or cloud sync.
        if not self.db.unlock_achievement(game_id, api_name, unlock_time):
            return

        unlocked_count, total_count, pct = self.db.get_achievement_stats(game_id)
        recent = self.db.get_recent_unlocked_achievements(game_id, limit=5)
        self.achievement_status_cache[game_id] = (unlocked_count, total_count, pct, recent)
        self._achievement_checked_ts[game_id] = time.time()
        self._save_persistent_cache()
        self._sync_launcher_metadata_async(game_id)

        # Retrieve display metadata
        ach_meta = next((a for a in schema if a.get("api_name") == api_name), None)
        display_name = ach_meta.get("display_name", api_name) if ach_meta else api_name
        description = ach_meta.get("description", "") if ach_meta else ""
        icon_path = ach_meta.get("icon_path", "") if ach_meta else ""

        toasts_enabled = self.settings.value("achievement_notifications_enabled", True, type=bool)
        desktop_enabled = self.settings.value("achievement_desktop_notifications", True, type=bool)

        if toasts_enabled:
            from ui.components.achievement_toast import AchievementToast
            toast = AchievementToast(display_name, description, icon_path=icon_path, parent=self)
            toast.show_animated(parent_widget=self)
            self.active_toasts.append(toast)
            # Prune closed toasts
            self.active_toasts = [t for t in self.active_toasts if t.isVisible()]

        if desktop_enabled:
            from ui.components.achievement_toast import send_desktop_notification
            send_desktop_notification(f"Achievement Unlocked: {display_name}", description, icon_path=icon_path)

        if self.selected_game and self.selected_game[0] == game_id:
            steam_id = str(self.selected_game[6]).strip() if len(self.selected_game) > 6 and self.selected_game[6] else ""
            self._update_achievement_inspector(game_id, steam_id)
            self._update_compact_game_page()

    def _on_achievement_schema_fetched(self, game_id: int, app_id: str, achievements: list):
        """Persist schema then commit live unlocks that arrived before it."""
        if not achievements:
            return
        self.db.save_achievement_schema(game_id, app_id, achievements)
        pending = self._pending_achievement_unlocks.pop(game_id, {})
        for api_name, unlock_time in pending.items():
            self._on_achievement_unlocked(
                game_id, app_id, {"api_name": api_name, "unlock_time": unlock_time}
            )

    def _on_achievement_resolution_ready(self, game_id: int, app_id: str, resolution):
        """Apply one local-first resolution to the durable DB and inspector."""
        self.achievement_resolution_cache[game_id] = resolution
        if getattr(resolution, "schema", None):
            self.db.save_achievement_schema(game_id, app_id, resolution.schema)
        if getattr(resolution, "state", None):
            self.db.unlock_achievements_batch(game_id, resolution.state)

        # A watcher may report an unlock before the schema request completes.
        # Replaying through the normal DB transition keeps notifications
        # exactly-once while preserving the event.
        if getattr(resolution, "schema", None) and game_id in self._pending_achievement_unlocks:
            pending = self._pending_achievement_unlocks.pop(game_id, {})
            for api_name, unlock_time in pending.items():
                self._on_achievement_unlocked(
                    game_id, app_id,
                    {"api_name": api_name, "unlock_time": unlock_time},
                )

        if self.selected_game and self.selected_game[0] == game_id:
            steam_id = str(self.selected_game[6]).strip() if len(self.selected_game) > 6 and self.selected_game[6] else ""
            self._update_achievement_inspector(game_id, steam_id)

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
            if self.library_view_mode in ("compact", "steam") and hasattr(self, "compact_container"):
                self.compact_container.sidebar_list.search_edit.setFocus()
                self.compact_container.sidebar_list.search_edit.selectAll()
                event.accept()
                return
            elif hasattr(self, "grid_search_input"):
                self.grid_search_input.setFocus()
                self.grid_search_input.selectAll()
                event.accept()
                return
            elif hasattr(self, "title_bar") and hasattr(self.title_bar, "search_input"):
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
        """Called after the session ledger is finalized when a game exits."""
        import time
        self.db.update_last_played(game_id, int(time.time()))
        total = self.db.get_playtime(game_id)
        if game_id in self.banner_widgets:
            self.banner_widgets[game_id].set_playtime(total)
        self._update_detail_panel()

    def _on_playtime_checkpoint(self, session_id: str, elapsed_seconds: int):
        """Persist an in-progress session without changing the visible total."""
        self.db.checkpoint_playtime_session(session_id, elapsed_seconds)

    def _on_playtime_session_recorded(self, session_id: str, elapsed_seconds: int, ended_at: int, finalized: bool):
        """Finalize the idempotent session event used by cloud metadata sync."""
        self.db.checkpoint_playtime_session(session_id, elapsed_seconds, finalized, ended_at)
        row = self.db.conn.execute("SELECT game_id FROM playtime_sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row:
            self._sync_launcher_metadata_async(int(row[0]))

    def _sync_launcher_metadata_async(self, game_id: int):
        """Sync launcher-owned metadata without blocking the GUI thread."""
        game = self.games_by_id.get(game_id)
        if not game:
            return
        name = game[1]
        app_id = str(game[6]).strip() if len(game) > 6 and game[6] else ""
        db_path = getattr(self.db, "db_path", None)

        def run():
            try:
                from database import GameDatabase
                from core.cloud_metadata_sync import CloudMetadataSync
                worker_db = GameDatabase(db_path) if db_path else GameDatabase()
                try:
                    # Achievements are account-wide and append-only; sync the
                    # profile before the legacy per-game metadata record.
                    profile_synced = CloudMetadataSync.sync_profile(worker_db)
                    CloudMetadataSync.sync_game(
                        worker_db, game_id, name, app_id,
                        drop_legacy_achievements=profile_synced,
                    )
                finally:
                    worker_db.close()
            except Exception as exc:
                logger.debug(f"Launcher metadata background sync failed for '{name}': {exc}")

        self._start_managed_task("SafeLauncher-MetadataSync", run)

    def _cleanup_tracker(self, tracker: PlaytimeTrackerThread):
        """Remove finished tracker from the list so it can be garbage collected."""
        session = self.game_sessions.get(tracker.game_id)
        if session is not None:
            self.game_sessions.finish(tracker.game_id, exit_code=tracker.process.poll())
        self._stopping_game_ids.discard(tracker.game_id)
        if tracker in self.playtime_trackers:
            self.playtime_trackers.remove(tracker)
        if tracker in self._background_workers:
            self._background_workers.remove(tracker)
        if self.selected_game and self.selected_game[0] == tracker.game_id:
            self._update_detail_launch_button(tracker.game_id)
        if hasattr(self, 'discord_rpc') and self.discord_rpc and len(self.playtime_trackers) == 0:
            self.discord_rpc.clear_activity()

        # Keep terminal sessions until diagnostics and UI consumers release
        # them. This prevents tracker completion from racing final reports.
        self.game_sessions.release(tracker.game_id)

        # Stop Achievement Watcher for this game
        if tracker.game_id in getattr(self, "achievement_watchers", {}):
            try:
                self.achievement_watchers[tracker.game_id].stop()
                del self.achievement_watchers[tracker.game_id]
            except Exception as ach_clean_err:
                logger.debug(f"Error stopping achievement watcher: {ach_clean_err}")

        # Recheck achievements upon game exit to persist any new unlocks
        self.request_achievement_recheck([tracker.game_id], tag="game_exit")

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

                def _exit_sync(name=g_name, gpath=g_path, sid=g_steam_id, gid=tracker.game_id):
                    payload = {"game": name, "game_id": gid, "outcome": "skipped", "reason": ""}
                    try:
                        # Allow 0.5s settling time for Wine/kernel to flush dirty pages after process exit
                        time.sleep(0.5)
                        status_result = self.cloud_sync_coordinator.check_status(gid, name, gpath, sid)
                        status = status_result.status
                        status_error = status_result.error
                        if status_error is not None:
                            payload["outcome"] = "failed"
                            payload["reason"] = status_error.error
                            payload["error"] = status_error.error
                            payload["guidance"] = status_error.guidance
                            payload["cloud_result"] = status_error
                            return payload
                        # Playtime can change even when the game did not touch
                        # a save file.  The archive manifest contains launcher
                        # metadata, and cloud deduplication skips this upload
                        # when both save data and metadata are unchanged.
                        if status in (SyncStatus.LOCAL_NEWER, SyncStatus.IN_SYNC):
                            result = self.cloud_sync_coordinator.upload_local_save(gid, name, gpath, steam_id=sid)
                            payload["cloud_result"] = result
                            payload["outcome"] = "uploaded" if result.success else "failed"
                            payload["reason"] = status.value
                            if not result.success:
                                payload["error"] = result.error
                                payload["guidance"] = result.guidance
                        else:
                            payload["outcome"] = "skipped"
                            payload["reason"] = status.value
                    except Exception as err:
                        logger.warning(f"Auto cloud save sync on game exit failed: {err}")
                        payload["outcome"] = "failed"
                        payload["reason"] = str(err)
                        payload["error"] = str(err)
                    return payload

                self._start_managed_task(
                    "SafeLauncher-ExitSync", _exit_sync, self._save_op_done.emit
                )
                # _save_op_done → _on_exit_save_sync_done is connected once in
                # __init__; concurrent exits each carry their own payload.
        except Exception as sync_exit_err:
            logger.warning(f"Auto cloud save sync on game exit failed: {sync_exit_err}")

    def _on_exit_save_sync_done(self, payload: dict):
        """GUI-thread slot reporting the outcome of the background exit upload."""
        name = payload.get("game", "")
        outcome = payload.get("outcome")
        if outcome == "uploaded":
            self._show_toast(f"Cloud save synced for '{name}'.")
            if self.selected_game and self.selected_game[1] == name:
                self._update_detail_panel()
            gid = payload.get("game_id")
            if gid is not None:
                # The upload just changed the cloud verdict — refresh the
                # badge instead of leaving the pre-upload state cached.
                self.refresh_cloud_status_for_game(gid)
        elif outcome == "failed":
            logger.warning(f"Exit cloud-save upload failed for '{name}'.")
            guidance = payload.get("guidance", "")
            message = payload.get("error", "Cloud sync failed.")
            self._show_toast(
                f"{message} Local save preserved. {guidance}".strip(),
                is_error=True,
            )
        elif payload.get("reason") in ("cloud_newer", "cloud_only"):

            logger.info(
                f"Skipping exit upload for '{name}': cloud save is newer "
                f"({payload['reason']}); SafeLauncher will ask which to keep on next launch."
            )
            if payload.get("reason") == "cloud_newer":
                # A newer save arrived from another device while this session
                # was running. The local fork stays safe on disk and is kept
                # as a backup generation next time the cloud side is accepted.
                self._show_toast(
                    f"Cloud save for '{name}' changed on another device — your session "
                    f"wasn't uploaded. SafeLauncher will ask which to keep next launch."
                )

    def request_cloud_recheck(self, game_ids=None, reason: str = ""):
        """THE single entry point for cloud status re-checks.

        Every feature routes through here — startup scan, poll timer, settings
        and account dialogs, game properties, exit uploads — so the re-check
        policy (backend gating, duplicate suppression, in-flight limits,
        prioritisation) is defined exactly once and behaves predictably.

        game_ids=None  -> every game (startup, cloud config change)
        game_ids=[]    -> only games whose cloud copy changed (listing diff)
        game_ids=[…]   -> exactly these games (after uploads, restores, edits)
        """
        import time
        from core.cloud_save_sync import backend_active, SyncStatus
        if not backend_active():
            return
        tag = f" ({reason})" if reason else ""
        generation = self.cloud_sync_coordinator.generation
        games_snapshot = [
            (g[0], g[1], g[2], str(g[6]).strip() if len(g) > 6 and g[6] else "")
            for g in list(self.games)
        ]

        if game_ids is None:
            # Whole library, most important first: uncached, then games whose
            # last check failed offline (retry), then stale (> 2 h), then
            # fresh. Cached values are already displayed instantly via
            # _refresh_library, so this only refines what the user sees.
            now = time.time()
            uncached, offline, stale, fresh = [], [], [], []
            for g in games_snapshot:
                cached = self.cloud_save_status_cache.get(g[0])
                if cached is None:
                    uncached.append(g)
                elif cached[0] == SyncStatus.CLOUD_OFFLINE:
                    offline.append(g)
                elif (now - self.save_state_store.checked_at(g[0])) > 7200:
                    stale.append(g)
                else:
                    fresh.append(g)
            targets = uncached + offline + stale + fresh
            if not targets:
                logger.info(f"Cloud recheck{tag}: nothing to scan.")
                return
            if any(isinstance(f, CloudSaveBatchQueueWorker) and f.isRunning()
                   for f in self.metadata_fetchers):
                logger.info(f"Cloud recheck{tag} skipped: batch worker already running.")
                return
            worker = CloudSaveBatchQueueWorker(
                targets,
                max_workers=3,
                parent=self,
                coordinator=self.cloud_sync_coordinator,
            )
            worker.game_status_ready.connect(
                lambda gid, status, local, cloud, g=generation:
                self._accept_cloud_status_for_context(g, gid, status, local, cloud)
            )
            worker.batch_finished.connect(self._on_cloud_batch_finished)
            self._track_metadata_fetcher(worker)
            return

        if game_ids:
            by_id = {g[0]: g for g in games_snapshot}
            self._spawn_status_fetchers(
                [by_id[gid] for gid in game_ids if gid in by_id],
                self._on_cloud_save_status_calculated, tag, generation=generation)
            return

        # Changed-only: diff a fresh listing against the cached statuses on a
        # worker thread, then fetch full statuses for the games that moved.
        if getattr(self, "_cloud_poll_in_flight", False):
            logger.info(f"Cloud recheck{tag} skipped: diff already in flight.")
            return
        self._cloud_poll_in_flight = True
        cache_snapshot = dict(self.cloud_save_status_cache)

        def _diff():
            return self.cloud_sync_coordinator.find_changed_games(games_snapshot, cache_snapshot)

        def _finish_diff(changed, expected_generation=generation):
            self._cloud_poll_in_flight = False
            if not self.cloud_sync_coordinator.accepts(expected_generation):
                logger.debug(
                    "Discarded cloud listing diff from retired context %s",
                    expected_generation,
                )
                return
            if changed:
                self._cloud_poll_changed.emit(changed)

        self._start_managed_task(
            "SafeLauncher-CloudRecheckDiff", _diff, _finish_diff
        )

    def _spawn_status_fetchers(self, targets: list, on_result, tag: str = "", generation=None):
        """Spawn per-game status fetchers, skipping games already in flight."""
        if generation is None:
            generation = self.cloud_sync_coordinator.generation
        for gid, name, path, steam_id in targets:
            if any(isinstance(f, CloudSaveStatusFetcherThread) and f.game_id == gid and f.isRunning()
                   for f in self.metadata_fetchers):
                logger.debug(f"Cloud recheck{tag}: fetcher for '{name}' already running.")
                continue
            fetcher = CloudSaveStatusFetcherThread(
                gid,
                name,
                path or "",
                steam_id or "",
                parent=self,
                coordinator=self.cloud_sync_coordinator,
            )
            def _deliver(gid, status, local, cloud, g=generation, callback=on_result):
                if not self.cloud_sync_coordinator.accepts(g):
                    logger.debug("Discarded cloud status for game %s from retired context %s", gid, g)
                    return
                callback(gid, status, local, cloud)

            fetcher.save_status_calculated.connect(_deliver)
            self._track_metadata_fetcher(fetcher)

    def _start_background_cloud_sync(self):
        """Startup cloud save check & sync queue across the library."""
        if getattr(self, "_offline_test_mode", False):
            return
        self.request_cloud_recheck(None, "startup")

    def _on_cloud_batch_finished(self, uploaded: list, newer_in_cloud: list):
        """GUI-thread slot when library background cloud batch queue completes."""
        if newer_in_cloud:
            names = ", ".join(newer_in_cloud[:2])
            extra = f" (+{len(newer_in_cloud)-2} more)" if len(newer_in_cloud) > 2 else ""
            self._show_toast(f"Newer cloud save(s) available for: {names}{extra}")
        elif uploaded:
            names = ", ".join(uploaded[:2])
            extra = f" (+{len(uploaded)-2} more)" if len(uploaded) > 2 else ""
            self._show_toast(f"Cloud Sync: Local save(s) ready to sync: {names}{extra}.")

    def _start_cloud_poll_timer(self):
        """Poll the cloud every 5 minutes so saves uploaded from another
        device surface mid-session instead of only at launch/exit."""
        self._cloud_poll_in_flight = False
        self._cloud_poll_timer = QTimer(self)
        self._cloud_poll_timer.setInterval(5 * 60 * 1000)
        self._cloud_poll_timer.timeout.connect(self._poll_cloud_for_changes)
        self._cloud_poll_changed.connect(self._on_cloud_poll_changed)
        self._cloud_poll_timer.start()

    def _poll_cloud_for_changes(self):
        self.request_cloud_recheck([], "poll")

    def _on_cloud_poll_changed(self, changed: list):
        """GUI-thread: re-derive full status for games whose cloud copy changed."""
        self._spawn_status_fetchers(changed, self._on_polled_cloud_status, "poll")

    def _on_polled_cloud_status(self, game_id: int, status, local_stats, cloud_stats):
        from core.cloud_save_sync import SyncStatus
        prev = self.cloud_save_status_cache.get(game_id)
        prev_status = prev[0] if prev else None
        self._on_cloud_save_status_calculated(game_id, status, local_stats, cloud_stats)
        if status == SyncStatus.CLOUD_NEWER and prev_status != SyncStatus.CLOUD_NEWER:
            if game_id in self.running_game_ids:
                return  # exit sync already surfaces the collision for this session
            name = self.games_by_id.get(game_id, (None, ""))[1]
            self._show_toast(
                f"Newer cloud save for '{name}' — uploaded from another device. "
                f"You'll be asked which to keep on launch."
            )

    def request_achievement_recheck(self, game_ids: Optional[list] = None, tag: str = ""):
        """Queue background achievement schema fetching and local unlock sync.

        game_ids:
          None  -> full library scan (startup, bulk reload).
          [id]  -> targeted recheck for specific game(s) (post-launch, selection).
        """
        tag = f" ({tag})" if tag else ""
        games_snapshot = list(self.games)
        if not games_snapshot:
            return

        if game_ids is None:
            now = time.time()
            uncached, stale, fresh = [], [], []
            for g in games_snapshot:
                steam_id = str(g[6]).strip() if len(g) > 6 and g[6] else ""
                if not steam_id:
                    continue
                gid = g[0]
                cached = self.achievement_status_cache.get(gid)
                if cached is None:
                    uncached.append(g)
                elif (now - getattr(self, "_achievement_checked_ts", {}).get(gid, 0)) > 3600:
                    stale.append(g)
                else:
                    fresh.append(g)
            targets = uncached + stale + fresh
            if not targets:
                logger.debug(f"Achievement recheck{tag}: no games with Steam IDs to scan.")
                return
            if any(isinstance(f, AchievementBatchQueueWorker) and f.isRunning() for f in self.metadata_fetchers):
                logger.debug(f"Achievement recheck{tag} skipped: batch worker already running.")
                return
            db_path = getattr(self.db, "db_path", None)
            worker = AchievementBatchQueueWorker(targets, max_workers=3, db_path=db_path, parent=self)
            worker.game_status_ready.connect(self._on_achievement_status_calculated)
            worker.batch_finished.connect(self._on_achievement_batch_finished)
            self._track_metadata_fetcher(worker)
            return

        if game_ids:
            by_id = {g[0]: g for g in games_snapshot}
            for gid in game_ids:
                if gid not in by_id:
                    continue
                g = by_id[gid]
                g_name = g[1]
                g_path = g[2]
                g_steam_id = str(g[6]).strip() if len(g) > 6 and g[6] else ""
                # GameRecord layout: index 9 is last_played; index 12 is proton_path.
                g_proton_path = str(g[12]).strip() if len(g) > 12 and g[12] else ""
                if not g_steam_id:
                    continue
                if any(isinstance(f, AchievementStatusFetcherThread) and f.game_id == gid and f.isRunning() for f in self.metadata_fetchers):
                    continue
                db_path = getattr(self.db, "db_path", None)
                fetcher = AchievementStatusFetcherThread(gid, g_name, g_path or "", g_steam_id, g_proton_path, db_path=db_path, parent=self)
                fetcher.resolution_ready.connect(self._on_achievement_resolution_ready)
                fetcher.achievement_status_calculated.connect(self._on_achievement_status_calculated)
                self._track_metadata_fetcher(fetcher)
                # _track_metadata_fetcher owns and starts the targeted worker,
                # matching the full-library queue path above.

    def _start_background_achievement_sync(self):
        """Start bounded achievement monitoring without a library-wide scan."""
        if getattr(self, "_offline_test_mode", False):
            return
        # Achievement resolution is deliberately lazy: selection, launch,
        # dialog open/close, and running-game polling are the explicit probes.
        # A startup sweep caused network bursts and made unavailable schemas
        # look like a library-wide failure.
        for game in list(self.games):
            if game and len(game) > 0:
                self._sync_launcher_metadata_async(int(game[0]))
        # Native Steam has no universal local unlock file.  When the user has
        # explicitly supplied Steam Web API credentials, recheck only games
        # that are actually running to provide bounded near-realtime updates.
        if self._achievement_poll_timer is None:
            self._achievement_poll_timer = QTimer(self)
            self._achievement_poll_timer.setInterval(60_000)
            self._achievement_poll_timer.timeout.connect(self._poll_running_achievements)
        self._achievement_poll_timer.start()

    def _poll_running_achievements(self):
        """Poll opted-in native Steam state without scanning the whole library."""
        running_ids = list(self.running_game_ids)
        if running_ids:
            self.request_achievement_recheck(running_ids, tag="running_poll")

    def _on_achievement_status_calculated(self, game_id: int, unlocked_count: int, total_count: int, pct: float, recent: list):
        """GUI-thread slot when an achievement worker finishes computing status for a game."""
        self.achievement_status_cache[game_id] = (unlocked_count, total_count, pct, recent)
        self._achievement_checked_ts[game_id] = time.time()
        if self.selected_game and self.selected_game[0] == game_id:
            steam_id = str(self.selected_game[6]).strip() if len(self.selected_game) > 6 and self.selected_game[6] else ""
            self._update_achievement_inspector(game_id, steam_id)
        self._sync_launcher_metadata_async(game_id)
        self._save_persistent_cache()

    def _on_achievement_batch_finished(self, total_games: int, total_unlocked: int):
        """GUI-thread slot when library background achievement batch queue completes."""
        logger.debug(f"Achievement batch queue complete: {total_games} games processed with achievements ({total_unlocked} total unlocked).")

    def _show_shutdown_progress(self):
        """Make cooperative shutdown visible instead of looking frozen."""
        progress = getattr(self, "_shutdown_progress", None)
        if progress is not None:
            return progress
        progress = QProgressDialog(
            "Ending running games and save operations safely…", "Keep SafeLauncher Open", 0, 0, self
        )
        progress.setWindowTitle("Closing SafeLauncher")
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.canceled.connect(self._abort_shutdown)
        progress.show()
        self._shutdown_progress = progress
        return progress

    def _abort_shutdown(self):
        """Return control instead of indefinitely hiding a stuck shutdown."""
        if not getattr(self, "_shutdown_deadline", 0.0):
            return
        progress = getattr(self, "_shutdown_progress", None)
        if progress is not None:
            progress.close()
            progress.deleteLater()
        self._shutdown_progress = None
        self._shutdown_deadline = 0.0
        self._shutdown_overdue_logged = False
        # A timer may already have queued one more close() pass. Consume it
        # rather than immediately starting a new shutdown after the user chose
        # to keep the launcher open.
        self._shutdown_abort_requested = True
        self.setEnabled(True)
        self.show()
        self.raise_()
        self.activateWindow()
        # A cancellation request is intentionally cooperative. It may have
        # stopped an optional refresh, so restore the recurring sources once
        # the user chooses to keep the launcher open.
        for timer_name in ("drive_check_timer", "_cloud_poll_timer", "_achievement_poll_timer"):
            timer = getattr(self, timer_name, None)
            if timer is not None and not timer.isActive():
                timer.start()
        self.game_sessions.start_observing()
        listener = getattr(self, "global_hotkeys", None)
        if listener is not None:
            try:
                listener.start()
            except Exception:
                pass
        self._show_toast("Shutdown cancelled. Some background work did not stop in time.", is_error=True)

    def closeEvent(self, event):
        """Cooperatively stop work without corrupting saves or freezing UI."""
        import time as _time

        if getattr(self, "_shutdown_abort_requested", False):
            self._shutdown_abort_requested = False
            event.ignore()
            return

        first_attempt = getattr(self, "_shutdown_deadline", 0.0) == 0.0
        if first_attempt:
            self._shutdown_deadline = _time.monotonic() + 12.0
            self._show_shutdown_progress()
            self.game_sessions.stop_observing()

            # Halt every source that schedules new background work while we
            # are trying to shut down.
            for timer_name in ("drive_check_timer", "_size_resort_timer", "_update_status_refresh_timer", "_cloud_poll_timer", "_achievement_poll_timer", "_update_check_timer"):
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
            if tracker.process and tracker.process.poll() is None:
                try:
                    tracker.process.terminate()
                except Exception:
                    pass
                if getattr(tracker, "sandbox_name", None):
                    _shutdown_firejail_sandbox(sandbox_name=tracker.sandbox_name)
            tracker.stop()

        # WorkerSupervisor is the authoritative registry. Semantic lists are
        # feature indexes only and may overlap.
        workers = self.worker_supervisor.workers(running_only=True)
        for worker in workers:
            if worker.isRunning():
                if hasattr(worker, "request_cancel"):
                    try:
                        worker.request_cancel()
                    except Exception:
                        pass
                elif hasattr(worker, "requestInterruption"):
                    try:
                        worker.requestInterruption()
                    except Exception:
                        pass
                # Network requests use short timeouts, but allow enough time
                # for the active request to return before Qt destroys QThread.
                # This is deliberately tiny: closeEvent is re-entered by a
                # timer, keeping the progress dialog responsive.
                worker.wait(25)

        still_running = self.worker_supervisor.wait(25)
        if still_running and _time.monotonic() < self._shutdown_deadline:
            progress = self._show_shutdown_progress()
            names = ", ".join(self.worker_supervisor.describe(worker) for worker in still_running[:4])
            if len(still_running) > 4:
                names += f" (+{len(still_running) - 4} more)"
            progress.setLabelText(
                f"Ending {len(still_running)} running operation(s) safely…\n{names}"
            )
            QTimer.singleShot(100, self.close)
            event.ignore()
            return

        if still_running:
            # QThread.terminate() can stop Python or a Qt extension while it
            # owns allocator/interpreter state. That turns a slow shutdown
            # into an intermittent native crash (and can corrupt a save
            # operation). Keep the hidden window alive until cooperative
            # cancellation finishes instead; every owned network task has a
            # bounded timeout and workers suppress completion after an
            # interruption request.
            if not getattr(self, "_shutdown_overdue_logged", False):
                self._shutdown_overdue_logged = True
                names = ", ".join(
                    self.worker_supervisor.describe(worker)
                    for worker in still_running
                )
                logger.warning(
                    "Cancelling shutdown after %d worker(s) missed its safe deadline: %s",
                    len(still_running), names,
                )
            # Never force-kill a Python/Qt worker: that can corrupt allocator
            # state or an in-progress save restore. Returning the window is
            # deterministic and leaves the user able to resolve the external
            # process instead of a permanently hidden launcher.
            QTimer.singleShot(0, self._abort_shutdown)
            event.ignore()
            return

        progress = getattr(self, "_shutdown_progress", None)
        if progress is not None:
            progress.close()
            progress.deleteLater()
            self._shutdown_progress = None

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

        for watcher in list(getattr(self, "achievement_watchers", {}).values()):
            try:
                watcher.stop()
            except Exception:
                pass
        getattr(self, "achievement_watchers", {}).clear()

        for toast in list(getattr(self, "active_toasts", [])):
            try:
                toast.close()
            except Exception:
                pass
        getattr(self, "active_toasts", []).clear()

        if hasattr(self, "_update_worker") and self._update_worker:
            try:
                self._update_worker.stop()
            except Exception:
                pass

        super().closeEvent(event)

        app = QApplication.instance()
        if app:
            app.quit()

    def _on_add(self, collection_name: str = ""):
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
                if collection_name.strip():
                    self.db.update_game_collection(game_id, collection_name.strip())
            self._refresh_library()
            if game_id and steam_id:
                self._capture_initial_steam_build(game_id, steam_id)
            self._show_toast(f"Game '{name}' added to library.")

    def _on_card_size_changed(self, value: int):
        """Update card banner size dynamically when user moves bottom size slider."""
        if hasattr(self, 'grid_container') and self.grid_container:
            self.grid_container.set_card_width(value)
        if hasattr(self, 'virtual_grid') and self.virtual_grid:
            self.virtual_grid.set_card_width(value)

    def set_virtualization_threshold(self, threshold: int) -> None:
        """Configure the library count threshold where virtualized grid activates."""
        self.virtualization_threshold = max(1, int(threshold))
        if self.library_view_mode not in ("grid", "list"):
            self.library_view_mode = "grid"
        self._refresh_library()

    def _show_toast(self, message: str, is_error: bool = False):
        """Show non-blocking toast overlay notification in bottom-right corner."""
        toast = ToastNotification(self, message, is_error=is_error)
        toast.show_toast(self)

    def _on_edit(self, game_id=None):
        """Edit details of the currently selected game."""
        if isinstance(game_id, int):
            self._select_game_by_id(game_id)
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

            # Clear selection before rebuilding compact view. Otherwise the
            # page can keep rendering the archived/deleted record even though
            # it has disappeared from the sidebar.
            self.selected_game = None
            self.library_selection.replace(self.library_selection.ids - {game_id})
            self._refresh_library()
            if self.library_view_mode in ("compact", "steam"):
                self._update_compact_game_page()

    def _on_export(self):
        game = self._get_selected_game()
        if not game:
            QMessageBox.warning(self, "Warning", "Please select a game.")
            return
        steam_id = str(game[6]).strip() if len(game) > 6 and game[6] else ""
        SaveManagerDialog(game[0], game[1], game[2], steam_id, self, self.cloud_sync_coordinator).exec()
        self.refresh_cloud_status_for_game(game[0])
    
    def _on_import(self):
        game = self._get_selected_game()
        if not game:
            self._show_toast("Please select a game to import save.", is_error=True)
            return
        steam_id = str(game[6]).strip() if len(game) > 6 and game[6] else ""
        dlg = SaveManagerDialog(game[0], game[1], game[2], steam_id, self, self.cloud_sync_coordinator)
        dlg._import_snapshot()
        self.refresh_cloud_status_for_game(game[0])
