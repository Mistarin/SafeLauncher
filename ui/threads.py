"""Compatibility QThread wrappers and explicit local/process workers.

Manager-backed callers must use the resource services.  The remote worker
classes in this module remain only for manager-less dialogs, plugins, and
older embedded integrations; when a RequestManager is supplied they delegate
to it.  The remaining workers are explicit file/process operations.
"""

import os
import subprocess
from typing import Optional
from PyQt6.QtCore import pyqtSignal

from core.safe_thread import SafeQThread
from core.steamgriddb_client import SteamGridDBClient
from core.archive_extractor import extract_archive_sandboxed
from core.proton_manager import fetch_online_ge_proton_releases
from database import _APP_DATA_DIR
from core.disk_utils import get_dir_size, format_size, peek_dir_size
from core.logger import get_logger
from core.network_policy import automatic_network_allowed
from core.cache_policy import cache_policy

logger = get_logger("UIThreads")

# Machine-readable inventory used by architecture audits and downstream
# embedders. These classes must not become a second production request pool.
COMPATIBILITY_REMOTE_WORKERS = frozenset({
    "BannerFetcher", "BannerDownloader", "BannerAutoFetcher",
    "HeroFetcherThread", "IconAutoFetcherThread",
    "CloudSaveStatusFetcherThread", "CloudSaveBatchQueueWorker",
    "AchievementStatusFetcherThread", "AchievementBatchQueueWorker",
})


class BannerFetcher(SafeQThread):
    """Background thread for searching game banners - thread-safe"""
    results_found = pyqtSignal(list)
    
    def __init__(self, game_name: str, sgdb_client: SteamGridDBClient, parent=None, request_manager=None):
        super().__init__(parent)
        self.game_name = game_name
        self.sgdb_client = sgdb_client
        self.request_manager = request_manager
    
    def safe_run(self):
        try:
            if not automatic_network_allowed() and self.request_manager is None:
                self.error_occurred.emit("Offline mode is enabled")
                return
            if self.request_manager is not None:
                from core.request_contracts import RequestKey, RequestPriority, ResourceStatus
                key = RequestKey("artwork-search", self.game_name.strip().casefold() or "empty")
                loader = lambda token: self._search(token)
                if getattr(self.request_manager, "cache", None) is not None:
                    handle = self.request_manager.request_cached(
                        key,
                        loader,
                        max_age_seconds=cache_policy("artwork-search").max_age_seconds,
                        priority=RequestPriority.NORMAL,
                        timeout_seconds=15,
                    )
                else:
                    handle = self.request_manager.request(
                        key,
                        loader,
                        priority=RequestPriority.NORMAL,
                        timeout_seconds=15,
                    )
                request_result = handle.future.result()
                if request_result.status != ResourceStatus.READY:
                    state = self.request_manager.state(key)
                    if state.status == ResourceStatus.STALE:
                        request_result = state
                result = request_result.value if request_result.status in {
                    ResourceStatus.READY,
                    ResourceStatus.STALE,
                } else None
            else:
                result = self._search(None)
            if result and result.get('found') and result.get('results'):
                self.results_found.emit(result['results'])
            else:
                self.error_occurred.emit("No games found matching your search")
        except Exception as e:
            self.error_occurred.emit(f"Error searching banner: {str(e)}")

    def _search(self, token):
        if token:
            token.raise_if_cancelled()
        result = self.sgdb_client.search_game(self.game_name)
        if token:
            token.raise_if_cancelled()
        return result


class BannerDownloader(SafeQThread):
    """Background thread for downloading selected banner image - thread-safe"""
    download_complete = pyqtSignal(str)
    download_failed = pyqtSignal(str)
    
    def __init__(self, banner_url: str, sgdb_client: SteamGridDBClient, parent=None, request_manager=None):
        super().__init__(parent)
        self.banner_url = banner_url
        self.sgdb_client = sgdb_client
        self.request_manager = request_manager
        
    def safe_run(self):
        try:
            if not automatic_network_allowed():
                self.download_failed.emit("Offline mode is enabled")
                return
            if self.request_manager is not None:
                from core.request_contracts import RequestKey, RequestPriority, ResourceStatus
                result = self.request_manager.request(
                    RequestKey("artwork-banner", self.banner_url),
                    lambda token: self._download(token),
                    priority=RequestPriority.NORMAL,
                ).future.result()
                path = result.value if result.status == ResourceStatus.READY else ""
            else:
                path = self._download(None)
            if path and os.path.exists(path):
                self.download_complete.emit(path)
            else:
                self.download_failed.emit("Failed to download banner image")
        except Exception as e:
            self.download_failed.emit(str(e))

    def _download(self, token):
        if token:
            token.raise_if_cancelled()
        result = self.sgdb_client.download_banner(self.banner_url)
        if token:
            token.raise_if_cancelled()
        return result


class BannerAutoFetcher(SafeQThread):
    """Background thread to auto-fetch missing cover art and icons for games in library"""
    banner_auto_downloaded = pyqtSignal(int, str, int, str)  # (game_id, downloaded_file_path, appid, icon_path)
    
    def __init__(self, game_id: int, game_name: str, sgdb_client: SteamGridDBClient, exe_path: str = "", steam_id: str = "", request_manager=None):
        super().__init__()
        self.game_id = game_id
        self.game_name = game_name
        self.sgdb_client = sgdb_client
        self.exe_path = exe_path
        self.steam_id = steam_id
        self.request_manager = request_manager
        
    def safe_run(self):
        if self.request_manager is not None:
            from core.request_contracts import RequestKey, RequestPriority, ResourceStatus
            key = RequestKey("artwork-auto", f"{self.game_id}:{self.steam_id or self.game_name}")
            handle = self.request_manager.request(
                key,
                lambda token: self._fetch_artwork(token),
                priority=RequestPriority.BACKGROUND,
            )
            result = handle.future.result()
            if result.status == ResourceStatus.READY and result.value and not self.isInterruptionRequested():
                banner_path, appid, icon_path = result.value
                if banner_path or icon_path:
                    self.banner_auto_downloaded.emit(self.game_id, banner_path, appid, icon_path)
            return
        self._fetch_artwork(None)

    def _fetch_artwork(self, token):
        try:
            if (token and token.cancelled) or self.isInterruptionRequested() or not automatic_network_allowed():
                return
            res = self.sgdb_client.search_game(self.game_name)
            if (token and token.cancelled) or self.isInterruptionRequested():
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
            
            if token:
                token.raise_if_cancelled()
            if self.request_manager is not None:
                return banner_path, appid or 0, icon_path
            if not self.isInterruptionRequested() and (banner_path or icon_path):
                self.banner_auto_downloaded.emit(self.game_id, banner_path, appid or 0, icon_path)
        except Exception as e:
            logger.warning(f"Error auto-fetching artwork for '{self.game_name}': {e}")


class ArchiveExtractorThread(SafeQThread):
    """Background thread for extracting game archives safely in Firejail sandbox"""
    extraction_complete = pyqtSignal(str, str, bool)  # (game_name, dest_dir, success)
    extraction_progress = pyqtSignal(int)
    
    def __init__(self, archive_path: str, dest_dir: str, parent=None):
        super().__init__(parent)
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
            if not automatic_network_allowed():
                self.fetch_failed.emit("Offline mode is enabled")
                return
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
        self.request_cancel()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def safe_run(self):
        if not automatic_network_allowed():
            self.completed.emit(False, 0)
            return
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
                    self.request_cancel()
                    self._terminate_process()
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
        finally:
            stdout = getattr(self.process, "stdout", None) if self.process else None
            if stdout is not None:
                try:
                    stdout.close()
                except (OSError, ValueError):
                    pass
            if self.process and self.process.poll() is None:
                self._terminate_process()
            self.process = None

    def _terminate_process(self) -> None:
        """Terminate and reap the bootstrap child without waiting on this QThread."""
        process = self.process
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        except OSError:
            pass


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
        cached = peek_dir_size(self.path)
        if cached is not None:
            size = cached
        else:
            size = get_dir_size(
                self.path,
                use_cache=False,
                cancel_callback=self.isInterruptionRequested,
            )
            if self.isInterruptionRequested():
                return
        logger.debug(f"Calculated disk size for game {self.game_id}: {size} bytes ({format_size(size)})")
        if not self.isInterruptionRequested():
            self.disk_size_calculated.emit(self.game_id, size)


class HeroFetcherThread(SafeQThread):
    """Background QThread for downloading wide hero banners without blocking GUI main thread."""
    hero_downloaded = pyqtSignal(int, str)  # (game_id, image_path)

    def __init__(self, game_id: int, name: str, steam_id: Optional[int], sgdb_client: SteamGridDBClient, exe_path: str = "", parent=None, request_manager=None):
        super().__init__(parent)
        self.game_id = game_id
        self.name = name
        self.steam_id = steam_id
        self.sgdb_client = sgdb_client
        self.exe_path = exe_path
        self.request_manager = request_manager

    def safe_run(self):
        # A cached hero is handled before this worker is created; this worker
        # is transport-only and must never start in offline mode.
        if self.isInterruptionRequested() or not automatic_network_allowed():
            return
        if self.request_manager is not None:
            from core.request_contracts import RequestKey, RequestPriority, ResourceStatus
            handle = self.request_manager.request(
                RequestKey("artwork-hero", f"{self.game_id}:{self.steam_id or self.name}"),
                lambda token: self._download_hero(token),
                priority=RequestPriority.NORMAL,
            )
            result = handle.future.result()
            hero_path = result.value if result.status == ResourceStatus.READY else ""
        else:
            hero_path = self._download_hero(None)
        if not self.isInterruptionRequested() and hero_path and os.path.exists(hero_path):
            self.hero_downloaded.emit(self.game_id, hero_path)

    def _download_hero(self, token):
        if token:
            token.raise_if_cancelled()
        result = self.sgdb_client.download_hero_banner(self.steam_id, self.game_id, self.name, exe_path=self.exe_path)
        if token:
            token.raise_if_cancelled()
        return result


class IconAutoFetcherThread(SafeQThread):
    """Background QThread for downloading game icons without blocking GUI main thread."""
    icon_downloaded = pyqtSignal(int, str)  # (game_id, icon_path)

    def __init__(self, game_id: int, name: str, steam_id: Optional[str], sgdb_client: SteamGridDBClient, exe_path: str = "", parent=None, request_manager=None):
        super().__init__(parent)
        self.game_id = game_id
        self.name = name
        self.steam_id = steam_id
        self.sgdb_client = sgdb_client
        self.exe_path = exe_path
        self.request_manager = request_manager

    def safe_run(self):
        if self.isInterruptionRequested() or not automatic_network_allowed():
            return
        if self.request_manager is not None:
            from core.request_contracts import RequestKey, RequestPriority, ResourceStatus
            handle = self.request_manager.request(
                RequestKey("artwork-icon", f"{self.game_id}:{self.steam_id or self.name}"),
                lambda token: self._download_icon(token),
                priority=RequestPriority.NORMAL,
            )
            result = handle.future.result()
            icon_path = result.value if result.status == ResourceStatus.READY else ""
        else:
            icon_path = self._download_icon(None)
        if not self.isInterruptionRequested() and icon_path and os.path.exists(icon_path):
            self.icon_downloaded.emit(self.game_id, icon_path)

    def _download_icon(self, token):
        if token:
            token.raise_if_cancelled()
        result = self.sgdb_client.fetch_and_cache_game_icon(self.game_id, self.steam_id, self.name, exe_path=self.exe_path)
        if token:
            token.raise_if_cancelled()
        return result


class CloudSaveStatusFetcherThread(SafeQThread):
    """Background QThread for checking local and cloud save status without blocking GUI."""
    save_status_calculated = pyqtSignal(int, object, object, object)  # (game_id, status, local_stats, cloud_stats)

    def __init__(self, game_id: int, game_name: str, path: str, steam_id: str = "", parent=None, coordinator=None, request_manager=None):
        super().__init__(parent)
        self.game_id = game_id
        self.game_name = game_name
        self.path = path
        self.steam_id = steam_id
        self.coordinator = coordinator
        self.request_manager = request_manager

    def safe_run(self):
        if self.isInterruptionRequested() or not automatic_network_allowed():
            return
        if self.request_manager is not None:
            from core.cloud_status_service import CloudStatusService, CloudStatusTarget
            from core.request_contracts import ResourceStatus

            service = CloudStatusService(
                self.request_manager,
                coordinator=self.coordinator,
            )
            handle = service.request_status(
                CloudStatusTarget(
                    self.game_id, self.game_name, self.path, self.steam_id
                )
            )
            result = handle.future.result()
            if (
                result.status == ResourceStatus.READY
                and result.value is not None
                and not self.isInterruptionRequested()
            ):
                status = result.value.status
                local_stats = result.value.local_stats
                cloud_stats = result.value.cloud_stats
                self.save_status_calculated.emit(
                    self.game_id, status, local_stats, cloud_stats
                )
            elif result.status == ResourceStatus.ERROR:
                logger.debug(
                    "Managed cloud status request failed for %s: %s",
                    self.game_name,
                    result.error,
                )
            return
        try:
            from core.cloud_operations import CloudSyncCoordinator
            from core.cloud_save_sync import SyncStatus
            coordinator = self.coordinator or CloudSyncCoordinator()
            result = coordinator.check_status(
                self.game_id, self.game_name, self.path, self.steam_id
            )
            status = result.status or SyncStatus.CLOUD_OFFLINE
            local_stats, cloud_stats = result.local_stats, result.cloud_stats
            if not self.isInterruptionRequested():
                self.save_status_calculated.emit(self.game_id, status, local_stats, cloud_stats)
        except Exception as e:
            logger.debug(f"CloudSaveStatusFetcherThread error for game {self.game_id}: {e}")


class CloudSaveBatchQueueWorker(SafeQThread):
    """Compatibility worker for cloud-save status batches.

    Managed callers use ``CloudStatusService``. Manager-less callers remain on
    this owning worker thread and are processed cooperatively without a
    second scheduler.
    """
    game_status_ready = pyqtSignal(int, object, object, object)  # (game_id, status, local_stats, cloud_stats)
    batch_finished = pyqtSignal(list, list)  # (uploaded_names, newer_in_cloud_names)

    def __init__(self, games: list, max_workers: Optional[int] = None, parent=None, coordinator=None, request_manager=None):
        super().__init__(parent)
        self.games = list(games)
        if max_workers is None:
            from PyQt6.QtCore import QSettings
            max_workers = QSettings("SafeLauncher", "SafeLauncher").value("cloud_sync_workers", 3, type=int)
        self.max_workers = max(1, min(max_workers, 5))
        self.coordinator = coordinator
        self.request_manager = request_manager

    def _safe_run_managed(self):
        from concurrent.futures import as_completed
        from core.cloud_status_service import CloudStatusService, CloudStatusTarget
        from core.cloud_save_sync import SyncStatus
        from core.request_contracts import ResourceStatus

        uploaded_names = []
        newer_in_cloud_names = []
        targets = []
        for game in self.games:
            if len(game) < 4:
                continue
            game_id, name, path = game[0], game[1], game[2]
            steam_id = str(game[6]).strip() if len(game) >= 7 and game[6] else str(game[3]).strip()
            target = CloudStatusTarget(int(game_id), str(name), str(path or ""), steam_id)
            targets.append(target)

        service = CloudStatusService(
            self.request_manager,
            coordinator=self.coordinator,
        )
        handles = service.request_many(targets)
        target_by_key = {service.key_for(target): target for target in targets}
        future_to_handle = {item.future: item for item in handles}
        try:
            for future in as_completed(future_to_handle):
                if self.isInterruptionRequested():
                    for pending in handles:
                        pending.cancel()
                    break
                handle = future_to_handle[future]
                result = future.result()
                if result.status != ResourceStatus.READY or not result.value:
                    continue
                target = target_by_key.get(handle.key)
                if target is None:
                    continue
                gid, name = target.game_id, target.game_name
                status = result.value.status
                local_stats = result.value.local_stats
                cloud_stats = result.value.cloud_stats
                if status == SyncStatus.LOCAL_NEWER:
                    uploaded_names.append(name)
                elif status in (SyncStatus.CLOUD_NEWER, SyncStatus.CLOUD_ONLY):
                    newer_in_cloud_names.append(name)
                self.game_status_ready.emit(gid, status, local_stats, cloud_stats)
        finally:
            for pending in handles:
                if self.isInterruptionRequested():
                    pending.cancel()
        if not self.isInterruptionRequested():
            self.batch_finished.emit(uploaded_names, newer_in_cloud_names)

    def safe_run(self):
        if self.isInterruptionRequested() or not self.games or not automatic_network_allowed():
            return
        if self.request_manager is not None:
            self._safe_run_managed()
            return

        from core.cloud_save_sync import SyncStatus, _get_cloud_listing

        try:
            _get_cloud_listing(force_refresh=True)
        except Exception as e:
            logger.debug(f"Startup cloud listing refresh failed (offline?): {e}")

        uploaded_names = []
        newer_in_cloud_names = []

        def _check_game(g):
            if self.isInterruptionRequested() or len(g) < 4:
                return None
            if len(g) >= 7:
                game_id, name, path = g[0], g[1], g[2]
                steam_id = str(g[6]).strip() if g[6] else ""
            else:
                game_id, name, path, steam_id = g[0], g[1], g[2], str(g[3]).strip()
            try:
                from core.cloud_operations import CloudSyncCoordinator
                from core.cloud_save_sync import SyncStatus
                coordinator = self.coordinator or CloudSyncCoordinator()
                result = coordinator.check_status(game_id, name, path, steam_id)
                status = result.status or SyncStatus.CLOUD_OFFLINE
                l_stat, c_stat = result.local_stats, result.cloud_stats
                if status == SyncStatus.LOCAL_NEWER:
                    uploaded_names.append(name)
                elif status in (SyncStatus.CLOUD_NEWER, SyncStatus.CLOUD_ONLY):
                    newer_in_cloud_names.append(name)
                return (game_id, status, l_stat, c_stat)
            except Exception as e:
                logger.debug(f"CloudSaveBatchQueueWorker error for '{name}': {e}")
                return None

        # This branch is only for legacy callers that do not supply the
        # application RequestManager. The owning SafeQThread already keeps
        # the work off the UI thread, so do not create a second scheduler.
        for game in self.games:
            if self.isInterruptionRequested():
                break
            try:
                res = _check_game(game)
                if res and not self.isInterruptionRequested():
                    gid, stat, l_stat, c_stat = res
                    self.game_status_ready.emit(gid, stat, l_stat, c_stat)
            except Exception as e:
                logger.debug(f"Failed processing game cloud status: {e}")

        if not self.isInterruptionRequested():
            self.batch_finished.emit(uploaded_names, newer_in_cloud_names)


class AchievementStatusFetcherThread(SafeQThread):
    """Background QThread for checking/fetching achievement schemas and syncing local unlocks without blocking GUI."""
    achievement_status_calculated = pyqtSignal(int, int, int, float, list)  # (game_id, unlocked_count, total_count, pct, recent_unlocked)
    resolution_ready = pyqtSignal(int, str, object)  # (game_id, app_id, AchievementResolution)

    def __init__(
        self,
        game_id: int,
        game_name: str,
        path: str,
        steam_id: str = "",
        proton_path: str = "",
        db_path: Optional[str] = None,
        parent=None,
        request_manager=None,
    ):
        super().__init__(parent)
        self.game_id = game_id
        self.game_name = game_name
        self.path = path
        self.steam_id = str(steam_id).strip()
        self.proton_path = proton_path
        self.db_path = db_path
        self.request_manager = request_manager

    def safe_run(self):
        # The resolver is local-first and its remote providers enforce the
        # network policy themselves. Keep this worker available for local
        # achievement files while offline.
        if self.isInterruptionRequested():
            return
        if self.request_manager is not None:
            from core.achievement_resource_service import (
                AchievementResourceService,
                AchievementTarget,
            )
            from core.request_contracts import RequestPriority, ResourceStatus

            service = AchievementResourceService(self.request_manager)
            handle = service.request_status(
                AchievementTarget(
                    self.game_id,
                    self.steam_id,
                    self.path,
                    self.proton_path,
                ),
                db_path=self.db_path,
                priority=RequestPriority.NORMAL,
                tag="compatibility-worker",
            )
            result = handle.future.result()
            if result.status == ResourceStatus.READY and result.value and not self.isInterruptionRequested():
                resolution, unlocked_cnt, total_cnt, pct, recent = result.value
                self.resolution_ready.emit(self.game_id, self.steam_id, resolution)
                self.achievement_status_calculated.emit(
                    self.game_id, unlocked_cnt, total_cnt, pct, recent
                )
            elif result.status == ResourceStatus.ERROR:
                logger.debug(
                    "Managed achievement request failed for %s: %s",
                    self.game_id,
                    result.error,
                )
            return
        db = None
        try:
            from database import GameDatabase
            from core.achievement_coordinator import coordinated_resolve
            from core.achievement_persistence import persist_resolution

            db = GameDatabase(self.db_path) if self.db_path else GameDatabase()
            app_id = self.steam_id

            resolution = coordinated_resolve(app_id, self.path, self.proton_path) if app_id else None
            if resolution is not None and not self.isInterruptionRequested():
                self.resolution_ready.emit(self.game_id, app_id, resolution)
            if resolution:
                persist_resolution(db, self.game_id, app_id, resolution)

            unlocked_cnt, total_cnt, pct = db.get_achievement_stats(self.game_id)
            if total_cnt == 0 and app_id:
                achs = resolution.schema if resolution else []
                if achs:
                    db.save_achievement_schema(self.game_id, app_id, achs)
                    if resolution:
                        persist_resolution(db, self.game_id, app_id, resolution)
                    unlocked_cnt, total_cnt, pct = db.get_achievement_stats(self.game_id)

            recent = db.get_recent_unlocked_achievements(self.game_id, limit=5)
            if not self.isInterruptionRequested():
                self.achievement_status_calculated.emit(self.game_id, unlocked_cnt, total_cnt, pct, recent)
        except Exception as e:
            logger.debug(f"AchievementStatusFetcherThread error for game {self.game_id} ({self.game_name}): {e}")
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass


class AchievementBatchQueueWorker(SafeQThread):
    """Compatibility worker for achievement batches.

    Managed callers use ``AchievementResourceService``. Manager-less callers
    remain on this owning worker thread without a second scheduler.
    """
    game_status_ready = pyqtSignal(int, int, int, float, list)  # (game_id, unlocked_count, total_count, pct, recent_unlocked)
    batch_finished = pyqtSignal(int, int)  # (games_with_achievements_count, total_unlocked_count)

    def __init__(self, games: list, max_workers: Optional[int] = None, db_path: Optional[str] = None, parent=None, request_manager=None):
        super().__init__(parent)
        self.games = list(games)
        self.db_path = db_path
        self.request_manager = request_manager
        if max_workers is None:
            from PyQt6.QtCore import QSettings
            max_workers = QSettings("SafeLauncher", "SafeLauncher").value("achievement_sync_workers", 3, type=int)
        self.max_workers = max(1, min(max_workers, 5))

    def _safe_run_managed(self):
        from concurrent.futures import as_completed
        from core.achievement_resource_service import (
            AchievementResourceService,
            AchievementTarget,
        )
        from core.request_contracts import ResourceStatus

        targets = []
        for game in self.games:
            if len(game) < 3:
                continue
            game_id, path = game[0], game[2]
            steam_id = str(game[6]).strip() if len(game) > 6 and game[6] else ""
            proton_path = str(game[12]).strip() if len(game) > 12 and game[12] else ""
            if not steam_id:
                continue
            targets.append(
                AchievementTarget(
                    int(game_id), steam_id, str(path or ""), proton_path
                )
            )

        service = AchievementResourceService(self.request_manager)
        handles = service.request_many(
            targets,
            db_path=self.db_path,
        )
        target_by_key = {service.key_for(target): target for target in targets}
        total_games_with_achs = 0
        total_unlocked_overall = 0
        future_to_handle = {item.future: item for item in handles}
        try:
            for future in as_completed(future_to_handle):
                if self.isInterruptionRequested():
                    for pending in handles:
                        pending.cancel()
                    break
                handle = future_to_handle[future]
                result = future.result()
                if result.status != ResourceStatus.READY or not result.value:
                    continue
                target = target_by_key.get(handle.key)
                if target is None:
                    continue
                gid = target.game_id
                unlocked_cnt, total_cnt, pct, recent = result.value
                if total_cnt > 0:
                    total_games_with_achs += 1
                    total_unlocked_overall += unlocked_cnt
                self.game_status_ready.emit(gid, unlocked_cnt, total_cnt, pct, recent)
        finally:
            for pending in handles:
                if self.isInterruptionRequested():
                    pending.cancel()
        if not self.isInterruptionRequested():
            self.batch_finished.emit(total_games_with_achs, total_unlocked_overall)

    def safe_run(self):
        # Local achievement state remains useful offline; the provider layer
        # skips its public/Steam transport when the policy is disabled.
        if self.isInterruptionRequested() or not self.games:
            return
        if self.request_manager is not None:
            self._safe_run_managed()
            return

        from database import GameDatabase
        from core.achievement_coordinator import coordinated_resolve
        from core.achievement_persistence import persist_resolution

        total_games_with_achs = 0
        total_unlocked_overall = 0

        def _sync_game_achievements(g):
            if self.isInterruptionRequested() or len(g) < 3:
                return None
            game_id = g[0]
            name = g[1]
            path = g[2]
            steam_id = str(g[6]).strip() if len(g) > 6 and g[6] else ""
            # GameRecord layout: index 9 is last_played; index 12 is proton_path.
            proton_path = str(g[12]).strip() if len(g) > 12 and g[12] else ""
            if not steam_id:
                return None

            try:
                db = GameDatabase(self.db_path) if self.db_path else GameDatabase()
                try:
                    resolution = coordinated_resolve(
                        steam_id, path, proton_path,
                        request_manager=self.request_manager,
                    )
                    persist_resolution(db, game_id, steam_id, resolution)

                    unlocked_cnt, total_cnt, pct = db.get_achievement_stats(game_id)
                    if total_cnt == 0:
                        achs = resolution.schema
                        if achs:
                            persist_resolution(db, game_id, steam_id, resolution)
                            unlocked_cnt, total_cnt, pct = db.get_achievement_stats(game_id)

                    recent = db.get_recent_unlocked_achievements(game_id, limit=5)
                    return (game_id, unlocked_cnt, total_cnt, pct, recent)
                finally:
                    try:
                        db.close()
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"AchievementBatchQueueWorker error for '{name}' (ID {game_id}): {e}")
                return None


        # Manager-less compatibility mode remains on this worker's thread.
        # Managed callers use AchievementResourceService.request_many above.
        for game in self.games:
            if self.isInterruptionRequested():
                break
            try:
                res = _sync_game_achievements(game)
                if res and not self.isInterruptionRequested():
                    gid, unlocked_cnt, total_cnt, pct, recent = res
                    if total_cnt > 0:
                        total_games_with_achs += 1
                        total_unlocked_overall += unlocked_cnt
                    self.game_status_ready.emit(gid, unlocked_cnt, total_cnt, pct, recent)
            except Exception as e:
                logger.debug(f"Failed processing game achievements: {e}")

        if not self.isInterruptionRequested():
            self.batch_finished.emit(total_games_with_achs, total_unlocked_overall)
