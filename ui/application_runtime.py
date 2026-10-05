"""Application-scoped service composition, independent of window widgets.

The window exposes compatibility references to these services, but does not
construct a second graph. Caller-supplied databases remain caller-owned.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from PyQt6.QtCore import QObject, QSettings

from core.achievement_persistence_service import AchievementPersistenceService
from core.achievement_resource_service import AchievementResourceService
from core.achievement_state_store import AchievementStateStore
from core.artwork_client import ArtworkClient
from core.artwork_resource_service import ArtworkResourceService
from core.central_auth import CentralAuthSession
from core.cloud_account_service import CloudAccountService
from core.cloud_center_service import CloudCenterService
from core.cloud_exit_sync_service import CloudExitSyncService
from core.cloud_metadata_service import CloudMetadataService
from core.cloud_operation_service import CloudOperationService
from core.cloud_operations import CloudSyncCoordinator
from core.cloud_status_service import CloudStatusService
from core.game_lifecycle_service import GameLifecycleService
from core.game_session import GameSessionManager
from core.library_achievement_coordinator import LibraryAchievementCoordinator
from core.library_artwork_coordinator import LibraryArtworkCoordinator
from core.library_controller import LibraryController
from core.library_metadata_state import LibraryMetadataState
from core.library_service import LibraryService
from core.library_state import LibraryStateStore
from core.library_steam_metadata_coordinator import LibrarySteamMetadataCoordinator
from core.logger import get_logger
from core.network_policy import automatic_network_allowed
from core.operation_registry import OperationRegistry
from core.performance_metrics import ResourcePerformanceTracker
from core.request_manager import RequestManager
from core.resource_cache import ResourceCache
from core.safe_thread import WorkerSupervisor
from core.save_state import SaveStateStore
from core.steam_client import SteamClient
from core.steam_resource_service import SteamResourceService


logger = get_logger("ApplicationRuntime")


def recommended_request_workers(cpu_count: int | None = None) -> int:
    """Pick a modest runtime worker bound without assuming network speed."""
    count = int(cpu_count or os.cpu_count() or 1)
    return min(6, max(4, count // 2))


class ApplicationRuntime(QObject):
    """Own the shared service graph and its transport lifetime.

    Construction unwinds already-created resources on failure. Disposal must
    run after feature subscriptions and supervised workers have drained.
    """

    def __init__(self, db, runner, backup, *, settings=None, parent=None):
        super().__init__(parent)
        self.db, self.runner, self.backup = db, runner, backup
        self.settings = settings if settings is not None else QSettings("SafeLauncher", "SafeLauncher")
        self._transient_network_unavailable: Callable[[], bool] = lambda: False
        self._cleanup: list[tuple[str, Callable[[], None]]] = []
        self._closed = False
        try:
            self.sgdb_client = ArtworkClient()
            self._own("artwork client", self.sgdb_client.close)
            self.steam_client = SteamClient()
            self._own("Steam client", self.steam_client.close)
            self.central_auth = CentralAuthSession()
            self._own("profile authentication", self.central_auth.close)
            self.cache_dir = self.sgdb_client.cache_dir
            self.resource_cache = ResourceCache(
                str(self.cache_dir.parent / "resources"),
                max_entries=512,
                max_disk_bytes=64 * 1024 * 1024,
                legacy_directories=(str(self.cache_dir / "resources"),),
            )
            self.performance_tracker = ResourcePerformanceTracker()
            self.library_metadata_state = LibraryMetadataState()
            self.save_state_store = SaveStateStore()
            self.cloud_sync_coordinator = CloudSyncCoordinator()
            self.achievement_state = AchievementStateStore()
            self.achievement_persistence_service = AchievementPersistenceService(db)
            self.library_controller = LibraryController()
            self.library_state = LibraryStateStore(self.library_controller)
            self.library_service = LibraryService(db, self.library_state)
            self.game_lifecycle_service = GameLifecycleService(self.library_service)
            self.request_manager = RequestManager(
                max_workers=recommended_request_workers(),
                cache=self.resource_cache, offline_check=self.network_unavailable,
            )
            self.achievement_resource_service = AchievementResourceService(self.request_manager)
            self.achievement_coordinator = LibraryAchievementCoordinator(self.achievement_resource_service)
            self.steam_resource_service = SteamResourceService(self.request_manager, client=self.steam_client)
            self.steam_metadata_coordinator = LibrarySteamMetadataCoordinator(self.steam_resource_service)
            self.artwork_resource_service = ArtworkResourceService(self.request_manager, client=self.sgdb_client)
            self.artwork_coordinator = LibraryArtworkCoordinator(self.artwork_resource_service)
            self.cloud_status_service = CloudStatusService(
                self.request_manager, coordinator=self.cloud_sync_coordinator,
                status_store=self.save_state_store, cache=self.resource_cache,
                legacy_cache_path=str(self.cache_dir.parent / "cloud_status_cache.json"),
            )
            self.cloud_operation_service = CloudOperationService(
                self.request_manager, coordinator=self.cloud_sync_coordinator,
            )
            self.cloud_exit_sync_service = CloudExitSyncService(self.cloud_operation_service)
            self.cloud_account_service = CloudAccountService(request_manager=self.request_manager)
            self._own("private cloud backend", self.cloud_account_service.reset_backend)
            self.cloud_metadata_service = CloudMetadataService(self.request_manager)
            self.cloud_center_service = CloudCenterService(
                self.request_manager, account_service=self.cloud_account_service,
                status_service=self.cloud_status_service, metadata_service=self.cloud_metadata_service,
                operation_service=self.cloud_operation_service, settings=self.settings,
            )
            self.worker_supervisor = WorkerSupervisor(self)
            self.game_sessions = GameSessionManager(self)
            self.operation_registry = OperationRegistry(self)
        except BaseException:
            self.close_resources()
            raise

    def _own(self, name: str, close: Callable[[], None]) -> None:
        self._cleanup.append((name, close))

    def set_network_gate(self, unavailable: Callable[[], bool]) -> None:
        self._transient_network_unavailable = unavailable

    def network_unavailable(self) -> bool:
        return not automatic_network_allowed(self.settings) or self._transient_network_unavailable()

    def close_resources(self) -> None:
        """Drain requests before closing clients; dispose each client once."""
        if self._closed:
            return
        sessions = getattr(self, "game_sessions", None)
        if sessions is not None:
            sessions.stop_observing()
        manager = getattr(self, "request_manager", None)
        if manager is not None:
            manager.shutdown(wait=True)
        self._closed = True
        while self._cleanup:
            name, close = self._cleanup.pop()
            try:
                close()
            except Exception:
                logger.exception("Could not close %s", name)
        self._transient_network_unavailable = lambda: False
