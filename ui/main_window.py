import os
import shutil
import subprocess
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QGridLayout, QFileDialog, QMessageBox, QDialog, QLabel, QLineEdit,
    QComboBox, QFormLayout, QScrollArea, QFrame, QListWidget, QListWidgetItem, QMenu,
    QApplication, QSystemTrayIcon, QCheckBox, QGraphicsOpacityEffect, QPlainTextEdit, QProgressBar,
    QStackedWidget, QSlider, QSplitter, QDialogButtonBox, QInputDialog
)
from PyQt6.QtCore import (
    Qt, QSize, QPoint, pyqtSignal, QVariantAnimation, QEasingCurve, QTimer,
    QUrl, QSettings, QAbstractAnimation,
)
from PyQt6.QtGui import QPixmap, QFont, QColor, QIcon, QPainter, QMovie, QDesktopServices
from core.interfaces import ISandboxRunner, IBackupManager
from core.steamgriddb_client import SteamGridDBClient
from core.playtime_tracker import PlaytimeTrackerThread
from core.steam_tags import SteamTagsFetcher
from core.steam_build_tracker import SteamBuildFetcher
from core.disk_utils import get_dir_size, format_size, get_disk_usage
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
    DiskSizeFetcherThread, HeroFetcherThread
)
from ui.components.banner_card import GameBannerWidget
from ui.components.responsive_grid import ResponsiveGridContainer
from ui.components.hero_background import HeroBackgroundWidget
from ui.components.sidebar import LeftSidebarWidget, CustomTitleBar, DialogTitleBar, add_soft_shadow
from ui.dialogs.proton_dialogs import ProtonSetupWizard, ProtonManagerDialog, UmuRuntimeManagerDialog
from ui.dialogs.game_dialogs import (
    AddGameDialog, EditGameDialog, LaunchOptionsDialog, SafeLaunchDialog,
    MissingDependencyDialog, ToastNotification, CustomRemoveDialog
)
from ui.dialogs.settings_dialog import UserSettingsDialog, ScreenshotGalleryDialog, DiskManagerDialog


class SafeLaunchDialog(QDialog):
    """Sleek, zero-jump animated dark card popup:
    Page 0: Animated Pudgy Penguin GIF intro (/home/martin/Stažené/penguin-pudgy.gif)
    Page 1: Clean 'Preparing Virtual Environment' header + progress bar + terminal console log
    Page 2: Confirmation screen ('Enjoy your time, Martin! ✨') with animated GIF (/home/martin/Stažené/smict.gif)
    Stage 4: Smooth 500ms opacity fade out & auto-close.
    """
    retry_requested = pyqtSignal(str)
    unsafe_launch_requested = pyqtSignal()

    def __init__(self, game_name: str, user_name: str = "Martin", process=None, parent=None):
        super().__init__(parent)
        self.game_name = game_name
        self.user_name = user_name
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

        self.setWindowTitle(f"Safe Launch - {game_name}")
        self.setFixedSize(760, 600)

        # Center over parent window if available
        if parent:
            p_geo = parent.geometry()
            self.move(
                p_geo.x() + (p_geo.width() - self.width()) // 2,
                p_geo.y() + (p_geo.height() - self.height()) // 2
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
        # PAGE 3: Launch failure screen
        # ---------------------------------------------------------------------
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
            self.append_log(f"[{t_str}] 🔒 [SECURITY] Applying filesystem whitelist & Firejail isolation...")
        else:
            self.append_log(f"[{t_str}] ⚠️ [WARNING] Firejail is not installed on this system.")
            self.append_log(f"[{t_str}] ⚡ [FALLBACK] Running game in direct unsandboxed execution mode.")
        self.append_log(f"[{t_str}] 🍷 [RUNNER] Loading Proton / Wine runtime container...")
        self.append_log(f"[{t_str}] 🚀 [EXEC] Launching process for '{game_name}'...")

        # Start log reader thread if process is piped
        self.process_log_path = getattr(self.process, "safelauncher_log_path", None)
        self._process_log_offset = 0
        if self.process_log_path:
            self.log_poll_timer = QTimer(self)
            self.log_poll_timer.setInterval(250)
            self.log_poll_timer.timeout.connect(self._poll_process_log)
            self.log_poll_timer.start()
        elif self.process and getattr(self.process, 'stdout', None):
            self.reader_thread = SafeLaunchLogReader(self.process, self)
            self.reader_thread.log_line.connect(self.append_log)
            self.reader_thread.start()

        # Watch the actual child process. A fixed animation must not report
        # success after Proton/UMU has already exited.
        self.process_timer = QTimer(self)
        self.process_timer.setInterval(200)
        self.process_timer.timeout.connect(self._check_process_state)
        self.process_timer.start()

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
            self.log_lines.append(text)
            if self.diagnostics:
                self.diagnostics.output.append(text)
            self.console.appendPlainText(text)
            if hasattr(self, "error_details") and self.stack.currentWidget() is self.page_error:
                self.error_details.appendPlainText(text)
            sb = self.console.verticalScrollBar()
            if sb:
                sb.setValue(sb.maximum())

            # UMU can print a clean-looking exit (including exit code 0) when
            # Proton never manages to keep the game alive. Treat this marker
            # as an early-startup failure and let the process watcher collect
            # the final exit code and remaining output.
            if "parent is shutting down" in text.lower() and not self.launch_finished:
                QTimer.singleShot(350, self._check_process_state)

    def _poll_process_log(self):
        """Append newly written game-process diagnostics without using a pipe."""
        path = getattr(self, "process_log_path", None)
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as stream:
                stream.seek(self._process_log_offset)
                new_text = stream.read()
                self._process_log_offset = stream.tell()
            if new_text:
                for line in new_text.splitlines():
                    self.append_log(line)
        except OSError:
            return

    def _goto_confirmation_stage(self):
        """Phase 3: Transition to Confirmation Screen ('Enjoy your time, Martin! ✨')."""
        if self.launch_finished:
            return
        # Do not transition to the success page if the child has already
        # exited but the polling timer has not delivered its last tick yet.
        if self.process and self.process.poll() is not None:
            self._check_process_state()
            return
        # This is only a visual handoff. Keep watching the process because
        # Proton/UMU may shut down immediately afterwards.
        self.handoff_shown = True
        import time
        t_str = time.strftime("%H:%M:%S")
        self.append_log(f"[{t_str}] ✔️ [SUCCESS] Sandbox container initialized cleanly.")
        self.append_log(f"[{t_str}] ✨ [STATUS] Handing off control to {self.game_name}. Have fun!")

        # Transition to the confirmation page. Use the widget reference because
        # the failure page is intentionally kept in the same stack.
        self.stack.setCurrentWidget(self.page_confirm)

        # Hold confirmation screen for 2.5s, then fade out entire dialog
        self.close_timer = QTimer(self)
        self.close_timer.setSingleShot(True)
        self.close_timer.timeout.connect(self._fade_out_dialog)
        # Keep the watchdog alive through the common Proton startup window.
        # If the game is still alive, the popup closes normally at the end.
        self.close_timer.start(int(self.startup_grace_seconds * 1000))

    def _check_process_state(self):
        """Show actionable diagnostics when the runtime exits during startup."""
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

        # Drain the persistent file once more so diagnostics include the final
        # Proton/UMU lines written just before process exit.
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
        if "libcrypto.so" in lower_details or "openssl_" in lower_details:
            reason += " A packaged launcher library was loaded by a host runtime tool. Restart using the updated SafeLauncher build."
        elif "no such file" in lower_details or "cannot open" in lower_details:
            reason += " Check that the selected executable path is correct."
        elif "no permissions to create a new namespace" in lower_details or "unprivileged_userns_clone" in lower_details:
            reason += " The kernel has disabled unprivileged user namespaces; enable kernel.unprivileged_userns_clone=1 or use a compatible kernel/container configuration."
        elif "proton" in lower_details or "umu" in lower_details:
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
        """Phase 4: Smooth opacity fade out of entire dialog before closing."""
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
        """Stop all background timers, animations, and stdout reader thread safely."""
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
        """Stop timers and stdout reader before Qt destroys the dialog."""
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
            elif any(x in id_str or x in like_str for x in ['debian', 'ubuntu', 'mint', 'pop']):
                return (name_str, 'sudo apt install firejail')
            return (name_str, 'sudo apt install firejail')
        except Exception:
            pass
    return (os_name, cmd)


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
        self._auto_fetch_attempted = set()
        self.metadata_fetchers = []
        self.metadata_attempted_builds = set()
        self.metadata_attempted_tags = set()
        self._hero_attempted = set()
        self.playtime_trackers = []  # keep references so GC doesn't kill running threads
        self.topbar_extractor_thread = None
        self.games_by_id = {}
        self.library_selection = LibrarySelectionModel()

        self.search_query = ""
        self.settings = QSettings("SafeLauncher", "SafeLauncher")
        self.library_view_mode = self.settings.value("library_view_mode", "grid", type=str)
        self.user_name = self.settings.value("user_name", "Martin", type=str).strip() or "Martin"
        self.proton_path = self.settings.value("proton_path", "", type=str).strip()
        if hasattr(self.runner, "set_proton_path"):
            self.runner.set_proton_path(self.proton_path)
        self.current_filter = "all"
        self.collection_filter = ""
        self.current_sort = 0  # 0: A-Z, 1: Playtime, 2: Recently Added

        self.setWindowTitle("🎮 SafeLauncher - Game Sandbox Manager")
        self.resize(1180, 750)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        
        if os.path.exists(LOGO_PATH):
            self.setWindowIcon(QIcon(LOGO_PATH))

        # Root Layout: Hero Background Canvas + Top Title Bar + Body (Left Sidebar + Center Grid & Right Inspector Splitter)
        self.hero_bg = HeroBackgroundWidget(self)
        self.setCentralWidget(self.hero_bg)
        
        root_vbox = QVBoxLayout(self.hero_bg)
        root_vbox.setContentsMargins(0, 0, 0, 0)
        root_vbox.setSpacing(0)
        
        # Top Custom Draggable Title Bar
        self.title_bar = CustomTitleBar(self)
        root_vbox.addWidget(self.title_bar)
        
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

        self.nav_library = self.sidebar.nav_library
        self.nav_sandbox = self.sidebar.nav_sandbox
        self.nav_sandbox.clicked.connect(self._open_sandbox_dir)
        self.nav_install_zip = self.sidebar.nav_install_zip
        self.nav_install_zip.clicked.connect(self._on_install_zip_archive)
        self.nav_sync = self.sidebar.nav_sync
        self.nav_sync.clicked.connect(self._on_sync_sandbox)
        self.nav_disk = self.sidebar.nav_disk
        self.nav_disk.clicked.connect(self._open_disk_manager)
        self.stat_label = self.sidebar.stat_label
        self.title_bar.search_changed.connect(self._on_search_query_changed)
        self.sidebar.btn_settings.clicked.connect(self._open_settings)
        self.sidebar.act_export.triggered.connect(self._on_export)
        self.sidebar.act_import.triggered.connect(self._on_import)

        # 2. Splitter Layout: Center Game Grid + Right Game Details Inspector Panel
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #15171c;
                width: 1px;
            }
            QSplitter::handle:hover {
                background-color: #3b3f46;
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
                background: #111318;
                border-left: 1px solid #15171c;
            }
            QLabel {
                color: #ffffff;
            }
            QPushButton {
                background: rgba(24, 24, 31, 0.85);
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 12px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #30343c;
            }
            QPushButton:disabled {
                background: rgba(18, 18, 21, 0.6);
                color: #52525b;
                border-color: transparent;
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
        detail_layout.setContentsMargins(18, 18, 18, 18)
        detail_layout.setSpacing(9)
        detail_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Selected Game Cover Art Preview
        self.detail_cover = QLabel()
        self.detail_cover.setFixedSize(QSize(180, 270))
        self.detail_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_cover.setStyleSheet("border: 1px solid #2b313c; border-radius: 10px; background: #171a20;")
        
        cover_row = QHBoxLayout()
        cover_row.addWidget(self.detail_cover)
        detail_layout.addLayout(cover_row)

        # Selected Game Title Header Row (Title + Star Favorite Icon)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        self.detail_title = QLabel("Select a Game")
        self.detail_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        self.detail_title.setWordWrap(True)
        self.detail_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_title.setStyleSheet("color: #ffffff; background: transparent;")
        title_row.addWidget(self.detail_title, 1)

        self.btn_detail_fav = QPushButton()
        self.btn_detail_fav.setFixedSize(QSize(32, 32))
        self.btn_detail_fav.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_detail_fav.setCheckable(True)
        self.btn_detail_fav.setToolTip("Add to Favorites")
        self.btn_detail_fav.setIcon(get_icon("ph.star", color="#c9ccd2"))
        self.btn_detail_fav.setIconSize(QSize(18, 18))
        self.btn_detail_fav.setStyleSheet("""
            QPushButton {
                background: #171a20;
                border: none;
                border-radius: 8px;
                padding: 0;
            }
            QPushButton:hover {
                background: #30343c;
            }
            QPushButton:checked {
                background: #332b1b;
            }
        """)
        self.btn_detail_fav.clicked.connect(self._on_toggle_favorite)
        title_row.addWidget(self.btn_detail_fav)
        detail_layout.addLayout(title_row)

        # Steam Tags Badge Container (Grey rounded boxes)
        self.tags_widget = QWidget()
        self.tags_layout = QHBoxLayout(self.tags_widget)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setSpacing(6)
        self.tags_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail_layout.addWidget(self.tags_widget)

        # Selected Game Playtime
        self.detail_playtime = QLabel("")
        self.detail_playtime.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_playtime.setStyleSheet("color: #aaaaaa; font-size: 11px; font-weight: bold;")
        detail_layout.addWidget(self.detail_playtime)

        # Selected Game Last Played
        self.detail_last_played = QLabel("")
        self.detail_last_played.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_last_played.setStyleSheet("""
            QLabel {
                color: #b7bbc3;
                background: #171a20;
                border-radius: 8px;
                padding: 8px 10px;
                font-size: 12px;
                font-weight: bold;
            }
        """)
        detail_layout.addWidget(self.detail_last_played)

        # Selected Game Disk Size
        self.detail_disk_size = QLabel("")
        self.detail_disk_size.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_disk_size.setStyleSheet("color: #8f949e; font-size: 11px; font-weight: bold; padding: 4px 0;")

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
        detail_layout.addSpacing(8)
        self.btn_detail_launch = QPushButton("Launch Game")
        self.btn_detail_launch.setObjectName("detailLaunch")
        self.btn_detail_launch.setIcon(get_icon("ph.play", color="#ffffff"))
        self.btn_detail_launch.setMinimumHeight(50)
        self.btn_detail_launch.setStyleSheet("""
            QPushButton#detailLaunch {
                background: #2f8f63;
                color: #ffffff;
                font-weight: bold;
                font-size: 14px;
                border: none;
                border-radius: 10px;
                padding: 10px 14px;
            }
            QPushButton#detailLaunch:hover {
                background: #3eaa77;
            }
            QPushButton#detailLaunch:disabled {
                background: #1b2029;
                color: #52525b;
                border-color: transparent;
            }
        """)
        self.btn_detail_launch.setIconSize(QSize(20, 20))
        self.btn_detail_launch.setMinimumWidth(200)
        # Do not apply a second graphics effect here: the parent inspector
        # already fades with QGraphicsOpacityEffect. Qt fails to paint this
        # child reliably when a drop-shadow effect is nested inside it.
        self.btn_detail_launch.clicked.connect(self._on_launch)
        detail_layout.addWidget(self.btn_detail_launch)
        detail_layout.addSpacing(8)

        # Action Buttons
        self.btn_detail_edit = QPushButton(" Edit Settings")
        self.btn_detail_edit.setIcon(get_app_icon("edit"))
        self.btn_detail_edit.setMinimumHeight(32)
        self.btn_detail_edit.clicked.connect(self._on_edit)
        detail_layout.addWidget(self.btn_detail_edit)

        self.btn_detail_screenshots = QPushButton(" Screenshots")
        self.btn_detail_screenshots.setIcon(get_icon("ph.camera-bold"))
        self.btn_detail_screenshots.setMinimumHeight(32)
        self.btn_detail_screenshots.clicked.connect(self._open_screenshot_gallery)
        detail_layout.addWidget(self.btn_detail_screenshots)

        self.btn_detail_export = QPushButton(" Export Save")
        self.btn_detail_export.setIcon(get_app_icon("export"))
        self.btn_detail_export.setMinimumHeight(32)
        self.btn_detail_export.clicked.connect(self._on_export)
        detail_layout.addWidget(self.btn_detail_export)

        self.btn_detail_import = QPushButton(" Import Save")
        self.btn_detail_import.setIcon(get_app_icon("import"))
        self.btn_detail_import.setMinimumHeight(32)
        self.btn_detail_import.clicked.connect(self._on_import)
        detail_layout.addWidget(self.btn_detail_import)

        self.btn_detail_prefix = QPushButton(" Prefix Maintenance")
        self.btn_detail_prefix.setMinimumHeight(32)
        self.btn_detail_prefix.clicked.connect(self._open_prefix_maintenance)
        detail_layout.addWidget(self.btn_detail_prefix)

        self.btn_detail_runtime = QPushButton(" Set per-game Proton")
        self.btn_detail_runtime.setMinimumHeight(32)
        self.btn_detail_runtime.clicked.connect(self._set_game_runtime)
        detail_layout.addWidget(self.btn_detail_runtime)

        self.btn_detail_remove = QPushButton(" Remove Game")
        self.btn_detail_remove.setIcon(get_app_icon("remove"))
        self.btn_detail_remove.setMinimumHeight(32)
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
        detail_layout.addWidget(self.detail_disk_size)

        # -------------------------------------------------------------
        # Center Main Content Panel (Game Library Grid)
        # -------------------------------------------------------------
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(28, 24, 28, 22)
        right_layout.setSpacing(16)

        # Add center game grid first, right detail panel second
        self.splitter.addWidget(right_panel)
        self.splitter.addWidget(self.detail_panel)

        saved_right_w = self.settings.value("right_inspector_width", 300, type=int)
        self.splitter.setSizes([880, saved_right_w])
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        
        # Right Header / Title & Filter Controls
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)
        header_title = QLabel("Game Library")
        header_title.setFont(QFont("Arial", 22, QFont.Weight.Bold))
        header_title.setStyleSheet("color: #fff;")
        header_layout.addWidget(header_title)
        
        self.btn_view_toggle = QPushButton("☷  List" if self.library_view_mode == "grid" else "▦  Grid")
        self.btn_view_toggle.setToolTip("Toggle grid/list library view")
        self.btn_view_toggle.clicked.connect(self._toggle_library_view)
        self.btn_view_toggle.setMinimumHeight(34)
        header_layout.addStretch()
        header_layout.addWidget(self.btn_view_toggle)

        # Sleek Segmented Filter Bar Container
        filter_container = QFrame()
        filter_container.setStyleSheet("""
            QFrame {
                background: #17191e;
                border: none;
                border-radius: 10px;
            }
        """)
        fc_layout = QHBoxLayout(filter_container)
        fc_layout.setContentsMargins(3, 3, 3, 3)
        fc_layout.setSpacing(4)

        filter_btn_style = """
            QPushButton {
                background: transparent;
                color: #a1a1aa;
                border: none;
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1f1f24;
                color: #ffffff;
            }
            QPushButton:checked {
                background: #30343c;
                color: #ffffff;
                border: none;
            }
        """

        self.btn_filter_all = QPushButton("All")
        self.btn_filter_all.setIcon(get_icon("ph.squares-four", color="#d5d7dc"))
        self.btn_filter_all.setCheckable(True)
        self.btn_filter_all.setChecked(True)
        self.btn_filter_all.setStyleSheet(filter_btn_style)
        self.btn_filter_all.clicked.connect(lambda: self._set_filter("all"))

        self.btn_filter_installed = QPushButton("Installed")
        self.btn_filter_installed.setIcon(get_icon("ph.check-circle", color="#d5d7dc"))
        self.btn_filter_installed.setCheckable(True)
        self.btn_filter_installed.setStyleSheet(filter_btn_style)
        self.btn_filter_installed.clicked.connect(lambda: self._set_filter("installed"))

        self.btn_filter_missing = QPushButton("Missing")
        self.btn_filter_missing.setIcon(get_icon("ph.warning-circle", color="#d5d7dc"))
        self.btn_filter_missing.setCheckable(True)
        self.btn_filter_missing.setStyleSheet(filter_btn_style)
        self.btn_filter_missing.clicked.connect(lambda: self._set_filter("missing"))

        self.btn_filter_fav = QPushButton("Favorites")
        self.btn_filter_fav.setIcon(get_icon("ph.star", color="#d5d7dc"))
        self.btn_filter_fav.setCheckable(True)
        self.btn_filter_fav.setStyleSheet(filter_btn_style)
        self.btn_filter_fav.clicked.connect(lambda: self._set_filter("favorites"))

        self.btn_filter_recent = QPushButton("Recent")
        self.btn_filter_recent.setIcon(get_icon("ph.clock", color="#d5d7dc"))
        self.btn_filter_recent.setCheckable(True)
        self.btn_filter_recent.setStyleSheet(filter_btn_style)
        self.btn_filter_recent.clicked.connect(lambda: self._set_filter("recent"))

        self.btn_filter_attention = QPushButton("Needs attention")
        self.btn_filter_attention.setIcon(get_icon("ph.warning-octagon", color="#d5d7dc"))
        self.btn_filter_attention.setCheckable(True)
        self.btn_filter_attention.setStyleSheet(filter_btn_style)
        self.btn_filter_attention.clicked.connect(lambda: self._set_filter("attention"))

        self.btn_filter_collection = QPushButton("Collection")
        self.btn_filter_collection.setIcon(get_icon("ph.folder-simple", color="#d5d7dc"))
        self.btn_filter_collection.setStyleSheet(filter_btn_style)
        self.btn_filter_collection.clicked.connect(self._choose_collection_filter)

        fc_layout.addWidget(self.btn_filter_all)
        fc_layout.addWidget(self.btn_filter_installed)
        fc_layout.addWidget(self.btn_filter_missing)
        fc_layout.addWidget(self.btn_filter_fav)
        fc_layout.addWidget(self.btn_filter_recent)
        fc_layout.addWidget(self.btn_filter_attention)
        fc_layout.addWidget(self.btn_filter_collection)
        for filter_button in (
            self.btn_filter_all, self.btn_filter_installed, self.btn_filter_missing,
            self.btn_filter_fav, self.btn_filter_recent, self.btn_filter_attention,
            self.btn_filter_collection,
        ):
            filter_button.setIconSize(QSize(17, 17))

        # Sorting ComboBox
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Sort: A–Z Title", "Sort: Most Played", "Sort: Recently Added", "Sort: Disk Size", "Sort: Runner"])
        self.sort_combo.setFixedHeight(28)
        self.sort_combo.setStyleSheet("""
            QComboBox {
                background: #181818;
                color: #ffffff;
                border: none;
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

        self.btn_reveal_detail = QPushButton(" Details")
        self.btn_reveal_detail.setIcon(get_icon("ph.caret-double-left", color="#c9ccd2"))
        self.btn_reveal_detail.setIconSize(QSize(16, 16))
        self.btn_reveal_detail.setToolTip("Show game details panel")
        self.btn_reveal_detail.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reveal_detail.setStyleSheet("""
            QPushButton {
                background: #08090b;
                color: #c9ccd2;
                border: none;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover { background: #17191e; color: #ffffff; }
        """)
        self.btn_reveal_detail.clicked.connect(lambda: self._animate_left_panel(True))
        add_soft_shadow(self.btn_reveal_detail, blur=16, y=3, alpha=90)
        self.btn_reveal_detail.setParent(right_panel)
        self.btn_reveal_detail.raise_()
        right_layout.addLayout(header_layout)
        self._reposition_reveal_button()
        right_layout.addWidget(filter_container)

        # Games Grid in Scroll Area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea, QWidget#qt_scrollarea_viewport { background: transparent; border: none; }")
        
        # Dynamic Responsive Grid Container (2:3 portrait cards, default width 200px)
        self.grid_container = ResponsiveGridContainer(card_width=200, spacing=15)
        self.grid_container.setStyleSheet("background: transparent;")
        self.list_view = LibraryListView()
        self.list_view.game_clicked.connect(self._select_game_by_id)
        self.list_view.game_double_clicked.connect(self._on_double_click_game)
        self.library_view_stack = QStackedWidget()
        self.library_view_stack.addWidget(self.grid_container)
        self.library_view_stack.addWidget(self.list_view)
        self.library_view_stack.setCurrentIndex(1 if self.library_view_mode == "list" else 0)
        scroll_area.setWidget(self.library_view_stack)
        right_layout.addWidget(scroll_area)

        # Action Buttons Layout (Add Game on bottom-left, Card Size Slider, Launch on bottom-right)
        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 5, 0, 0)
        action_layout.setSpacing(15)
        
        self.btn_add = QPushButton("Add Game")
        self.btn_add.setObjectName("addGameButton")
        self.btn_add.setIcon(get_app_icon("add"))
        self.btn_add.clicked.connect(self._on_add)
        self.btn_add.setMinimumHeight(40)
        self.btn_add.setStyleSheet("QPushButton#addGameButton { background: #2f8f63; color: #ffffff; font-weight: bold; border-radius: 8px; border: none; padding: 10px 20px; } QPushButton#addGameButton:hover { background: #3eaa77; }")
        self.btn_add.setIconSize(QSize(19, 19))
        add_soft_shadow(self.btn_add, blur=20, y=5, alpha=105)
        action_layout.addWidget(self.btn_add)

        self.btn_select_all = QPushButton("Select all")
        self.btn_select_all.clicked.connect(self._select_all_visible)
        action_layout.addWidget(self.btn_select_all)
        self.btn_clear_selection = QPushButton("Clear")
        self.btn_clear_selection.clicked.connect(self._clear_library_selection)
        action_layout.addWidget(self.btn_clear_selection)
        self.btn_collection = QPushButton("Collection…")
        self.btn_collection.clicked.connect(self._assign_selected_collection)
        action_layout.addWidget(self.btn_collection)
        self.btn_favorite_selected = QPushButton("★ Selected")
        self.btn_favorite_selected.clicked.connect(self._favorite_selected)
        action_layout.addWidget(self.btn_favorite_selected)

        # Card Size Zoom Slider
        zoom_layout = QHBoxLayout()
        zoom_layout.setSpacing(8)
        zoom_label = QLabel("🔍 Size:")
        zoom_label.setStyleSheet("color: #a1a1aa; font-weight: bold; font-size: 12px;")
        zoom_layout.addWidget(zoom_label)

        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(140, 280)
        self.size_slider.setValue(200)
        self.size_slider.setFixedWidth(130)
        self.size_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.size_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 6px;
                background: #27272a;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #52525b;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                width: 14px;
                height: 14px;
                margin: -4px 0;
                border-radius: 7px;
            }
        """)
        self.size_slider.valueChanged.connect(self._on_card_size_changed)
        zoom_layout.addWidget(self.size_slider)
        action_layout.addLayout(zoom_layout)

        action_layout.addStretch()
        right_layout.addLayout(action_layout)
        
        self.setStyleSheet("""
            QMainWindow { background: #0b0d10; }
            QPushButton { 
                background: #1a1e25;
                color: #ffffff; 
                border: none;
                padding: 8px 15px;
                border-radius: 8px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover { 
                background: #252b35;
            }
            QLabel { color: #fff; }
            QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 0; }
            QScrollBar::handle:vertical { background: #303846; border-radius: 5px; min-height: 40px; }
            QScrollBar::handle:vertical:hover { background: #46556b; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
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
                self.title_bar.btn_max.setText("[]")
                self.title_bar.btn_max.setToolTip("Maximize window")
        else:
            self.showMaximized()
            if hasattr(self, 'title_bar'):
                self.title_bar.btn_max.setText("=")
                self.title_bar.btn_max.setToolTip("Restore window")

    def _open_settings(self):
        """Open launcher preferences and persist profile changes."""
        dialog = UserSettingsDialog(self.user_name, self.proton_path, self)
        dialog.runtime_manager_requested.connect(self._open_runtime_manager)
        dialog.proton_manager_requested.connect(self._open_proton_manager)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.user_name = dialog.get_user_name()
            self.proton_path = dialog.get_proton_path()
            self.settings.setValue("user_name", self.user_name)
            self.settings.setValue("proton_path", self.proton_path)
            if hasattr(self.runner, "set_proton_path"):
                self.runner.set_proton_path(self.proton_path)
            self._show_toast(f"✓ Display name changed to {self.user_name}.")

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
        self.proton_path = proton_path.strip()
        self.settings.setValue("proton_path", self.proton_path)
        if hasattr(self.runner, "set_proton_path"):
            self.runner.set_proton_path(self.proton_path)
        display_name = os.path.basename(self.proton_path) if self.proton_path else "System / UMU Default"
        self._show_toast(f"✓ Global default Proton set to: {display_name}")

    def _apply_proton_to_selected_game(self, proton_path: str):
        if not self.selected_game:
            self._set_global_proton_path(proton_path)
            return
        self.proton_path = proton_path
        self.settings.setValue("proton_path", proton_path)
        if hasattr(self.runner, "set_proton_path"):
            self.runner.set_proton_path(proton_path)
        self._show_toast(f"✓ Applied {os.path.basename(proton_path)} to '{self.selected_game.name}'!")

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
        act_disk = self.tray_menu.addAction(get_app_icon("search"), "🔍 Disk Space Manager")
        act_disk.triggered.connect(self._open_disk_manager)

        self.tray_menu.addSeparator()

        # Quit
        act_quit = self.tray_menu.addAction(get_app_icon("close"), "Quit SafeLauncher")
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
            "Archive Files (*.zip *.7z *.tar.gz *.rar)"
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
        self.btn_filter_recent.setChecked(filter_mode == "recent")
        self.btn_filter_attention.setChecked(filter_mode == "attention")
        self._refresh_library()

    def _choose_collection_filter(self):
        collections = sorted({str(game[13]).strip() for game in self.games if len(game) > 13 and str(game[13]).strip()})
        choices = ["All collections"] + collections
        choice, accepted = QInputDialog.getItem(self, "Filter collection", "Collection:", choices, 0, False)
        if not accepted:
            return
        self.collection_filter = "" if choice == "All collections" else choice
        self.btn_filter_collection.setText(self.collection_filter or "Collection")
        self._refresh_library()

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
            # The database toggle is intentionally used only for the batch action;
            # it preserves each game's existing state and avoids a new UI model.
            self.db.toggle_favorite(game_id)
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
        self.games_by_id = {game[0]: game for game in self.games}
        self.library_selection.replace(self.library_selection.ids.intersection(self.games_by_id))
        self.stat_label.setText(f"{len(self.games)} Game(s) Total")

        if not self.games:
            label = QLabel("🎮 No games in your library yet.\nClick 'Add Game' or 'Sync Library' to get started!")
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
            searchable = " ".join(str(value or "") for value in (name, g[10] if len(g) > 10 else "", executable, steam_id, mode, g[12] if len(g) > 12 else "" )).lower()
            if self.search_query and self.search_query not in searchable:
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
            elif self.current_filter == "recent" and not (len(g) > 9 and g[9]):
                continue
            elif self.current_filter == "attention" and not (is_missing or not steam_id or (mode.startswith("umu") and not (len(g) > 12 and g[12]))):
                continue
            if self.collection_filter and (len(g) <= 13 or str(g[13]).strip() != self.collection_filter):
                continue

            processed.append((g, is_missing, playtime, is_fav))

        # 3. Sorting
        if self.current_sort == 0:  # A-Z Title
            processed.sort(key=lambda x: x[0][1].lower())
        elif self.current_sort == 1:  # Most Played
            processed.sort(key=lambda x: x[2], reverse=True)
        elif self.current_sort == 2:  # Recently Added (id desc)
            processed.sort(key=lambda x: x[0][14] if len(x[0]) > 14 and x[0][14] else x[0][0], reverse=True)
        elif self.current_sort == 3:  # Disk size
            processed.sort(key=lambda x: get_dir_size(x[0][2]), reverse=True)
        elif self.current_sort == 4:  # Runner
            processed.sort(key=lambda x: x[0][4].lower())

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
            widget.set_selected(game_id in self.library_selection.ids)
            widget.clicked.connect(self._select_game_by_id)
            widget.doubleClicked.connect(self._on_double_click_game)
            
            widgets.append(widget)
            self.banner_widgets[game_id] = widget
            
            if banner_url is None and game_id not in self._auto_fetch_attempted:
                self._auto_fetch_attempted.add(game_id)
                fetcher = BannerAutoFetcher(game_id, name, self.sgdb_client)
                fetcher.banner_auto_downloaded.connect(self._on_auto_banner_downloaded)
                fetcher.finished.connect(lambda f=fetcher: self._cleanup_auto_fetcher(f))
                fetcher.start()
                self.auto_fetchers.append(fetcher)
            
        self.grid_container.set_banner_widgets(widgets)
        self.list_view.set_games([item[0] for item in processed], self.library_selection.ids)
        self._check_games_on_drive()
        self._update_tray_menu()

        # Pre-cache 16:9 hero background artwork for all library games in background threads
        for game in self.games:
            g_id, g_name, _, _, _, _, s_id = game[:7]
            hero_cache_file = os.path.join(self.sgdb_client.cache_dir, "heroes", f"hero_{g_id}.jpg")
            if not os.path.exists(hero_cache_file) and g_id not in self._hero_attempted:
                if not any(isinstance(f, HeroFetcherThread) and f.game_id == g_id for f in self.metadata_fetchers):
                    self._hero_attempted.add(g_id)
                    hero_thread = HeroFetcherThread(g_id, g_name, s_id, self.sgdb_client, parent=self)
                    hero_thread.hero_downloaded.connect(self._on_hero_downloaded)
                    self._track_metadata_fetcher(hero_thread)

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

    def _on_auto_banner_downloaded(self, game_id: int, image_path: str, steam_id: int = 0):
        """Update DB and widget when background auto-fetch completes"""
        self.db.update_game_banner(game_id, image_path)
        if steam_id:
            self.db.update_game_steam_id(game_id, steam_id)
        if game_id in self.banner_widgets:
            self.banner_widgets[game_id].set_banner(image_path)

    def _cleanup_auto_fetcher(self, fetcher):
        if fetcher in self.auto_fetchers:
            self.auto_fetchers.remove(fetcher)

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

    def _select_game_by_id(self, game_id: int):
        """Select a game card visually and update the left detail panel"""
        additive = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
        self.library_selection.click(game_id, additive=additive)
        if not self.selected_game or self.selected_game[0] != game_id:
            self._cancel_metadata_fetchers()
        for widget in self.banner_widgets.values():
            widget.set_selected(widget.game_id in self.library_selection.ids)
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

    def _on_splitter_moved(self, pos: int, index: int):
        sizes = self.splitter.sizes()
        if len(sizes) > 1 and sizes[1] > 150:
            self.settings.setValue("right_inspector_width", sizes[1])
        self._reposition_reveal_button()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_reveal_button()

    def _reposition_reveal_button(self):
        """Keep the hidden-inspector affordance floating over the library edge."""
        button = getattr(self, "btn_reveal_detail", None)
        if button is None or not hasattr(button, "parentWidget") or button.parentWidget() is None:
            return
        host = button.parentWidget()
        button.adjustSize()
        button.move(max(12, host.width() - button.width() - 16), 14)
        button.raise_()

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
        if not self.selected_game or self.selected_game[0] != game_id:
            return
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

    def _on_hero_downloaded(self, game_id: int, image_path: str):
        if self.selected_game and self.selected_game[0] == game_id:
            self.hero_bg.set_hero_image(image_path)

    def _on_disk_size_calculated(self, game_id: int, size_bytes: int):
        if self.selected_game and self.selected_game[0] == game_id:
            self.detail_disk_size.setText(f"💾 Size: {format_size(size_bytes)}")

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
        self.detail_disk_size.setText("💾 Size: Calculating...")
        if path and os.path.exists(path):
            disk_thread = DiskSizeFetcherThread(game_id, path, parent=self)
            disk_thread.disk_size_calculated.connect(self._on_disk_size_calculated)
            self._track_metadata_fetcher(disk_thread)
        else:
            self.detail_disk_size.setText("💾 Size: --")

        local_build_id = game[11] if len(game) > 11 and game[11] else ""
        if steam_id and steam_id != "0" and game_id not in self.metadata_attempted_builds and not any(
            isinstance(fetcher, SteamBuildFetcher) and fetcher.game_id == game_id
            for fetcher in self.metadata_fetchers
        ):
            fetcher = SteamBuildFetcher(game_id, steam_id, local_build_id, parent=self)
            fetcher.update_checked.connect(self._on_steam_build_checked)
            self._track_metadata_fetcher(fetcher)
            self.metadata_attempted_builds.add(game_id)
        else:
            self.detail_update_widget.setVisible(False)

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
        self.btn_detail_screenshots.setText(f" Screenshots ({count})")

        self.btn_detail_fav.setChecked(is_fav)
        self.btn_detail_fav.setIcon(get_icon("ph.star-fill", color="#d9a441") if is_fav else get_icon("ph.star", color="#c9ccd2"))
        self.btn_detail_fav.setToolTip("Remove from Favorites" if is_fav else "Add to Favorites")

        self.btn_detail_launch.setEnabled(True)
        self.btn_detail_launch.setVisible(True)
        self.btn_detail_launch.raise_()
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
            if hasattr(self.runner, "set_proton_path"):
                self.runner.set_proton_path(selected_proton)
            process = self.runner.launch(path, exe, selected_mode, steam_id, sandbox=sandbox)
            if process:
                logger.info(f"Successfully launched '{game_name}' (PID: {process.pid})")
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
                popup = SafeLaunchDialog(game_name, user_name=self.user_name, process=process, parent=self)
                popup.retry_requested.connect(
                    lambda retry_mode: self._launch_mode(game_id, path, exe, retry_mode, sandbox=True)
                )
                popup.unsafe_launch_requested.connect(
                    lambda: self._launch_mode(game_id, path, exe, selected_mode, sandbox=False)
                )
                popup.show()
                QApplication.processEvents()
                popup.exec()
                if popup.requires_proton_setup:
                    logger.info("Proton setup requested by launch popup.")
                    wizard = ProtonSetupWizard(self.proton_path, self)
                    if wizard.exec() == QDialog.DialogCode.Accepted:
                        proton_path = wizard.get_path()
                        if proton_path:
                            self.proton_path = proton_path
                            self.settings.setValue("proton_path", proton_path)
                            if hasattr(self.runner, "set_proton_path"):
                                self.runner.set_proton_path(proton_path)
                        retry_mode = "umu_net" if wizard.retry_with_network else selected_mode
                        self._show_toast("Retrying Proton setup…")
                        self._launch_mode(game_id, path, exe, retry_mode)
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

    def closeEvent(self, event):
        """Stop all background workers before destroying the main window."""
        for fetcher in list(self.metadata_fetchers):
            if fetcher.isRunning():
                fetcher.requestInterruption()
        for fetcher in list(self.auto_fetchers):
            if fetcher.isRunning() and hasattr(fetcher, "requestInterruption"):
                fetcher.requestInterruption()
        for tracker in list(self.playtime_trackers):
            tracker.stop()

        extractor = getattr(self, "topbar_extractor_thread", None)
        if extractor and extractor.isRunning():
            extractor.requestInterruption()
            extractor.quit()
            extractor.wait(7000)
            if extractor.isRunning():
                QTimer.singleShot(100, self.close)
                event.ignore()
                return

        workers = list(self.metadata_fetchers) + list(self.auto_fetchers) + list(self.playtime_trackers)
        for worker in workers:
            if worker.isRunning():
                # Network requests use short timeouts, but allow enough time
                # for the active request to return before Qt destroys QThread.
                worker.wait(7000)

        # Never destroy a parented QThread while it is running, but also never
        # freeze the GUI indefinitely. Retry close after the bounded wait.
        if any(worker.isRunning() for worker in workers):
            QTimer.singleShot(100, self.close)
            event.ignore()
            return

        if hasattr(self, "discord_rpc") and self.discord_rpc:
            self.discord_rpc.clear_activity()
        super().closeEvent(event)

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

            resolved_path = os.path.realpath(os.path.expanduser(game_path))
            if dialog.choice == 'delete_disk':
                sandbox_root = os.path.realpath(os.path.expanduser(DEFAULT_SANDBOX_DIR))
                try:
                    inside_sandbox = os.path.commonpath([sandbox_root, resolved_path]) == sandbox_root
                except ValueError:
                    inside_sandbox = False
                if not inside_sandbox or resolved_path == sandbox_root:
                    self._show_toast("Refused to delete files outside the sandbox directory.", is_error=True)
                    return
            
            self.db.remove_game(game_id)
            
            if dialog.choice == 'delete_disk':
                if os.path.exists(resolved_path):
                    try:
                        shutil.rmtree(resolved_path)
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
