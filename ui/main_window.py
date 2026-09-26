import os
import re
import time
import shutil
import subprocess
import uuid
from dataclasses import replace
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
from PyQt6.QtGui import QPixmap, QFont, QColor, QIcon, QPainter, QMovie, QDesktopServices, QKeySequence, QShortcut, QGuiApplication
from core.interfaces import ISandboxRunner, IBackupManager
from core.artwork_client import ArtworkClient
from core.playtime_tracker import PlaytimeTrackerThread
from core.steam_tags import SteamTagsFetcher
from core.steam_build_tracker import (
    SteamBuildFetcher,
    backfill_matching_build_date,
    has_resolved_build_reference,
    read_local_steam_build,
)
from core.steam_ids import normalize_steam_app_id
from core.date_formatting import format_datetime_timestamp, format_timestamp, get_date_format_key
from core.disk_utils import format_size, get_disk_usage, peek_dir_size, has_fresh_dir_size
from core.discord_rpc import DiscordRPC
from core.host_process import host_process_env
from core.archive_extractor import (
    DEFAULT_SANDBOX_DIR, ensure_sandbox_dir,
    find_executables, save_sandbox_config, scan_sandbox_games
)
from core.archive_installer import ArchiveInstaller
from core.game_archive_updater import recover_incomplete_game_updates
from core.proton_manager import GEProtonDownloader
from database import GameDatabase, _APP_DATA_DIR
from core.logger import get_logger
from core.launch_diagnostics import persist_diagnostics
from core.library_state import LibraryStateStore
from core.library_controller import LibraryController, LibraryQuery, LibrarySnapshot
from core.library_service import LibraryService
from core.library_metadata_state import LibraryMetadataState
from core.game_status import GameStatusState, cloud_indicator, is_cloud_conflict
from core.launch_session_coordinator import LaunchSessionContext, LaunchSessionCoordinator
from core.launch_policy import LaunchAction, LaunchPolicy
from core.compatibility_worker_index import CompatibilityWorkerIndex
from core.cloud_exit_sync_service import CloudExitSyncResult, CloudExitSyncService
from core.achievement_persistence_service import AchievementPersistenceService
from core.save_state import SaveStateStore
from core.cloud_operations import CloudStatusResult, CloudSyncCoordinator
from core.cloud_operation_service import CloudOperationService, CloudOperationTarget
from core.cloud_metadata_service import CloudMetadataService, CloudMetadataTarget
from core.cloud_account_service import CloudAccountService
from core.cloud_center_service import CloudCenterService, CloudOverview
from core.cloud_status_service import CloudStatusService, CloudStatusTarget
from core.cloud_status_polling_service import CloudStatusPollingService
from core.achievement_resource_service import AchievementResourceService, AchievementTarget
from core.achievement_state_store import AchievementStateStore
from core.library_achievement_coordinator import LibraryAchievementCoordinator
from core.artwork_resource_service import ArtworkResourceService, ArtworkTarget
from core.library_artwork_coordinator import LibraryArtworkCoordinator
from core.steam_resource_service import SteamResourceService
from core.library_steam_metadata_coordinator import LibrarySteamMetadataCoordinator
from core.cache_policy import cache_policy
from core.game_names import is_placeholder_game_name
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
    GameArchiveUpdateThread,
    GitHubReleasesFetcherThread, UmuBootstrapWorker, SafeLaunchLogReader,
    DiskSizeFetcherThread,
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
from ui.components.extraction_spinner import ExtractionSpinner
from ui.components.sort_combo import SortComboBox
from ui.components.sidebar import LeftSidebarWidget, CustomTitleBar, DialogTitleBar, add_soft_shadow
from ui.dialogs.proton_dialogs import ProtonSetupWizard, ProtonManagerDialog, UmuRuntimeManagerDialog
from ui.dialogs.game_dialogs import (
    AddGameDialog, EditGameDialog, LaunchOptionsDialog, SafeLaunchDialog,
    MissingDependencyDialog, ToastNotification, CustomRemoveDialog,
    ManageCollectionGamesDialog, CreateCollectionDialog, RenameCollectionDialog
)
from ui.dialogs.settings_dialog import UserSettingsDialog, ScreenshotGalleryDialog, VideoGalleryDialog, DiskManagerDialog
from ui.dialogs.cloud_center_dialog import CloudCenterDialog
from ui.dialogs.game_properties_dialog import GamePropertiesDialog
from ui.dialogs.save_manager_dialog import SaveManagerDialog
from ui.dialogs.save_conflict_dialog import SaveConflictDialog
from core.cloud_models import SyncStatus
from core.performance_env import MANAGED_ENV_KEYS
from ui.theme import (
    get_application_stylesheet, btn_primary_style, btn_secondary_style,
    btn_tertiary_style, btn_destructive_style, BG_APP, SURFACE, SURFACE_ELEVATED,
    BORDER, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, ACCENT_PRIMARY
)


import getpass
from core.playtime_tracker import PlaytimeTrackerThread, _shutdown_firejail_sandbox
from core.game_session import GameSessionManager
from core.safe_thread import FunctionWorker, TaskSupervisor, WorkerSupervisor
from core.operation_registry import OperationRegistry
from core.secret_store import get_secret
from core.network_policy import automatic_network_allowed, is_offline_mode, set_offline_mode
from core.network_probe import probe_internet
from ui.dialogs.network_dialog import NetworkUnavailableDialog
from ui.components.activity_drawer import ActivityDrawer
from ui.components.profile_page import ProfilePageWidget
from ui.components.cloud_ui import cloud_progress, confirm_restore
from core.central_auth import CentralAuthSession
from core.profile_service import get_profile_service_url
from core.profile_resource_service import ProfileResourceService
from core.profile_models import HANDLE_RE, load_profile_settings
from core.request_contracts import RequestKey, RequestPriority, ResourceStatus
from core.request_manager import RequestManager
from core.resource_cache import ResourceCache
from core.performance_metrics import ResourcePerformanceTracker
from core.save_history import normalize_history_entries
from core.steam_client import SteamClient
from ui.resource_binding import ResourceBinding, ResourceBindingRegistry, bind_resource, bind_request


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
    _managed_task_done = pyqtSignal(object)
    _profile_sync_done = pyqtSignal(object)
    _managed_cloud_batch_done = pyqtSignal(object)
    _cloud_auto_restore_done = pyqtSignal(object)
    _cloud_auto_upload_done = pyqtSignal(object)
    _managed_achievement_batch_done = pyqtSignal(object)
    _managed_steam_update_batch_done = pyqtSignal(object)
    _network_probe_done = pyqtSignal(object)

    # Compatibility views for older dialogs and rendering helpers.  The
    # dictionaries themselves belong to LibraryMetadataState; these accessors
    # keep the existing MainWindow-facing shape during the migration.
    @property
    def metadata_attempted_builds(self):
        return self.library_metadata_state.attempted_builds

    @property
    def metadata_attempted_tags(self):
        return self.library_metadata_state.attempted_tags

    @property
    def update_status_by_game_id(self):
        return self.library_metadata_state.update_status_by_game_id

    @property
    def game_status_by_id(self):
        return self.library_metadata_state.game_status_by_id

    @property
    def steam_check_results(self):
        return self.library_metadata_state.steam_check_results

    @property
    def local_version_by_game_id(self):
        return self.library_metadata_state.local_version_by_game_id


    def __init__(self, db: GameDatabase, runner: ISandboxRunner, backup: IBackupManager):
        super().__init__()
        self.db = db
        self.runner = runner
        self.backup = backup
        self.settings = QSettings("SafeLauncher", "SafeLauncher")
        self.sgdb_client = ArtworkClient()
        self.steam_client = SteamClient()
        # Keep the cache path available to every library presentation,
        # including the empty-filter branch used by compact view.
        self.cache_dir = self.sgdb_client.cache_dir
        resource_cache_dir = self.cache_dir.parent / "resources"
        self.resource_cache = ResourceCache(
            str(resource_cache_dir),
            max_entries=512,
            max_disk_bytes=64 * 1024 * 1024,
            legacy_directories=(str(self.cache_dir / "resources"),),
        )
        self.performance_tracker = ResourcePerformanceTracker()
        self.games = []
        self.selected_game = None
        self._settings_dialog_active = False
        self.banner_widgets = {}
        self.auto_fetchers = CompatibilityWorkerIndex("artwork-active-fallback")
        self._pending_auto_fetchers = CompatibilityWorkerIndex("artwork-pending-fallback")
        self.max_concurrent_auto_fetchers = 3
        # Compatibility-only artwork fetch guard.  Managed artwork attempts
        # are owned by LibraryArtworkCoordinator; this set exists only for
        # embedded callers that do not provide RequestManager.
        self._compat_auto_fetch_attempted = set()
        self.metadata_fetchers = CompatibilityWorkerIndex("metadata-active-fallback")
        self.library_metadata_state = LibraryMetadataState()
        self.save_state_store = SaveStateStore()
        self.cloud_sync_coordinator = CloudSyncCoordinator()
        # Incremented whenever the configured cloud identity changes. Every
        # asynchronous status result is bound to the generation that created it.
        # Compatibility alias; the coordinator is the authoritative owner of
        # cloud configuration generations.
        self._cloud_context_generation = self.cloud_sync_coordinator.generation
        self._cloud_status_bindings = ResourceBindingRegistry()
        # A library sweep, selected-game detail panel, and manual action can
        # all observe the same managed resource. Keep every consumer instead
        # of letting the last caller overwrite the previous callback.
        self._cloud_status_callbacks: dict[RequestKey, list[tuple[int, int, object]]] = {}
        self._cloud_status_target_ids: dict[RequestKey, int] = {}
        # Background cloud restores are keyed by game and context generation.
        # This prevents overlapping polling/startup/detail callbacks from
        # downloading the same version more than once.
        self._cloud_auto_restore_in_flight: dict[int, tuple[int, object]] = {}
        self._cloud_auto_upload_in_flight: dict[int, tuple[int, object]] = {}
        # A single cloud snapshot must not be restored forever when a remote
        # timestamp cannot converge with the extracted local files.  The
        # counter is reset automatically when the cloud version/signature
        # changes, and also bounds transient retry noise.
        self._cloud_auto_restore_attempts: dict[int, tuple[tuple, int]] = {}
        self._cloud_auto_upload_attempts: dict[int, tuple[tuple, int]] = {}
        self._closing = False
        self.achievement_state = AchievementStateStore()
        self.achievement_persistence_service = AchievementPersistenceService(self.db)
        self._achievement_poll_timer = None
        # A watcher can observe an unlock before its schema worker finishes.
        # Keep it in memory until the schema gives the API name a durable row;
        # otherwise the one-shot live event would be silently lost.
        self.playtime_trackers = []  # keep references so GC doesn't kill running threads
        self.library_controller = LibraryController()
        self.library_state = LibraryStateStore(self.library_controller)
        self.library_service = LibraryService(self.db, self.library_state)
        # A transient connectivity loss acts as a request gate until the
        # connectivity probe succeeds again. Keep this separate from the
        # user's explicit Offline Mode preference.
        self._transient_network_unavailable = False
        self.request_manager = RequestManager(
            max_workers=3,
            cache=self.resource_cache,
            offline_check=lambda: (
                not automatic_network_allowed(getattr(self, "settings", None))
                or bool(getattr(self, "_transient_network_unavailable", False))
            ),
        )
        self.achievement_resource_service = AchievementResourceService(self.request_manager)
        self.achievement_coordinator = LibraryAchievementCoordinator(
            self.achievement_resource_service,
        )
        self.steam_resource_service = SteamResourceService(
            self.request_manager,
            client=self.steam_client,
        )
        # One binding per AppID repairs cloud-only placeholder titles while
        # retaining the normal RequestManager cache/deduplication lifecycle.
        self._steam_name_bindings: dict[RequestKey, ResourceBinding] = {}
        self.steam_metadata_coordinator = LibrarySteamMetadataCoordinator(
            self.steam_resource_service,
        )
        self.artwork_resource_service = ArtworkResourceService(
            self.request_manager,
            client=self.sgdb_client,
        )
        self.artwork_coordinator = LibraryArtworkCoordinator(
            self.artwork_resource_service,
        )
        self.cloud_status_service = CloudStatusService(
            self.request_manager,
            coordinator=self.cloud_sync_coordinator,
            status_store=self.save_state_store,
            cache=self.resource_cache,
            legacy_cache_path=os.path.join(
                os.path.dirname(os.fspath(self.cache_dir)), "cloud_status_cache.json"
            ),
        )
        self.cloud_status_polling = CloudStatusPollingService(
            self.cloud_status_service,
            on_changed=self._cloud_poll_changed.emit,
            on_error=lambda error: logger.debug("Cloud status polling failed: %s", error),
        )
        self.cloud_operation_service = CloudOperationService(
            self.request_manager,
            coordinator=self.cloud_sync_coordinator,
        )
        self.cloud_exit_sync_service = CloudExitSyncService(self.cloud_operation_service)
        self.cloud_account_service = CloudAccountService(
            request_manager=self.request_manager,
        )
        self.cloud_metadata_service = CloudMetadataService(self.request_manager)
        self.cloud_center_service = CloudCenterService(
            self.request_manager,
            account_service=self.cloud_account_service,
            status_service=self.cloud_status_service,
            metadata_service=self.cloud_metadata_service,
            operation_service=self.cloud_operation_service,
            settings=self.settings,
        )
        self._cloud_center_overview_binding: ResourceBinding | None = None
        self.cloud_save_status_cache = self.cloud_status_service.status_cache
        self._public_profile_generation = 0
        self._public_profile_binding: ResourceBinding | None = None
        self._profile_sync_generation = 0
        self._profile_sync_done.connect(self._on_managed_profile_sync_done)
        self._managed_steam_update_batch_done.connect(self._on_managed_steam_update_batch_done)
        self.game_sessions = GameSessionManager(self)
        self.game_sessions.session_state_changed.connect(self._on_game_session_state_changed)
        self.launch_session_coordinator = LaunchSessionCoordinator(
            self.runner,
            self.library_service,
            self.game_sessions,
            tracker_factory=lambda game_id, process, session_id: PlaytimeTrackerThread(
                game_id, process, session_id=session_id, parent=self
            ),
        )
        self._stopping_game_ids = set()  # game IDs transitioning from running to stopped
        self.worker_supervisor = WorkerSupervisor(self)
        # running_game_ids is derived from the session supervisor, not from
        # UI widgets or the playtime tracker feature list.
        self.topbar_extractor_thread = None
        self._size_fetch_scheduled = set()  # game dirs queued for background sizing
        self._size_resort_timer = QTimer(self)
        self._size_resort_timer.setSingleShot(True)
        self._size_resort_timer.setInterval(400)
        self._size_resort_timer.timeout.connect(self._refresh_library)
        # Compatibility timer for older Steam snapshot callers. Current cloud
        # and presentation updates are applied incrementally so an active
        # list/grid is never torn down underneath the pointer.
        self._update_status_refresh_timer = QTimer(self)
        self._update_status_refresh_timer.setSingleShot(True)
        self._update_status_refresh_timer.setInterval(80)
        self._update_status_refresh_timer.timeout.connect(self._refresh_library)
        self.games_by_id = {}
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
        self._managed_task_done.connect(self._on_managed_task_done)
        self._network_probe_done.connect(self._on_network_probe_done)
        self._managed_cloud_batch_done.connect(self._on_managed_cloud_batch_done)
        self._cloud_auto_restore_done.connect(self._on_cloud_auto_restore_done)
        self._cloud_auto_upload_done.connect(self._on_cloud_auto_upload_done)
        self._managed_achievement_batch_done.connect(self._on_managed_achievement_batch_done)
        self._cloud_poll_changed.connect(self._on_cloud_poll_changed)
        self._managed_task_callbacks = {}


        # Background maintenance: prune orphaned temp files
        try:
            from core.prefix_sanitizer import cleanup_global_temp_files
            self._start_managed_task(
                "SafeLauncher-TempPrune",
                cleanup_global_temp_files,
                lambda result: logger.debug("Temporary-file cleanup finished: %s", result),
                allow_offline=True,
            )
        except Exception:
            pass

        self.search_query = ""
        # Central public-profile identity is deliberately separate from the
        # per-user private SafeLauncherCloud credential.
        self.central_auth = CentralAuthSession()
        self.date_format = self.settings.value("date_format", get_date_format_key(), type=str)
        # CI/UI smoke tests must not depend on DNS or third-party response
        # timing.  The shared policy also supports the user-facing offline
        # setting, which is enforced by automatic and optional network paths.
        self._offline_test_mode = os.environ.get("SAFELAUNCHER_OFFLINE_TEST_MODE") == "1"
        self._offline_mode = is_offline_mode(self.settings)
        self._network_reachability_known = False
        self._network_reachable = False
        self._network_probe_in_flight = False
        self._network_loss_pending = False
        self._network_loss_dialog = None
        self._network_probe_timer = QTimer(self)
        self._network_probe_timer.setInterval(5_000)
        self._network_probe_timer.timeout.connect(self._probe_network_now)
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
        if self.library_view_mode in ("steam", "", "list"):
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
            replay_hotkey=self.settings.value("gpu_recorder_replay_hotkey", self.settings.value("wl_screenrec_replay_hotkey", "F10", type=str), type=str),
            in_game_overlay=self.settings.value("gpu_recorder_in_game_overlay", self.settings.value("wl_screenrec_in_game_overlay", True, type=bool), type=bool),
        )
        self.wl_recorder_config = self.gpu_recorder_config
        GpuRecorderService.instance().apply_config(self.gpu_recorder_config)

        # Headless smoke tests exercise widget wiring, not OS-wide input.
        # Starting an X11 listener there can leave native display work racing
        # Qt teardown. Normal application runs retain the owned listener.
        self.global_hotkeys = GlobalHotkeyListener(self)
        self._update_global_hotkeys()
        self.global_hotkeys.hotkey_triggered.connect(self._on_global_hotkey)
        # OS-wide input hooks are not meaningful for Qt's headless/minimal
        # platforms.  More importantly, starting Xlib from an offscreen test
        # can race with Qt teardown and leave a native listener alive while
        # the QApplication is processing deferred deletes.
        qt_platform = QGuiApplication.platformName().lower()
        if not self._offline_test_mode and qt_platform not in ("offscreen", "minimal"):
            self.global_hotkeys.start()

        self.setWindowTitle("SafeLauncher - Game Sandbox Manager")
        self._resize_to_available_screen(1180, 750, minimum=(760, 520), margin=32)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        
        if os.path.exists(LOGO_PATH):
            self.setWindowIcon(QIcon(LOGO_PATH))

        # Root Layout: Hero Background Canvas + Top Title Bar + Body (Left Sidebar + Center Grid & Right Inspector Splitter)
        self.hero_bg = HeroBackgroundWidget(self)
        self.setCentralWidget(self.hero_bg)
        self.extraction_spinner = ExtractionSpinner(self)
        recovered_updates = recover_incomplete_game_updates(ensure_sandbox_dir())
        if recovered_updates:
            QTimer.singleShot(
                0,
                lambda: self._show_toast(
                    f"Recovered {len(recovered_updates)} interrupted game update(s).",
                    is_error=True,
                ),
            )
        
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
        self.title_bar.profile_requested.connect(self._open_achievement_profile)
        self.title_bar.cloud_center_requested.connect(self._open_cloud_center)
        self.title_bar.public_profile_requested.connect(self._open_public_profile_prompt)
        self.title_bar.friends_requested.connect(self._open_friends_popup)
        self.title_bar.settings_requested.connect(self._open_settings)
        self.title_bar.sync_requested.connect(self._on_sync_sandbox)
        self.title_bar.install_archive_requested.connect(self._on_install_zip_archive)
        self.title_bar.update_game_files_requested.connect(self._on_update_game_files_from_archive)
        self.title_bar.check_updates_requested.connect(self._check_all_steam_updates)
        self.title_bar.open_sandbox_requested.connect(self._open_sandbox_dir)
        self.title_bar.export_save_requested.connect(self._on_export)
        self.title_bar.import_save_requested.connect(self._on_import)
        self.title_bar.disk_manager_requested.connect(self._open_disk_manager)
        self._update_header_identity()

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
        # The body is the only flexible section between the title/banner and
        # footer.  Giving it the stretch explicitly keeps both columns inside
        # that exact vertical boundary.
        body_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root_vbox.addWidget(body_widget, 1)

        # 1. Left Collections Sidebar (On by default, collapsed)
        self.sidebar = LeftSidebarWidget(self)
        self.sidebar.setVisible(True)
        default_compact = self.settings.value("collections_collapsed", True, type=bool)
        self.sidebar.set_compact(default_compact)
        # The sidebar is a full-height column.  Do not use AlignTop here: it
        # would shrink the frame to its contents and leave an empty strip
        # above the footer.  Its internal collections scroll area owns the
        # remaining height inside this frame.
        self.sidebar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        body_layout.addWidget(self.sidebar, 0)
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
                background: #2A2D34;
                width: 8px;
                margin: 0px;
            }
            QSplitter::handle:hover {
                background: #3B9FE8;
            }
        """)
        # Keep a real hit target for manually resizing the inspector.
        self.splitter.setHandleWidth(8)
        body_layout.addWidget(self.splitter, 1)

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
        cloud_box_layout = QVBoxLayout(cloud_box)
        cloud_box_layout.setContentsMargins(0, 0, 0, 0)
        cloud_box_layout.setSpacing(2)

        cloud_status_row = QHBoxLayout()
        cloud_status_row.setContentsMargins(0, 0, 0, 0)
        cloud_status_row.setSpacing(6)

        self.detail_cloud_status = QLabel("--")
        self.detail_cloud_status.setStyleSheet("color: #A1A1A6; font-size: 11px; font-weight: 500; background: transparent;")
        cloud_status_row.addWidget(self.detail_cloud_status)

        self.btn_detail_cloud_restore = QPushButton("Restore latest cloud save")
        self.btn_detail_cloud_restore.setAccessibleName("Restore latest cloud save")
        self.btn_detail_cloud_restore.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_detail_cloud_restore.setToolTip("Restore latest cloud save for this game")
        self.btn_detail_cloud_restore.setStyleSheet(
            "QPushButton { background: #2563EB; color: #FFFFFF; border: none; border-radius: 4px; "
            "padding: 2px 8px; font-size: 10px; font-weight: bold; } "
            "QPushButton:hover { background: #3B82F6; }"
        )
        self.btn_detail_cloud_restore.hide()
        self.btn_detail_cloud_restore.clicked.connect(self._restore_selected_game_cloud_save)
        cloud_status_row.addWidget(self.btn_detail_cloud_restore)

        cloud_status_row.addStretch()
        cloud_box_layout.addLayout(cloud_status_row)

        self.detail_cloud_metadata = QLabel("")
        self.detail_cloud_metadata.setStyleSheet(
            "color: #6F7682; font-size: 9px; font-weight: 500; background: transparent;"
        )
        self.detail_cloud_metadata.setAccessibleName("Cloud save time and device")
        self.detail_cloud_metadata.setVisible(False)
        cloud_box_layout.addWidget(self.detail_cloud_metadata)

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

        self.lbl_update_dates = QLabel("")
        self.lbl_update_dates.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_update_dates.setStyleSheet(
            "QLabel { color: #F4F4F5; background: transparent; "
            "font-size: 10px; padding: 0 4px; }"
        )
        self.lbl_update_dates.setVisible(False)
        self.detail_update_layout.addWidget(self.lbl_update_dates)

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
        self.btn_detail_achievements.setAccessibleName("Open achievements")
        self.btn_detail_achievements.setToolTip("Open the full achievement list for this game")
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
        self.detail_ach_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
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

        # Game lifecycle button: opens the shared uninstall/delete chooser.
        self.btn_detail_remove = QPushButton("Uninstall / Delete")
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

        # Add center game grid (inspector panel removed, central grid takes full width)
        self.splitter.addWidget(self.right_panel)
        self.detail_panel.setParent(self)
        self.detail_panel.setVisible(False)

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
        self.grid_search_input.setFixedWidth(210)
        self.grid_search_input.setFixedHeight(32)
        self.grid_search_input.setClearButtonEnabled(True)
        self.grid_search_input.addAction(get_icon("ph.magnifying-glass-bold", color="#8E8E93"), QLineEdit.ActionPosition.LeadingPosition)
        self.grid_search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {SURFACE};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 8px;
                padding: 0 10px 0 28px;
                font-size: 11px;
            }}
            QLineEdit:focus {{
                border-color: #0A84FF;
                background-color: {SURFACE_ELEVATED};
            }}
            QLineEdit::placeholder {{
                color: {TEXT_MUTED};
            }}
        """)
        self.grid_search_input.textChanged.connect(self._on_search_query_changed)
        header_layout.addWidget(self.grid_search_input)

        # List/grid views keep the high-frequency library filters beside the
        # search field. Compact has its own sidebar filter bar.
        self.library_filter_buttons = {}
        filter_specs = (
            ("all", "All", "ph.squares-four-bold"),
            ("installed", "Installed", "ph.check-circle-bold"),
            ("favorites", "Favorites", "ph.heart-bold"),
            ("archived", "Not installed", "ph.archive-bold"),
        )
        filter_button_style = f"""
            QPushButton {{
                background: {SURFACE};
                color: {TEXT_SECONDARY};
                border: 1px solid {BORDER};
                border-radius: 8px;
                padding: 0 10px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {SURFACE_ELEVATED};
                color: {TEXT_PRIMARY};
                border-color: rgba(255, 255, 255, 0.16);
            }}
            QPushButton:checked {{
                background: rgba(10, 132, 255, 0.18);
                color: #38BDF8;
                border-color: rgba(10, 132, 255, 0.45);
            }}
        """
        for filter_mode, label, icon_name in filter_specs:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setIcon(get_icon(icon_name, color=TEXT_SECONDARY))
            button.setIconSize(QSize(14, 14))
            button.setFixedHeight(32)
            button.setMinimumWidth(58 if filter_mode == "all" else 78)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(f"Show {label.lower()} games")
            button.setStyleSheet(filter_button_style)
            button.clicked.connect(
                lambda _checked=False, mode=filter_mode: self._set_filter(mode)
            )
            self.library_filter_buttons[filter_mode] = button
            header_layout.addWidget(button)
        self._update_library_filter_buttons()

        # Sorting ComboBox
        self.sort_combo = SortComboBox()
        self.sort_combo.addItems(["Sort: A–Z Title", "Sort: Most Played", "Sort: Recently Added", "Sort: Disk Size", "Sort: Runner"])
        self.sort_combo.setFixedHeight(32)
        self.sort_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {SURFACE};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 8px;
                padding: 0 12px;
                font-size: 11px;
                font-weight: 500;
            }}
            QComboBox:hover {{
                border-color: rgba(255, 255, 255, 0.18);
                background-color: {SURFACE_ELEVATED};
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 0px;
                border: none;
            }}
            QComboBox::down-arrow {{
                image: none;
                width: 0px;
                height: 0px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {SURFACE_ELEVATED};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                selection-background-color: {BORDER};
                border-radius: 8px;
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
        self.virtual_grid = self.library_view_host.virtual_grid
        self.compact_container = self.library_view_host.compact_container
        self.steam_container = self.compact_container
        self.detail_page = self.library_view_host.detail_page

        self.library_view_host.game_selected.connect(self._select_game_by_id)
        self.library_view_host.game_double_clicked.connect(self._on_double_click_game)
        self.library_view_host.game_launch_requested.connect(self._launch_game_by_id)
        self.library_view_host.favorite_requested.connect(self._on_card_favorite_clicked)
        self.library_view_host.edit_requested.connect(self._on_edit)
        self.library_view_host.properties_requested.connect(self._open_game_properties)
        self.library_view_host.save_manager_requested.connect(self._on_export)
        self.library_view_host.cloud_menu_requested.connect(self._show_game_cloud_menu)
        self.library_view_host.cloud_action_requested.connect(self._on_game_cloud_action)
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
        self.library_view_host.back_requested.connect(self._close_game_detail)
        self.library_view_host.remove_requested.connect(self._on_remove_by_id)
        if self.library_view_mode in ("compact", "steam"):
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

        # Profile is a first-class page in the same central surface. Keeping
        # it persistent avoids modal lifetime races and makes public profiles
        # navigable without disturbing the selected game.
        self.profile_page = ProfilePageWidget(
            self.db, self.settings, self.right_panel,
            worker_registry=self.worker_supervisor,
            auth_session=self.central_auth,
            request_manager=self.request_manager,
            resource_cache=self.resource_cache,
        )
        self.profile_page.hide()
        self.profile_page.back_requested.connect(self._close_profile_page)
        self.profile_page.open_profile_handle_requested.connect(self._open_public_profile_handle)
        self.profile_page.profile_changed.connect(self._on_profile_changed)
        self.profile_page.private_profile_changed.connect(self._on_private_profile_changed)
        self.profile_page.avatar_pixmap_changed.connect(self.title_bar.set_profile_avatar)
        self.title_bar.set_profile_avatar(self.profile_page.current_avatar_pixmap())
        right_layout.addWidget(self.profile_page, 1)
        self._profile_view_active = False
        self._profile_remote_tasks = TaskSupervisor(self, worker_registry=self.worker_supervisor)

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

        view_labels = {"compact": "Compact", "grid": "Grid", "steam": "Compact"}
        self.btn_view_toggle = QPushButton(f"View: {view_labels.get(self.library_view_mode, 'Grid')}")
        self.btn_view_toggle.setObjectName("viewToggleButton")
        self.btn_view_toggle.setToolTip("Change library view (currently " + view_labels.get(self.library_view_mode, "Grid") + ")")
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

        self.lbl_network_status = QLabel("Offline")
        self.lbl_network_status.setObjectName("networkStatusLabel")
        self.lbl_network_status.setStyleSheet(
            "QLabel#networkStatusLabel { color: #777C86; background: transparent; "
            "font-size: 10px; font-weight: 500; padding: 0 2px; }"
        )
        self.lbl_network_status.setToolTip(
            "Remote metadata checks are paused or unavailable. Cached and local data remain available."
        )
        self.lbl_network_status.setAccessibleName("Network status: offline")
        self.lbl_network_status.setVisible(bool(getattr(self, "_offline_mode", False)))
        footer_layout.addWidget(self.lbl_network_status)

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
        self.btn_reveal_detail.setVisible(False)

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
        if self._automatic_network_allowed():
            # Reconcile the private catalog before background status scans so
            # cloud-only/not-installed records are materialized in this UI.
            QTimer.singleShot(500, self._sync_profile_metadata_async)
            QTimer.singleShot(300, self._check_all_steam_updates)
            QTimer.singleShot(800, self._start_background_cloud_sync)
            QTimer.singleShot(1200, self._start_background_achievement_sync)
        if self._automatic_network_allowed() and os.environ.get("SAFELAUNCHER_DISABLE_UPDATE_CHECK") != "1":
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
            self._automatic_network_allowed()
            and os.environ.get("SAFELAUNCHER_DISABLE_UPDATE_CHECK") != "1"
        ):
            # Let the main window and welcome wizard finish opening before the
            # optional network probe can present a notification.
            QTimer.singleShot(3200, self._check_backend_update_on_startup)
        self._start_cloud_poll_timer()
        self._start_network_monitor()

        show_wizard = self.settings.value("show_welcome_wizard", True, type=bool)
        # The deterministic/offline harness must never open a modal wizard as
        # a side effect of processing pending events.  Normal launches retain
        # the first-run experience; tests and headless callers can explicitly
        # open it through the normal action when needed.
        if show_wizard and not self._offline_test_mode and self._automatic_network_allowed():
            QTimer.singleShot(150, self._show_welcome_wizard)

    def _resize_to_available_screen(
        self,
        preferred_width: int,
        preferred_height: int,
        *,
        minimum: tuple[int, int],
        margin: int = 32,
    ) -> None:
        """Choose a usable initial size without exceeding a small display."""
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(preferred_width, preferred_height)
            return
        available = screen.availableGeometry()
        available_width = max(1, available.width() - margin)
        available_height = max(1, available.height() - margin)
        width = min(
            available_width,
            max(minimum[0], min(preferred_width, available_width)),
        )
        height = min(
            available_height,
            max(minimum[1], min(preferred_height, available_height)),
        )
        self.resize(width, height)

    def _automatic_network_allowed(self) -> bool:
        """Return the current background-network policy.

        Read the setting each time instead of caching it permanently: Settings
        can enable offline mode while the window is already open.
        """
        allowed = automatic_network_allowed(getattr(self, "settings", None))
        self._offline_mode = is_offline_mode(getattr(self, "settings", None))
        return allowed

    def _start_network_monitor(self) -> None:
        """Probe connectivity periodically while automatic networking is allowed."""
        if self._offline_test_mode or not self._automatic_network_allowed():
            self._network_probe_timer.stop()
            return
        self._network_probe_timer.start()
        # Give the first library render a chance to settle before spending a
        # request slot on the connectivity check.
        QTimer.singleShot(1500, self._probe_network_now)

    def _probe_network_now(self) -> None:
        """Run the connectivity check through the shared request workers."""
        if (
            self._offline_test_mode
            or not self._automatic_network_allowed()
            or self._network_probe_in_flight
        ):
            return
        self._network_probe_in_flight = True
        handle = self.request_manager.request(
            RequestKey("internet-connectivity", "public", "v1"),
            lambda token: (
                token.raise_if_cancelled(),
                probe_internet(timeout=3.0),
                token.raise_if_cancelled(),
            )[1],
            priority=RequestPriority.BACKGROUND,
            metadata={"allow_offline": True, "connectivity_probe": True},
            timeout_seconds=5,
        )
        handle.future.add_done_callback(
            lambda future: self._network_probe_done.emit(future)
        )

    def _on_network_probe_done(self, future) -> None:
        """Update the footer and surface only an online→offline transition."""
        self._network_probe_in_flight = False
        if not self._automatic_network_allowed():
            self._network_probe_timer.stop()
            return
        reachable = False
        reason = "The internet connection could not be reached."
        try:
            result = future.result()
            if result.status == ResourceStatus.READY and isinstance(result.value, tuple):
                reachable = bool(result.value[0])
                reason = str(result.value[1] or reason)
        except Exception as error:
            logger.debug("Connectivity probe failed: %s", error)

        previous = self._network_reachable
        had_previous = self._network_reachability_known
        self._network_reachability_known = True
        self._network_reachable = reachable
        if reachable:
            was_transiently_unavailable = self._transient_network_unavailable
            self._transient_network_unavailable = False
            self._network_loss_pending = False
            self._set_network_status(False)
            dialog = self._network_loss_dialog
            if dialog is not None:
                try:
                    dialog.close()
                except RuntimeError:
                    pass
                self._network_loss_dialog = None
            if had_previous and not previous:
                self._show_toast("Internet connection restored.")
                if was_transiently_unavailable:
                    self._refresh_library()
                    self._start_cloud_poll_timer()
                    self.request_cloud_recheck(None, "network-restored", auto_sync=True)
                    QTimer.singleShot(300, self._check_all_steam_updates)
            return

        self._transient_network_unavailable = True
        self.request_manager.cancel_matching(
            lambda spec: not bool(spec.metadata.get("allow_offline", False))
        )
        self._set_network_status(True, reason)
        dialog = self._network_loss_dialog
        if dialog is not None:
            dialog.set_retrying(False)
        if had_previous and previous:
            self._network_loss_pending = True
            self._show_network_loss_dialog()

    def _show_network_loss_dialog(self) -> None:
        """Show one actionable prompt once the window is focused."""
        if not self._network_loss_pending or not self.isActiveWindow():
            return
        dialog = self._network_loss_dialog
        if dialog is not None:
            try:
                if dialog.isVisible():
                    dialog.raise_()
                    dialog.activateWindow()
                    return
            except RuntimeError:
                self._network_loss_dialog = None
        dialog = NetworkUnavailableDialog(self)
        self._network_loss_dialog = dialog
        dialog.retry_requested.connect(self._retry_network_connection)
        dialog.offline_requested.connect(self._switch_to_offline_from_network_loss)
        dialog.finished.connect(lambda _result: self._clear_network_loss_dialog(dialog))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _clear_network_loss_dialog(self, dialog) -> None:
        if self._network_loss_dialog is dialog:
            self._network_loss_dialog = None

    def _retry_network_connection(self) -> None:
        dialog = self._network_loss_dialog
        if dialog is not None:
            dialog.set_retrying(True)
        self._network_loss_pending = True
        QTimer.singleShot(0, self._probe_network_now)

    def _switch_to_offline_from_network_loss(self) -> None:
        was_offline = is_offline_mode(self.settings)
        set_offline_mode(True, self.settings)
        self._network_loss_pending = False
        dialog = self._network_loss_dialog
        if dialog is not None:
            dialog.close()
        self._apply_network_policy_change(was_offline)

    def _maybe_show_pending_network_loss(self) -> None:
        if self.isActiveWindow():
            self._show_network_loss_dialog()

    def _set_network_status(self, offline: bool, reason: str = "") -> None:
        """Keep explicit Offline Mode separate from transient connectivity loss.

        The footer is a policy indicator, not a live internet monitor.  A
        failed probe is handled by the actionable connection-loss dialog; it
        must not make the persistent ``Offline`` footer appear while the user
        is still in Online Mode.
        """
        self._offline_mode = is_offline_mode(getattr(self, "settings", None))
        explicit_offline = self._offline_mode or bool(
            getattr(self, "_offline_test_mode", False)
        )
        self._network_offline_detected = bool(offline) or explicit_offline
        label = getattr(self, "lbl_network_status", None)
        if label is None:
            return
        label.setVisible(explicit_offline)
        if explicit_offline:
            label.setText("Offline")
            label.setToolTip(
                "Offline mode is enabled; remote metadata checks are paused. "
                "Cached and local data remain available."
            )
            label.setAccessibleName("Network status: offline mode")
        else:
            label.setText("")
            label.setToolTip("Online: remote metadata checks are allowed.")
            label.setAccessibleName("Network status: online")

    def _apply_network_policy_change(self, was_offline: bool) -> None:
        """Stop optional network work immediately after Settings changes."""
        now_offline = is_offline_mode(self.settings)
        self._offline_mode = now_offline
        self._transient_network_unavailable = False
        if now_offline or not self._automatic_network_allowed():
            network_timer = getattr(self, "_network_probe_timer", None)
            if network_timer is not None:
                network_timer.stop()
            self._network_probe_in_flight = False
            self._set_network_status(
                True,
                "Offline mode is enabled; remote metadata checks are paused. Cached and local data remain available.",
            )
            self.cloud_status_polling.stop()
            for timer_name in (
                "_achievement_poll_timer", "_update_check_timer"
            ):
                timer = getattr(self, timer_name, None)
                if timer is not None:
                    try:
                        timer.stop()
                    except RuntimeError:
                        pass
            self._pending_auto_fetchers.clear()
            for fetcher in list(self.auto_fetchers):
                try:
                    if fetcher.isRunning():
                        fetcher.requestInterruption()
                except RuntimeError:
                    pass
            self._cancel_metadata_fetchers()
            self._cancel_optional_network_tasks()
            self.cloud_center_service.cancel_pending_reads()
            profile_page = getattr(self, "profile_page", None)
            if profile_page is not None:
                try:
                    profile_page._publish_timer.stop()
                except (AttributeError, RuntimeError):
                    pass
            self._mark_cloud_offline()
            if now_offline and not was_offline:
                self._show_toast("Offline mode enabled — using cached and local data only.")
            return

        if was_offline:
            start_monitor = getattr(self, "_start_network_monitor", None)
            if callable(start_monitor):
                start_monitor()
            self._set_network_status(False)
            self._show_toast("Online mode enabled — refreshing optional metadata.")
            self._refresh_library()
            self._start_cloud_poll_timer()
            # Do not let an unavailable account snapshot survive a policy
            # transition. The next Cloud Center open must perform a real read.
            cloud_center_service = getattr(self, "cloud_center_service", None)
            if cloud_center_service is not None:
                cloud_center_service.invalidate_account_reads()
            self._refresh_cloud_center_indicator()
            # Offline verdicts are deliberately persisted so the library can
            # render a useful state without networking.  Re-entering online
            # mode must immediately re-check those verdicts instead of
            # waiting for the next poll interval or treating them as fresh.
            self.request_cloud_recheck(None, "online-mode", auto_sync=True)
            # The normal startup path performs this check after the window is
            # visible.  Do the same on an online transition so cached offline
            # game-version badges and the selected-game detail are refreshed.
            QTimer.singleShot(300, self._check_all_steam_updates)
            QTimer.singleShot(100, self._start_background_achievement_sync)
            QTimer.singleShot(200, self._sync_profile_metadata_async)
            self._startup_backend_health = None
            self._startup_update_notice_shown = False
            if self.settings.value("convex_site_url", "", type=str).strip():
                QTimer.singleShot(1200, self._check_backend_update_on_startup)
            if os.environ.get("SAFELAUNCHER_DISABLE_UPDATE_CHECK") != "1":
                timer = getattr(self, "_update_check_timer", None)
                if timer is None:
                    timer = QTimer(self)
                    timer.setSingleShot(True)
                    timer.timeout.connect(self._check_app_updates)
                    self._update_check_timer = timer
                timer.start(1000)

    def _cancel_optional_network_tasks(self) -> None:
        """Request cancellation for one-shot network tasks already in flight."""
        self._close_managed_cloud_status_bindings()
        self._close_managed_achievement_bindings()
        self._close_managed_artwork_bindings()
        self._close_managed_steam_metadata_bindings()
        markers = (
            "account", "backend", "cloud", "metadata", "profile", "telemetry",
            "update", "ludusavi", "publicprofile", "public_profile",
        )
        for worker in self.worker_supervisor.workers(running_only=True):
            name = self.worker_supervisor.describe(worker).lower()
            if not any(marker in name for marker in markers):
                continue
            try:
                if hasattr(worker, "request_cancel"):
                    worker.request_cancel()
                else:
                    worker.requestInterruption()
            except RuntimeError:
                pass

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
            self.btn_detail_remove: "Uninstall or permanently delete the selected game",
            self.btn_detail_cloud_restore: "Restore latest cloud save for selected game",
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
        if (
            not self._automatic_network_allowed()
            or os.environ.get("SAFELAUNCHER_DISABLE_UPDATE_CHECK") == "1"
        ):
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
        if not self._automatic_network_allowed():
            self._startup_backend_health = {}
            self._maybe_show_startup_update_notice()
            return
        if self._startup_backend_health is not None:
            return
        try:
            from core.cloud_backend import get_site_url

            site_url = get_site_url()
            if not site_url:
                self._startup_backend_health = {}
                self._maybe_show_startup_update_notice()
                return

            from core.secret_store import get_secret
            secret_key = get_secret("cloud_secret_key", legacy_name="cloud_secret_key")

            def _probe():
                try:
                    return self.cloud_account_service.health(
                        site_url, secret_key, timeout=5.0
                    )
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
        self._register_worker(self._dl_worker)
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

    def _update_header_identity(self) -> None:
        """Keep the local username visible without exposing auth credentials."""
        if not hasattr(self, "title_bar"):
            return
        profile = load_profile_settings(self.settings, fallback_name=self.user_name)
        self.title_bar.set_profile_identity(
            str(profile.get("display_name", "") or self.user_name),
            str(profile.get("public_handle", "") or ""),
        )

    def _open_cloud_center(self) -> None:
        """Open the single private-cloud overview and action surface."""
        dialog = CloudCenterDialog(
            self,
            cloud_center_service=self.cloud_center_service,
            db_path=getattr(self.db, "db_path", None),
            last_sync_at=self.settings.value("cloud_center_last_sync_at", 0.0, type=float),
        )
        dialog.sync_finished.connect(self._on_cloud_center_sync_finished)
        dialog.overview_changed.connect(self._on_cloud_center_overview_changed)
        dialog.connection_restored.connect(self._on_cloud_connection_restored)
        dialog.setup_requested.connect(self._open_cloud_setup_from_center)
        dialog.settings_requested.connect(self._open_cloud_settings_from_center)
        dialog.history_requested.connect(self._open_cloud_history_from_center)
        dialog.conflicts_requested.connect(self._open_cloud_history_from_center)
        dialog.exec()

    def _on_cloud_center_overview_changed(self, overview) -> None:
        """Keep the compact header cloud indicator in sync with the center."""
        if hasattr(self, "title_bar"):
            self.title_bar.set_cloud_status_indicator(
                getattr(overview, "connection", "unavailable")
            )

    def _refresh_cloud_center_indicator(self) -> None:
        """Refresh the compact cloud indicator without opening Cloud Center."""
        service = getattr(self, "cloud_center_service", None)
        manager = getattr(service, "request_manager", None)
        request_overview = getattr(service, "request_overview", None)
        if service is None or manager is None or not callable(request_overview):
            return
        binding = getattr(self, "_cloud_center_overview_binding", None)
        if binding is not None:
            binding.close()
            binding.deleteLater()
            self._cloud_center_overview_binding = None
        try:
            handle = request_overview(
                force=True,
                priority=RequestPriority.BACKGROUND,
            )
        except Exception as error:
            logger.debug("Cloud header overview refresh could not start: %s", error)
            self._set_cloud_indicator_for_overview_failure(None)
            return
        self._cloud_center_overview_binding = bind_resource(
            manager,
            handle.key,
            self._on_cloud_header_overview_state,
            self,
            cancel_on_close=True,
        )

    def _on_cloud_header_overview_state(self, result) -> None:
        """Apply the latest managed overview to the closed-shell indicator."""
        if result.status in {ResourceStatus.IDLE, ResourceStatus.LOADING}:
            return
        if result.status in {ResourceStatus.READY, ResourceStatus.STALE}:
            self._on_cloud_center_overview_changed(
                CloudOverview.from_payload(result.value)
            )
            return
        if result.status == ResourceStatus.CANCELLED:
            return
        self._set_cloud_indicator_for_overview_failure(result)

    def _set_cloud_indicator_for_overview_failure(self, result) -> None:
        """Keep the compact indicator honest when the overview fails."""
        if not hasattr(self, "title_bar"):
            return
        service = getattr(self, "cloud_center_service", None)
        try:
            context = service.current_context() if service is not None else None
        except Exception:
            context = None
        if context is not None and not context.network_allowed:
            connection = "offline"
        elif result is not None and result.status == ResourceStatus.AUTHENTICATION_REQUIRED:
            connection = "setup_required"
        elif context is not None and context.backend_active and not context.authentication_configured:
            connection = "setup_required"
        else:
            connection = "unavailable"
        self.title_bar.set_cloud_status_indicator(connection)

    def _on_cloud_connection_restored(self) -> None:
        """Refresh every library save projection after a healthy probe."""
        if not self._automatic_network_allowed():
            return
        self.request_cloud_recheck(None, "cloud-probe-success", auto_sync=True)

    def _on_cloud_center_sync_finished(self, result) -> None:
        """Refresh library/cloud badges after the central sync action."""
        if getattr(result, "success", False):
            self.settings.setValue("cloud_center_last_sync_at", time.time())
            self.request_cloud_recheck(None, "cloud-center-sync", auto_sync=True)
            self._refresh_library()

    def _open_cloud_setup_from_center(self) -> None:
        """Run setup without exposing credentials to Cloud Center."""
        try:
            from ui.dialogs.cloud_wizard_dialog import CloudWizardDialog
            wizard = CloudWizardDialog(self)
            if wizard.exec() == QDialog.DialogCode.Accepted:
                self._refresh_cloud_after_config_change()
        except Exception as error:
            self._show_toast(f"Could not open cloud setup: {error}", is_error=True)

    def _open_cloud_settings_from_center(self) -> None:
        """Open the existing settings dialog directly on its Cloud page."""
        self._open_settings(initial_tab=3)

    def _open_cloud_history_from_center(self) -> None:
        """Open advanced account-wide storage and device management."""
        try:
            from ui.dialogs.account_dialog import AccountDialog
            dialog = AccountDialog(
                self,
                request_manager=self.request_manager,
                cloud_account_service=self.cloud_account_service,
                cloud_operation_service=self.cloud_operation_service,
            )
            dialog.exec()
            self.request_cloud_recheck(None, "cloud-history", auto_sync=True)
        except Exception as error:
            self._show_toast(f"Could not open cloud storage management: {error}", is_error=True)

    def _open_settings(self, initial_tab: int | None = None):
        """Open Settings once at a time and keep menu/dialog lifecycles separate."""
        if self._settings_dialog_active:
            return
        self._settings_dialog_active = True
        try:
            self._open_settings_dialog(initial_tab=initial_tab)
        finally:
            self._settings_dialog_active = False

    def _open_settings_dialog(self, initial_tab: int | None = None):
        """Open launcher preferences and persist profile changes."""
        show_wizard = self.settings.value("show_welcome_wizard", True, type=bool)
        offline_before = is_offline_mode(self.settings)
        cloud_dir = self.settings.value("cloud_saves_dir", "", type=str)
        dialog = UserSettingsDialog(
            self.user_name,
            self.proton_path,
            show_welcome_wizard=show_wizard,
            gpu_config=self.gpu_recorder_config,
            screenshot_screen=self.screenshot_screen,
            screenshot_hotkey=self.screenshot_hotkey,
            cloud_saves_dir=cloud_dir,
            parent=self,
            date_format=self.date_format,
            request_manager=self.request_manager,
            initial_tab=initial_tab,
        )
        # PopupDialog uses WA_DeleteOnClose, but this handler reads the form
        # values after exec() returns. Keep the dialog alive until those reads
        # and schedule its deletion explicitly below.
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        dialog.runtime_manager_requested.connect(self._open_runtime_manager)
        dialog.proton_manager_requested.connect(self._open_proton_manager)
        dialog.profile_auth_requested.connect(self.profile_page.toggle_profile_auth)
        dialog.profile_publish_requested.connect(self.profile_page.publish_profile)
        dialog.profile_resync_requested.connect(self.profile_page.resync_profile)
        dialog.conflicts_requested.connect(self._open_cloud_history_from_center)
        self.profile_page.profile_action_state_changed.connect(dialog.set_profile_action_state)
        self.profile_page.profile_action_status_changed.connect(dialog.set_profile_action_status)
        dialog.set_profile_action_state(*self.profile_page.profile_action_state())
        cloud_before = (
            self.settings.value("cloud_mode", "local", type=str),
            self.settings.value("convex_site_url", "", type=str),
            get_secret("cloud_secret_key", legacy_name="cloud_secret_key"),
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
            self.date_format = dialog.get_date_format()
            self.settings.setValue("date_format", self.date_format)
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
            if self.selected_game:
                self._update_detail_panel()

        # A successful Settings save, or an explicitly accepted cloud setup
        # action, may change the backend context. Cancelled form edits are
        # rolled back by UserSettingsDialog before this comparison runs.
        self._maybe_refresh_cloud_config(cloud_before)
        self._apply_network_policy_change(offline_before)
        self._update_header_identity()
        dialog.deleteLater()

    def _maybe_refresh_cloud_config(self, before: tuple):
        current = (
            self.settings.value("cloud_mode", "local", type=str),
            self.settings.value("convex_site_url", "", type=str),
            get_secret("cloud_secret_key", legacy_name="cloud_secret_key"),
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
        self.cloud_account_service.reset_backend()
        self._cloud_context_generation = self.cloud_status_service.invalidate_context().generation
        self.cloud_operation_service.invalidate_context(generation=self._cloud_context_generation)
        self.cloud_account_service.invalidate_context()
        self.cloud_metadata_service.invalidate_context()
        # Cloud Center owns derived overview/device/history projection keys;
        # retire them after the shared context services move to the new
        # identity so no old projection remains rendered.
        self.cloud_center_service.invalidate_account_reads()
        self._close_managed_cloud_status_bindings()
        for widget in self.banner_widgets.values():
            widget.set_cloud_status(None)
        if hasattr(self, "library_view_host"):
            for game_id in self.games_by_id:
                self.library_view_host.update_cloud_status(game_id, None)
        if self.selected_game:
            self.detail_cloud_status.setText("<font color='#6F7682'>Cloud Save: checking…</font>")
            self.detail_cloud_status.setToolTip("Cloud settings changed — re-checking.")
            if hasattr(self, "detail_cloud_metadata"):
                self.detail_cloud_metadata.clear()
                self.detail_cloud_metadata.setVisible(False)
        self._save_persistent_cache()
        self.request_cloud_recheck(None, "config-change", auto_sync=True)

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
        self.library_service.update_runtime_settings(game_id, proton_path=proton_path)
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
        """Install a supported game archive directly from the top bar."""
        zip_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Game Archive",
            "",
            "Archive Files (*.zip *.7z *.rar *.tar.gz *.tgz)"
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
        thread.finished.connect(self._hide_extraction_spinner)
        self._show_extraction_spinner()
        self._show_toast(f"Extracting '{archive_name}' in background...")
        self._register_worker(thread)
        thread.start()
        self.topbar_extractor_thread = thread

    def _on_update_game_files_from_archive(self):
        """Replace one installed game's payload while protecting its state."""
        game = self._get_selected_game()
        if (
            not game
            or not game[2]
            or not os.path.isdir(game[2])
            or (len(game) > 17 and game[17])
        ):
            active_games = [
                candidate for candidate in self.games
                if len(candidate) > 2
                and candidate[2]
                and os.path.isdir(candidate[2])
                and not (len(candidate) > 17 and candidate[17])
            ]
            if not active_games:
                self._show_toast("Select an installed game before updating its files.", is_error=True)
                return
            labels = [str(candidate[1]) for candidate in active_games]
            selected, accepted = QInputDialog.getItem(
                self,
                "Select Game to Update",
                "Installed game:",
                labels,
                0,
                False,
            )
            if not accepted:
                return
            game = active_games[labels.index(selected)]

        if game[0] in self.running_game_ids:
            QMessageBox.warning(
                self,
                "Game is running",
                "Close the game before replacing its files.",
            )
            return

        archive_path, _ = QFileDialog.getOpenFileName(
            self,
            f"Select Update Archive for {game[1]}",
            "",
            "Archive Files (*.zip *.7z *.rar *.tar.gz *.tgz)",
        )
        if not archive_path or not os.path.isfile(archive_path):
            return

        try:
            inspection = ArchiveInstaller().inspect(
                archive_path,
                os.path.dirname(os.path.abspath(game[2])) or game[2],
            )
        except Exception as error:
            QMessageBox.critical(self, "Archive preflight failed", str(error))
            return

        if not inspection.enough_space:
            QMessageBox.warning(
                self,
                "Not enough disk space",
                f"The archive needs about {inspection.required_bytes / (1024 ** 3):.2f} GB, "
                f"but only {inspection.free_bytes / (1024 ** 3):.2f} GB is free.",
            )
            return

        answer = QMessageBox.question(
            self,
            "Update game files",
            f"Replace the game files for '{game[1]}' from this archive?\n\n"
            "SafeLauncher will stage and validate the archive first, preserve the "
            "Wine/UMU prefix, launcher configuration, and detected saves, then "
            "swap the installation with rollback protection. If save locations "
            "cannot be verified, the update will be aborted safely.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        thread = GameArchiveUpdateThread(archive_path, game, parent=self)
        thread.update_progress.connect(
            lambda message: self._show_toast(f"{game[1]}: {message}")
        )
        thread.update_complete.connect(
            lambda result, target=game: self._on_game_archive_update_complete(target, result)
        )
        thread.finished.connect(self._hide_extraction_spinner)
        self._show_extraction_spinner()
        self._show_toast(f"Preparing a safe update for '{game[1]}'…")
        self._register_worker(thread)
        thread.start()
        self.game_archive_update_thread = thread

    def _on_game_archive_update_complete(self, game, result) -> None:
        if bool(getattr(result, "success", False)):
            self._refresh_library()
            self._show_toast(f"Updated game files for '{game[1]}' successfully.")
            return
        error = str(getattr(result, "error", "Game update failed.") or "Game update failed.")
        guidance = str(getattr(result, "guidance", "") or "")
        message = f"{error}\n\n{guidance}".strip()
        self._show_toast(f"Could not update '{game[1]}'.", is_error=True)
        QMessageBox.critical(self, "Game file update failed", message)

    def _on_topbar_extraction_complete(self, game_name: str, dest_dir: str, success: bool):
        """Callback when topbar archive extraction completes"""
        self._hide_extraction_spinner()
        if not success:
            self._show_toast(f"Failed to extract '{game_name}'.", is_error=True)
            return

        self._show_toast(f"Extracted '{game_name}' successfully.")
        exes = find_executables(dest_dir)
        default_exe = exes[0] if exes else ""

        dialog = AddGameDialog(self, self.sgdb_client, request_manager=self.request_manager)
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
                steam_id = dialog.get_steam_id()
                version_override, patch_notes_url = dialog.get_version_metadata()
                build_id = dialog.get_build_id()
                build_date = dialog.get_build_date()
                save_sandbox_config(path, exe)
                game_id = self.library_service.upsert_game(
                    name, path, exe, mode, banner_path, steam_id or None
                )
                if game_id:
                    self.library_service.update_game_details(
                        game_id, name, path, exe, mode, banner_path,
                        steam_id=steam_id or None,
                        version_override=version_override,
                        patch_notes_url=patch_notes_url,
                    )
                    self._record_initial_steam_build(game_id, path, steam_id, build_id, build_date)
                self._refresh_library()
                if game_id:
                    self._sync_launcher_metadata_async(game_id)
                if hasattr(self, "profile_page"):
                    self.profile_page.mark_local_data_changed()
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
        if filter_mode not in {"all", "installed", "favorites", "archived"}:
            return
        self.current_filter = filter_mode
        self.collection_filter = ""
        self._update_library_filter_buttons()
        if hasattr(self, "compact_container"):
            self.compact_container.set_filter(filter_mode)
        self._refresh_library()

    def _update_library_filter_buttons(self) -> None:
        """Keep the desktop filter segment synchronized with the active view."""
        buttons = getattr(self, "library_filter_buttons", None)
        if not buttons:
            return
        active = getattr(self, "current_filter", "all")
        for mode, button in buttons.items():
            button.blockSignals(True)
            button.setChecked(mode == active)
            button.blockSignals(False)

    def _set_collection_filter(self, col_name: str):
        """Filter library to a specific collection and update banner."""
        self.collection_filter = col_name.strip()
        self.current_filter = "" if self.collection_filter else "all"
        self._update_library_filter_buttons()
        self._refresh_library()

    def _on_add_collection(self):
        """Prompt to create a new collection with custom styled modal."""
        dlg = CreateCollectionDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            col_name = dlg.get_collection_name()
            if col_name:
                self.library_service.create_collection(col_name)
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
                    self.library_service.update_collection_membership(g_id, self.collection_filter)
                elif len(g) > 13 and str(g[13]).strip() == self.collection_filter:
                    self.library_service.update_collection_membership(g_id, "")
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
                self.library_service.rename_collection(self.collection_filter, new_col)
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
            self.library_service.delete_collection(self.collection_filter)
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
            ("Escape", self._on_escape_pressed),
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
        if mode not in {"compact", "grid"} or self.library_view_mode == mode:
            return
        self.library_view_mode = mode
        self.settings.setValue("library_view_mode", mode)
        # Reuse the existing view transition logic without changing the public
        # cycle behavior of the footer button.
        current = self.library_view_mode
        previous = {"compact": "grid", "grid": "compact"}[current]
        self.library_view_mode = previous
        self._toggle_library_view()

    def _toggle_library_view(self):
        cycle = {"compact": "grid", "grid": "compact", "steam": "grid"}
        self.library_view_mode = cycle.get(self.library_view_mode, "compact")
        self.settings.setValue("library_view_mode", self.library_view_mode)
        use_virtual = len(self.banner_widgets) >= getattr(self, "virtualization_threshold", 200)
        if self.library_view_mode in ("compact", "steam"):
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
        else:
            self.library_view_host.set_mode("grid")
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            if hasattr(self, "library_header_bar"):
                self.library_header_bar.setVisible(True)
            if hasattr(self, "right_layout"):
                self.right_layout.setContentsMargins(18, 14, 18, 14)
                self.right_layout.setSpacing(12)
        view_labels = {"compact": "Compact", "grid": "Grid", "steam": "Compact"}
        current_label = view_labels.get(self.library_view_mode, "Grid")
        self.btn_view_toggle.setText(f"View: {current_label}")
        self.btn_view_toggle.setToolTip(f"Change library view (currently {current_label})")

    def _visible_library_ids(self) -> set[int]:
        return self.library_view_host.visible_ids(self.library_view_mode)

    def _selected_library_ids(self) -> set[int]:
        service = getattr(self, "library_service", None)
        return set(service.selected_ids if service is not None else self.library_selection.ids)

    def _replace_library_selection(self, game_ids) -> set[int]:
        service = getattr(self, "library_service", None)
        if service is not None:
            return service.replace_selection(game_ids)
        return self.library_selection.replace(game_ids)

    def _toggle_library_selection(self, game_id: int, *, additive: bool = False) -> set[int]:
        service = getattr(self, "library_service", None)
        if service is not None:
            return service.toggle_selection(game_id, additive=additive)
        return self.library_selection.click(game_id, additive=additive)

    def _clear_library_selection_state(self) -> None:
        service = getattr(self, "library_service", None)
        if service is not None:
            service.clear_selection()
        else:
            self.library_selection.clear()

    def _select_all_visible(self):
        self._replace_library_selection(self._visible_library_ids())
        self._refresh_library()

    def _clear_library_selection(self):
        self._clear_library_selection_state()
        self._refresh_library()

    def _assign_selected_collection(self):
        selected = self._selected_library_ids()
        if not selected:
            self._show_toast("Select one or more games first.", is_error=True)
            return
        collection, accepted = QInputDialog.getText(self, "Assign collection", "Collection name (empty removes it):")
        if not accepted:
            return
        for game_id in selected:
            self.library_service.update_collection_membership(game_id, collection.strip())
        self._refresh_library()

    def _favorite_selected(self):
        selected = self._selected_library_ids()
        if not selected:
            self._show_toast("Select one or more games first.", is_error=True)
            return
        for game_id in selected:
            self.library_service.toggle_favorite(game_id)
        self._refresh_library()
        for game_id in selected:
            self._sync_launcher_metadata_async(game_id)
        if hasattr(self, "profile_page"):
            self.profile_page.mark_local_data_changed()

    def _on_toggle_favorite(self):
        """Toggle favorite status for currently selected game"""
        game = self.selected_game
        if not game:
            return
        game_id = game[0]
        new_fav = self.library_service.toggle_favorite(game_id)
        self._show_toast("Added to Favorites" if new_fav else "Removed from Favorites")
        self._set_local_favorite_state(game_id, new_fav)
        if self.current_filter == "favorites" and not new_fav:
            self._refresh_library()
        self._sync_launcher_metadata_async(game_id)
        if hasattr(self, "profile_page"):
            self.profile_page.mark_local_data_changed()

    def _on_card_favorite_clicked(self, game_id: int):
        """Toggle a game's favorite without rebuilding the active view."""
        new_fav = self.library_service.toggle_favorite(game_id)
        self._show_toast("Added to Favorites" if new_fav else "Removed from Favorites")
        self._set_local_favorite_state(game_id, new_fav)
        # Removing an item from the Favorites filter changes membership and
        # therefore needs one query refresh. All other filters can update in
        # place, preserving scroll, hover, and inspector geometry.
        if self.current_filter == "favorites" and not new_fav:
            self._refresh_library()
        self._sync_launcher_metadata_async(game_id)
        if hasattr(self, "profile_page"):
            self.profile_page.mark_local_data_changed()

    def _set_local_favorite_state(self, game_id: int, is_favorite: bool) -> None:
        """Keep cached records and all library renderers aligned after a toggle."""
        game_id = int(game_id)
        for index, game in enumerate(getattr(self, "games", ())):
            if int(game[0]) != game_id:
                continue
            if hasattr(game, "is_favorite"):
                game.is_favorite = int(bool(is_favorite))
            else:
                values = list(game)
                while len(values) <= 8:
                    values.append(0)
                values[8] = int(bool(is_favorite))
                self.games[index] = tuple(values)
            self.games_by_id[game_id] = self.games[index]
            if self.selected_game is not None and int(self.selected_game[0]) == game_id:
                self.selected_game = self.games[index]
            break
        self.library_view_host.update_favorite(game_id, bool(is_favorite))

    def _refresh_library(self):
        """Clear and reload game banners into dynamic responsive grid based on search, status filter, and sorting."""
        self.performance_tracker.mark_library_refresh()
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
        
        self.games = list(self.library_service.read_games())
        self.games_by_id = {game[0]: game for game in self.games}
        self._resolve_missing_steam_names()
        self.cloud_status_polling.set_targets(self._cloud_status_targets_snapshot())
        if selected_game_id is not None:
            self.selected_game = self.games_by_id.get(selected_game_id)
        self._replace_library_selection(self._selected_library_ids().intersection(self.games_by_id))
        self.stat_label.setText(f"{len(self.games)} Game(s) Total")

        # Compute dynamic sidebar statistics
        self._update_sidebar_counts()

        if not self.games:
            self.sidebar.update_collections_list([])
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
                self._clear_library_selection_state()
                self.selected_game = None
            self.performance_tracker.mark_library_render()
            return

        # Reconcile persisted/legacy maps into the single status model before
        # any view receives the snapshot.
        self.library_metadata_state.game_status_by_id = self.library_service.reconcile_statuses(
            self.games,
            current=self.game_status_by_id,
            update_status=self.update_status_by_game_id,
            cloud_status=self.cloud_save_status_cache,
            build_results=self.steam_check_results,
        )

        # One authoritative query/snapshot feeds every renderer. Views no
        # longer independently decide which games are visible.
        projection = self.library_service.refresh(
            LibraryQuery(
                search=self.search_query,
                filter_mode=self.current_filter,
                collection=self.collection_filter,
                sort_index=self.current_sort,
            ),
            games=tuple(self.games),
            update_status=self.update_status_by_game_id,
            cloud_status=self.cloud_save_status_cache,
            status=self.game_status_by_id,
        )
        self.library_snapshot = projection.snapshot
        self.sidebar.update_collections_list(list(projection.collection_counts))
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
            self._clear_library_selection_state()
            self.library_view_host.render_snapshot(
                self.library_snapshot,
                self.sgdb_client.cache_dir,
                self._selected_library_ids(),
            )
            self.library_view_host.set_empty_grid_message(msg, show_add=True)
            self.performance_tracker.mark_library_render()
            return

        # A selected game can disappear when a collection/status filter changes.
        # Keep compact detail bound to a visible game, never to a stale record.
        visible_ids = {item[0][0] for item in processed}
        if not self.selected_game or self.selected_game[0] not in visible_ids:
            self.selected_game = processed[0][0]
            self._replace_library_selection({processed[0][0][0]})

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
                is_archived = bool(len(g) > 17 and g[17])
                banner_missing = not banner_url or not os.path.exists(banner_url)
                icon_missing = not icon_url or not os.path.exists(icon_url)
                if self._automatic_network_allowed() and not is_archived and (banner_missing or icon_missing):
                    full_exe = os.path.join(path, executable) if (path and executable) else ""
                    self._start_auto_artwork_fetch(
                        game_id,
                        name,
                        full_exe,
                        str(steam_id or ""),
                    )

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
                update_state = self.game_status_by_id.get(game_id, GameStatusState())
                widget.set_update_status(
                    bool(self.update_status_by_game_id.get(game_id, False)),
                    source=getattr(update_state, "update_source", "unknown"),
                    checked_at=getattr(update_state, "update_checked_at", 0.0),
                )
                widget.set_favorite(is_fav)
                widget.set_selected(game_id in self._selected_library_ids())
                cached_cloud = self.cloud_save_status_cache.get(game_id)
                if cached_cloud:
                    widget.set_cloud_status(cached_cloud[0])
                widget.clicked.connect(self._select_game_by_id)
                widget.doubleClicked.connect(self._on_double_click_game)
                widget.favoriteClicked.connect(self._on_card_favorite_clicked)
                widget.launchClicked.connect(self._launch_game_by_id)
                widget.rightClicked.connect(self._show_game_cloud_menu)
                widget.cloudActionRequested.connect(self._on_card_cloud_badge_clicked)
                
                widgets.append(widget)
                self.banner_widgets[game_id] = widget
                
                banner_missing = not banner_url or not os.path.exists(banner_url)
                icon_missing = not icon_url or not os.path.exists(icon_url)
                is_archived = bool(len(g) > 17 and g[17])
                if self._automatic_network_allowed() and not is_archived and (banner_missing or icon_missing):
                    full_exe = os.path.join(path, executable) if (path and executable) else ""
                    self._start_auto_artwork_fetch(
                        game_id,
                        name,
                        full_exe,
                        str(steam_id or ""),
                    )
                
            try:
                self.library_view_host.set_grid_widgets(widgets)
            except (RuntimeError, AttributeError):
                pass

        try:
            self.library_view_host.render_snapshot(self.library_snapshot, self.sgdb_client.cache_dir, self._selected_library_ids())
        except (RuntimeError, AttributeError):
            pass
        self.performance_tracker.mark_library_render()

        if self.library_view_mode in ("compact", "steam"):
            self.library_view_host.set_mode("compact")
            self.detail_panel.setVisible(False)
            self.btn_reveal_detail.setVisible(False)
            self._update_compact_game_page()
        elif use_virtual:
            self.library_view_host.set_mode("grid", use_virtual=True)
        else:
            self.library_view_host.set_mode("grid")
        self._check_games_on_drive()
        self._update_tray_menu()

        # Prefetch 16:9 hero artwork and game icons through the shared manager.
        for game in self.games:
            if len(game) > 17 and game[17]:
                # Archived records are intentionally presentation-only. They
                # use a neutral archive icon and must not start artwork work.
                continue
            g_id = game[0]
            g_name = game[1] if len(game) > 1 else ""
            g_path = game[2] if len(game) > 2 else ""
            g_exe = game[3] if len(game) > 3 else ""
            s_id = game[6] if len(game) > 6 else ""
            full_exe = os.path.join(g_path, g_exe) if (g_path and g_exe) else ""

            hero_cache_file = self.sgdb_client.get_hero_cached_path(steam_id=s_id, game_name=g_name, exe_path=full_exe, game_id=g_id)
            if self._automatic_network_allowed() and not hero_cache_file:
                self._request_managed_hero_artwork(
                    g_id,
                    g_name,
                    s_id,
                    full_exe,
                    priority=RequestPriority.BACKGROUND,
                )

            icon_url = game[18] if len(game) > 18 and game[18] else ""
            if self._automatic_network_allowed() and (not icon_url or not os.path.exists(icon_url)):
                self._request_managed_icon_artwork(
                    g_id,
                    g_name,
                    str(s_id or ""),
                    full_exe,
                    priority=RequestPriority.BACKGROUND,
                )

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

    def _is_archived_game_id(self, game_id: int) -> bool:
        """Return the current archive state before accepting artwork results."""
        for game in getattr(self, "games", ()):
            if game and int(game[0]) == int(game_id):
                return bool(len(game) > 17 and game[17])
        database = getattr(self, "db", None)
        connection = getattr(database, "conn", None)
        if connection is None:
            return False
        try:
            row = connection.execute(
                "SELECT is_archived FROM games WHERE id = ?", (int(game_id),)
            ).fetchone()
            return bool(row and row[0])
        except Exception:
            return False

    def _on_icon_downloaded(self, game_id: int, icon_path: str):
        """Save downloaded game icon path in DB and update card and compact list."""
        if self._is_archived_game_id(game_id):
            return
        if game_id in self.banner_widgets or (self.selected_game and self.selected_game[0] == game_id):
            self.performance_tracker.mark_visible_artwork()
        self.library_service.set_artwork_identity(game_id, icon_url=icon_path)
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

    def _start_auto_artwork_fetch(
        self,
        game_id: int,
        game_name: str,
        exe_path: str,
        steam_id: str,
    ) -> None:
        """Schedule missing cover/icon artwork through the shared manager."""
        if self.request_manager is not None:
            self._request_managed_auto_artwork(
                game_id,
                game_name,
                exe_path,
                steam_id,
                priority=RequestPriority.BACKGROUND,
            )
            return

        # Compatibility for embedded callers that do not provide a manager.
        if game_id in self._compat_auto_fetch_attempted:
            return
        self._compat_auto_fetch_attempted.add(game_id)
        fetcher = BannerAutoFetcher(
            game_id,
            game_name,
            self.sgdb_client,
            exe_path=exe_path,
            steam_id=steam_id,
        )
        fetcher.banner_auto_downloaded.connect(self._on_auto_banner_downloaded)
        fetcher.finished.connect(lambda f=fetcher: self._cleanup_auto_fetcher(f))
        if len(self.auto_fetchers) < self.max_concurrent_auto_fetchers:
            self.auto_fetchers.append(fetcher)
            self._register_worker(fetcher)
            fetcher.start()
        else:
            self._pending_auto_fetchers.append(fetcher)

    def _request_managed_auto_artwork(
        self,
        game_id: int,
        game_name: str,
        exe_path: str,
        steam_id: str,
        *,
        priority: RequestPriority,
    ) -> None:
        """Resolve one cover/icon pair and share it across duplicate rows."""
        if self.request_manager is None:
            return
        artwork_target = ArtworkTarget(
            int(game_id),
            str(game_name),
            str(steam_id or ""),
            str(exe_path or ""),
        )
        plan = self.artwork_coordinator.prepare(
            "auto",
            artwork_target,
            priority=priority,
            mark_attempted=True,
        )
        if plan is None:
            return
        key = plan.key
        if not plan.new_binding:
            current = self.request_manager.state(key)
            if current.usable and isinstance(current.value, (tuple, list)):
                self._apply_managed_auto_artwork(game_id, current.value)
            return

        binding = bind_resource(
            self.request_manager,
            key,
            lambda result, key=key: self._on_managed_auto_artwork_state(key, result),
            self,
            cancel_on_close=True,
        )
        self.artwork_coordinator.attach_binding(plan, binding)
        try:
            self.artwork_coordinator.request(plan)
        except Exception:
            binding = self.artwork_coordinator.discard("auto", key)
            if binding is not None:
                binding.close()
                binding.deleteLater()

    def _apply_managed_auto_artwork(self, game_id: int, value) -> None:
        if not isinstance(value, (tuple, list)) or len(value) < 3:
            return
        self._on_auto_banner_downloaded(
            game_id,
            str(value[0] or ""),
            int(value[1] or 0),
            str(value[2] or ""),
        )

    def _on_managed_auto_artwork_state(self, key: RequestKey, result) -> None:
        if result.status in {ResourceStatus.READY, ResourceStatus.STALE}:
            for game_id in self.artwork_coordinator.game_ids("auto", key):
                self._apply_managed_auto_artwork(game_id, result.value)
        elif result.status not in {ResourceStatus.LOADING}:
            logger.debug("Managed automatic artwork request failed for %s: %s", key, result.error or result.status.value)

        if result.status in {
            ResourceStatus.READY,
            ResourceStatus.STALE,
            ResourceStatus.ERROR,
            ResourceStatus.OFFLINE,
            ResourceStatus.UNAVAILABLE,
            ResourceStatus.AUTHENTICATION_REQUIRED,
            ResourceStatus.PERMISSION_DENIED,
            ResourceStatus.CONFLICT,
            ResourceStatus.CANCELLED,
        }:
            binding = self.artwork_coordinator.discard("auto", key)
            if binding is not None:
                binding.close()
                binding.deleteLater()

    def _on_auto_banner_downloaded(self, game_id: int, image_path: str, steam_id: int = 0, icon_path: str = ""):
        """Update DB and widget when background auto-fetch completes"""
        if self._is_archived_game_id(game_id):
            return
        if image_path and (game_id in self.banner_widgets or (self.selected_game and self.selected_game[0] == game_id)):
            self.performance_tracker.mark_visible_artwork()
        self.library_service.set_artwork_identity(
            game_id,
            banner_url=image_path or None,
            icon_url=icon_path or None,
            steam_id=steam_id or None,
        )

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
        if steam_id and hasattr(self, "profile_page"):
            self.profile_page.mark_local_data_changed()

    def _cleanup_auto_fetcher(self, fetcher):
        if fetcher in self.auto_fetchers:
            self.auto_fetchers.remove(fetcher)
        self._start_next_pending_fetcher()

    def _start_next_pending_fetcher(self):
        """Launch the next queued art fetch once a slot frees up."""
        if not self._automatic_network_allowed():
            self._pending_auto_fetchers.clear()
            return
        while self._pending_auto_fetchers:
            if len(self.auto_fetchers) >= self.max_concurrent_auto_fetchers:
                return
            fetcher = self._pending_auto_fetchers.pop(0)
            if fetcher.isInterruptionRequested():
                continue
            self.auto_fetchers.append(fetcher)
            self._register_worker(fetcher)
            fetcher.start()
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

    def _request_managed_hero_artwork(
        self,
        game_id: int,
        game_name: str,
        steam_id: str,
        exe_path: str,
        *,
        priority: RequestPriority,
    ) -> None:
        """Load one shared hero resource for every library row that needs it."""
        if self.request_manager is None:
            return
        artwork_target = ArtworkTarget(
            int(game_id),
            str(game_name),
            str(steam_id or ""),
            str(exe_path or ""),
        )
        plan = self.artwork_coordinator.prepare(
            "hero",
            artwork_target,
            priority=priority,
            mark_attempted=True,
        )
        if plan is None:
            return
        key = plan.key

        binding = self.artwork_coordinator.binding("hero", key)
        if binding is not None:
            current = self.request_manager.state(key)
            if current.usable and current.value and os.path.exists(str(current.value)):
                self._on_hero_downloaded(game_id, str(current.value))
            return

        binding = bind_resource(
            self.request_manager,
            key,
            lambda result, key=key: self._on_managed_hero_state(key, result),
            self,
            cancel_on_close=True,
        )
        self.artwork_coordinator.attach_binding(plan, binding)
        self.artwork_coordinator.request(plan)

    def _on_managed_hero_state(self, key: RequestKey, result) -> None:
        if result.status not in {ResourceStatus.READY, ResourceStatus.STALE}:
            return
        path = str(result.value or "")
        if not path or not os.path.exists(path):
            return
        for game_id in self.artwork_coordinator.game_ids("hero", key):
            self._on_hero_downloaded(game_id, path)

    def _request_managed_icon_artwork(
        self,
        game_id: int,
        game_name: str,
        steam_id: str,
        exe_path: str,
        *,
        priority: RequestPriority,
    ) -> None:
        """Load one shared icon resource for every library row that needs it."""
        if self.request_manager is None:
            return
        artwork_target = ArtworkTarget(
            int(game_id),
            str(game_name),
            str(steam_id or ""),
            str(exe_path or ""),
        )
        plan = self.artwork_coordinator.prepare(
            "icon",
            artwork_target,
            priority=priority,
            mark_attempted=True,
        )
        if plan is None:
            return
        key = plan.key

        binding = self.artwork_coordinator.binding("icon", key)
        if binding is not None:
            current = self.request_manager.state(key)
            if current.usable and current.value and os.path.exists(str(current.value)):
                self._on_icon_downloaded(game_id, str(current.value))
            return

        binding = bind_resource(
            self.request_manager,
            key,
            lambda result, key=key: self._on_managed_icon_state(key, result),
            self,
            cancel_on_close=True,
        )
        self.artwork_coordinator.attach_binding(plan, binding)
        self.artwork_coordinator.request(plan)

    def _on_managed_icon_state(self, key: RequestKey, result) -> None:
        if result.status not in {ResourceStatus.READY, ResourceStatus.STALE}:
            return
        path = str(result.value or "")
        if not path or not os.path.exists(path):
            return
        for game_id in self.artwork_coordinator.game_ids("icon", key):
            self._on_icon_downloaded(game_id, path)

    def _close_managed_artwork_bindings(self) -> None:
        for binding in self.artwork_coordinator.close():
            binding.close()
            binding.deleteLater()

    def _register_worker(self, worker):
        """Register an application-owned worker with the shutdown supervisor."""
        self.worker_supervisor.register(worker)

    def _start_managed_task(
        self, name: str, work, on_complete=None, *, allow_offline: bool = False
    ):
        """Start a one-shot task owned by this window and shut it down safely."""
        if self.request_manager is not None:
            operation = self.operation_registry.start(
                name.replace("_", " ").strip().title(),
                category="Background",
            )
            key = RequestKey("window-task", f"{name}:{id(work)}")
            handle = self.request_manager.request(
                key,
                lambda token: (token.raise_if_cancelled(), work(), token.raise_if_cancelled())[1],
                priority=RequestPriority.NORMAL,
                metadata={"allow_offline": bool(allow_offline)},
                timeout_seconds=120,
            )
            operation.cancel = handle.cancel
            operation.retry = lambda: self._start_managed_task(
                name, work, on_complete, allow_offline=allow_offline
            )
            self._managed_task_callbacks[handle.request_id] = (name, operation, on_complete)
            handle.future.add_done_callback(
                lambda future, request_id=handle.request_id: self._managed_task_done.emit(
                    (request_id, future)
                )
            )
            return handle

        worker = FunctionWorker(work, parent=self)
        worker.setObjectName(name)
        operation = self.operation_registry.start(
            name.replace("_", " ").strip().title(),
            category="Background",
            cancel=getattr(worker, "request_cancel", worker.requestInterruption),
        )
        operation.retry = lambda: self._start_managed_task(
            name, work, on_complete, allow_offline=allow_offline
        )
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

        def _retire():
            if operation.active:
                self.operation_registry.finish(operation.operation_id, state="cancelled")

        worker.finished.connect(_retire)
        self._register_worker(worker)
        worker.start()
        return worker

    def _on_managed_task_done(self, payload: object) -> None:
        request_id, future = payload
        task_data = self._managed_task_callbacks.pop(request_id, None)
        if task_data is None:
            return
        name, operation, on_complete = task_data
        try:
            result = future.result()
        except Exception as error:
            self._on_managed_task_error(name, operation.operation_id, str(error))
            return
        if result.status == ResourceStatus.READY:
            self.operation_registry.finish_result(operation.operation_id, result.value)
            if on_complete is not None:
                on_complete(result.value)
        elif result.status == ResourceStatus.CANCELLED:
            self.operation_registry.finish(operation.operation_id, state="cancelled")
        else:
            self._on_managed_task_error(
                name,
                operation.operation_id,
                str(result.error or result.status.value),
            )

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

    def _select_game_by_id(self, game_id: int, open_detail: bool = True):
        """Select a game card visually and update game details"""
        additive = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
        self._toggle_library_selection(game_id, additive=additive)
        if not self.selected_game or self.selected_game[0] != game_id:
            self._cancel_metadata_fetchers()
        for widget in list(self.banner_widgets.values()):
            try:
                widget.set_selected(widget.game_id in self._selected_library_ids())
            except (RuntimeError, AttributeError):
                pass
        # Selection is application state, not a property of whichever view
        # happened to receive the click. Keep every presentation synchronized
        # so changing view never appears to lose the current selection.
        selected_ids = self._selected_library_ids()
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
        if self.library_view_mode not in ("compact", "steam") and open_detail:
            self._open_game_detail(game_id)

    def _on_escape_pressed(self):
        """Handle Escape key to close the game detail page if active."""
        if getattr(self, "library_view_host", None) is not None:
            if self.library_view_host.currentIndex() == LibraryViewHost.DETAIL:
                self._close_game_detail()

    def _on_detail_back_clicked(self):
        """Return from game detail page to grid view."""
        self._close_game_detail()

    def _close_game_detail(self):
        """Return from Game Detail Page back to the Grid view."""
        if hasattr(self, "library_header_bar"):
            self.library_header_bar.setVisible(True)
        use_virtual = len(self.banner_widgets) >= getattr(self, "virtualization_threshold", 200)
        self.library_view_host.set_mode("grid", use_virtual=use_virtual)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def _open_game_detail(self, game_id: int):
        """Open the dedicated Game Detail Page for the clicked banner in Grid view."""
        game = None
        for g in self.games:
            if g[0] == game_id:
                game = g
                break
        if not game:
            return

        detail_page = self.library_view_host.detail_page
        detail_page.load_game(game, self.cache_dir)

        # Disk size peek or calculation
        path = game[2] if len(game) > 2 else ""
        cached_size = peek_dir_size(path) if (path and os.path.exists(path)) else None
        if cached_size is not None:
            detail_page.set_disk_size(cached_size)
        elif path and os.path.exists(path):
            disk_thread = DiskSizeFetcherThread(game_id, path, parent=self)
            disk_thread.disk_size_calculated.connect(
                lambda gid, sz: detail_page.set_disk_size(sz) if detail_page.current_game_id == gid else None
            )
            self._track_metadata_fetcher(disk_thread)

        # Cloud save status
        cached_save = self.cloud_save_status_cache.get(game_id)
        if MainWindow._cloud_auto_sync_in_flight(self, game_id):
            cached_local = cached_save[1] if cached_save else None
            cached_cloud = cached_save[2] if cached_save else None
            detail_page.set_cloud_status(SyncStatus.SYNCING, cached_local, cached_cloud)
        elif cached_save is not None:
            c_status, c_local, c_cloud = cached_save
            detail_page.set_cloud_status(c_status, c_local, c_cloud)

        # Achievements
        try:
            achievement_projection, all_achs = self.achievement_persistence_service.stats_and_schema(game_id)
            if achievement_projection and achievement_projection.total > 0:
                badges = []
                unlocked_achs = [a for a in all_achs if a.get("unlocked")]
                for ach in unlocked_achs[:6]:
                    icon_p = ach.get("icon_path")
                    if icon_p and os.path.exists(icon_p):
                        badges.append(QPixmap(icon_p))
                detail_page.set_achievements(achievement_projection.unlocked, achievement_projection.total, badges)
            else:
                detail_page.set_achievements(0, 0)
        except Exception as e:
            logger.debug("Failed loading achievements for detail page: %s", e)

        # Tags
        tags_str = game[10] if len(game) > 10 and game[10] else ""
        tags_list = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
        if tags_list:
            detail_page.set_tags(tags_list)

        # Description
        name = game[1]
        steam_id = game[6] if len(game) > 6 and game[6] else ""
        self._fetch_detail_page_description(game_id, name, steam_id)

        # Update hero background image
        self._update_hero_background_for_game(game)

        # Hide grid header search/sort bar and transition stack to detail page
        if hasattr(self, "library_header_bar"):
            self.library_header_bar.setVisible(False)
        self.library_view_host.set_mode("detail")
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _update_hero_background_for_game(self, game: tuple):
        if not game:
            self.hero_bg.set_hero_image(None)
            return
        g_id, name, path, exe = game[:4]
        banner_url = game[5] if len(game) > 5 else ""
        steam_id = game[6] if len(game) > 6 else ""
        full_exe = os.path.join(path, exe) if (path and exe) else ""
        hero_cache_path = self.sgdb_client.get_hero_cached_path(steam_id=steam_id, game_name=name, exe_path=full_exe, game_id=g_id)
        if hero_cache_path and os.path.exists(hero_cache_path):
            self.hero_bg.set_hero_image(hero_cache_path)
        elif banner_url and os.path.exists(banner_url):
            self.hero_bg.set_hero_image(banner_url)
        else:
            self.hero_bg.set_hero_image(None)

    def _fetch_detail_page_description(self, game_id: int, game_name: str, steam_id: str):
        detail_page = self.library_view_host.detail_page
        saved_notes = self.settings.value(f"game_notes/{game_id}", "", type=str)

        def worker():
            desc = ""
            tags = []
            client = SteamClient()
            try:
                sid = steam_id
                if not sid:
                    items = client.search(game_name, timeout=6)
                    if items and items[0].get("id"):
                        sid = str(items[0]["id"])
                if sid:
                    details = client.app_details(sid, timeout=6)
                    if details:
                        desc = details.get("short_description") or details.get("detailed_description") or ""
                        genres = details.get("genres", [])
                        categories = details.get("categories", [])
                        extracted_tags = [
                            str(item.get("description", ""))
                            for grp in (genres, categories)
                            if isinstance(grp, list)
                            for item in grp
                            if isinstance(item, dict) and item.get("description")
                        ]
                        tags = list(dict.fromkeys(extracted_tags))[:6]
            except Exception as e:
                logger.debug("Could not fetch Steam details for %s: %s", game_name, e)
            finally:
                client.close()
            return desc, tags

        def on_done(result):
            if detail_page.current_game_id != game_id:
                return
            if result:
                desc, tags = result
                if desc:
                    detail_page.set_description(desc)
                elif saved_notes:
                    detail_page.set_description(saved_notes)
                if tags and not detail_page.tags_container.isVisible():
                    detail_page.set_tags(tags)
            elif saved_notes:
                detail_page.set_description(saved_notes)

        thread = FunctionWorker(worker, parent=self)
        thread.completed.connect(on_done)
        self._track_metadata_fetcher(thread)
        thread.start()

    def _on_remove_by_id(self, game_id: int):
        self._select_game_by_id(game_id, open_detail=False)
        self._on_remove()
        if getattr(self, "library_view_host", None) is not None:
            if self.library_view_host.currentIndex() == LibraryViewHost.DETAIL:
                self._close_game_detail()

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
            achievement_projection, all_achs = self.achievement_persistence_service.stats_and_schema(g_id)
            ach_stats = (
                achievement_projection.unlocked_count,
                achievement_projection.total_count,
                achievement_projection.percentage,
            )
            recent_achs = list(achievement_projection.recent[:6])
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
        current_build_id = game[11] if len(game) > 11 and game[11] else ""
        current_build_date = game[20] if len(game) > 20 and game[20] else 0
        cached_local_build = self.local_version_by_game_id.get(g_id)
        if cached_local_build:
            current_build_id = cached_local_build[0] or current_build_id
            current_build_date = cached_local_build[1] or current_build_date
        latest_build_id = ""
        latest_build_date = 0
        cached_build_result = self.steam_check_results.get(g_id)
        if cached_build_result:
            latest_build_id = str(cached_build_result[0] or "")
            latest_build_date = int(cached_build_result[1] or 0)
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

        if self._automatic_network_allowed() and not hero_cache_path:
            self._request_managed_hero_artwork(
                g_id,
                g_name,
                s_id,
                full_exe,
                priority=RequestPriority.NORMAL,
            )

        self.hero_bg.set_hero_image(hero_file)

        is_running = g_id in self.running_game_ids
        full_game_exe = os.path.join(g_path, g_exe) if (g_path and g_exe) else g_path
        is_missing = not bool(g_path and os.path.exists(g_path) and (
            not g_exe or os.path.exists(full_game_exe)
        ))
        update_state = self.game_status_by_id.get(g_id, GameStatusState())

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
            update_source=getattr(update_state, "update_source", "unknown"),
            update_checked_at=getattr(update_state, "update_checked_at", 0.0),
            current_build_id=current_build_id,
            current_build_date=current_build_date,
            current_build_found=has_resolved_build_reference(current_build_id, current_build_date),
            latest_build_id=latest_build_id,
            latest_build_date=latest_build_date,
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
        pass

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
        self._position_extraction_spinner()

    def _position_extraction_spinner(self):
        spinner = getattr(self, "extraction_spinner", None)
        if spinner is not None:
            spinner.move(20, max(0, self.height() - spinner.height() - 20))

    def _show_extraction_spinner(self):
        self._position_extraction_spinner()
        self.extraction_spinner.start()

    def _hide_extraction_spinner(self):
        if hasattr(self, "extraction_spinner"):
            self.extraction_spinner.stop()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_window_controls()
        elif event.type() == QEvent.Type.ActivationChange:
            QTimer.singleShot(0, self._maybe_show_pending_network_loss)

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

    def _panel_animation_progress(self) -> float:
        """Return the current inspector animation value, safely normalized."""
        value = self.panel_anim.currentValue()
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return 1.0 if self.detail_panel.isVisible() else 0.0

    def _animate_left_panel(self, expand: bool):
        """Inspector panel is removed; keep as safe no-op for backward compatibility."""
        self.detail_panel.setVisible(False)
        self.btn_reveal_detail.setVisible(False)

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
        self._open_game_properties_for_game(game)

    def _open_game_properties_for_game(self, game) -> None:
        """Open the canonical per-game cloud and properties surface."""
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
        return format_timestamp(timestamp)

    def _render_update_date_detail(self, latest_date: int, local_date: int, available: bool):
        """Render the compact date comparison below the Steam status pill."""
        if not available:
            self.lbl_update_dates.clear()
            self.lbl_update_dates.setVisible(False)
            return
        latest = escape(format_timestamp(latest_date))
        local = escape(format_timestamp(local_date))
        self.lbl_update_dates.setText(
            f"<font color='#F4F4F5'>New update: {latest}</font>"
            " <font color='#525866'>│</font> "
            f"<font color='#F4F4F5'>Installed: {local}</font>"
        )
        self.lbl_update_dates.setVisible(True)

    def _check_all_steam_updates(self):
        """Check every Steam-linked game once, used on startup and from the tools menu."""
        if not self._automatic_network_allowed():
            self._updates_offline = True
            self._set_network_status(
                True,
                "Offline mode is enabled. Cached game-version results remain available where saved.",
            )
            if hasattr(self, "nav_updates") and self.nav_updates is not None:
                self.nav_updates.setEnabled(True)
                self.nav_updates.setText(" Check for Updates (offline)")
            self._show_toast("Offline mode enabled — Steam update checks are disabled.")
            return
        games = self.library_service.read_games()
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

        if self.request_manager is not None:
            # One request per distinct AppID. Bind every local game to that
            # resource so each game still compares against its own installed
            # build reference while the network fetch is shared and cached.
            specs_by_key = {}
            for game in games:
                game_id, _, path, _, _, _, steam_id = game[:7]
                app_id = normalize_steam_app_id(steam_id)
                if not app_id:
                    continue
                self.metadata_attempted_builds.discard(game_id)
                local_build_id = game[11] if len(game) > 11 and game[11] else ""
                local_build_date = game[20] if len(game) > 20 and game[20] else 0
                manifest_build, manifest_date = read_local_steam_build(path, app_id)
                local_build_id = manifest_build or local_build_id
                local_build_date = manifest_date or local_build_date
                if manifest_build or manifest_date:
                    self.library_service.set_build_reference(game_id, local_build_id, local_build_date)
                self.local_version_by_game_id[game_id] = (local_build_id, local_build_date)
                self.metadata_attempted_builds.add(game_id)
                self._request_managed_steam_build(
                    game_id,
                    app_id,
                    local_build_id,
                    local_build_date,
                    priority=RequestPriority.NORMAL,
                    schedule=False,
                )
                key = self.steam_resource_service.build_key(app_id)
                if key not in specs_by_key:
                    specs_by_key[key] = self.steam_resource_service.build_spec(
                        app_id,
                        priority=RequestPriority.NORMAL,
                        tag="library_update_check",
                    )
            if specs_by_key:
                try:
                    self.request_manager.request_many_cached(
                        list(specs_by_key.values()),
                        max_age_seconds=cache_policy("library-update").max_age_seconds,
                        stale_while_revalidate=True,
                        on_complete=lambda results: self._managed_steam_update_batch_done.emit(results),
                    )
                except Exception as exc:
                    logger.debug("Managed Steam update batch failed to start: %s", exc)
                    self._managed_steam_update_batch_done.emit([])
            else:
                self._managed_steam_update_batch_done.emit([])
            return

        for game in games:
            game_id, _, path, _, _, _, steam_id = game[:7]
            steam_id = normalize_steam_app_id(steam_id)
            if not steam_id:
                continue
            if any(isinstance(fetcher, SteamBuildFetcher) and fetcher.game_id == game_id for fetcher in self.metadata_fetchers):
                continue

            self.metadata_attempted_builds.discard(game_id)
            local_build_id = game[11] if len(game) > 11 and game[11] else ""
            local_build_date = game[20] if len(game) > 20 and game[20] else 0
            manifest_build, manifest_date = read_local_steam_build(path, str(steam_id))
            local_build_id = manifest_build or local_build_id
            local_build_date = manifest_date or local_build_date
            if manifest_build or manifest_date:
                self.library_service.set_build_reference(game_id, local_build_id, local_build_date)
            self.local_version_by_game_id[game_id] = (local_build_id, local_build_date)

            fetcher = SteamBuildFetcher(game_id, steam_id, local_build_id, local_build_date, parent=self, request_manager=self.request_manager, steam_client=self.steam_client)
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

    def _capture_initial_steam_build(
        self, game_id: int, steam_id: str, local_build_id: str = "", local_build_date: int = 0
    ):
        """Check a newly added/edited game against its installed reference.

        This deliberately does not copy Steam's latest build into the local
        record.  A new game without a reference stays unresolved until the
        user supplies its installed Build ID or date.
        """
        if not self._automatic_network_allowed():
            return
        steam_id = normalize_steam_app_id(steam_id)
        if not steam_id:
            return
        if self.request_manager is not None:
            self.metadata_attempted_builds.add(game_id)
            self._request_managed_steam_build(
                game_id,
                str(steam_id),
                local_build_id,
                local_build_date,
                priority=RequestPriority.CRITICAL,
            )
            return
        if any(
            isinstance(fetcher, SteamBuildFetcher) and fetcher.game_id == game_id
            for fetcher in self.metadata_fetchers
        ):
            return
        self.metadata_attempted_builds.add(game_id)
        fetcher = SteamBuildFetcher(
            game_id, steam_id, local_build_id, local_build_date, parent=self
        )
        fetcher.update_checked.connect(self._on_steam_build_checked)
        fetcher.check_failed.connect(self._on_steam_check_failed)
        fetcher.offline_detected.connect(self._on_update_check_offline)
        self._track_metadata_fetcher(fetcher)

    def _record_initial_steam_build(
        self, game_id: int, game_path: str, steam_id: str,
        build_id: str = "", build_date: int = 0
    ):
        """Persist the installed build reference for a newly added game.

        Prefer an explicitly entered build ID, then a manifest inside the
        imported game directory.
        If neither exists, perform a real check but keep the local reference
        unresolved instead of treating the newest online build as installed.
        """
        steam_id = normalize_steam_app_id(steam_id)
        build_id = str(build_id or "").strip()
        build_date = int(build_date or 0)

        if steam_id:
            manifest_build, manifest_date = read_local_steam_build(game_path, steam_id)
            build_id = build_id or manifest_build
            build_date = build_date or manifest_date

        self.library_service.set_build_reference(game_id, build_id, build_date)
        self.local_version_by_game_id[game_id] = (build_id, build_date)
        self.steam_check_results.pop(game_id, None)
        self.update_status_by_game_id.pop(game_id, None)
        if steam_id:
            self._capture_initial_steam_build(game_id, steam_id, build_id, build_date)

    def _set_game_update_status(
        self,
        game_id: int,
        is_available: bool,
        *,
        source: str = "live",
        checked_at: float = 0.0,
        latest_build_id: str = "",
        latest_build_date: int = 0,
        error: str = "",
    ) -> None:
        """Commit one game-version fact and fan it out to every library view.

        Grid, List, Virtual Grid, and Compact are presentations of the same
        library state.  A Steam worker must never update only whichever view
        happened to create a banner widget first.
        """
        is_available = bool(is_available)
        source = str(source or "live").strip().lower()
        if source not in {"live", "cached", "offline", "unknown"}:
            source = "unknown"
        checked_at = float(checked_at or 0.0)
        self.update_status_by_game_id[game_id] = is_available
        current = self.game_status_by_id.get(game_id, GameStatusState())
        self.game_status_by_id[game_id] = replace(
            current,
            update_available=is_available,
            update_error=str(error or ""),
            update_build_id=str(latest_build_id or current.update_build_id),
            update_build_date=int(latest_build_date or current.update_build_date or 0),
            update_checked_at=checked_at or current.update_checked_at,
            update_source=source,
        )
        state = self.game_status_by_id[game_id]
        if source == "live":
            self._set_network_status(False)

        # Every presentation receives the same derived state immediately.
        # The coalesced refresh below still rebuilds the shared snapshot so
        # view switches and persisted state remain correct.
        try:
            if game_id in self.banner_widgets:
                self.banner_widgets[game_id].set_update_status(
                    is_available,
                    source=state.update_source,
                    checked_at=state.update_checked_at,
                )
        except (RuntimeError, AttributeError):
            pass
        self._update_library_item("update_update_state", game_id, state)

    def _request_managed_steam_build(
        self,
        game_id: int,
        steam_id: str,
        local_build_id: str,
        local_build_date: int,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        schedule: bool = True,
    ) -> bool:
        """Subscribe one game to a shared cached public Steam build."""
        if self.request_manager is None:
            return False
        app_id = normalize_steam_app_id(steam_id)
        if not app_id:
            return False
        plan = self.steam_metadata_coordinator.prepare_build(
            game_id,
            app_id,
            local_build_id,
            local_build_date,
            priority=priority,
        )
        key = plan.key
        if self.steam_metadata_coordinator.binding("build", key) is None:
            binding = bind_resource(
                self.request_manager,
                key,
                lambda result, key=key: self._on_managed_steam_build_state(key, result),
                self,
                cancel_on_close=True,
            )
            self.steam_metadata_coordinator.attach_binding("build", plan, binding)

        if not schedule:
            return True

        try:
            self.steam_resource_service.request_build(
                app_id,
                priority=priority,
                tag="game_update_check",
            )
        except Exception as exc:
            logger.debug("Managed Steam build request failed to start for %s: %s", app_id, exc)
            self._on_steam_check_failed(int(game_id), str(exc))
        return True

    def _on_managed_steam_build_state(self, key: RequestKey, result) -> None:
        if result.status in {ResourceStatus.READY, ResourceStatus.STALE}:
            # ``READY`` can still be a cache hit.  Preserve that provenance so
            # an offline launch never presents an old comparison as a live
            # Steam answer.  ``updated_at`` is the cache's stored timestamp for
            # cached results and the completion timestamp for live results.
            source = "cached" if bool(getattr(result, "from_cache", False)) else "live"
            checked_at = float(getattr(result, "updated_at", 0.0) or 0.0)
            value = result.value
            if isinstance(value, (tuple, list)) and len(value) >= 2:
                latest_build_id = str(value[0] or "")
                try:
                    latest_build_date = int(value[1] or 0)
                except (TypeError, ValueError, OverflowError):
                    latest_build_date = 0
                if not latest_build_id:
                    for game_id in tuple(self.steam_metadata_coordinator.build_games(key)):
                        self._on_steam_check_failed(
                            game_id,
                            "Steam returned no public branch build for this AppID",
                        )
                    return
                for game_id, (local_build_id, local_build_date) in tuple(
                    self.steam_metadata_coordinator.build_games(key).items()
                ):
                    if local_build_id:
                        is_update = latest_build_id != local_build_id
                    elif local_build_date > 0:
                        is_update = latest_build_date > local_build_date
                    else:
                        self._on_steam_check_failed(
                            game_id,
                            "No installed Steam build reference; enter a current Build ID or date",
                        )
                        continue
                    self._on_steam_build_checked(
                        game_id,
                        latest_build_id,
                        latest_build_date,
                        is_update,
                        source=source,
                        checked_at=checked_at,
                    )
            return
        if result.status == ResourceStatus.OFFLINE:
            self._set_network_status(
                True,
                "No internet connection. Showing the last known game-version result when available.",
            )
            for game_id in tuple(self.steam_metadata_coordinator.build_games(key)):
                self._on_update_check_offline(game_id)
        elif result.status not in {ResourceStatus.READY, ResourceStatus.STALE, ResourceStatus.CANCELLED}:
            reason = str(result.error or "Steam build check failed")
            for game_id in tuple(self.steam_metadata_coordinator.build_games(key)):
                self._on_steam_check_failed(game_id, reason)

    def _on_managed_steam_update_batch_done(self, _results: object) -> None:
        """Finish the UI action after all distinct AppID resources settle."""
        if hasattr(self, "nav_updates") and self.nav_updates is not None:
            self.nav_updates.setEnabled(True)
            if getattr(self, "_updates_offline", False):
                self.nav_updates.setText(" Check for Updates (offline)")
            else:
                self.nav_updates.setText(" Check for Updates")
        if getattr(self, "_updates_offline", False):
            self._show_toast("Update check finished — offline; showing last known results where available.")
        else:
            self._show_toast("Steam update check complete.")

    def _request_managed_steam_tags(
        self,
        game_id: int,
        game_name: str,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
    ) -> bool:
        """Subscribe one game to a shared cached Steam tag lookup."""
        if self.request_manager is None:
            return False
        identity = str(game_name or "").strip().casefold()
        if not identity:
            return False
        plan = self.steam_metadata_coordinator.prepare_tags(
            game_id,
            game_name,
            priority=priority,
        )
        key = plan.key
        if self.steam_metadata_coordinator.binding("tag", key) is None:
            binding = bind_resource(
                self.request_manager,
                key,
                lambda result, key=key: self._on_managed_steam_tag_state(key, result),
                self,
                cancel_on_close=True,
            )
            self.steam_metadata_coordinator.attach_binding("tag", plan, binding)

        try:
            self.steam_resource_service.request_tags(
                game_name,
                priority=priority,
                tag="game_tags",
            )
        except Exception as exc:
            logger.debug("Managed Steam tag request failed to start for %s: %s", game_name, exc)
            self._on_steam_tags_found(int(game_id), [], "")
        return True

    def _on_managed_steam_tag_state(self, key: RequestKey, result) -> None:
        if result.status in {ResourceStatus.READY, ResourceStatus.STALE}:
            value = result.value
            if isinstance(value, (tuple, list)) and len(value) >= 2:
                tags = value[0] if isinstance(value[0], list) else []
                app_id = str(value[1] or "")
                for game_id in self.steam_metadata_coordinator.tag_game_ids(key):
                    self._on_steam_tags_found(game_id, tags, app_id)
        elif result.status in {
            ResourceStatus.ERROR,
            ResourceStatus.OFFLINE,
            ResourceStatus.UNAVAILABLE,
            ResourceStatus.AUTHENTICATION_REQUIRED,
            ResourceStatus.PERMISSION_DENIED,
            ResourceStatus.CONFLICT,
        }:
            for game_id in self.steam_metadata_coordinator.tag_game_ids(key):
                self._on_steam_tags_found(game_id, [], "")

    def _close_managed_steam_metadata_bindings(self) -> None:
        for binding in self.steam_metadata_coordinator.close():
            binding.close()
            binding.deleteLater()
        for binding in tuple(getattr(self, "_steam_name_bindings", {}).values()):
            try:
                binding.close()
                binding.deleteLater()
            except RuntimeError:
                pass
        getattr(self, "_steam_name_bindings", {}).clear()

    def _resolve_missing_steam_names(self, *, priority: RequestPriority = RequestPriority.BACKGROUND) -> None:
        """Repair archived/cloud-only Steam titles through cached App Details."""
        if self.request_manager is None or not hasattr(self, "steam_resource_service"):
            return
        for game in self.library_service.read_games():
            app_id = str(game.steam_id or "").strip()
            if not app_id or app_id == "0" or not is_placeholder_game_name(game.name, app_id):
                continue
            key = self.steam_resource_service.app_details_key(app_id)
            if key in self._steam_name_bindings:
                continue
            try:
                handle = self.steam_resource_service.request_app_details(
                    app_id,
                    priority=priority,
                    tag="repair_game_name",
                )
                binding = bind_request(
                    self.request_manager,
                    handle,
                    lambda result, key=key: self._on_managed_steam_name_state(key, result),
                    self,
                    cancel_on_close=True,
                )
                self._steam_name_bindings[key] = binding
            except Exception as exc:
                logger.debug("Managed Steam App Details request failed for %s: %s", app_id, exc)

    def _on_managed_steam_name_state(self, key: RequestKey, result) -> None:
        """Apply only verified Steam titles to matching placeholder records."""
        if result.status in {ResourceStatus.READY, ResourceStatus.STALE}:
            details = result.value if isinstance(result.value, dict) else {}
            name = str(details.get("name", "") or "").strip()
            if name:
                app_id = key.identity.rsplit(":", 1)[-1]
                changed = False
                for game in self.library_service.read_games():
                    if str(game.steam_id or "").strip() != app_id:
                        continue
                    changed = self.library_service.apply_metadata_name(game.id, name) or changed
                if changed:
                    self._refresh_library()
            return
        if result.status in {
            ResourceStatus.ERROR,
            ResourceStatus.OFFLINE,
            ResourceStatus.UNAVAILABLE,
            ResourceStatus.AUTHENTICATION_REQUIRED,
            ResourceStatus.PERMISSION_DENIED,
            ResourceStatus.CONFLICT,
            ResourceStatus.CANCELLED,
        }:
            binding = self._steam_name_bindings.pop(key, None)
            if binding is not None:
                try:
                    binding.close()
                    binding.deleteLater()
                except RuntimeError:
                    pass

    def _on_steam_build_checked(
        self,
        game_id: int,
        latest_build_id: str,
        latest_build_date: int,
        is_update_available: bool,
        *,
        source: str = "live",
        checked_at: float | None = None,
    ):
        """Callback when background SteamBuildFetcher returns build info."""
        source = str(source or "live").strip().lower()
        if source not in {"live", "cached", "offline", "unknown"}:
            source = "unknown"
        if checked_at is None:
            checked_at = time.time() if source == "live" else float(
                self.library_metadata_state.steam_build_checked_at.get(game_id, 0.0)
            )
        checked_at = float(checked_at or 0.0)
        self._backfill_current_build_date(game_id, latest_build_id, latest_build_date)
        self.steam_check_results[game_id] = (latest_build_id, latest_build_date, is_update_available, "")
        self._set_game_update_status(
            game_id,
            bool(is_update_available and latest_build_id),
            source=source,
            checked_at=checked_at,
            latest_build_id=latest_build_id,
            latest_build_date=latest_build_date,
        )
        if source == "live":
            self._updates_offline = False
            if hasattr(self, "nav_updates") and self.nav_updates is not None:
                self.nav_updates.setText(" Check for Updates")
        is_update_available = self.update_status_by_game_id[game_id]
        self.library_metadata_state.steam_build_checked_at[game_id] = checked_at
        self._save_persistent_cache()
        if not self.selected_game or self.selected_game[0] != game_id:
            return

        if self.selected_game and self.selected_game[0] == game_id:
            if not latest_build_id:
                local_build_id, local_date = self.local_version_by_game_id.get(game_id, ("", 0))
                if not local_build_id and len(self.selected_game) > 11:
                    local_build_id = self.selected_game[11] or ""
                if not local_date and len(self.selected_game) > 20:
                    local_date = self.selected_game[20] or 0
                local_build_id = local_build_id or "Not recorded"
                local_build_found_suffix = (
                    " <font color='#35C98A'>(found)</font>"
                    if has_resolved_build_reference(local_build_id, local_date)
                    else ""
                )
                steam_app_id = str(self.selected_game[6]).strip() if len(self.selected_game) > 6 and self.selected_game[6] else "Not linked"
                self.lbl_detail_update.setText("Steam check unavailable")
                self._render_update_date_detail(0, local_date, False)
                self.lbl_detail_update.setStyleSheet("background: #3f3f46; color: #d4d4d8; border: 1px solid #71717a; border-radius: 6px; padding: 4px 8px; font-size: 10px; font-weight: bold;")
                self.lbl_detail_versions.setText(
                    "<table width='100%' cellspacing='0' cellpadding='1' style='margin:0; padding:0; border-collapse:collapse;'>"
                    f"<tr><td align='left'><font color='#A7ADB8'>Display version</font></td><td align='right'><b>{escape(str(self.selected_game[15] or 'Not set'))}</b></td></tr>"
                    f"<tr><td align='left'><font color='#A7ADB8'>Steam AppID</font></td><td align='right'><b>{escape(steam_app_id)}</b></td></tr>"
                    f"<tr><td align='left'><font color='#A7ADB8'>Installed build</font></td><td align='right'><b>{escape(str(local_build_id))}</b>{local_build_found_suffix}</td></tr>"
                    f"<tr><td align='left'><font color='#A7ADB8'>Updated</font></td><td align='right'>{self._format_version_date(local_date)}</td></tr>"
                    "<tr><td colspan='2' align='right'><font color='#6F7682'>Steam build unavailable</font></td></tr>"
                    "</table>"
                )
                self.detail_update_widget.setVisible(True)
                return
            local_build_id, local_date = self.local_version_by_game_id.get(game_id, ("", 0))
            if not local_build_id and len(self.selected_game) > 11:
                local_build_id = self.selected_game[11] or ""
            if not local_date and len(self.selected_game) > 20:
                local_date = self.selected_game[20] or 0
            local_build_id = local_build_id or "Not recorded"
            local_build_found = has_resolved_build_reference(local_build_id, local_date)
            version_override = self.selected_game[15] if len(self.selected_game) > 15 and self.selected_game[15] else "Version unavailable"
            patch_notes_url = self.selected_game[16] if len(self.selected_game) > 16 and self.selected_game[16] else ""
            steam_app_id = str(self.selected_game[6]).strip() if len(self.selected_game) > 6 and self.selected_game[6] else "Not linked"
            patch_link = f"<br><a href='{escape(patch_notes_url, quote=True)}'>Open patch notes</a>" if patch_notes_url else ""
            local_build_found_suffix = " <font color='#35C98A'>(found)</font>" if local_build_found else ""
            cached_suffix = " · cached" if source == "cached" else ""
            status = ("Needs update" if is_update_available else "Up to date") + cached_suffix
            status_color = ("rgba(229, 169, 61, 0.12)", "#E5A93D", "rgba(229, 169, 61, 0.3)") if is_update_available else ("rgba(53, 201, 138, 0.12)", "#35C98A", "rgba(53, 201, 138, 0.3)")
            self.lbl_detail_update.setText(status)
            if source == "cached":
                checked_text = format_datetime_timestamp(
                    int(checked_at or 0), fallback="an unknown time"
                )
                self.lbl_detail_update.setToolTip(
                    "This is the last known Steam comparison, not a live result. "
                    f"Last checked online: {checked_text}. Reconnect to verify."
                )
            else:
                self.lbl_detail_update.setToolTip(
                    "A newer game version is available." if is_update_available
                    else "No newer game version was found."
                )
            self.lbl_detail_update.setStyleSheet(
                f"background: {status_color[0]}; color: {('#8493A7' if source == 'cached' else status_color[1])}; border: 1px solid {status_color[2]}; border-radius: 4px; padding: 2px 8px; font-size: 10px; font-weight: 600;"
            )
            self._render_update_date_detail(latest_build_date, local_date, is_update_available)
            self.lbl_detail_versions.setText(
                "<table width='100%' cellspacing='0' cellpadding='1' style='margin:0; padding:0; border-collapse:collapse;'>"
                "<tr><td></td><td align='center'><font color='#6F7682'>LOCAL</font></td>"
                "<td align='center'><font color='#6F7682'>STEAM</font></td></tr>"
                f"<tr><td><font color='#A7ADB8'>Display version</font></td><td align='center'><b>{escape(str(version_override))}</b></td>"
                f"<td align='center'><font color='#6F7682'>—</font></td></tr>"
                f"<tr><td><font color='#A7ADB8'>Steam AppID</font></td><td colspan='2' align='center'><b>{escape(steam_app_id)}</b></td></tr>"
                f"<tr><td><font color='#A7ADB8'>Build</font></td><td align='center'><b>{escape(str(local_build_id))}</b>{local_build_found_suffix}</td>"
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
            self._update_compact_game_page()

    def _backfill_current_build_date(self, game_id: int, latest_build_id: str, latest_build_date: int) -> bool:
        """Record the latest date only when it describes the saved current ID."""
        local_build_id, local_build_date = self.local_version_by_game_id.get(game_id, ("", 0))
        game = self.games_by_id.get(game_id) if hasattr(self, "games_by_id") else None
        if not local_build_id and game is not None:
            local_build_id = game[11] if len(game) > 11 and game[11] else ""
        if not local_build_date and game is not None:
            local_build_date = game[20] if len(game) > 20 and game[20] else 0
        try:
            local_build_date = int(local_build_date or 0)
        except (TypeError, ValueError, OverflowError):
            local_build_date = 0

        filled_date = backfill_matching_build_date(
            local_build_id,
            local_build_date,
            latest_build_id,
            latest_build_date,
        )
        if filled_date == local_build_date:
            return False

        self.library_service.set_build_reference(game_id, local_build_id, filled_date)
        self.local_version_by_game_id[game_id] = (str(local_build_id or ""), filled_date)

        # GameRecord instances are mutable.  Keep all in-memory references in
        # sync so a later selection does not replace the newly recorded date
        # with the stale tuple value that existed before the async check.
        for record in getattr(self, "games", []):
            if record[0] == game_id and hasattr(record, "build_date"):
                record.build_date = filled_date
        if self.selected_game and self.selected_game[0] == game_id and hasattr(self.selected_game, "build_date"):
            self.selected_game.build_date = filled_date
        logger.info(
            "Recorded current build date for game %s: build=%s date=%s",
            game_id,
            local_build_id,
            filled_date,
        )
        return True

    def _on_steam_check_failed(self, game_id: int, reason: str):
        self.steam_check_results[game_id] = ("", 0, False, reason)
        reason_text = str(reason or "Update check failed")
        source = "offline" if "offline" in reason_text.casefold() else "unknown"
        if source == "offline":
            self._set_network_status(
                True,
                "No internet connection. Showing the last known game-version result when available.",
            )
        self._set_game_update_status(
            game_id,
            False,
            source=source,
            error=reason_text,
        )
        if self.selected_game and self.selected_game[0] == game_id:
            self.lbl_detail_update.setText("Steam check failed")
            self._render_update_date_detail(0, 0, False)
            steam_app_id = str(self.selected_game[6]).strip() if len(self.selected_game) > 6 and self.selected_game[6] else "Not linked"
            version_override = self.selected_game[15] if len(self.selected_game) > 15 and self.selected_game[15] else "Not set"
            self.lbl_detail_versions.setText(
                f"<b>Display version:</b> {escape(str(version_override))} &nbsp;·&nbsp; "
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
        self._set_network_status(
            True,
            "No internet connection. Showing the last known game-version result when available.",
        )
        cached_result = self.steam_check_results.get(game_id)
        cached_build = str(cached_result[0] or "") if cached_result else ""
        if cached_result and cached_build:
            # Keep the last known comparison usable, but explicitly mark it as
            # cached.  Replacing it with an empty offline tuple would make the
            # next offline selection lose the useful result and could make a
            # real update look like "up to date".
            self._on_steam_build_checked(
                game_id,
                cached_build,
                int(cached_result[1] or 0),
                bool(cached_result[2]),
                source="cached",
                checked_at=self.library_metadata_state.steam_build_checked_at.get(game_id, 0.0),
            )
            self.metadata_attempted_builds.discard(game_id)
            return

        self.steam_check_results[game_id] = ("", 0, False, "offline")
        self._set_game_update_status(
            game_id,
            False,
            source="offline",
            error="offline",
        )
        if self.selected_game and self.selected_game[0] == game_id:
            self.lbl_detail_update.setText("<font color='#6F7682'>Offline — update check not performed</font>")
            self._render_update_date_detail(0, 0, False)
            self.lbl_detail_update.setStyleSheet("background: #1A1E26; color: #A7ADB8; border: 1px solid #252A33; border-radius: 4px; padding: 2px 8px; font-size: 10px; font-weight: 500;")
            steam_app_id = str(self.selected_game[6]).strip() if len(self.selected_game) > 6 and self.selected_game[6] else "Not linked"
            version_override = self.selected_game[15] if len(self.selected_game) > 15 and self.selected_game[15] else "Not set"
            self.lbl_detail_versions.setText(
                f"<b>Display version:</b> {escape(str(version_override))} &nbsp;·&nbsp; "
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
        if self.request_manager is not None:
            for key in self.steam_metadata_coordinator.build_keys():
                games = self.steam_metadata_coordinator.build_games(key)
                if game_id in games:
                    self.request_manager.invalidate(key)
                    break
        self.metadata_attempted_builds.discard(game_id)
        self._update_detail_panel()

    def _commit_current_steam_build(self, game_id: int, build_id: str, build_date: int) -> None:
        """Persist a current Steam build and clear any stale update result."""
        game_id = int(game_id)
        build_id = str(build_id or "").strip()
        build_date = int(build_date or 0)
        self.library_service.set_build_reference(game_id, build_id, build_date)
        self.local_version_by_game_id[game_id] = (build_id, build_date)

        # A previous check may have cached the same Steam release as an
        # update. Replace that result as well as the derived view state, so a
        # refresh cannot replay the stale update badge.
        self.steam_check_results[game_id] = (build_id, build_date, False, "")
        self.library_metadata_state.steam_build_checked_at[game_id] = time.time()
        if hasattr(self, "_set_game_update_status"):
            # Marking a locally entered build is not an online Steam check.
            # Do not clear the global Offline indicator as a side effect.
            self._set_game_update_status(game_id, False, source="unknown")
        elif hasattr(self, "update_status_by_game_id"):
            # Keep lightweight hosts/test doubles in sync with the canonical
            # status cache when they do not provide the renderer helper.
            self.update_status_by_game_id[game_id] = False
            if hasattr(self, "game_status_by_id"):
                current = self.game_status_by_id.get(game_id, GameStatusState())
                try:
                    self.game_status_by_id[game_id] = replace(
                        current,
                        update_available=False,
                        update_error="",
                    )
                except (TypeError, AttributeError):
                    # Non-dataclass lightweight hosts can still expose a
                    # mutable status object for the same contract.
                    if hasattr(current, "update_available"):
                        current.update_available = False
                    if hasattr(current, "update_error"):
                        current.update_error = ""
        self._save_persistent_cache()

    def _mark_build_current_from_config(self, game_id: int):
        """Record a manually installed Steam build from the game settings dialog."""
        if not self.selected_game or self.selected_game[0] != game_id:
            return
        latest = getattr(self, 'latest_checked_build_id', "")
        if not latest:
            self._show_toast("Steam has not provided a build to record yet.", is_error=True)
            return
        latest_date = getattr(self, "latest_checked_build_date", 0)
        self._commit_current_steam_build(game_id, latest, latest_date)
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
        latest_date = getattr(self, "latest_checked_build_date", 0)
        self._commit_current_steam_build(game_id, latest, latest_date)
        self._show_toast("Steam build marked as current. Game files were not changed.")
        self._refresh_library()
        self._select_game_by_id(game_id)

    def _metadata_cache_path(self) -> str:
        """Return the metadata cache beside the shared artwork cache.

        Keeping this path relative to the application cache selected by
        ``ArtworkClient`` makes tests and packaged/portable installs honor the
        same cache root.  It also avoids a second hard-coded ``~/.cache``
        policy next to the request-manager cache.
        """
        return os.path.join(os.path.dirname(os.fspath(self.cache_dir)), "metadata_cache.json")

    def _metadata_resource_key(self) -> RequestKey:
        return RequestKey("library-metadata-projection", "local", "v1")

    def _load_persistent_cache(self):
        """Load local metadata projections through the shared persistent cache."""
        self.achievement_state.checked_at.clear()
        self.achievement_state.status.clear()
        self.achievement_state.resolutions.clear()
        key = self._metadata_resource_key()
        shared = self.resource_cache.get(key) if self.resource_cache is not None else None
        if shared is not None and self.library_metadata_state.load_payload(
            shared.value, self.achievement_state
        ):
            if self.resource_cache is not None and self.resource_cache.directory is not None:
                try:
                    os.unlink(self._metadata_cache_path())
                except OSError:
                    pass
            return
        if self.library_metadata_state.load_legacy_cache(
            self._metadata_cache_path(), self.achievement_state
        ) and self.resource_cache is not None:
            self.resource_cache.put(
                key,
                self.library_metadata_state.cache_payload(self.achievement_state),
                content_type="application/json",
            )
            if self.resource_cache.directory is not None:
                try:
                    os.unlink(self._metadata_cache_path())
                except OSError:
                    pass

    def _save_persistent_cache(self):
        """Persist local metadata projections in the shared resource cache."""
        if self.resource_cache is not None:
            self.resource_cache.put(
                self._metadata_resource_key(),
                self.library_metadata_state.cache_payload(self.achievement_state),
                content_type="application/json",
            )

    def _on_steam_tags_found(self, game_id: int, tags_list: list, steam_app_id: str = ""):
        """Callback when background SteamTagsFetcher returns genres/categories"""
        if steam_app_id and steam_app_id.isdigit() and int(steam_app_id) > 0:
            self.library_service.set_artwork_identity(game_id, steam_id=steam_app_id)
        if tags_list:
            tags_str = ", ".join(tags_list)
            self.library_service.set_tags(game_id, tags_str)
        self.metadata_attempted_tags.add(game_id)
        self._save_persistent_cache()
        if not self.selected_game or self.selected_game[0] != game_id:
            return

        # Reload the row so the newly discovered AppID is used by future launches.
        self._refresh_library()
        self._select_game_by_id(game_id)
        if steam_app_id and hasattr(self, "profile_page"):
            self.profile_page.mark_local_data_changed()

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
        if self._is_archived_game_id(game_id):
            return
        if self.selected_game and self.selected_game[0] == game_id:
            self.performance_tracker.mark_visible_artwork()
            self.hero_bg.set_hero_image(image_path)
            self._update_compact_game_page()

    def performance_metrics(self) -> dict:
        """Return user-visible loading timings plus request-manager counters."""
        request_metrics = (
            self.request_manager.metrics()
            if getattr(self, "request_manager", None) is not None else None
        )
        return self.performance_tracker.snapshot(request_metrics)

    def _on_disk_size_calculated(self, game_id: int, size_bytes: int):
        if self.selected_game and self.selected_game[0] == game_id:
            self.detail_disk_size.setText(f"Size: {format_size(size_bytes)}")

    def _render_cloud_status(
        self,
        game_id: int,
        status,
        local_stats=None,
        cloud_stats=None,
        *,
        stale: bool = False,
    ):
        """Update both library card badge and left detail inspector panel."""
        indicator = cloud_indicator(status)
        if game_id in self.banner_widgets:
            self.banner_widgets[game_id].set_cloud_status(status)

        display_local = local_stats
        display_cloud = cloud_stats
        if display_local is None or display_cloud is None:
            cached_entry = self.cloud_save_status_cache.get(game_id)
            if cached_entry:
                display_local = display_local or cached_entry[1]
                display_cloud = display_cloud or cached_entry[2]

        self._update_library_item(
            "update_cloud_status", game_id, status, display_local, display_cloud
        )


        if self.selected_game and self.selected_game[0] == game_id:
            label = indicator.label + (" · Cached" if stale else "")
            self.detail_cloud_status.setText(
                f"<font color='{indicator.color}'><b>{label}</b></font>"
            )
            self.detail_cloud_status.setToolTip(
                indicator.tooltip + (" Last known result; refresh pending." if stale else "")
            )

            if hasattr(self, "detail_cloud_metadata"):
                metadata_stats = (
                    display_local
                    if status == SyncStatus.LOCAL_NEWER
                    or (
                        status == SyncStatus.SYNCING
                        and int(game_id) in getattr(self, "_cloud_auto_upload_in_flight", {})
                    )
                    else display_cloud
                )
                if metadata_stats is not None and getattr(metadata_stats, "exists", False):
                    saved_at = format_datetime_timestamp(
                        getattr(metadata_stats, "last_modified", 0.0),
                        "%H:%M",
                        fallback="Unknown time",
                    )
                    device_name = escape(
                        str(getattr(metadata_stats, "device_name", "") or "Unknown device")
                    )
                    self.detail_cloud_metadata.setText(
                        f"Saved: {escape(saved_at)} · Device: {device_name}"
                    )
                    self.detail_cloud_metadata.setVisible(True)
                else:
                    self.detail_cloud_metadata.clear()
                    self.detail_cloud_metadata.setVisible(False)

            if hasattr(self, "btn_detail_cloud_restore"):
                c_stats = cloud_stats
                if c_stats is None:
                    cached_entry = self.cloud_save_status_cache.get(game_id)
                    if cached_entry:
                        c_stats = cached_entry[2]
                actions_available = status not in {
                    SyncStatus.SYNCING,
                    SyncStatus.CLOUD_OFFLINE,
                    SyncStatus.CLOUD_AUTH_REQUIRED,
                    SyncStatus.CLOUD_UNAVAILABLE,
                }
                if actions_available and c_stats and getattr(c_stats, "exists", False):
                    self.btn_detail_cloud_restore.show()
                else:
                    self.btn_detail_cloud_restore.hide()

    def _set_detail_cloud_checking(self) -> None:
        """Show a clear in-flight state while the selected save is probed."""
        self.detail_cloud_status.setText(
            "<font color='#6F7682'><b>Cloud Save: Checking…</b></font>"
        )
        self.detail_cloud_status.setToolTip("Checking the configured cloud backend and save status…")
        if hasattr(self, "detail_cloud_metadata"):
            self.detail_cloud_metadata.clear()
            self.detail_cloud_metadata.setVisible(False)
        if hasattr(self, "btn_detail_cloud_restore"):
            self.btn_detail_cloud_restore.hide()

    def _restore_selected_game_cloud_save(self):
        """Restore cloud save for the currently selected library game.

        Shows a progress dialog immediately (before any network I/O) so the
        user gets instant feedback. The preflight runs first; replacement is
        dispatched only after the user explicitly confirms the restore.
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

        target = CloudOperationTarget(game_id, game_name, game_path, steam_id)
        try:
            handle = self.cloud_operation_service.request_restore_preflight(
                target,
                priority=RequestPriority.CRITICAL,
                tag="manual_restore",
            )
        except Exception as exc:
            if hasattr(self, "_active_restore_progress") and self._active_restore_progress:
                self._active_restore_progress.close()
                self._active_restore_progress.deleteLater()
                self._active_restore_progress = None
            if hasattr(self, "btn_detail_cloud_restore"):
                self.btn_detail_cloud_restore.setEnabled(True)
            self._show_toast(f"Could not check the cloud save: {exc}", is_error=True)
            return

        def _deliver(future):
            try:
                resource = future.result()
                value = resource.value if resource.status == ResourceStatus.READY else None
            except Exception as exc:
                value = None
                error = str(exc)
            else:
                error = str(resource.error or "") if value is None else ""
            self._save_restore_finished.emit({
                "game_id": game_id,
                "game_name": game_name,
                "game_path": game_path,
                "steam_id": steam_id,
                "phase": "preflight",
                "preflight": value,
                "restore_plan": value.get("restore_plan") if isinstance(value, dict) else None,
                "error": error or (value.get("error") if isinstance(value, dict) else ""),
            })

        handle.future.add_done_callback(_deliver)

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

        if payload.get("phase") == "preflight":
            preflight = payload.get("preflight")
            if not isinstance(preflight, dict) or preflight.get("kind") == "error":
                detail = preflight.get("error") if isinstance(preflight, dict) else None
                message = (
                    getattr(detail, "error", None)
                    or detail
                    or payload.get("error")
                    or "Could not check the cloud save."
                )
                guidance = getattr(detail, "guidance", "")
                self._show_toast(f"{message} {guidance}".strip(), is_error=True)
                if hasattr(self, "btn_detail_cloud_restore"):
                    self.btn_detail_cloud_restore.setEnabled(True)
                return

            if preflight.get("kind") not in {"ready", "history"} or not preflight.get("cloud_exists", True):
                display_name = game_name or "this game"
                QMessageBox.information(
                    self, "No Cloud Save",
                    f"No cloud save found for '{display_name}'.",
                )
                if hasattr(self, "btn_detail_cloud_restore"):
                    self.btn_detail_cloud_restore.setEnabled(True)
                return

            entries = normalize_history_entries([preflight.get("history_entry")])
            selected_entry = entries[0] if entries else None
            if not confirm_restore(
                self,
                game_name=game_name,
                entry=selected_entry,
                target_path=payload.get("game_path", ""),
                technical_details=str(preflight.get("display_path") or ""),
                title="Restore latest cloud save",
            ):
                if hasattr(self, "btn_detail_cloud_restore"):
                    self.btn_detail_cloud_restore.setEnabled(True)
                return

            if hasattr(self, "btn_detail_cloud_restore"):
                self.btn_detail_cloud_restore.setEnabled(False)
            progress = cloud_progress(
                self, f"Restoring latest cloud save for '{game_name}'…"
            )
            self._active_restore_progress = progress
            target = CloudOperationTarget(
                game_id,
                game_name,
                payload.get("game_path", ""),
                payload.get("steam_id", ""),
            )
            history_entry = preflight.get("history_entry") or {}
            target_version = history_entry.get("version")
            try:
                target_version = int(target_version) if target_version is not None else None
            except (TypeError, ValueError):
                target_version = None
            try:
                handle = self.cloud_operation_service.request_restore(
                    target,
                    priority=RequestPriority.CRITICAL,
                    tag="manual_restore",
                    target_version=target_version,
                    restore_plan=payload.get("restore_plan"),
                )
            except Exception as exc:
                from core.cloud_operations import CloudOperationResult
                self._save_restore_finished.emit({
                    "game_id": game_id,
                    "game_name": game_name,
                    "phase": "restore",
                    "result": CloudOperationResult(
                        False,
                        "Cloud restore",
                        game_name,
                        error=str(exc),
                        category="backend_unavailable",
                        guidance="Check the cloud connection and try again.",
                    ),
                })
                return

            def _deliver_restore(future):
                from core.cloud_operations import CloudOperationResult
                try:
                    resource = future.result()
                    restored = resource.value if resource.status == ResourceStatus.READY else None
                    error = str(resource.error or "") if restored is None else ""
                except Exception as exc:
                    restored = None
                    error = str(exc)
                if restored is None:
                    restored = CloudOperationResult(
                        False,
                        "Cloud restore",
                        game_name,
                        error=error or "Cloud restore failed.",
                        category="backend_unavailable",
                        guidance="Check the cloud connection and try again.",
                    )
                self._save_restore_finished.emit({
                    "game_id": game_id,
                    "game_name": game_name,
                    "phase": "restore",
                    "result": restored,
                })

            handle.future.add_done_callback(_deliver_restore)
            return

        ok = bool(getattr(result, "success", False))
        # Re-enable the restore button regardless of outcome (L-5 companion)
        if hasattr(self, "btn_detail_cloud_restore"):
            self.btn_detail_cloud_restore.setEnabled(True)
        # Handle the typed no-cloud-save result from the operation service.
        if getattr(result, "category", "") == "cloud_missing":
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


    def _on_cloud_save_status_calculated(
        self,
        game_id: int,
        status,
        local_stats,
        cloud_stats,
        *,
        auto_sync: bool = False,
    ):
        if getattr(self, "_closing", False):
            return
        checked_at = time.time()
        self.cloud_status_service.record_status(
            game_id,
            status,
            local_stats,
            cloud_stats,
            checked_at=checked_at,
            generation=self.cloud_sync_coordinator.generation,
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
        self._save_persistent_cache()
        if auto_sync:
            self._maybe_auto_upload_cloud_save(
                game_id,
                status,
                local_stats,
                cloud_stats,
            )
            self._maybe_auto_restore_cloud_save(
                game_id,
                status,
                local_stats,
                cloud_stats,
            )

    def _accept_cloud_status_for_context(
        self,
        generation: int,
        game_id: int,
        status,
        local_stats,
        cloud_stats,
        *,
        auto_sync: bool = False,
    ):
        """Discard an asynchronous cloud result from a retired configuration."""
        if not self.cloud_sync_coordinator.accepts(generation):
            logger.debug("Discarded cloud status for game %s from retired context %s", game_id, generation)
            return
        self._on_cloud_save_status_calculated(
            game_id,
            status,
            local_stats,
            cloud_stats,
            auto_sync=auto_sync,
        )

    def _set_cloud_syncing(self, game_id: int, local_stats=None, cloud_stats=None) -> None:
        """Render a transient state while an automatic cloud sync runs."""
        current = self.game_status_by_id.get(game_id, GameStatusState())
        local_stats = local_stats if local_stats is not None else current.local_stats
        cloud_stats = cloud_stats if cloud_stats is not None else current.cloud_stats
        self.game_status_by_id[game_id] = replace(
            current,
            cloud_status=SyncStatus.SYNCING,
            local_stats=local_stats,
            cloud_stats=cloud_stats,
            cloud_checked_at=time.time(),
        )
        # Do not persist SYNCING: a restart must re-establish the real state.
        self._render_cloud_status(
            game_id,
            SyncStatus.SYNCING,
            local_stats,
            cloud_stats,
        )
        if hasattr(self, "btn_detail_launch"):
            self._update_detail_launch_button(game_id)

    def _cloud_auto_sync_in_flight(self, game_id: int) -> bool:
        """Return whether an automatic upload or restore owns this game's save."""
        return (
            int(game_id) in getattr(self, "_cloud_auto_restore_in_flight", {})
            or int(game_id) in getattr(self, "_cloud_auto_upload_in_flight", {})
        )

    @staticmethod
    def _cloud_restore_signature(cloud_stats) -> tuple:
        """Identify one cloud snapshot independently of backend list order."""
        if cloud_stats is None:
            return (None, 0.0, 0, 0, "")
        return (
            getattr(cloud_stats, "cloud_version", None),
            round(float(getattr(cloud_stats, "last_modified", 0.0) or 0.0), 3),
            int(getattr(cloud_stats, "size_bytes", 0) or 0),
            int(getattr(cloud_stats, "file_count", 0) or 0),
            str(getattr(cloud_stats, "device_name", "") or ""),
        )

    def _request_manager_accepts_work(self) -> bool:
        """Return whether new managed requests can still be submitted."""
        if getattr(self, "_closing", False):
            return False
        if not hasattr(self, "request_manager"):
            # Keep the helper usable by lightweight embedding/test doubles;
            # real MainWindow instances always create the manager eagerly.
            return True
        manager = getattr(self, "request_manager", None)
        return manager is not None and not getattr(manager, "_closed", False)

    def _maybe_auto_upload_cloud_save(
        self,
        game_id: int,
        status,
        local_stats=None,
        cloud_stats=None,
    ) -> None:
        """Upload a newer local save automatically once the game is stopped."""
        if not MainWindow._request_manager_accepts_work(self):
            return
        if status != SyncStatus.LOCAL_NEWER:
            return
        if game_id in self.running_game_ids:
            return
        if not self._automatic_network_allowed():
            return
        if MainWindow._cloud_auto_sync_in_flight(self, game_id):
            return
        game = self.games_by_id.get(game_id)
        if not game:
            return

        active_operations = self.cloud_operation_service.active_operations()
        if any(
            record.game_id == int(game_id)
            and (
                str(record.operation).startswith("restore")
                or record.operation in {"exit-sync", "prelaunch", "upload"}
            )
            for record in active_operations
        ):
            return

        game_name = str(game[1] if len(game) > 1 else "")
        game_path = str(game[2] if len(game) > 2 else "")
        steam_id = str(game[6] if len(game) > 6 and game[6] else "")
        signature = (
            self.cloud_sync_coordinator.generation,
            round(float(getattr(local_stats, "last_modified", 0.0) or 0.0), 3),
            int(getattr(local_stats, "size_bytes", 0) or 0),
            int(getattr(local_stats, "file_count", 0) or 0),
            str(getattr(local_stats, "device_name", "") or ""),
        )
        attempts = getattr(self, "_cloud_auto_upload_attempts", {})
        previous = attempts.get(int(game_id))
        attempt_count = previous[1] if previous and previous[0] == signature else 0
        if attempt_count >= 2:
            logger.warning(
                "Automatic cloud upload stopped for game %s after %d attempts "
                "for unchanged local snapshot %s",
                game_id,
                attempt_count,
                signature,
            )
            return
        generation = self.cloud_sync_coordinator.generation
        marker = (generation, "upload")
        attempts[int(game_id)] = (signature, attempt_count + 1)
        setattr(self, "_cloud_auto_upload_attempts", attempts)
        self._cloud_auto_upload_in_flight[int(game_id)] = marker
        self._set_cloud_syncing(game_id, local_stats, cloud_stats)
        target = CloudOperationTarget(int(game_id), game_name, game_path, steam_id)
        try:
            handle = self.cloud_operation_service.request_upload(
                target,
                priority=RequestPriority.BACKGROUND,
                generation=generation,
                tag="automatic-cloud-upload",
            )
        except Exception as exc:
            logger.exception("Unable to queue automatic cloud upload for game %s", game_id)
            self._cloud_auto_upload_done.emit({
                "game_id": int(game_id),
                "game_name": game_name,
                "generation": generation,
                "success": False,
                "error": str(exc),
                "guidance": "Check the cloud connection and try again.",
            })
            return

        def _deliver(future):
            from core.cloud_operations import CloudOperationResult
            try:
                resource = future.result()
                result = resource.value if resource.status == ResourceStatus.READY else None
                error = str(resource.error or "") if result is None else ""
            except Exception as exc:
                result = None
                error = str(exc)
            if not isinstance(result, CloudOperationResult):
                self._cloud_auto_upload_done.emit({
                    "game_id": int(game_id),
                    "game_name": game_name,
                    "generation": generation,
                    "success": False,
                    "error": error or "Cloud upload failed.",
                    "guidance": "Check the cloud connection and try again.",
                })
                return
            self._cloud_auto_upload_done.emit({
                "game_id": int(game_id),
                "game_name": game_name,
                "generation": generation,
                "success": bool(result.success),
                "error": str(result.error or ""),
                "guidance": str(result.guidance or ""),
            })

        handle.future.add_done_callback(_deliver)

    def _on_cloud_auto_upload_done(self, payload: object) -> None:
        """Finish an automatic upload and re-derive the authoritative status."""
        if not isinstance(payload, dict):
            return
        game_id = int(payload.get("game_id", 0) or 0)
        if getattr(self, "_closing", False):
            self._cloud_auto_upload_in_flight.pop(game_id, None)
            return
        marker = self._cloud_auto_upload_in_flight.get(game_id)
        expected = (int(payload.get("generation", -1)), "upload")
        if marker != expected:
            return
        self._cloud_auto_upload_in_flight.pop(game_id, None)
        self._update_detail_launch_button(game_id)
        if not self.cloud_sync_coordinator.accepts(expected[0]):
            return

        game_name = str(payload.get("game_name", "this game"))
        if payload.get("success"):
            self._show_toast(f"Local save synced to cloud for '{game_name}'.")
            self.request_cloud_recheck(
                [game_id],
                "automatic-upload-complete",
                auto_sync=False,
            )
            return

        message = str(payload.get("error") or "Cloud upload failed.")
        guidance = str(payload.get("guidance") or "")
        self._show_toast(
            f"{message} Local save preserved. {guidance}".strip(),
            is_error=True,
        )
        self.request_cloud_recheck([game_id], "automatic-upload-failed")

    def _maybe_auto_restore_cloud_save(
        self,
        game_id: int,
        status,
        local_stats=None,
        cloud_stats=None,
    ) -> None:
        """Restore a newly detected cloud save when it is safe to mutate disk."""
        if not MainWindow._request_manager_accepts_work(self):
            return
        if status not in (SyncStatus.CLOUD_NEWER, SyncStatus.CLOUD_ONLY):
            return
        if game_id in self.running_game_ids:
            return
        if not self._automatic_network_allowed():
            return
        if MainWindow._cloud_auto_sync_in_flight(self, game_id):
            return
        game = self.games_by_id.get(game_id)
        if not game:
            return

        active_operations = self.cloud_operation_service.active_operations()
        if any(
            record.game_id == int(game_id)
            and (
                str(record.operation).startswith("restore")
                or record.operation in {"exit-sync", "prelaunch", "upload"}
            )
            for record in active_operations
        ):
            return

        game_name = str(game[1] if len(game) > 1 else "")
        game_path = str(game[2] if len(game) > 2 else "")
        steam_id = str(game[6] if len(game) > 6 and game[6] else "")
        target_version = getattr(cloud_stats, "cloud_version", None)
        signature = (
            self.cloud_sync_coordinator.generation,
            *MainWindow._cloud_restore_signature(cloud_stats),
        )
        attempts = getattr(self, "_cloud_auto_restore_attempts", {})
        previous = attempts.get(int(game_id))
        attempt_count = previous[1] if previous and previous[0] == signature else 0
        # Two attempts cover a transient transfer failure.  A successful
        # restore that still reports the same cloud snapshot must never turn
        # into a periodic destructive restore loop.
        if attempt_count >= 2:
            logger.warning(
                "Automatic cloud restore stopped for game %s after %d attempts "
                "for unchanged snapshot %s",
                game_id,
                attempt_count,
                signature,
            )
            return
        generation = self.cloud_sync_coordinator.generation
        marker = (generation, target_version)
        attempts[int(game_id)] = (signature, attempt_count + 1)
        setattr(self, "_cloud_auto_restore_attempts", attempts)
        self._cloud_auto_restore_in_flight[int(game_id)] = marker
        self._set_cloud_syncing(game_id, local_stats, cloud_stats)

        target = CloudOperationTarget(int(game_id), game_name, game_path, steam_id)
        try:
            handle = self.cloud_operation_service.request_restore(
                target,
                priority=RequestPriority.BACKGROUND,
                generation=generation,
                tag="automatic-cloud-restore",
                target_version=target_version,
            )
        except Exception as exc:
            logger.exception("Unable to queue automatic cloud restore for game %s", game_id)
            self._cloud_auto_restore_done.emit({
                "game_id": int(game_id),
                "game_name": game_name,
                "generation": generation,
                "target_version": target_version,
                "success": False,
                "error": str(exc),
                "guidance": "Check the cloud connection and try again.",
            })
            return

        def _deliver(future):
            from core.cloud_operations import CloudOperationResult
            try:
                resource = future.result()
                result = resource.value if resource.status == ResourceStatus.READY else None
                error = str(resource.error or "") if result is None else ""
            except Exception as exc:
                result = None
                error = str(exc)
            if not isinstance(result, CloudOperationResult):
                self._cloud_auto_restore_done.emit({
                    "game_id": int(game_id),
                    "game_name": game_name,
                    "generation": generation,
                    "target_version": target_version,
                    "success": False,
                    "error": error or "Cloud restore failed.",
                    "guidance": "Check the cloud connection and try again.",
                })
                return
            self._cloud_auto_restore_done.emit({
                "game_id": int(game_id),
                "game_name": game_name,
                "generation": generation,
                "target_version": target_version,
                "success": bool(result.success),
                "error": str(result.error or ""),
                "guidance": str(result.guidance or ""),
            })

        handle.future.add_done_callback(_deliver)

    def _on_cloud_auto_restore_done(self, payload: object) -> None:
        """Finish an automatic restore on the GUI thread and re-derive status."""
        if not isinstance(payload, dict):
            return
        game_id = int(payload.get("game_id", 0) or 0)
        if getattr(self, "_closing", False):
            self._cloud_auto_restore_in_flight.pop(game_id, None)
            return
        marker = self._cloud_auto_restore_in_flight.get(game_id)
        expected = (
            int(payload.get("generation", -1)),
            payload.get("target_version"),
        )
        if marker != expected:
            return
        self._cloud_auto_restore_in_flight.pop(game_id, None)
        self._update_detail_launch_button(game_id)
        if not self.cloud_sync_coordinator.accepts(expected[0]):
            return

        game_name = str(payload.get("game_name", "this game"))
        if payload.get("success"):
            self._show_toast(f"Newest cloud save restored for '{game_name}'.")
            self.request_cloud_recheck(
                [game_id],
                "automatic-restore-complete",
                # Re-read and render the authoritative status, but do not
                # immediately feed a non-converged result back into restore.
                # The normal poll path can handle a genuinely new snapshot.
                auto_sync=False,
            )
            return

        message = str(payload.get("error") or "Cloud restore failed.")
        guidance = str(payload.get("guidance") or "")
        self._show_toast(
            f"{message} Local save preserved. {guidance}".strip(),
            is_error=True,
        )
        # Recheck without launching another restore loop after a failure.
        self.request_cloud_recheck([game_id], "automatic-restore-failed")

    def _update_detail_panel(self):
        """Update left panel with current selected game details and trigger smooth slide animation."""
        game = self.selected_game
        if not game:
            if self.library_view_mode not in ("compact", "steam"):
                self._animate_left_panel(False)
            self.hero_bg.set_hero_image(None)
            return

        game_id, name, path, exe, mode, banner_url, steam_id = game[:7]
        steam_id = normalize_steam_app_id(steam_id)
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

        if self._automatic_network_allowed() and not hero_cache_path:
            self._request_managed_hero_artwork(
                game_id,
                name,
                steam_id,
                full_exe,
                priority=RequestPriority.CRITICAL,
            )

        self.detail_panel.setVisible(False)
        self.btn_reveal_detail.setVisible(False)
        return

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
        cloud_context = self.cloud_status_service.current_context()
        cached_save = self.cloud_save_status_cache.get(game_id)
        now = time.time()
        last_checked = self.save_state_store.checked_at(game_id)
        is_stale = (now - last_checked) > 1800  # 30 mins

        network_allowed = cloud_context.network_allowed
        cloud_auth_required = (
            network_allowed
            and cloud_context.backend_active
            and not cloud_context.authentication_configured
        )
        if not network_allowed:
            self._mark_cloud_offline([game_id])
            cached_save = self.cloud_save_status_cache.get(game_id)
            is_stale = False
        elif cloud_auth_required:
            self._mark_cloud_auth_required([game_id])
            cached_save = self.cloud_save_status_cache.get(game_id)
            is_stale = False

        if MainWindow._cloud_auto_sync_in_flight(self, game_id):
            cached_local = cached_save[1] if cached_save else None
            cached_cloud = cached_save[2] if cached_save else None
            self._render_cloud_status(
                game_id,
                SyncStatus.SYNCING,
                cached_local,
                cached_cloud,
                stale=False,
            )
        elif cached_save is not None:
            c_status, c_local, c_cloud = cached_save
            self._render_cloud_status(
                game_id,
                c_status,
                c_local,
                c_cloud,
                stale=is_stale or c_status in {
                    SyncStatus.CLOUD_OFFLINE,
                    SyncStatus.CLOUD_UNAVAILABLE,
                },
            )
        else:
            if not network_allowed:
                self.detail_cloud_status.setText("Cloud Save: Offline mode")
                self.detail_cloud_status.setToolTip("Offline mode is enabled; using local/cached data only.")
            else:
                self.detail_cloud_status.setText("Cloud Save: Checking...")
                self.detail_cloud_status.setToolTip("Checking save sync status...")
            if hasattr(self, "detail_cloud_metadata"):
                self.detail_cloud_metadata.clear()
                self.detail_cloud_metadata.setVisible(False)
            if hasattr(self, "btn_detail_cloud_restore"):
                self.btn_detail_cloud_restore.hide()

        cached_cloud_status = cached_save[0] if cached_save else None
        cloud_status_needs_refresh = cached_cloud_status in {
            SyncStatus.CLOUD_OFFLINE,
            SyncStatus.CLOUD_UNAVAILABLE,
        }
        if network_allowed and not cloud_auth_required and not MainWindow._cloud_auto_sync_in_flight(self, game_id) and (
            cached_save is None or is_stale or cloud_status_needs_refresh
        ):
            self._set_detail_cloud_checking()
            self._spawn_status_fetchers(
                [(game_id, name, path or "", str(steam_id or ""))],
                lambda gid, status, local, cloud: self._on_cloud_save_status_calculated(
                    gid,
                    status,
                    local,
                    cloud,
                    auto_sync=True,
                ),
                "detail",
                generation=self.cloud_sync_coordinator.generation,
                force=True,
            )

        local_build_id = game[11] if len(game) > 11 and game[11] else ""
        local_build_date = game[20] if len(game) > 20 and game[20] else 0
        manifest_build, manifest_date = read_local_steam_build(path, str(steam_id or ""))
        local_build_id = manifest_build or local_build_id
        local_build_date = manifest_date or local_build_date
        if manifest_build or manifest_date:
            self.library_service.set_build_reference(game_id, local_build_id, local_build_date)
        self.local_version_by_game_id[game_id] = (local_build_id, local_build_date)
        self.lbl_detail_update.setText("Checking Steam…" if network_allowed else "Offline mode")
        self._render_update_date_detail(0, 0, False)
        self.lbl_detail_update.setStyleSheet("background: #1f2937; color: #d1d5db; border: 1px solid #4b5563; border-radius: 6px; padding: 4px 8px; font-size: 10px; font-weight: bold;")
        self.lbl_detail_versions.setText(
            "Checking current and Steam versions…"
            if network_allowed else "Offline mode — cached data only"
        )
        self.lbl_detail_versions.setToolTip("")
        self.btn_retry_steam.setVisible(False)
        self.latest_checked_build_id = ""
        self.latest_checked_build_date = 0
        steam_last_checked = self.library_metadata_state.steam_build_checked_at.get(game_id, 0)
        steam_is_stale = (now - steam_last_checked) > 7200  # 2 hours

        steam_id = normalize_steam_app_id(steam_id)
        if (network_allowed and steam_id
                and (game_id not in self.metadata_attempted_builds or steam_is_stale)
                and (
                    self.request_manager is not None
                    or not any(
                        isinstance(fetcher, SteamBuildFetcher) and fetcher.game_id == game_id
                        for fetcher in self.metadata_fetchers
                    )
                )):
            self.metadata_attempted_builds.add(game_id)
            if self.request_manager is not None:
                self._request_managed_steam_build(
                    game_id,
                    str(steam_id),
                    local_build_id,
                    local_build_date,
                    priority=RequestPriority.CRITICAL,
                )
            else:
                fetcher = SteamBuildFetcher(
                    game_id,
                    steam_id,
                    local_build_id,
                    local_build_date,
                    parent=self,
                )
                fetcher.update_checked.connect(self._on_steam_build_checked)
                fetcher.check_failed.connect(self._on_steam_check_failed)
                self._track_metadata_fetcher(fetcher)
        else:
            cached_result = self.steam_check_results.get(game_id)
            if cached_result:
                cached_build, cached_date, cached_update, cached_error = cached_result
                if cached_build:
                    self._on_steam_build_checked(
                        game_id,
                        cached_build,
                        cached_date,
                        cached_update,
                        source="cached",
                        checked_at=self.library_metadata_state.steam_build_checked_at.get(game_id, 0.0),
                    )
                else:
                    self._on_steam_check_failed(game_id, cached_error or "Steam check unavailable")
            else:
                self.detail_update_widget.setVisible(False)
                self._render_update_date_detail(0, 0, False)
                self.lbl_detail_versions.setText(
                    "Offline mode — cached data only" if not network_allowed else ""
                )

        # Steam Tags Display & Auto Fetcher
        if tags_str:
            tags_list = [t.strip() for t in tags_str.split(",") if t.strip()]
            self._update_tags_pills(tags_list)
        # Existing cached tags must not prevent resolving the Steam AppID:
        # UMU needs GAMEID=umu-<appid> for Steamworks/protonfixes games.
        if network_allowed and not steam_id:
            if game_id not in self.metadata_attempted_tags and (
                self.request_manager is not None
                or not any(
                    isinstance(fetcher, SteamTagsFetcher) and fetcher.game_id == game_id
                    for fetcher in self.metadata_fetchers
                )
            ):
                self.metadata_attempted_tags.add(game_id)
                if self.request_manager is not None:
                    self._request_managed_steam_tags(
                        game_id,
                        name,
                        priority=RequestPriority.NORMAL,
                    )
                else:
                    fetcher = SteamTagsFetcher(game_id, name, parent=self)
                    fetcher.tags_found.connect(self._on_steam_tags_found)
                    self._track_metadata_fetcher(fetcher)
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
            last_checked = self.achievement_state.checked_at.get(game_id, 0)
            if game_id not in self.achievement_state.status or (now - last_checked) > 900:
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
            self.btn_detail_remove.setText("Manage Game")
            self.btn_detail_remove.setToolTip("Manage this uninstalled game or permanently delete it.")
            return

        if (
            MainWindow._cloud_auto_sync_in_flight(self, game_id)
            and game_id not in self.running_game_ids
        ):
            self.btn_detail_launch.setText("Syncing Cloud Save…")
            self.btn_detail_launch.setIcon(get_icon("ph.arrows-clockwise-bold", color="#6F7682"))
            self.btn_detail_launch.setIconSize(QSize(15, 15))
            self.btn_detail_launch.setEnabled(False)
            self.btn_detail_launch.setToolTip(
                "The newest cloud save is being downloaded before launch."
            )
            return

        self.btn_detail_remove.setText("Uninstall / Delete")
        self.btn_detail_remove.setToolTip(
            "Uninstall the game while keeping its record, or permanently delete it."
        )
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
        for tracker in self.launch_session_coordinator.trackers():
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
        if MainWindow._cloud_auto_sync_in_flight(self, game_id):
            self._show_toast("Please wait for the cloud save to finish syncing.")
            return
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
            game_env_vars = self.library_service.runtime_env_vars(game_id)
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
        """Resolve cloud-save state for a pending launch through the operation service."""
        game_name = ctx["game_name"]
        path = ctx["path"]
        steam_id = ctx["steam_id"]
        if not self._automatic_network_allowed():
            self._show_toast(f"Offline mode — launching '{game_name}' with local saves.")
            self._continue_launch(ctx)
            return
        self._show_toast(f"Checking cloud saves for '{game_name}'…")
        target = CloudOperationTarget(ctx["game_id"], game_name, path, steam_id)
        handle = self.cloud_operation_service.request_prelaunch_resolution(
            target,
            auto_prefer_newer=self.settings.value("auto_prefer_newer_saves", False, type=bool),
            auto_prefer_local=self.settings.value("auto_prefer_local_saves", False, type=bool),
            priority=RequestPriority.CRITICAL,
            tag="prelaunch",
        )

        def _deliver(future):
            payload = {"proceed": True, "needs_conflict": False, "toast": "", "ctx": ctx}
            try:
                resource = future.result()
                value = resource.value if resource.status == ResourceStatus.READY else None
            except Exception as exc:
                value = None
                payload["error"] = str(exc)
                payload["toast"] = f"Cloud check failed — launching '{game_name}' with local saves."
            if value is not None:
                preflight = value.get("preflight")
                status = value.get("status")
                local_stats = value.get("local_stats")
                cloud_stats = value.get("cloud_stats")
                payload.update({
                    "local_stats": local_stats,
                    "cloud_stats": cloud_stats,
                })
                if value.get("cloud_error") is not None:
                    error = value["cloud_error"]
                    payload.update({
                        "cloud_error": error,
                        "error": error.error,
                        "guidance": error.guidance,
                        "toast": f"Cloud check failed — launching '{game_name}' with local saves.",
                    })
                elif status == SyncStatus.CLOUD_AUTH_REQUIRED:
                    payload["toast"] = f"Cloud setup required — launching '{game_name}' with local saves."
                elif status in (SyncStatus.CLOUD_OFFLINE, SyncStatus.CLOUD_UNAVAILABLE):
                    payload["toast"] = f"Cloud not reachable — launching '{game_name}' with local saves."
                elif value.get("cloud_result") is not None:
                    result = value["cloud_result"]
                    payload["cloud_result"] = result
                    payload["toast"] = (
                        f"Restored cloud save for '{game_name}'." if result.success else
                        f"Cloud restore failed — launching '{game_name}' with local saves."
                    )
                    if not result.success:
                        payload.update({"error": result.error, "guidance": result.guidance})
                elif value.get("needs_cloud_only_prompt"):
                    payload["needs_cloud_only_prompt"] = True
                elif value.get("needs_conflict"):
                    payload["needs_conflict"] = True
                elif value.get("local_ready"):
                    payload["toast"] = f"Local saves ready for '{game_name}'."
                elif value.get("local_preferred"):
                    payload["toast"] = f"Local saves preferred for '{game_name}'."
            self._prelaunch_resolved.emit(payload)

        handle.future.add_done_callback(_deliver)

    def _queue_prelaunch_cloud_operation(
        self,
        ctx: dict,
        operation: str,
        success_toast: str,
        failure_toast: str,
        target_version=None,
    ) -> None:
        """Submit a conflict choice without letting the UI own the worker."""
        target = CloudOperationTarget(
            ctx["game_id"],
            ctx["game_name"],
            ctx["path"],
            ctx.get("steam_id", ""),
        )
        if operation == "restore":
            handle = self.cloud_operation_service.request_restore(
                target,
                priority=RequestPriority.CRITICAL,
                tag="prelaunch_conflict",
                target_version=target_version,
            )
        else:
            handle = self.cloud_operation_service.request_upload(
                target,
                priority=RequestPriority.CRITICAL,
                tag="prelaunch_conflict",
            )

        def _deliver(future):
            try:
                resource = future.result()
                result = resource.value if resource.status == ResourceStatus.READY else None
            except Exception as exc:
                result = None
                error = str(exc)
            else:
                error = ""
            payload = {
                "ctx": ctx,
                "ok": bool(result is not None and result.success),
                "cloud_result": result,
                "toast": success_toast if result is not None and result.success else failure_toast,
            }
            if result is None:
                payload.update({"error": error or "Cloud operation failed.", "guidance": "Check the cloud connection and try again."})
            elif not result.success:
                payload.update({"error": result.error, "guidance": result.guidance})
            self._prelaunch_restore_done.emit(payload)

        handle.future.add_done_callback(_deliver)

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
                    self._queue_prelaunch_cloud_operation(
                        ctx,
                        "restore",
                        f"Restored cloud save for '{ctx['game_name']}' — your previous save was kept as a local backup.",
                        f"Could not restore the cloud save for '{ctx['game_name']}' — launched with local saves.",
                        target_version=getattr(payload.get("cloud_stats"), "cloud_version", None),
                    )
                    return  # resume in _on_prelaunch_restore_done
                else:
                    # Keep local: upload in background, launch immediately
                    self._queue_prelaunch_cloud_operation(
                        ctx,
                        "upload",
                        "Overwrote cloud save with local version.",
                        "Could not upload the local save — launching with local state.",
                    )
                    return  # resume in _on_prelaunch_restore_done
            else:
                # Closing the conflict dialog cancels the launch — say so
                # instead of silently dropping the user's Play click.
                self._show_toast(f"Launch cancelled — resolve the save conflict for '{game_name}' first.")
                return

        elif payload.get("needs_cloud_only_prompt"):
            c_stats = payload.get("cloud_stats")
            should_restore = confirm_restore(
                self,
                game_name=game_name,
                target_path=ctx.get("path", ""),
                technical_details=(
                    f"Cloud save: {getattr(c_stats, 'display_path', 'Latest cloud save version')}"
                ),
                title="Restore latest cloud save",
            )
            if should_restore:
                self._queue_prelaunch_cloud_operation(
                    ctx,
                    "restore",
                    f"Restored cloud save for '{ctx.get('game_name', '')}'.",
                    f"Failed to restore cloud save for '{ctx.get('game_name', '')}'.",
                    target_version=getattr(c_stats, "cloud_version", None),
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
            launch_result = self.launch_session_coordinator.start(
                LaunchSessionContext(
                    game_id=game_id,
                    game_name=game_name,
                    path=path,
                    executable=exe,
                    mode=selected_mode,
                    steam_id=steam_id,
                    proton_path=selected_proton,
                    sandbox=sandbox,
                    env_vars=env_vars,
                )
            )
            if launch_result.already_running:
                self._stop_game(game_id)
                return
            process = launch_result.process
            if launch_result.started and process:
                logger.info(f"Successfully launched '{game_name}' (PID: {process.pid})")
                # The coordinator registers the authoritative session before
                # UI refresh; MainWindow only wires presentation callbacks.
                session_id = launch_result.session_id
                tracker = launch_result.tracker
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

                        cached_achs = self.achievement_persistence_service.schema(game_id)
                        # Launch is a bounded backfill point: reconcile local
                        # state even when the schema is already cached. The
                        # compatibility worker preserves its historical icon
                        # behavior when no manager is available.
                        if self._automatic_network_allowed():
                            if self.request_manager is not None:
                                # Launch reconciliation uses the same
                                # manager-backed status resource as selection
                                # and polling. This shares in-flight work and
                                # keeps persistence on the managed path.
                                self.request_achievement_recheck([game_id], tag="launch")
                            else:
                                # Compatibility for embedded callers that
                                # construct a window without the manager.
                                from core.achievement_schema import SteamAchievementFetcherWorker

                                fetcher = SteamAchievementFetcherWorker(
                                    game_id,
                                    str(steam_id).strip(),
                                    game_path=path,
                                    proton_path=selected_proton or "",
                                    download_icons=not bool(cached_achs),
                                    parent=self,
                                )
                                fetcher.resolution_ready.connect(self._on_achievement_resolution_ready)
                                fetcher.schema_fetched.connect(self._on_achievement_schema_fetched)
                                self._track_metadata_fetcher(fetcher)

                        if game_id in self.achievement_watchers:
                            try:
                                old_watcher = self.achievement_watchers[game_id]
                                old_watcher.stop()
                                old_watcher.deleteLater()
                            except Exception:
                                pass

                        watcher = AchievementWatcher(game_id, str(steam_id).strip(), selected_proton or "", path or "", parent=self)
                        watcher.achievement_unlocked.connect(self._on_achievement_unlocked)
                        watcher.state_refreshed.connect(self._on_achievement_state_refreshed)
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
            cached = self.achievement_state.get_status(game_id)
            if cached:
                unlocked_count, total_count, pct, recent = cached
            else:
                projection = self.achievement_persistence_service.projection(game_id)
                unlocked_count = projection.unlocked_count
                total_count = projection.total_count
                pct = projection.percentage
                recent = list(projection.recent[:5])
                self.achievement_state.set_status(
                    game_id, (unlocked_count, total_count, pct, recent)
                )

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
                            badge_color = "rgba(48, 209, 88, 0.2)" if ach.get("verified") else "rgba(255, 159, 10, 0.2)"
                            b_lbl.setStyleSheet(f"background-color: {badge_color}; border-radius: 6px;")

                        d_name = html.escape(ach.get("display_name", ""))
                        d_desc = html.escape(ach.get("description", ""))
                        b_lbl.setAccessibleName(ach.get("display_name") or ach.get("api_name") or "Achievement badge")
                        b_lbl.setAccessibleDescription(ach.get("description") or "Unlocked achievement")
                        source = "Steam verified" if ach.get("verified") else "Local source · unverified"
                        source_color = "#30D158" if ach.get("verified") else "#FF9F0A"
                        b_lbl.setToolTip(f"<div style='background: #1C1C1E; color: #FFF; padding: 3px;'><b>{d_name}</b><br/><span style='color: #A1A1A6; font-size: 11px;'>{d_desc}</span><br/><span style='color: {source_color}; font-size: 10px;'>{source}</span></div>")
                        self.detail_ach_badges_layout.addWidget(b_lbl)
                    self.detail_ach_badges_layout.addStretch()
                else:
                    lbl_no_yet = QLabel("No badges unlocked yet")
                    lbl_no_yet.setStyleSheet("color: #636366; font-size: 10px; background: transparent;")
                    self.detail_ach_badges_layout.addWidget(lbl_no_yet)
                    self.detail_ach_badges_layout.addStretch()
            else:
                resolution = self.achievement_state.resolution(game_id)
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
                    unavailable.setMinimumWidth(0)
                    unavailable.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
                    unavailable.setToolTip(unavailable.text())
                    unavailable.setStyleSheet("color: #FF9F0A; font-size: 10px; background: transparent;")
                    self.detail_ach_badges_layout.addWidget(unavailable)
                    self.detail_ach_badges_layout.addStretch()
                elif resolution is not None and availability == "available":
                    self.lbl_detail_ach_count.setText("0 / available")
                    self.detail_ach_progress.setValue(0)
                    self.btn_detail_achievements.setText("Achievements · no unlock state")
                    self.btn_detail_achievements.setVisible(True)
                    self.detail_ach_card.setVisible(True)
                    while self.detail_ach_badges_layout.count() > 0:
                        item = self.detail_ach_badges_layout.takeAt(0)
                        if item.widget():
                            item.widget().deleteLater()
                    no_state = QLabel("Achievement definitions are available, but no local or verified unlock state has been found yet.")
                    no_state.setWordWrap(True)
                    no_state.setMinimumWidth(0)
                    no_state.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
                    no_state.setToolTip(no_state.text())
                    no_state.setStyleSheet("color: #AEAEB2; font-size: 10px; background: transparent;")
                    self.detail_ach_badges_layout.addWidget(no_state)
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
        dialog = AchievementsDialog(game, self.db, parent=self, request_manager=self.request_manager)
        dialog.exec()
        self.request_achievement_recheck([game[0]], tag="dialog_close")
        self._update_detail_panel()
        self._update_compact_game_page()

    def _open_achievement_profile(self):
        self._show_profile_page()

    def _show_profile_page(self):
        """Switch the central surface to the persistent owner profile page."""
        if getattr(self, "_profile_view_active", False):
            self.profile_page.show_owner()
            return
        self._profile_view_active = True
        self._profile_sidebar_visible = self.sidebar.isVisible()
        self._profile_footer_visible = self.footer_bar.isVisible()
        self.library_header_bar.hide()
        self.collection_banner.hide()
        self.scroll_area.hide()
        self.detail_panel.hide()
        self.btn_reveal_detail.hide()
        self.sidebar.hide()
        self.footer_bar.hide()
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(0)
        self.profile_page.show_owner()
        self.profile_page.show()

    def _close_profile_page(self):
        """Return from a profile page without changing the library mode."""
        if not getattr(self, "_profile_view_active", False):
            return
        self._profile_view_active = False
        self.profile_page.hide()
        self.scroll_area.show()
        self.footer_bar.setVisible(getattr(self, "_profile_footer_visible", True))
        self.sidebar.setVisible(getattr(self, "_profile_sidebar_visible", True))
        compact = self.library_view_mode in ("compact", "steam")
        self.library_header_bar.setVisible(not compact)
        self.collection_banner.setVisible(bool(self.collection_filter) and not compact)
        if compact:
            self.detail_panel.hide()
            self.btn_reveal_detail.hide()
            self.right_layout.setContentsMargins(0, 0, 0, 0)
            self.right_layout.setSpacing(0)
        else:
            self.right_layout.setContentsMargins(18, 14, 18, 14)
            self.right_layout.setSpacing(12)
            if self.selected_game:
                self._animate_left_panel(True)
            else:
                self.detail_panel.hide()
                self.btn_reveal_detail.show()
        self._update_detail_panel()

    def _open_public_profile_prompt(self):
        """Open the social hub focused on finding another profile."""
        self._open_friends_popup(focus_find=True)

    def _open_friends_popup(self, focus_find: bool = False) -> None:
        """Show the single custom friends surface used by header navigation."""
        from ui.dialogs.friends_dialog import FriendsDialog

        existing = getattr(self, "_friends_dialog", None)
        if existing is not None:
            try:
                if existing.isVisible():
                    existing.raise_()
                    existing.activateWindow()
                    return
            except RuntimeError:
                self._friends_dialog = None

        dialog = FriendsDialog(
            self.settings,
            self.central_auth,
            self,
            worker_registry=self.worker_supervisor,
            request_manager=self.request_manager,
            focus_find=focus_find,
        )
        self._friends_dialog = dialog

        def open_profile(handle: str) -> None:
            dialog.close()
            self._open_public_profile_handle(handle)

        def open_owner() -> None:
            dialog.close()
            self._open_achievement_profile()

        dialog.open_profile_requested.connect(open_profile)
        dialog.open_owner_profile_requested.connect(open_owner)
        try:
            dialog.exec()
        finally:
            if self._friends_dialog is dialog:
                self._friends_dialog = None

    def _open_public_profile_handle(self, handle: str):
        """Fetch and display a public profile without opening another window."""
        if not self._automatic_network_allowed() and self.request_manager is None:
            QMessageBox.information(
                self,
                "Public Profile",
                "Offline mode is enabled. Public profiles are unavailable until online mode is restored.",
            )
            return
        value = str(handle or "").strip().lstrip("@").lower()
        if not HANDLE_RE.fullmatch(value):
            QMessageBox.warning(self, "Public Profile", "That is not a valid SafeLauncher profile username.")
            return
        service_url = get_profile_service_url()
        profile_resources = self.profile_page.profile_resources
        configured = profile_resources.configured(service_url)
        if not configured:
            QMessageBox.information(
                self,
                "Public Profile Service",
                "The central profile gateway is not configured for this build. Set SAFELAUNCHER_PROFILE_SERVICE_URL only for an explicit development gateway.",
            )
            return
        self.profile_page.footer_status.setText("Loading public profile…")
        def _fetch_public_profile():
            return profile_resources.fetch_public(value, service_url)

        if self.request_manager is not None:
            self._public_profile_generation += 1
            generation = self._public_profile_generation
            key = RequestKey(
                "public-profile",
                f"{ProfileResourceService.endpoint_fingerprint(service_url)}:{value}",
            )
            loader = lambda token: (token.raise_if_cancelled(), _fetch_public_profile())[1]
            if self._public_profile_binding is not None:
                self._public_profile_binding.close()
                self._public_profile_binding.deleteLater()
                self._public_profile_binding = None
            if getattr(self.request_manager, "cache", None) is not None:
                handle = self.request_manager.request_cached(
                    key,
                    loader,
                    max_age_seconds=cache_policy("public-profile").max_age_seconds,
                    priority=RequestPriority.NORMAL,
                    generation=generation,
                    timeout_seconds=20,
                    content_type="application/json",
                )
            else:
                handle = self.request_manager.request(
                    key,
                    loader,
                    priority=RequestPriority.NORMAL,
                    generation=generation,
                    timeout_seconds=20,
                )
            self._public_profile_binding = bind_resource(
                self.request_manager,
                key,
                lambda result, generation=generation, key=key: self._on_managed_public_profile_state(
                    generation, key, result
                ),
                self,
                cancel_on_close=True,
            )
            return

        worker = self._profile_remote_tasks.start(
            "SafeLauncher-OpenPublicProfile",
            # Create the requests session in the worker that uses it.
            _fetch_public_profile,
            lambda document: self._on_public_profile_loaded(document),
        )
        worker.error_occurred.connect(lambda error: self._on_public_profile_error(error))

    def _on_public_profile_loaded(self, document: dict):
        self._show_profile_page()
        self.profile_page.show_public(document)

    def _on_managed_public_profile_state(self, generation: int, key: RequestKey, result) -> None:
        if generation != self._public_profile_generation:
            return
        if result.status in {ResourceStatus.READY, ResourceStatus.STALE} and isinstance(result.value, dict):
            self._on_public_profile_loaded(result.value)
            if result.status == ResourceStatus.STALE:
                self.profile_page.footer_status.setText(
                    "Showing cached public profile; refresh will retry when online."
                )
        elif result.status not in {ResourceStatus.CANCELLED, ResourceStatus.LOADING}:
            self._on_public_profile_error(str(result.error or "Public profile could not be loaded."))

    def _on_public_profile_error(self, error: str):
        QMessageBox.warning(self, "Public Profile", str(error))

    def _on_profile_changed(self):
        """Persist profile presentation metadata through the private ledger."""
        self._update_header_identity()
        if hasattr(self, "profile_page"):
            self.profile_page.mark_local_data_changed()
        self._sync_profile_metadata_async()

    def _on_private_profile_changed(self):
        """Persist a publish-state or handle change without republishing."""
        self._update_header_identity()
        self._sync_profile_metadata_async()

    def _sync_profile_metadata_async(self):
        db_path = getattr(self.db, "db_path", None)

        self._profile_sync_generation += 1
        generation = self._profile_sync_generation
        handle = self.cloud_metadata_service.request_profile(
            db_path,
            force=True,
            priority=RequestPriority.CRITICAL,
            generation=generation,
            tag="profile_change",
        )
        handle.future.add_done_callback(
            lambda future, generation=generation: self._profile_sync_done.emit(
                (generation, future)
            )
        )

    def _on_managed_profile_sync_done(self, payload: object) -> None:
        """Refresh the local projection after private cloud reconciliation."""
        generation, future = payload
        if generation != self._profile_sync_generation:
            return
        try:
            result = future.result()
        except Exception as error:
            logger.debug("Profile metadata sync failed: %s", error)
            return
        if result.status == ResourceStatus.READY and result.value:
            self._refresh_library()
            if (
                hasattr(self, "profile_page")
                and self.profile_page.isVisible()
                and getattr(self.profile_page, "_mode", "owner") == "owner"
            ):
                self.profile_page.show_owner()

    def _on_achievement_unlocked(self, game_id: int, app_id: str, data: dict):
        """Handle real-time achievement unlock event from watcher."""
        api_name = data.get("api_name", "")
        unlock_time = float(data.get("unlock_time", 0.0) or 0.0)
        if not api_name:
            return

        schema = self.achievement_persistence_service.schema(game_id)
        schema_has_api = any(a.get("api_name") == api_name for a in schema)
        if not schema_has_api:
            self.achievement_state.pending_for(game_id)[api_name] = unlock_time
            logger.info(
                "Queued achievement %s for game %s until its schema is available.", api_name, game_id
            )
            self.request_achievement_recheck([game_id], tag="realtime_schema")
            return

        # Database transition is the deduplication authority.  A duplicate
        # inotify/poll event must not emit a second toast or cloud sync.
        persistence = self.achievement_persistence_service.unlock(
            game_id,
            api_name,
            unlock_time,
            provenance=str(data.get("provenance", "local_emulator") or "local_emulator"),
            verified=bool(data.get("verified", False)),
            source_format=str(data.get("source_format", "") or ""),
            source_path=str(data.get("source_path", "") or ""),
        )
        # The service's row-level claim handles both the normal transition
        # and the race where a resolver persisted the same state first.
        if not persistence.claimed:
            return

        if hasattr(self, "profile_page"):
            self.profile_page.mark_local_data_changed()

        projection = persistence.projection
        self.achievement_state.set_status(
            game_id,
            (
                projection.unlocked_count,
                projection.total_count,
                projection.percentage,
                list(projection.recent),
            ),
        )
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

    def _on_achievement_state_refreshed(self, game_id: int, app_id: str, state: dict):
        """Persist a watcher snapshot as a silent, append-only reconciliation.

        The per-achievement signal drives notifications. This snapshot signal
        is a safety net for emulators that write several achievements in one
        atomic replacement or for an unlock that arrived before its schema was
        cached. The database/profile ledger remains the deduplication and
        append-only authority, so it cannot remove an earlier unlock.
        """
        if not state or not app_id:
            return
        try:
            persistence = self.achievement_persistence_service.record_state(
                game_id, app_id, state,
                provenance="local_emulator", verified=False,
                source_format="json", source_path="",
            )
            projection = persistence.projection
            self.achievement_state.set_status(
                game_id,
                (
                    projection.unlocked_count,
                    projection.total_count,
                    projection.percentage,
                    list(projection.recent),
                ),
            )
            if persistence.changed:
                if hasattr(self, "_sync_launcher_metadata_async"):
                    self._sync_launcher_metadata_async(game_id)
                if hasattr(self, "profile_page"):
                    self.profile_page.mark_local_data_changed()
            self._save_persistent_cache()
            if self.selected_game and self.selected_game[0] == game_id:
                steam_id = str(self.selected_game[6]).strip() if len(self.selected_game) > 6 and self.selected_game[6] else ""
                self._update_achievement_inspector(game_id, steam_id)
        except Exception as exc:
            logger.debug("Could not persist achievement state snapshot for game %s: %s", game_id, exc)

    def _on_achievement_schema_fetched(self, game_id: int, app_id: str, achievements: list):
        """Persist schema then commit live unlocks that arrived before it."""
        if not achievements:
            return
        self.achievement_persistence_service.save_schema(game_id, app_id, achievements)
        pending = self.achievement_state.pop_pending(game_id)
        for api_name, unlock_time in pending.items():
            self.achievement_persistence_service.arm_notification(game_id, api_name)
            self._on_achievement_unlocked(
                game_id, app_id, {"api_name": api_name, "unlock_time": unlock_time}
            )

    def _on_achievement_resolution_ready(self, game_id: int, app_id: str, resolution):
        """Apply one local-first resolution to the durable DB and inspector."""
        self.achievement_state.set_resolution(game_id, resolution)
        self.achievement_persistence_service.persist_resolution(game_id, app_id, resolution)

        # A watcher may report an unlock before the schema request completes.
        # Replaying through the normal DB transition keeps notifications
        # exactly-once while preserving the event.
        if getattr(resolution, "schema", None) and game_id in self.achievement_state.pending_unlocks:
            pending = self.achievement_state.pop_pending(game_id)
            for api_name, unlock_time in pending.items():
                self.achievement_persistence_service.arm_notification(game_id, api_name)
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
            self.library_service.update_runtime_settings(
                game[0], proton_path=os.path.realpath(path)
            )
            self._refresh_library()
            self._select_game_by_id(game[0])

    def _get_library_service(self) -> LibraryService | None:
        """Return the application library boundary, including test adapters.

        Production MainWindow always constructs ``library_service``.  The
        small fallback exists for compatibility hosts that call lifecycle
        helpers directly (for example, lightweight dialog/test hosts); even
        there, database mutations still pass through ``LibraryService``.
        """
        service = getattr(self, "library_service", None)
        if service is not None:
            return service
        database = getattr(self, "db", None)
        if database is None:
            return None
        state = getattr(self, "library_state", None)
        if state is None:
            state = LibraryStateStore(LibraryController())
        return LibraryService(database, state)

    def _on_restore_game(self):
        """Restore an archived game back to the active library."""
        game = self._get_selected_game()
        if not game:
            return
        game_id = game[0]
        library_service = self._get_library_service()
        restored = library_service.restore_game(game_id) if library_service else False
        if not restored:
            self._show_toast(f"Could not restore '{game[1]}' to the library.", is_error=True)
            return
        self._show_toast(f"Restored '{game[1]}' to library.")
        self._refresh_library()
        self._select_game_by_id(game_id)
        if hasattr(self, "_sync_launcher_metadata_async"):
            self._sync_launcher_metadata_async(game_id)

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

        modifiers = QApplication.keyboardModifiers()
        shift_pressed = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        decision = LaunchPolicy.decide(
            game,
            is_running=game[0] in self.running_game_ids,
            shift_pressed=shift_pressed,
        )
        if decision.action == LaunchAction.RESTORE:
            self._on_restore_game()
            return
        if decision.action == LaunchAction.STOP:
            game_id = game[0]
            self._stop_game(game_id)
            return

        game_id, name, path, exe, mode, banner_url, steam_id, *_ = (*game, 0)

        if not path or not os.path.exists(path):
            self._show_toast(f"Cannot launch '{name}'. Directory does not exist on disk.", is_error=True)
            return

        if decision.action == LaunchAction.LAUNCH:
            self._launch_mode(game_id, path, exe, decision.mode)
        else:
            dialog = LaunchOptionsDialog(game, self)
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_mode:
                if dialog.set_as_default_cb.isChecked():
                    self.library_service.update_runtime_settings(
                        game_id, mode=dialog.selected_mode
                    )
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
        total = self.library_service.record_playtime_finished(game_id)
        if game_id in self.banner_widgets:
            self.banner_widgets[game_id].set_playtime(total)
        self._update_detail_panel()
        if hasattr(self, "profile_page"):
            self.profile_page.mark_local_data_changed()

    def _on_playtime_checkpoint(self, session_id: str, elapsed_seconds: int):
        """Persist an in-progress session without changing the visible total."""
        self.library_service.checkpoint_playtime_session(session_id, elapsed_seconds)

    def _on_playtime_session_recorded(self, session_id: str, elapsed_seconds: int, ended_at: int, finalized: bool):
        """Finalize the idempotent session event used by cloud metadata sync."""
        game_id = self.library_service.checkpoint_playtime_session(
            session_id,
            elapsed_seconds,
            finalized=finalized,
            ended_at=ended_at,
        )
        if game_id is not None:
            self._sync_launcher_metadata_async(game_id)

    def _sync_launcher_metadata_async(self, game_id: int):
        """Sync launcher-owned metadata without blocking the GUI thread."""
        game = self.games_by_id.get(game_id)
        if not game:
            return
        name = game[1]
        app_id = str(game[6]).strip() if len(game) > 6 and game[6] else ""
        db_path = getattr(self.db, "db_path", None)
        target = CloudMetadataTarget(game_id, name, app_id)
        return self.cloud_metadata_service.request_latest_game(
            target,
            db_path,
            priority=RequestPriority.BACKGROUND,
            tag="game_metadata",
        )

    def _cleanup_tracker(self, tracker: PlaytimeTrackerThread):
        """Remove finished tracker from the list so it can be garbage collected."""
        session = self.launch_session_coordinator.finish_tracker(tracker)
        self._stopping_game_ids.discard(tracker.game_id)
        if tracker in self.playtime_trackers:
            self.playtime_trackers.remove(tracker)
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
                watcher = self.achievement_watchers[tracker.game_id]
                watcher.stop()
                watcher.deleteLater()
                del self.achievement_watchers[tracker.game_id]
            except Exception as ach_clean_err:
                logger.debug(f"Error stopping achievement watcher: {ach_clean_err}")

        # Recheck achievements upon game exit to persist any new unlocks
        self.request_achievement_recheck([tracker.game_id], tag="game_exit")
        # Some Wine/Goldberg builds flush achievement state during their final
        # shutdown sequence, after the tracked process has exited. Give that
        # write a short grace period and perform one bounded second read.
        QTimer.singleShot(
            1500,
            lambda game_id=tracker.game_id: self.request_achievement_recheck(
                [game_id], tag="game_exit_flush"
            ),
        )

        # Standby: if no games are running, stop automatic recorder so launcher stays idle
        if not self.running_game_ids:
            if getattr(self, "gpu_recorder_config", None) and self.gpu_recorder_config.enabled:
                if self.gpu_recorder_config.mode in ("replay_buffer", "auto_game"):
                    rec_svc = GpuRecorderService.instance()
                    if rec_svc.is_running():
                        rec_svc.stop_recording()
                        logger.info("GPU recorder put on standby (all games closed)")

        # Offline mode must also cover the automatic exit upload. Otherwise
        # closing a game would create a cloud worker after all visible UI work
        # had already stopped, which is exactly the kind of hidden operation
        # that makes an offline shutdown appear hung.
        if not self._automatic_network_allowed():
            return

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

                target = CloudOperationTarget(tracker.game_id, g_name, g_path, g_steam_id)
                handle = self.cloud_exit_sync_service.request(
                    target,
                    settle_seconds=0.5,
                    priority=RequestPriority.BACKGROUND,
                    tag="game_exit",
                )

                def _deliver(future):
                    result = self.cloud_exit_sync_service.resolve(target, future)
                    self._save_op_done.emit(result)

                handle.future.add_done_callback(_deliver)
                # _save_op_done → _on_exit_save_sync_done is connected once in
                # __init__; concurrent exits each carry their own payload.
        except Exception as sync_exit_err:
            logger.warning(f"Auto cloud save sync on game exit failed: {sync_exit_err}")

    def _on_exit_save_sync_done_legacy(self, payload: dict):
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
        elif outcome == "restored":
            self._show_toast(f"Newest cloud save restored for '{name}'.")
            if self.selected_game and self.selected_game[1] == name:
                self._update_detail_panel()
            gid = payload.get("game_id")
            if gid is not None:
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

    def _on_exit_save_sync_done(self, payload):
        """GUI-thread slot applying service-owned exit-sync presentation."""
        if isinstance(payload, dict):
            # Keep compatibility with older signal emitters during migration.
            result = CloudExitSyncResult(
                game_id=int(payload.get("game_id", 0)),
                game_name=str(payload.get("game", "")),
                outcome=str(payload.get("outcome", "failed")),
                reason=str(payload.get("reason", "")),
                error=str(payload.get("error", "")),
                guidance=str(payload.get("guidance", "")),
                error_category=str(payload.get("error_category", "")),
            )
        else:
            result = payload
        if not isinstance(result, CloudExitSyncResult):
            logger.warning("Ignoring malformed exit cloud-save result")
            return

        presentation = result.presentation()
        if result.outcome == "failed":
            logger.warning("Exit cloud-save upload failed for '%s'.", result.game_name)
        elif result.outcome == "restored":
            logger.info(
                "Restored the newest cloud save for '%s' after game exit.",
                result.game_name,
            )

        if presentation.message:
            self._show_toast(
                presentation.message,
                is_error=presentation.kind == "error",
            )
        if presentation.refresh_detail:
            if self.selected_game and self.selected_game[1] == result.game_name:
                self._update_detail_panel()
        if presentation.refresh_status and result.game_id:
            # The upload changed the cloud verdict; refresh the badge instead
            # of leaving the pre-upload state cached.
            self.refresh_cloud_status_for_game(result.game_id)

    def request_cloud_recheck(
        self,
        game_ids=None,
        reason: str = "",
        *,
        auto_sync: bool = False,
    ):
        """THE single entry point for cloud status re-checks.

        Every feature routes through here — startup scan, poll timer, settings
        and account dialogs, game properties, exit uploads — so the re-check
        policy (backend gating, duplicate suppression, in-flight limits,
        prioritisation) is defined exactly once and behaves predictably.

        game_ids=None  -> every game (startup, cloud config change)
        game_ids=[]    -> only games whose cloud copy changed (listing diff)
        game_ids=[…]   -> exactly these games (after uploads, restores, edits)
        """
        if not MainWindow._request_manager_accepts_work(self):
            logger.debug("Ignoring cloud recheck during launcher shutdown")
            return
        context = self.cloud_status_service.current_context()
        if not context.network_allowed:
            if game_ids is None or game_ids:
                self._mark_cloud_offline(game_ids)
            return
        if not context.backend_active:
            return
        # The SafeLauncherCloud API deliberately fails closed without its
        # client secret. Do not start one worker per game just to receive the
        # same 401; render a useful setup state synchronously instead.
        if not context.authentication_configured:
            if game_ids is None:
                self._mark_cloud_auth_required()
            elif game_ids:
                self._mark_cloud_auth_required(game_ids)
            return
        tag = f" ({reason})" if reason else ""
        # Explicit recovery and user actions must not be satisfied by the
        # resource-cache entry created before the action. Startup and polling
        # retain normal TTL behavior to avoid unnecessary network traffic.
        force_refresh = reason not in {"", "startup", "poll", "listing-diff"}
        generation = context.generation
        games_snapshot = [
            (g[0], g[1], g[2], str(g[6]).strip() if len(g) > 6 and g[6] else "")
            for g in list(self.games)
        ]
        targets_snapshot = [CloudStatusTarget(int(gid), str(name), str(path or ""), str(steam_id or ""))
                            for gid, name, path, steam_id in games_snapshot]

        if game_ids is None:
            plan = self.cloud_status_service.plan_recheck(
                targets_snapshot,
                None,
                reason=reason,
            )
            targets = [
                (target.game_id, target.game_name, target.game_path, target.steam_id)
                for target in plan.targets
            ]
            if not targets:
                logger.info(f"Cloud recheck{tag}: nothing to scan.")
                return
            self._spawn_status_fetchers(
                targets,
                lambda gid, status, local, cloud, g=generation, should_auto_sync=auto_sync:
                self._accept_cloud_status_for_context(
                    g,
                    gid,
                    status,
                    local,
                    cloud,
                    auto_sync=should_auto_sync,
                ),
                tag,
                generation=generation,
                force=force_refresh,
                on_batch_complete=lambda results, g=generation: self._managed_cloud_batch_done.emit(
                    (g, results)
                ),
            )
            return

        if game_ids:
            by_id = {g[0]: g for g in games_snapshot}
            plan = self.cloud_status_service.plan_recheck(
                targets_snapshot,
                game_ids,
                reason=reason,
            )
            callback = self._on_cloud_save_status_calculated
            if auto_sync:
                callback = lambda gid, status, local, cloud: self._on_cloud_save_status_calculated(
                    gid,
                    status,
                    local,
                    cloud,
                    auto_sync=True,
                )
            self._spawn_status_fetchers(
                [(target.game_id, target.game_name, target.game_path, target.steam_id)
                 for target in plan.targets if target.game_id in by_id],
                callback,
                tag,
                generation=generation,
                force=force_refresh,
            )
            return

        # Changed-only: diff a fresh listing against the cached statuses on a
        # service, then fetch full statuses for the games that moved.
        def _finish_diff(changed, expected_generation=generation):
            if not self.cloud_sync_coordinator.accepts(expected_generation):
                logger.debug(
                    "Discarded cloud listing diff from retired context %s",
                    expected_generation,
                )
                return
            if changed:
                self._cloud_poll_changed.emit([
                    (target.game_id, target.game_name, target.game_path, target.steam_id)
                    for target in changed
                ])

        self.cloud_status_service.request_changed_diff(
            targets_snapshot,
            generation=generation,
            on_complete=_finish_diff,
            force=True,
        )

    def _mark_cloud_auth_required(self, game_ids=None):
        """Show cloud setup guidance without issuing doomed HTTP requests."""
        from core.cloud_models import SyncStatus

        if game_ids is None:
            target_ids = [int(game[0]) for game in self.games]
        else:
            target_ids = [int(game_id) for game_id in game_ids]
        generation = self.cloud_sync_coordinator.generation
        changed = False
        status = SyncStatus.CLOUD_AUTH_REQUIRED

        changed_ids = self.cloud_status_service.mark_status(
            target_ids,
            status,
            generation=generation,
        )

        for game_id in changed_ids:
            if game_id not in self.games_by_id:
                continue
            changed = True
            current = self.game_status_by_id.get(game_id, GameStatusState())
            self.game_status_by_id[game_id] = replace(
                current,
                cloud_status=status,
                # Keep the last-known statistics attached to the offline
                # verdict; connectivity and freshness are separate from the
                # most recent successful comparison.
                local_stats=current.local_stats,
                cloud_stats=current.cloud_stats,
                cloud_checked_at=self.save_state_store.checked_at(game_id),
            )
            self._render_cloud_status(game_id, status)
        if changed:
            self._save_persistent_cache()

    def _mark_cloud_offline(self, game_ids=None):
        """Render a stable offline verdict without touching the network."""
        from core.cloud_models import SyncStatus

        if game_ids is None:
            target_ids = [int(game[0]) for game in self.games]
        else:
            target_ids = [int(game_id) for game_id in game_ids]
        generation = self.cloud_sync_coordinator.generation
        changed = False
        status = SyncStatus.CLOUD_OFFLINE

        changed_ids = self.cloud_status_service.mark_status(
            target_ids,
            status,
            generation=generation,
        )

        for game_id in changed_ids:
            if game_id not in self.games_by_id:
                continue
            changed = True
            current = self.game_status_by_id.get(game_id, GameStatusState())
            self.game_status_by_id[game_id] = replace(
                current,
                cloud_status=status,
                local_stats=current.local_stats,
                cloud_stats=current.cloud_stats,
                cloud_checked_at=self.save_state_store.checked_at(game_id),
            )
            self._render_cloud_status(game_id, status)
        if changed:
            self._save_persistent_cache()

    def _spawn_status_fetchers(
        self,
        targets: list,
        on_result,
        tag: str = "",
        generation=None,
        on_batch_complete=None,
        force: bool = False,
    ):
        """Request per-game cloud statuses through the shared manager."""
        if not MainWindow._request_manager_accepts_work(self):
            return
        if not self._automatic_network_allowed():
            return
        if generation is None:
            generation = self.cloud_sync_coordinator.generation

        if self.request_manager is None:
            logger.error("Cloud status refresh requested without the application RequestManager")
            return
        status_targets = []
        priority = (
            RequestPriority.CRITICAL
            if "detail" in tag.lower()
            else RequestPriority.NORMAL
        )
        for item in targets:
            if isinstance(item, CloudStatusTarget):
                target = item
            else:
                gid, name, path, steam_id = item
                target = CloudStatusTarget(
                    int(gid),
                    str(name),
                    str(path or ""),
                    str(steam_id or ""),
                )
            status_targets.append(target)
            spec = self.cloud_status_service.status_spec(
                target,
                priority=priority,
                generation=generation,
                tag=tag,
            )
            key = spec.key
            self._cloud_status_callbacks.setdefault(key, []).append(
                (generation, target.game_id, on_result)
            )
            self._cloud_status_target_ids[key] = target.game_id
            if key not in self._cloud_status_bindings:
                self._cloud_status_bindings[key] = bind_resource(
                    self.request_manager,
                    key,
                    lambda result, key=key: self._on_managed_cloud_status_state(
                        key, result
                    ),
                    self,
                    cancel_on_close=True,
                )

        try:
            self.cloud_status_service.request_many(
                status_targets,
                priority=priority,
                generation=generation,
                tag=tag,
                force=force,
                on_complete=on_batch_complete,
            )
        except RuntimeError as exc:
            # closeEvent can retire the shared manager between a queued Qt
            # callback and this submission.  Late UI work is harmless and
            # must not become an unhandled exception during shutdown.
            if "shut down" in str(exc).lower() or getattr(self, "_closing", False):
                logger.debug("Cloud status request ignored during shutdown: %s", exc)
                return
            raise

    def _on_managed_cloud_status_state(self, key: RequestKey, result) -> None:
        """Apply a managed cloud status only on the current UI generation."""
        callback_data = list(self._cloud_status_callbacks.get(key, ()))
        if not callback_data:
            return
        if result.status in {ResourceStatus.IDLE, ResourceStatus.LOADING}:
            return

        # A stale notification without an error is the normal first phase of
        # stale-while-revalidate. Keep subscribers until the network result
        # arrives. A stale notification carrying an error is terminal.
        if result.status == ResourceStatus.STALE and result.error is None:
            return

        if result.status != ResourceStatus.READY:
            status = self._cloud_status_failure_status(result)
            self._deliver_managed_cloud_status(
                key,
                callback_data,
                status,
                None,
                None,
                error=result.error or result.status.value,
            )
            return

        status = None
        local_stats = None
        cloud_stats = None
        if isinstance(result.value, CloudStatusResult):
            if result.value.error is not None:
                logger.debug(
                    "Managed cloud status result failed for game %s: %s",
                    self._cloud_status_target_ids.get(key, "unknown"),
                    result.value.error.error,
                )
                failure = self._cloud_status_failure_status(
                    result,
                    domain_error=result.value.error,
                )
                self._deliver_managed_cloud_status(
                    key,
                    callback_data,
                    failure,
                    None,
                    None,
                    error=result.value.error.error,
                )
                return
            status = result.value.status
            local_stats = result.value.local_stats
            cloud_stats = result.value.cloud_stats
        elif isinstance(result.value, tuple) and len(result.value) == 3:
            # Compatibility with any already-completed request submitted by
            # an older embedding caller during the service migration.
            status, local_stats, cloud_stats = result.value
        if status is None:
            failure = self._cloud_status_failure_status(result)
            self._deliver_managed_cloud_status(
                key,
                callback_data,
                failure,
                None,
                None,
                error="invalid cloud status payload",
            )
            return
        self._deliver_managed_cloud_status(
            key,
            callback_data,
            status,
            local_stats,
            cloud_stats,
        )

    def _deliver_managed_cloud_status(
        self,
        key: RequestKey,
        callback_data,
        status,
        local_stats,
        cloud_stats,
        *,
        error=None,
    ) -> None:
        """Fan out one terminal resource state and retire its subscribers."""
        try:
            for generation, game_id, callback in callback_data:
                if not self.cloud_sync_coordinator.accepts(generation):
                    logger.debug(
                        "Discarded cloud status for game %s from retired context %s",
                        game_id,
                        generation,
                    )
                    continue
                if error is not None:
                    logger.debug(
                        "Managed cloud status request failed for game %s: %s",
                        game_id,
                        error,
                    )
                try:
                    callback(game_id, status, local_stats, cloud_stats)
                except Exception:
                    logger.exception(
                        "Cloud status consumer failed for game %s",
                        game_id,
                    )
        finally:
            current = self._cloud_status_callbacks.get(key, [])
            if current[:len(callback_data)] == callback_data:
                remaining = current[len(callback_data):]
            else:
                remaining = [item for item in current if item not in callback_data]
            if remaining:
                self._cloud_status_callbacks[key] = remaining
            else:
                self._cloud_status_callbacks.pop(key, None)

    def _cloud_status_failure_status(self, result, *, domain_error=None):
        """Map every managed request failure to a final cloud-save verdict."""
        if result.status == ResourceStatus.OFFLINE or not self._automatic_network_allowed():
            return SyncStatus.CLOUD_OFFLINE
        category = str(
            getattr(domain_error, "category", "")
            or getattr(result, "error_category", "")
            or ""
        ).strip().lower().replace("-", "_")
        if result.status == ResourceStatus.AUTHENTICATION_REQUIRED or category in {
            "auth",
            "authentication",
            "authentication_required",
            "auth_required",
            "unauthorized",
        }:
            return SyncStatus.CLOUD_AUTH_REQUIRED
        return SyncStatus.CLOUD_UNAVAILABLE

    def _on_managed_cloud_batch_done(self, payload: object) -> None:
        generation, results = payload
        if not self.cloud_sync_coordinator.accepts(generation):
            return
        uploaded = []
        newer_in_cloud = []
        for result in results if isinstance(results, list) else []:
            if result.status != ResourceStatus.READY:
                continue
            if isinstance(result.value, CloudStatusResult):
                if result.value.error is not None:
                    continue
                status = result.value.status
            elif isinstance(result.value, tuple) and result.value:
                status = result.value[0]
            else:
                continue
            game_id = self._cloud_status_target_ids.get(result.key)
            name = ""
            if game_id is not None:
                game = self.games_by_id.get(game_id)
                name = game[1] if game and len(game) > 1 else ""
            if status == SyncStatus.LOCAL_NEWER:
                uploaded.append(name)
            elif status in (SyncStatus.CLOUD_NEWER, SyncStatus.CLOUD_ONLY):
                newer_in_cloud.append(name)
        self._on_cloud_batch_finished(uploaded, newer_in_cloud)

    def _close_managed_cloud_status_bindings(self) -> None:
        for binding in self._cloud_status_bindings.take_all():
            binding.close()
            binding.deleteLater()
        self._cloud_status_callbacks.clear()
        self._cloud_status_target_ids.clear()

    def _start_background_cloud_sync(self):
        """Startup cloud save check & sync queue across the library."""
        if not self._automatic_network_allowed():
            return
        self.request_cloud_recheck(None, "startup", auto_sync=True)

    def _on_cloud_batch_finished(self, uploaded: list, newer_in_cloud: list):
        """GUI-thread slot when library background cloud batch queue completes."""
        # Uploads and restores are now queued automatically by each terminal
        # status callback. The operation completion handler owns user feedback;
        # this batch-level hook deliberately stays quiet to avoid stale
        # "ready to sync" messages after an operation has already started.

    def _start_cloud_poll_timer(self):
        """Start service-owned periodic listing checks.

        The method name remains as a compatibility entry point for startup and
        settings code; the timer and polling lifecycle now belong to
        ``CloudStatusPollingService``.
        """
        if self._automatic_network_allowed():
            self.cloud_status_polling.start()

    def _poll_cloud_for_changes(self):
        """Compatibility hook for older callers that manually trigger polls."""
        self.cloud_status_polling.poll_once(reason="poll")

    def _cloud_status_targets_snapshot(self) -> tuple[CloudStatusTarget, ...]:
        """Return a detached target snapshot for the polling service."""
        return tuple(
            CloudStatusTarget(
                int(game[0]),
                str(game[1] or ""),
                str(game[2] or "") if len(game) > 2 else "",
                str(game[6] or "") if len(game) > 6 else "",
            )
            for game in list(self.games)
        )

    def _on_cloud_poll_changed(self, changed: list):
        """GUI-thread: re-derive full status for games whose cloud copy changed."""
        if not self._automatic_network_allowed():
            return
        self._spawn_status_fetchers(changed, self._on_polled_cloud_status, "poll")

    def _on_polled_cloud_status(self, game_id: int, status, local_stats, cloud_stats):
        prev = self.cloud_save_status_cache.get(game_id)
        prev_status = prev[0] if prev else None
        is_running = game_id in self.running_game_ids
        self._on_cloud_save_status_calculated(
            game_id,
            status,
            local_stats,
            cloud_stats,
            auto_sync=not is_running,
        )
        if status == SyncStatus.CLOUD_NEWER and prev_status != SyncStatus.CLOUD_NEWER:
            if is_running:
                # Exit sync already handles the collision for this session;
                # never replace files underneath a running game.
                return
            name = self.games_by_id.get(game_id, (None, ""))[1]
            self._show_toast(
                f"Syncing newest cloud save for '{name}'…"
            )

    def _ensure_managed_achievement_binding(self, key: RequestKey) -> None:
        if self.achievement_coordinator.binding(key) is not None:
            return
        binding = bind_resource(
            self.request_manager,
            key,
            lambda result, key=key: self._on_managed_achievement_state(key, result),
            self,
            cancel_on_close=True,
        )
        self.achievement_coordinator.attach_binding(key, binding)

    def _on_managed_achievement_state(self, key: RequestKey, result) -> None:
        if result.status == ResourceStatus.CANCELLED:
            return
        if result.status != ResourceStatus.READY or not isinstance(result.value, tuple):
            if result.status in {
                ResourceStatus.ERROR,
                ResourceStatus.OFFLINE,
                ResourceStatus.UNAVAILABLE,
                ResourceStatus.AUTHENTICATION_REQUIRED,
                ResourceStatus.PERMISSION_DENIED,
                ResourceStatus.CONFLICT,
            }:
                logger.debug(
                    "Managed achievement request failed for %s: %s",
                    key,
                    result.error or result.status.value,
                )
            return
        callback_data = self.achievement_coordinator.callback_data(key)
        if callback_data is None:
            return
        game_id, app_id = callback_data
        resolution, unlocked_count, total_count, pct, recent = result.value
        self._on_achievement_resolution_ready(game_id, app_id, resolution)
        self._on_achievement_status_calculated(
            game_id, unlocked_count, total_count, pct, recent
        )

    def _on_managed_achievement_batch_done(self, results: object) -> None:
        self.achievement_coordinator.set_batch_in_flight(False)
        total_games = 0
        total_unlocked = 0
        for result in results if isinstance(results, list) else []:
            if result.status != ResourceStatus.READY or not isinstance(result.value, tuple):
                continue
            _resolution, unlocked_count, total_count, _pct, _recent = result.value
            if total_count > 0:
                total_games += 1
                total_unlocked += unlocked_count
        self._on_achievement_batch_finished(total_games, total_unlocked)

    def _close_managed_achievement_bindings(self) -> None:
        for binding in self.achievement_coordinator.close():
            binding.close()
            binding.deleteLater()

    def request_achievement_recheck(self, game_ids: Optional[list] = None, tag: str = ""):
        """Queue background achievement schema fetching and local unlock sync.

        game_ids:
          None  -> full library scan (startup, bulk reload).
          [id]  -> targeted recheck for specific game(s) (post-launch, selection).
        """
        if not self._automatic_network_allowed():
            logger.debug("Achievement recheck skipped: offline mode is enabled.")
            return
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
                cached = self.achievement_state.get_status(gid)
                if cached is None:
                    uncached.append(g)
                elif self.achievement_state.is_stale(gid, 3600, now=now):
                    stale.append(g)
                else:
                    fresh.append(g)
            targets = uncached + stale + fresh
            if not targets:
                logger.debug(f"Achievement recheck{tag}: no games with Steam IDs to scan.")
                return
            if self.request_manager is not None:
                if self.achievement_coordinator.batch_in_flight:
                    logger.debug(f"Achievement recheck{tag} skipped: batch already running.")
                    return
                self.achievement_coordinator.set_batch_in_flight(True)
                achievement_targets = []
                for game in targets:
                    if len(game) < 3:
                        continue
                    game_id, name, path = game[0], game[1], game[2]
                    app_id = str(game[6]).strip() if len(game) > 6 and game[6] else ""
                    proton_path = str(game[12]).strip() if len(game) > 12 and game[12] else ""
                    if not app_id:
                        continue
                    target = AchievementTarget(
                        int(game_id), app_id, str(path or ""), proton_path
                    )
                    plan = self.achievement_coordinator.prepare(target)
                    self._ensure_managed_achievement_binding(plan.key)
                    achievement_targets.append(target)
                if not achievement_targets:
                    self.achievement_coordinator.set_batch_in_flight(False)
                    return
                self.achievement_resource_service.request_many(
                    achievement_targets,
                    db_path=getattr(self.db, "db_path", None),
                    priority=(
                        RequestPriority.CRITICAL
                        if "running" in tag.lower() or "realtime" in tag.lower()
                        else RequestPriority.NORMAL
                    ),
                    tag=tag,
                    on_complete=lambda results: self._managed_achievement_batch_done.emit(
                        results
                    ),
                )
                return
            if any(
                f.isRunning() and f.__class__.__name__ in {
                    "AchievementBatchQueueWorker", "AchievementStatusFetcherThread", "SteamAchievementFetcherWorker"
                }
                for f in self.metadata_fetchers
            ):
                logger.debug(f"Achievement recheck{tag} skipped: batch worker already running.")
                return
            db_path = getattr(self.db, "db_path", None)
            worker = AchievementBatchQueueWorker(
                targets, max_workers=3, db_path=db_path, parent=self,
                request_manager=self.request_manager,
            )
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
                if self.request_manager is not None:
                    target = AchievementTarget(
                        int(gid), g_steam_id, str(g_path or ""), g_proton_path
                    )
                    self.achievement_resource_service.invalidate(target)
                    plan = self.achievement_coordinator.prepare(target)
                    key = plan.key
                    self._ensure_managed_achievement_binding(key)
                    self.achievement_resource_service.request_status(
                        target,
                        db_path=getattr(self.db, "db_path", None),
                        priority=RequestPriority.CRITICAL,
                        tag=tag,
                    )
                    continue
                if any(
                    f.isRunning()
                    and f.__class__.__name__ in {"AchievementStatusFetcherThread", "SteamAchievementFetcherWorker"}
                    and getattr(f, "game_id", None) == gid
                    for f in self.metadata_fetchers
                ):
                    continue
                from core.achievement_coordinator import invalidate
                invalidate(g_steam_id, g_path or "", g_proton_path)
                db_path = getattr(self.db, "db_path", None)
                fetcher = AchievementStatusFetcherThread(
                    gid,
                    g_name,
                    g_path or "",
                    g_steam_id,
                    g_proton_path,
                    db_path=db_path,
                    parent=self,
                    request_manager=self.request_manager,
                )
                fetcher.resolution_ready.connect(self._on_achievement_resolution_ready)
                fetcher.achievement_status_calculated.connect(self._on_achievement_status_calculated)
                self._track_metadata_fetcher(fetcher)
                # _track_metadata_fetcher owns and starts the targeted worker,
                # matching the full-library queue path above.

    def _start_background_achievement_sync(self):
        """Start bounded achievement monitoring without a library-wide scan."""
        if not self._automatic_network_allowed():
            if self._achievement_poll_timer is not None:
                self._achievement_poll_timer.stop()
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
        self.achievement_state.set_status(
            game_id, (unlocked_count, total_count, pct, recent)
        )
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
        self._closing = False
        self.setEnabled(True)
        self.show()
        self.raise_()
        self.activateWindow()
        # A cancellation request is intentionally cooperative. It may have
        # stopped an optional refresh, so restore the recurring sources once
        # the user chooses to keep the launcher open.
        if self._automatic_network_allowed():
            self.cloud_status_polling.start()
        for timer_name in ("drive_check_timer", "_achievement_poll_timer"):
            timer = getattr(self, timer_name, None)
            if timer is not None and not timer.isActive():
                timer.start()
        self.game_sessions.start_observing()
        listener = getattr(self, "global_hotkeys", None)
        if listener is not None and QGuiApplication.platformName().lower() not in ("offscreen", "minimal"):
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
            self._closing = True
            self._shutdown_deadline = _time.monotonic() + 12.0
            self._show_shutdown_progress()
            self._network_probe_timer.stop()
            pending_network_dialog = self._network_loss_dialog
            if pending_network_dialog is not None:
                try:
                    pending_network_dialog.close()
                except RuntimeError:
                    pass
                self._network_loss_dialog = None
            self.game_sessions.stop_observing()

            # Halt every source that schedules new background work while we
            # are trying to shut down.
            self.cloud_status_polling.stop()
            for timer_name in ("drive_check_timer", "_size_resort_timer", "_update_status_refresh_timer", "_achievement_poll_timer", "_update_check_timer"):
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

        # These lists are compatibility-only indexes. WorkerSupervisor owns
        # the actual QThread lifetimes and is the sole shutdown registry.
        self._pending_auto_fetchers.clear()  # queued fetchers were never started
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
                watcher.deleteLater()
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

        if getattr(self, "_public_profile_binding", None) is not None:
            try:
                self._public_profile_binding.close()
                self._public_profile_binding.deleteLater()
            except RuntimeError:
                pass
            self._public_profile_binding = None
        if getattr(self, "_cloud_center_overview_binding", None) is not None:
            try:
                self._cloud_center_overview_binding.close()
                self._cloud_center_overview_binding.deleteLater()
            except RuntimeError:
                pass
            self._cloud_center_overview_binding = None
        self._close_managed_cloud_status_bindings()
        self._close_managed_achievement_bindings()
        self._close_managed_artwork_bindings()
        self._close_managed_steam_metadata_bindings()

        # All workers have been reaped above. Close long-lived client pools
        # explicitly so repeated embedded launches do not retain sockets or
        # stale backend sessions until Python garbage collection.
        try:
            if getattr(self, "sgdb_client", None) is not None:
                self.sgdb_client.close()
        except Exception:
            pass
        try:
            if getattr(self, "steam_client", None) is not None:
                self.steam_client.close()
        except Exception:
            pass
        try:
            if getattr(self, "central_auth", None) is not None:
                self.central_auth.close()
        except Exception:
            pass
        try:
            self.cloud_account_service.reset_backend()
        except Exception:
            pass
        try:
            if getattr(self, "request_manager", None) is not None:
                logger.info("Request manager metrics at shutdown: %s", self.request_manager.metrics())
                logger.info("Resource performance metrics at shutdown: %s", self.performance_metrics())
                self.request_manager.shutdown(wait=True)
        except Exception:
            logger.exception("Failed to shut down the cloud request manager cleanly")
        try:
            from core.achievement_schema import close_achievement_http_session
            close_achievement_http_session()
        except Exception:
            pass

        super().closeEvent(event)

    def _on_add(self, collection_name: object = ""):
        # QPushButton.clicked carries a boolean checked argument when this
        # slot is connected directly.  Treat that signal payload as “no
        # collection” instead of allowing it to reach string-only code below.
        collection_name = collection_name.strip() if isinstance(collection_name, str) else ""
        dialog = AddGameDialog(self, self.sgdb_client, request_manager=self.request_manager)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, path, exe, mode, banner_path = dialog.get_values()
            steam_id = dialog.get_steam_id()
            version_override, patch_notes_url = dialog.get_version_metadata()
            build_id = dialog.get_build_id()
            build_date = dialog.get_build_date()
            if not name or not path or not exe:
                QMessageBox.warning(self, "Error", "All fields are required.")
                return
            if not os.path.isdir(path):
                QMessageBox.warning(self, "Error", "Invalid game path.")
                return
            
            path = os.path.abspath(os.path.expanduser(path))
            save_sandbox_config(path, exe)
            game_id = self.library_service.upsert_game(
                name, path, exe, mode, banner_path, steam_id or None
            )
            if game_id:
                self.library_service.update_game_details(
                    game_id, name, path, exe, mode, banner_path,
                    steam_id=steam_id or None,
                    version_override=version_override,
                    patch_notes_url=patch_notes_url,
                )
                if collection_name.strip():
                    self.library_service.update_collection_membership(game_id, collection_name.strip())
                self._record_initial_steam_build(game_id, path, steam_id, build_id, build_date)
            self._refresh_library()
            if game_id:
                self._sync_launcher_metadata_async(game_id)
            if hasattr(self, "profile_page"):
                self.profile_page.mark_local_data_changed()
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
        if self.library_view_mode != "grid":
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
        # Read the dialog's already-snapshotted result before allowing Qt to
        # destroy its child widgets.  Lifecycle actions use a distinct result
        # and must never fall through to the normal edit-save path.
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        dialog.mark_current_requested.connect(self._mark_build_current_from_config)
        result = dialog.exec()
        lifecycle_action = str(getattr(dialog, "lifecycle_action", "") or "")
        if lifecycle_action:
            dialog.deleteLater()
            self._apply_game_lifecycle_action(game, lifecycle_action)
            return
        if result != QDialog.DialogCode.Accepted:
            dialog.deleteLater()
            return

        name, path, exe, mode, banner_path = dialog.get_values()
        version_override, patch_notes_url = dialog.get_version_metadata()
        manual_build_id = dialog.get_build_id()
        manual_build_date = dialog.get_build_date()
        manual_steam_id = dialog.get_steam_id()
        dialog.deleteLater()
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
        self.library_service.update_game_details(
            game_id, name, path, exe, mode, banner_path,
            steam_id=manual_steam_id,
            version_override=version_override,
            patch_notes_url=patch_notes_url,
        )
        logger.info(f"Saved game settings for {game_id}: executable='{exe}', mode='{mode}'")
        if manual_build_id is not None:
            self.library_service.set_build_reference(game_id, manual_build_id, manual_build_date)
            self.local_version_by_game_id[game_id] = (manual_build_id, manual_build_date)
            # Clear cached update status so it re-checks against new manual build
            self.metadata_attempted_builds.discard(game_id)
            self.steam_check_results.pop(game_id, None)
            self._capture_initial_steam_build(
                game_id, manual_steam_id, manual_build_id, manual_build_date
            )
        self._refresh_library()
        self._sync_launcher_metadata_async(game_id)
        if hasattr(self, "profile_page"):
            self.profile_page.mark_local_data_changed()
        self._show_toast(f"Updated settings for '{name}'.")
    

    def _on_sync_sandbox(self, quiet: bool = False):
        """Auto-discover installed games in ~/Games/Sandbox without creating duplicate entries."""
        found = scan_sandbox_games(DEFAULT_SANDBOX_DIR)
        db_games = self.library_service.read_games()
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
                self.library_service.upsert_game(
                    game['name'], norm_path, game['executable'], game['mode']
                )
                added_count += 1
                existing_paths.add(norm_path)
                existing_names.add(folder_clean)
                
        if added_count > 0:
            self._refresh_library()
            if hasattr(self, "profile_page"):
                self.profile_page.mark_local_data_changed()
            if not quiet:
                self._show_toast(f"Found and added {added_count} new game(s) from sandbox.")
        else:
            if not quiet:
                self._show_toast("Sandbox synced (no new games found).")

    @staticmethod
    def _remove_game_files_from_disk(game_path: str) -> tuple[bool, str]:
        """Delete one registered game directory without following its root symlink."""
        raw_value = os.path.expanduser(str(game_path or "").strip())
        if not raw_value:
            return False, "The game has no directory recorded."
        raw_path = os.path.abspath(raw_value)
        if os.path.islink(raw_path):
            return False, "Refusing to delete a symlinked game directory. Remove the library record instead."

        target_path = os.path.realpath(raw_path)
        if target_path != raw_path:
            return False, "Refusing to delete a path containing a symlink. Remove the library record instead."
        protected_paths = {
            os.path.realpath(os.path.abspath(os.sep)),
            os.path.realpath(os.path.expanduser("~")),
            os.path.realpath(os.path.expanduser(DEFAULT_SANDBOX_DIR)),
        }
        if target_path in protected_paths:
            return False, "Refusing to delete a protected system, home, or sandbox directory."
        if not os.path.lexists(target_path):
            return True, ""

        failures: list[str] = []

        def _handle_readonly(func, subpath, exc_info):
            try:
                os.chmod(subpath, 0o700)
                func(subpath)
            except Exception as exc:
                failures.append(f"{subpath}: {exc}")

        try:
            if os.path.isdir(target_path):
                try:
                    shutil.rmtree(target_path, onexc=_handle_readonly)
                except TypeError:
                    shutil.rmtree(target_path, onerror=_handle_readonly)
            else:
                os.chmod(target_path, 0o700)
                os.unlink(target_path)
        except Exception as exc:
            failures.append(str(exc))

        if failures or os.path.lexists(target_path):
            detail = failures[0] if failures else "the directory still exists"
            logger.warning(f"Could not remove game files at '{target_path}': {detail}")
            return False, "Could not remove the game files. Check permissions and try again."
        logger.info(f"Removed game files from disk: {target_path}")
        return True, ""

    @staticmethod
    def _stage_game_files_for_lifecycle(game_path: str) -> tuple[str | None, str]:
        """Move game files aside until the corresponding DB mutation succeeds.

        Lifecycle operations must not delete an installation and only then
        discover that the library transaction failed.  The staging name stays
        beside the original path, so the move is normally atomic and can be
        rolled back without copying multi-gigabyte game data.
        """
        raw_value = os.path.expanduser(str(game_path or "").strip())
        if not raw_value:
            return None, ""
        raw_path = os.path.abspath(raw_value)
        if os.path.islink(raw_path):
            return None, "Refusing to move a symlinked game directory. Remove the library record instead."

        target_path = os.path.realpath(raw_path)
        if target_path != raw_path:
            return None, "Refusing to move a path containing a symlink. Remove the library record instead."
        protected_paths = {
            os.path.realpath(os.path.abspath(os.sep)),
            os.path.realpath(os.path.expanduser("~")),
            os.path.realpath(os.path.expanduser(DEFAULT_SANDBOX_DIR)),
        }
        if target_path in protected_paths:
            return None, "Refusing to move a protected system, home, or sandbox directory."
        if not os.path.lexists(target_path):
            return None, ""

        staged_path = f"{target_path}.safelauncher-pending-{uuid.uuid4().hex}"
        try:
            shutil.move(target_path, staged_path)
        except Exception as exc:
            logger.warning(f"Could not stage game files at '{target_path}': {exc}")
            return None, "Could not prepare the game files safely. Check permissions and try again."
        return staged_path, ""

    @staticmethod
    def _restore_staged_game_files(staged_path: str, original_path: str) -> tuple[bool, str]:
        """Restore a staged installation after a failed library mutation."""
        if not staged_path or not os.path.lexists(staged_path):
            return True, ""
        original_path = os.path.abspath(os.path.expanduser(str(original_path or "").strip()))
        if not original_path:
            return False, "The original game path is empty; staged files were preserved."
        try:
            if os.path.lexists(original_path):
                return False, "The original game path is no longer empty; staged files were preserved."
            shutil.move(staged_path, original_path)
            return True, ""
        except Exception as exc:
            logger.warning(
                "Could not restore staged game files from '%s' to '%s': %s",
                staged_path,
                original_path,
                exc,
            )
            return False, "Could not restore the staged game files; they were preserved for safety."

    def _finish_game_lifecycle_change(self, game_id: int) -> None:
        """Drop stale UI/runtime state after a game row changes lifecycle."""
        self.selected_game = None
        service = getattr(self, "library_service", None)
        if service is not None:
            service.remove_from_selection(game_id)
        else:
            self.library_selection.replace(self.library_selection.ids - {game_id})
        self.library_metadata_state.clear_game(game_id)
        self.cloud_status_service.forget_status(game_id)
        self.achievement_state.clear_game(game_id)
        self._refresh_library()
        if hasattr(self, "profile_page"):
            # Archive changes and removals can change the public projection;
            # the profile publisher handles coalescing and append-only history.
            self.profile_page.mark_local_data_changed()
        if self.library_view_mode in ("compact", "steam"):
            self._update_compact_game_page()

    def _apply_game_lifecycle_action(self, game, action: str) -> bool:
        """Apply uninstall or permanent deletion and refresh all consumers."""
        if not game:
            return False
        action = str(action or "").strip().lower()
        if action not in {"uninstall", "delete_all_data"}:
            logger.warning(f"Ignoring unknown game lifecycle action: {action!r}")
            return False

        game_id = int(game[0])
        game_name = str(game[1] or "Game")
        library_service = MainWindow._get_library_service(self)
        game_path = str(game[2] if len(game) > 2 else "")
        staged_path, stage_error = MainWindow._stage_game_files_for_lifecycle(game_path)
        if stage_error:
            self._show_toast(stage_error, is_error=True)
            return False

        def _restore_after_failure(message: str) -> bool:
            restored, restore_error = MainWindow._restore_staged_game_files(staged_path, game_path)
            detail = restore_error if not restored else "The installation was left unchanged."
            self._show_toast(f"{message} {detail}", is_error=True)
            return False

        def _finish_success() -> None:
            self._finish_game_lifecycle_change(game_id)
            if action == "uninstall" and hasattr(self, "_sync_launcher_metadata_async"):
                self._sync_launcher_metadata_async(game_id)
            elif action != "delete_all_data" and hasattr(self, "_sync_profile_metadata_async"):
                self._sync_profile_metadata_async()

        try:
            mutation_ok = (
                bool(library_service and library_service.archive_game(game_id))
                if action == "uninstall"
                else bool(library_service and library_service.delete_all_game_data(game_id))
            )
        except Exception as exc:
            logger.exception("Game lifecycle database mutation failed for %s", game_id)
            mutation_ok = False

        if not mutation_ok:
            action_label = "mark" if action == "uninstall" else "delete"
            return _restore_after_failure(
                f"Could not {action_label} local data for '{game_name}'. Try again."
            )

        if action == "uninstall":
            if staged_path:
                deleted, error = MainWindow._remove_game_files_from_disk(staged_path)
                if not deleted:
                    restored, restore_error = MainWindow._restore_staged_game_files(staged_path, game_path)
                    if restored:
                        detail = "The files were restored; the record remains marked uninstalled."
                    else:
                        detail = f"The staged files were preserved: {restore_error or error}"
                    _finish_success()
                    self._show_toast(
                        f"'{game_name}' was marked uninstalled, but its files could not be removed. {detail}",
                        is_error=True,
                    )
                    return True
            self._show_toast(
                f"Uninstalled '{game_name}'. The SafeLauncher record and statistics were preserved."
            )
        else:
            if staged_path:
                deleted, error = MainWindow._remove_game_files_from_disk(staged_path)
                if not deleted:
                    restored, restore_error = MainWindow._restore_staged_game_files(staged_path, game_path)
                    if restored:
                        detail = "The files were restored, but the local SafeLauncher record was deleted."
                    else:
                        detail = f"The staged files were preserved: {restore_error or error}"
                    _finish_success()
                    self._show_toast(
                        f"Local data for '{game_name}' was deleted, but its files could not be removed. {detail}",
                        is_error=True,
                    )
                    return True
            self._show_toast(
                f"Deleted all local data for '{game_name}'. Remote cloud save versions were kept."
            )

        _finish_success()
        return True

    def _on_remove(self):
        game = self._get_selected_game()
        if not game:
            self._show_toast("Please select a game to uninstall or delete.", is_error=True)
            return
        dialog = CustomRemoveDialog(
            game[1],
            self,
            is_archived=bool(game[17]) if len(game) > 17 and game[17] else False,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.choice:
            self._apply_game_lifecycle_action(game, dialog.choice)

    def _on_export(self):
        game = self._get_selected_game()
        if not game:
            QMessageBox.warning(self, "Warning", "Please select a game.")
            return
        steam_id = str(game[6]).strip() if len(game) > 6 and game[6] else ""
        SaveManagerDialog(game[0], game[1], game[2], steam_id, self).exec()
        self.refresh_cloud_status_for_game(game[0])

    def _game_by_id(self, game_id: int):
        """Resolve a game from the current authoritative library snapshot."""
        try:
            wanted = int(game_id)
        except (TypeError, ValueError):
            return None
        for game in self.games:
            try:
                if int(game[0]) == wanted:
                    return game
            except (TypeError, ValueError, IndexError):
                continue
        return None

    def _show_game_cloud_menu(self, game_id: int, global_pos: QPoint) -> None:
        """Show one safe Cloud menu for cards and list rows."""
        game = self._game_by_id(game_id)
        if not game:
            return
        menu = QMenu(self)
        menu.setTitle(str(game[1]))
        for name, label, icon_name in (
            ("restore", "Restore latest cloud save", "ph.cloud-arrow-down-bold"),
            ("history", "Open Game Properties", "ph.clock-counter-clockwise-bold"),
            ("resolve", "Resolve conflict", "ph.warning-bold"),
        ):
            action = menu.addAction(get_icon(icon_name, color="#A1A1AA"), label)
            if name == "resolve":
                cached = self.cloud_save_status_cache.get(int(game_id))
                action.setEnabled(is_cloud_conflict(cached))
                action.setToolTip(
                    "Open conflict resolution for this game"
                    if action.isEnabled() else
                    "Available when this game has a cloud-save conflict"
                )
            action.triggered.connect(
                lambda _checked=False, action_name=name, gid=int(game_id):
                self._on_game_cloud_action(gid, action_name)
            )
        menu.addSeparator()
        center = menu.addAction(get_icon("ph.cloud-bold", color="#3B9FE8"), "Open Cloud Center")
        # Finish native menu teardown before opening the frameless Cloud Center.
        center.triggered.connect(lambda _checked=False: QTimer.singleShot(0, self._open_cloud_center))
        anchor = global_pos if global_pos and not global_pos.isNull() else self.cursor().pos()
        menu.exec(anchor)

    def _on_card_cloud_badge_clicked(self, game_id: int) -> None:
        """Make the rendered cloud badge an actionable library control."""
        cached = self.cloud_save_status_cache.get(int(game_id))
        status = cached[0] if cached else None
        self._on_game_cloud_action(int(game_id), "history")

    def _on_game_cloud_action(self, game_id: int, action: str) -> None:
        """Route per-game commands after the originating menu has closed.

        Cloud actions are emitted by QMenu/QToolButton menus. Opening a
        frameless modal dialog directly from that native menu callback can
        re-enter Qt's menu/compositor teardown and crash the process on some
        Wayland/X11 combinations. Defer the actual dialog work by one event
        loop turn so the menu is fully gone first.
        """
        game = self._game_by_id(game_id)
        if not game:
            return
        normalized_action = str(action or "").lower()
        QTimer.singleShot(
            0,
            lambda gid=int(game_id), name=normalized_action: self._perform_game_cloud_action(gid, name),
        )

    def _perform_game_cloud_action(self, game_id: int, action: str) -> None:
        """Perform a cloud action once any source menu has finished closing."""
        game = self._game_by_id(game_id)
        if not game:
            return
        if action == "center":
            self._open_cloud_center()
            return
        self._select_game_by_id(int(game_id))
        if action in {"history", "resolve"}:
            self._open_game_properties_for_game(game)
        elif action == "restore":
            self._open_save_manager_for_game(game, tab="restore")
        else:
            self._open_save_manager_for_game(game)

    def _open_save_manager_for_game(self, game, *, tab: str | None = None) -> None:
        """Open the existing managed Save Manager without auto-destructive work."""
        steam_id = str(game[6]).strip() if len(game) > 6 and game[6] else ""
        dialog = SaveManagerDialog(
            game[0], game[1], game[2], steam_id, self
        )
        if tab == "history" and hasattr(dialog, "tab_history"):
            dialog.tabs.setCurrentWidget(dialog.tab_history)
            if hasattr(dialog, "_load_history"):
                dialog._load_history()
        elif tab == "restore" and hasattr(dialog, "_restore_from_cloud"):
            # Save Manager performs its existing confirmation and conflict checks.
            QTimer.singleShot(0, dialog._restore_from_cloud)
        dialog.exec()
        self.refresh_cloud_status_for_game(game[0])
    
    def _on_import(self):
        game = self._get_selected_game()
        if not game:
            self._show_toast("Please select a game to import save.", is_error=True)
            return
        steam_id = str(game[6]).strip() if len(game) > 6 and game[6] else ""
        dlg = SaveManagerDialog(game[0], game[1], game[2], steam_id, self)
        dlg._import_snapshot()
        self.refresh_cloud_status_for_game(game[0])
