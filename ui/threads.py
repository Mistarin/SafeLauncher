import os
import subprocess
from typing import Optional
from PyQt6.QtCore import pyqtSignal

from core.safe_thread import SafeQThread
from core.steamgriddb_client import SteamGridDBClient
from core.archive_extractor import extract_archive_sandboxed
from core.proton_manager import fetch_online_ge_proton_releases
from database import _APP_DATA_DIR
from core.disk_utils import get_dir_size, format_size
from core.logger import get_logger

logger = get_logger("UIThreads")


class BannerFetcher(SafeQThread):
    """Background thread for searching game banners - thread-safe"""
    results_found = pyqtSignal(list)
    
    def __init__(self, game_name: str, sgdb_client: SteamGridDBClient):
        super().__init__()
        self.game_name = game_name
        self.sgdb_client = sgdb_client
    
    def safe_run(self):
        try:
            result = self.sgdb_client.search_game(self.game_name)
            if result and result.get('found') and result.get('results'):
                self.results_found.emit(result['results'])
            else:
                self.error_occurred.emit("No games found matching your search")
        except Exception as e:
            self.error_occurred.emit(f"Error searching banner: {str(e)}")


class BannerDownloader(SafeQThread):
    """Background thread for downloading selected banner image - thread-safe"""
    download_complete = pyqtSignal(str)
    download_failed = pyqtSignal(str)
    
    def __init__(self, banner_url: str, sgdb_client: SteamGridDBClient):
        super().__init__()
        self.banner_url = banner_url
        self.sgdb_client = sgdb_client
        
    def safe_run(self):
        try:
            path = self.sgdb_client.download_banner(self.banner_url)
            if path and os.path.exists(path):
                self.download_complete.emit(path)
            else:
                self.download_failed.emit("Failed to download banner image")
        except Exception as e:
            self.download_failed.emit(str(e))


class BannerAutoFetcher(SafeQThread):
    """Background thread to auto-fetch missing cover art and icons for games in library"""
    banner_auto_downloaded = pyqtSignal(int, str, int, str)  # (game_id, downloaded_file_path, appid, icon_path)
    
    def __init__(self, game_id: int, game_name: str, sgdb_client: SteamGridDBClient, exe_path: str = "", steam_id: str = ""):
        super().__init__()
        self.game_id = game_id
        self.game_name = game_name
        self.sgdb_client = sgdb_client
        self.exe_path = exe_path
        self.steam_id = steam_id
        
    def safe_run(self):
        try:
            if self.isInterruptionRequested():
                return
            res = self.sgdb_client.search_game(self.game_name)
            if self.isInterruptionRequested():
                return
            banner_path = ""
            appid = 0
            if res and res.get('found') and res.get('primary'):
                url = res['primary'].get('banner_url')
                appid = res['primary'].get('appid') or 0
                if url:
                    banner_path = self.sgdb_client.download_banner(url, self.game_id) or ""
            
            resolved_appid = str(appid) if appid else (self.steam_id or "")
            icon_path = self.sgdb_client.fetch_and_cache_game_icon(
                self.game_id, resolved_appid, self.game_name, exe_path=self.exe_path
            ) or ""
            
            if not self.isInterruptionRequested() and (banner_path or icon_path):
                self.banner_auto_downloaded.emit(self.game_id, banner_path, appid or 0, icon_path)
        except Exception as e:
            logger.warning(f"Error auto-fetching artwork for '{self.game_name}': {e}")


class ArchiveExtractorThread(SafeQThread):
    """Background thread for extracting game archives safely in Firejail sandbox"""
    extraction_complete = pyqtSignal(str, str, bool)  # (game_name, dest_dir, success)
    extraction_progress = pyqtSignal(int)
    
    def __init__(self, archive_path: str, dest_dir: str):
        super().__init__()
        self.archive_path = archive_path
        self.dest_dir = dest_dir
        
    def safe_run(self):
        game_name = os.path.splitext(os.path.basename(self.archive_path))[0]
        if game_name.endswith(".tar"):
            game_name = os.path.splitext(game_name)[0]
        success = extract_archive_sandboxed(
            self.archive_path,
            self.dest_dir,
            cancel_callback=self.isInterruptionRequested,
            progress_callback=self.extraction_progress.emit,
        )
        if not self.isInterruptionRequested():
            self.extraction_complete.emit(game_name, self.dest_dir, success)


class GitHubReleasesFetcherThread(SafeQThread):
    releases_fetched = pyqtSignal(list)
    fetch_failed = pyqtSignal(str)

    def safe_run(self):
        try:
            releases = fetch_online_ge_proton_releases(max_results=12)
            self.releases_fetched.emit(releases)
        except Exception as e:
            self.fetch_failed.emit(str(e))


class UmuBootstrapWorker(SafeQThread):
    """Provision UMU's Proton and Steam Runtime using an explicit network step."""
    output_line = pyqtSignal(str)
    completed = pyqtSignal(bool, int)

    def __init__(self, proton_path: str = "", parent=None):
        super().__init__(parent)
        self.proton_path = proton_path.strip() or "GE-Proton"
        self.process = None

    def stop(self):
        """Stop the child process before allowing the QThread to be destroyed."""
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def safe_run(self):
        try:
            prefix = os.path.join(_APP_DATA_DIR, "umu-bootstrap-prefix")
            os.makedirs(prefix, mode=0o700, exist_ok=True)
            env = os.environ.copy()
            env["WINEPREFIX"] = prefix
            if self.proton_path:
                env["PROTONPATH"] = self.proton_path

            self.process = subprocess.Popen(
                ["umu-run", "cmd.exe", "/c", "exit"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            for line in iter(self.process.stdout.readline, ""):
                if self.isInterruptionRequested():
                    self.stop()
                    break
                stripped = line.rstrip()
                if stripped:
                    self.output_line.emit(stripped)
            ret = self.process.wait() if self.process else -1
            if not self.isInterruptionRequested():
                self.completed.emit(ret == 0, ret)
        except Exception as e:
            logger.error(f"UMU bootstrap failed: {e}")
            if not self.isInterruptionRequested():
                self.completed.emit(False, -1)


class SafeLaunchLogReader(SafeQThread):
    """Background reader thread to stream stdout/stderr lines from Firejail process to SafeLaunchDialog console."""
    log_line = pyqtSignal(str)

    def __init__(self, process, parent=None):
        super().__init__(parent)
        self.process = process

    def stop(self):
        """Unblock readline and wait until the reader thread has exited."""
        self.requestInterruption()
        stdout = getattr(self.process, "stdout", None)
        if stdout:
            try:
                stdout.close()
            except (OSError, ValueError):
                pass
        if self.isRunning():
            self.wait(3000)
        if self.isRunning():
            self.wait()

    def safe_run(self):
        if not self.process or not getattr(self.process, 'stdout', None):
            return
        try:
            for line in iter(self.process.stdout.readline, ''):
                if self.isInterruptionRequested():
                    break
                if not line:
                    break
                self.log_line.emit(line.rstrip('\r\n'))
        except (ValueError, OSError):
            pass


class DiskSizeFetcherThread(SafeQThread):
    """Background QThread for calculating directory disk size without blocking GUI main thread."""
    disk_size_calculated = pyqtSignal(int, object)  # (game_id, bytes)

    def __init__(self, game_id: int, path: str, parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.path = path

    def safe_run(self):
        if self.isInterruptionRequested() or not self.path or not os.path.exists(self.path):
            return
        size = get_dir_size(self.path)
        logger.debug(f"Calculated disk size for game {self.game_id}: {size} bytes ({format_size(size)})")
        if not self.isInterruptionRequested():
            self.disk_size_calculated.emit(self.game_id, size)


class HeroFetcherThread(SafeQThread):
    """Background QThread for downloading wide hero banners without blocking GUI main thread."""
    hero_downloaded = pyqtSignal(int, str)  # (game_id, image_path)

    def __init__(self, game_id: int, name: str, steam_id: Optional[int], sgdb_client: SteamGridDBClient, parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.name = name
        self.steam_id = steam_id
        self.sgdb_client = sgdb_client

    def safe_run(self):
        if self.isInterruptionRequested():
            return
        hero_path = self.sgdb_client.download_hero_banner(self.steam_id, self.game_id, self.name)
        if not self.isInterruptionRequested() and hero_path and os.path.exists(hero_path):
            self.hero_downloaded.emit(self.game_id, hero_path)


class IconAutoFetcherThread(SafeQThread):
    """Background QThread for downloading game icons without blocking GUI main thread."""
    icon_downloaded = pyqtSignal(int, str)  # (game_id, icon_path)

    def __init__(self, game_id: int, name: str, steam_id: Optional[str], sgdb_client: SteamGridDBClient, exe_path: str = "", parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.name = name
        self.steam_id = steam_id
        self.sgdb_client = sgdb_client
        self.exe_path = exe_path

    def safe_run(self):
        if self.isInterruptionRequested():
            return
        icon_path = self.sgdb_client.fetch_and_cache_game_icon(self.game_id, self.steam_id, self.name, exe_path=self.exe_path)
        if not self.isInterruptionRequested() and icon_path and os.path.exists(icon_path):
            self.icon_downloaded.emit(self.game_id, icon_path)

