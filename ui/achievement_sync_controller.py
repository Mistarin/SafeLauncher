"""Application-level achievement request orchestration for the UI."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import time
from typing import Any, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.achievement_resource_service import AchievementResourceService, AchievementTarget
from core.library_achievement_coordinator import LibraryAchievementCoordinator
from core.logger import get_logger
from core.request_contracts import RequestKey, RequestPriority, ResourceStatus
from core.request_manager import RequestManager
from ui.resource_binding import bind_resource
from ui.threads import AchievementBatchQueueWorker, AchievementStatusFetcherThread


logger = get_logger("UI.Achievements")


class AchievementSyncController(QObject):
    """Own achievement request scheduling and managed binding lifecycles.

    UI state and persistence remain owned by their existing services/window
    callbacks. Dependencies are passed explicitly so this controller does not
    reach through a MainWindow instance.
    """

    _managed_batch_done = pyqtSignal(object)

    def __init__(
        self,
        *,
        request_manager: RequestManager | None,
        resource_service: AchievementResourceService,
        coordinator: LibraryAchievementCoordinator,
        state_store: Any,
        persistence_service: Any,
        settings: Any,
        games_provider: Callable[[], list],
        selected_game_provider: Callable[[], Any],
        db_path_provider: Callable[[], str | None],
        online_allowed: Callable[[], bool],
        workers_provider: Callable[[], Iterable],
        track_worker: Callable[[Any], None],
        save_cache: Callable[[], None],
        sync_launcher_metadata: Callable[[int], None],
        mark_profile_changed: Callable[[], None],
        refresh_inspector: Callable[[int, str], None],
        refresh_compact_page: Callable[[], None],
        on_batch_finished: Callable[[int, int], None],
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._request_manager = request_manager
        self._resource_service = resource_service
        self._coordinator = coordinator
        self._state_store = state_store
        self._persistence_service = persistence_service
        self._settings = settings
        self._games_provider = games_provider
        self._selected_game_provider = selected_game_provider
        self._db_path_provider = db_path_provider
        self._online_allowed = online_allowed
        self._workers_provider = workers_provider
        self._track_worker = track_worker
        self._save_cache = save_cache
        self._sync_launcher_metadata = sync_launcher_metadata
        self._mark_profile_changed = mark_profile_changed
        self._refresh_inspector = refresh_inspector
        self._refresh_compact_page = refresh_compact_page
        self._on_batch_finished = on_batch_finished
        self._active_toasts: list[Any] = []
        self.watchers: dict[int, Any] = {}
        self._exit_timers: dict[int, QTimer] = {}
        self._suspended = False
        self._managed_batch_done.connect(self.handle_managed_batch_done)

    def start_watching(self, game_id: int, app_id: str, prefix: str, path: str) -> None:
        """Local unlock observation does not depend on network availability."""
        if self._suspended or not app_id or str(app_id).strip() == "0":
            return
        from core.achievement_watcher import AchievementWatcher

        self.stop_watching(game_id)
        watcher = AchievementWatcher(game_id, str(app_id).strip(), prefix, path, parent=self)
        watcher.achievement_unlocked.connect(self.handle_unlock)
        watcher.state_refreshed.connect(self.handle_state_refreshed)
        self.watchers[game_id] = watcher
        try:
            watcher.start()
            self.request_recheck([game_id], tag="launch")
        except Exception:
            self.stop_watching(game_id)
            logger.exception("Could not initialize achievement observation for game %s", game_id)

    def stop_watching(self, game_id: int) -> None:
        timer = self._exit_timers.pop(game_id, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        watcher = self.watchers.pop(game_id, None)
        if watcher is not None:
            try:
                watcher.stop()
            finally:
                watcher.deleteLater()

    def finish_session(self, game_id: int) -> None:
        """Read now and once after emulator exit writes, including offline."""
        watcher = self.watchers.get(game_id)
        if watcher is None or self._suspended:
            return
        try:
            watcher.check_updates()
        except Exception:
            logger.exception("Could not read final achievement state for game %s", game_id)
        self.request_recheck([game_id], tag="game_exit")
        previous = self._exit_timers.pop(game_id, None)
        if previous is not None:
            previous.stop()
            previous.deleteLater()
        timer = QTimer(self)
        timer.setSingleShot(True)
        self._exit_timers[game_id] = timer

        def flush():
            if not self._suspended and self.watchers.get(game_id) is watcher:
                try:
                    watcher.check_updates()
                    self.request_recheck([game_id], tag="game_exit_flush")
                finally:
                    self.stop_watching(game_id)

        timer.timeout.connect(flush)
        timer.start(1500)

    def suspend(self) -> None:
        self._suspended = True
        for timer in self._exit_timers.values():
            timer.stop()
        for watcher in self.watchers.values():
            watcher.stop()

    def resume(self, active_game_ids) -> None:
        self._suspended = False
        for game_id, watcher in list(self.watchers.items()):
            if game_id in active_game_ids:
                watcher.start()
            else:
                self.finish_session(game_id)

    def dispose(self) -> None:
        self._suspended = True
        for game_id in list(self.watchers):
            try:
                self.stop_watching(game_id)
            except Exception:
                logger.exception("Could not stop achievement watcher for game %s", game_id)
        self.close_bindings()
        self.close_toasts()

    def close_bindings(self) -> None:
        for binding in self._coordinator.close():
            binding.close()
            binding.deleteLater()

    def close_toasts(self) -> None:
        for toast in list(self._active_toasts):
            try:
                toast.close()
            except Exception:
                pass
        self._active_toasts.clear()

    def handle_unlock(self, game_id: int, app_id: str, data: dict) -> None:
        if self._suspended:
            return
        api_name = data.get("api_name", "")
        unlock_time = float(data.get("unlock_time", 0.0) or 0.0)
        if not api_name:
            return

        schema = self._persistence_service.schema(game_id)
        schema_has_api = any(item.get("api_name") == api_name for item in schema)
        if not schema_has_api:
            self._state_store.pending_for(game_id)[api_name] = unlock_time
            logger.info(
                "Queued achievement %s for game %s until its schema is available.", api_name, game_id
            )
            self.request_recheck([game_id], tag="realtime_schema")
            return

        persistence = self._persistence_service.unlock(
            game_id,
            api_name,
            unlock_time,
            provenance=str(data.get("provenance", "local_emulator") or "local_emulator"),
            verified=bool(data.get("verified", False)),
            source_format=str(data.get("source_format", "") or ""),
            source_path=str(data.get("source_path", "") or ""),
        )
        if not persistence.claimed:
            return

        self._mark_profile_changed()
        projection = persistence.projection
        self._state_store.set_status(
            game_id,
            (
                projection.unlocked_count,
                projection.total_count,
                projection.percentage,
                list(projection.recent),
            ),
        )
        self._save_cache()
        self._sync_launcher_metadata(game_id)

        metadata = next((item for item in schema if item.get("api_name") == api_name), None)
        display_name = metadata.get("display_name", api_name) if metadata else api_name
        description = metadata.get("description", "") if metadata else ""
        icon_path = metadata.get("icon_path", "") if metadata else ""
        if self._settings.value("achievement_notifications_enabled", True, type=bool):
            from ui.components.achievement_toast import AchievementToast

            toast = AchievementToast(display_name, description, icon_path=icon_path, parent=self.parent())
            toast.show_animated(parent_widget=self.parent())
            self._active_toasts.append(toast)
            self._active_toasts = [item for item in self._active_toasts if item.isVisible()]
        if self._settings.value("achievement_desktop_notifications", True, type=bool):
            from ui.components.achievement_toast import send_desktop_notification

            send_desktop_notification(
                f"Achievement Unlocked: {display_name}", description, icon_path=icon_path
            )

        selected = self._selected_game_provider()
        if selected and selected[0] == game_id:
            steam_id = str(selected[6]).strip() if len(selected) > 6 and selected[6] else ""
            self._refresh_inspector(game_id, steam_id)
            self._refresh_compact_page()

    def handle_state_refreshed(self, game_id: int, app_id: str, state: dict) -> None:
        """Persist watcher snapshots as silent append-only reconciliation."""
        if self._suspended:
            return
        if not state or not app_id:
            return
        try:
            persistence = self._persistence_service.record_state(
                game_id,
                app_id,
                state,
                provenance="local_emulator",
                verified=False,
                source_format="json",
                source_path="",
            )
            projection = persistence.projection
            self._state_store.set_status(
                game_id,
                (
                    projection.unlocked_count,
                    projection.total_count,
                    projection.percentage,
                    list(projection.recent),
                ),
            )
            if persistence.changed:
                self._sync_launcher_metadata(game_id)
                self._mark_profile_changed()
            self._save_cache()
            self._refresh_if_selected(game_id)
        except Exception as error:
            logger.debug("Could not persist achievement state snapshot for game %s: %s", game_id, error)

    def handle_schema_fetched(self, game_id: int, app_id: str, achievements: list) -> None:
        if self._suspended:
            return
        if not achievements:
            return
        self._persistence_service.save_schema(game_id, app_id, achievements)
        pending = self._state_store.pop_pending(game_id)
        for api_name, unlock_time in pending.items():
            self._persistence_service.arm_notification(game_id, api_name)
            self.handle_unlock(game_id, app_id, {"api_name": api_name, "unlock_time": unlock_time})

    def handle_resolution_ready(self, game_id: int, app_id: str, resolution: Any) -> None:
        if self._suspended:
            return
        self._state_store.set_resolution(game_id, resolution)
        self._persistence_service.persist_resolution(game_id, app_id, resolution)
        if getattr(resolution, "schema", None) and game_id in self._state_store.pending_unlocks:
            pending = self._state_store.pop_pending(game_id)
            for api_name, unlock_time in pending.items():
                self._persistence_service.arm_notification(game_id, api_name)
                self.handle_unlock(
                    game_id, app_id, {"api_name": api_name, "unlock_time": unlock_time}
                )
        self._refresh_if_selected(game_id)

    def handle_status_calculated(
        self, game_id: int, unlocked_count: int, total_count: int, percentage: float, recent: list
    ) -> None:
        if self._suspended:
            return
        self._state_store.set_status(
            game_id, (unlocked_count, total_count, percentage, recent)
        )
        self._refresh_if_selected(game_id)
        self._sync_launcher_metadata(game_id)
        self._save_cache()

    def _refresh_if_selected(self, game_id: int) -> None:
        selected = self._selected_game_provider()
        if selected and selected[0] == game_id:
            app_id = str(selected[6]).strip() if len(selected) > 6 and selected[6] else ""
            self._refresh_inspector(game_id, app_id)

    def request_recheck(self, game_ids: Optional[list] = None, tag: str = "") -> None:
        """Schedule full-library or targeted local achievement resolution."""
        if self._suspended:
            return
        if not self._online_allowed() and self._request_manager is None:
            logger.debug("Achievement recheck skipped: offline mode is enabled.")
            return
        tag = f" ({tag})" if tag else ""
        games_snapshot = list(self._games_provider())
        if not games_snapshot:
            return

        if game_ids is None:
            self._request_library_recheck(games_snapshot, tag)
        elif game_ids:
            self._request_targeted_recheck(games_snapshot, game_ids, tag)

    def _request_library_recheck(self, games_snapshot: list, tag: str) -> None:
        now = time.time()
        uncached, stale, fresh = [], [], []
        for game in games_snapshot:
            steam_id = str(game[6]).strip() if len(game) > 6 and game[6] else ""
            if not steam_id:
                continue
            game_id = game[0]
            cached = self._state_store.get_status(game_id)
            if cached is None:
                uncached.append(game)
            elif self._state_store.is_stale(game_id, 3600, now=now):
                stale.append(game)
            else:
                fresh.append(game)
        targets = uncached + stale + fresh
        if not targets:
            logger.debug("Achievement recheck%s: no games with Steam IDs to scan.", tag)
            return

        if self._request_manager is not None:
            if self._coordinator.batch_in_flight:
                logger.debug("Achievement recheck%s skipped: batch already running.", tag)
                return
            self._coordinator.set_batch_in_flight(True)
            achievement_targets = []
            for game in targets:
                if len(game) < 3:
                    continue
                game_id, _name, path = game[0], game[1], game[2]
                app_id = str(game[6]).strip() if len(game) > 6 and game[6] else ""
                proton_path = str(game[12]).strip() if len(game) > 12 and game[12] else ""
                if not app_id:
                    continue
                target = AchievementTarget(int(game_id), app_id, str(path or ""), proton_path)
                plan = self._coordinator.prepare(target)
                self._ensure_binding(plan.key)
                achievement_targets.append(target)
            if not achievement_targets:
                self._coordinator.set_batch_in_flight(False)
                return
            self._resource_service.request_many(
                achievement_targets,
                db_path=self._db_path_provider(),
                priority=(
                    RequestPriority.CRITICAL
                    if "running" in tag.lower() or "realtime" in tag.lower()
                    else RequestPriority.NORMAL
                ),
                tag=tag,
                on_complete=self._managed_batch_done.emit,
            )
            return

        workers = list(self._workers_provider())
        if any(
            worker.isRunning()
            and worker.__class__.__name__ in {
                "AchievementBatchQueueWorker",
                "AchievementStatusFetcherThread",
                "SteamAchievementFetcherWorker",
            }
            for worker in workers
        ):
            logger.debug("Achievement recheck%s skipped: batch worker already running.", tag)
            return
        worker = AchievementBatchQueueWorker(
            targets,
            max_workers=3,
            db_path=self._db_path_provider(),
            parent=self.parent(),
            request_manager=self._request_manager,
        )
        worker.game_status_ready.connect(self.handle_status_calculated)
        worker.batch_finished.connect(self._on_batch_finished)
        self._track_worker(worker)

    def _request_targeted_recheck(self, games_snapshot: list, game_ids: list, tag: str) -> None:
        by_id = {game[0]: game for game in games_snapshot}
        for game_id in game_ids:
            game = by_id.get(game_id)
            if game is None:
                continue
            game_name = game[1]
            game_path = game[2]
            app_id = str(game[6]).strip() if len(game) > 6 and game[6] else ""
            proton_path = str(game[12]).strip() if len(game) > 12 and game[12] else ""
            if not app_id:
                continue
            if self._request_manager is not None:
                target = AchievementTarget(int(game_id), app_id, str(game_path or ""), proton_path)
                self._resource_service.invalidate(target)
                plan = self._coordinator.prepare(target)
                self._ensure_binding(plan.key)
                self._resource_service.request_status(
                    target,
                    db_path=self._db_path_provider(),
                    priority=RequestPriority.CRITICAL,
                    tag=tag,
                )
                continue

            workers = list(self._workers_provider())
            if any(
                worker.isRunning()
                and worker.__class__.__name__ in {
                    "AchievementStatusFetcherThread",
                    "SteamAchievementFetcherWorker",
                }
                and getattr(worker, "game_id", None) == game_id
                for worker in workers
            ):
                continue
            from core.achievement_coordinator import invalidate

            invalidate(app_id, game_path or "", proton_path)
            worker = AchievementStatusFetcherThread(
                game_id,
                game_name,
                game_path or "",
                app_id,
                proton_path,
                db_path=self._db_path_provider(),
                parent=self.parent(),
                request_manager=self._request_manager,
            )
            worker.resolution_ready.connect(self.handle_resolution_ready)
            worker.achievement_status_calculated.connect(self.handle_status_calculated)
            self._track_worker(worker)

    def _ensure_binding(self, key: RequestKey) -> None:
        if self._coordinator.binding(key) is not None:
            return
        binding = bind_resource(
            self._request_manager,
            key,
            lambda result, key=key: self.handle_managed_state(key, result),
            self,
            cancel_on_close=True,
        )
        self._coordinator.attach_binding(key, binding)

    def ensure_binding(self, key: RequestKey) -> None:
        """Compatibility entry point for callers migrating to the controller."""
        self._ensure_binding(key)

    def handle_managed_state(self, key: RequestKey, result) -> None:
        if self._suspended:
            return
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
                log = logger.warning if result.status == ResourceStatus.ERROR else logger.debug
                log(
                    "Managed achievement request ended for %s (status=%s): %s",
                    key,
                    result.status.value,
                    result.error or result.status.value,
                )
            return
        callback_data = self._coordinator.callback_data(key)
        if callback_data is None:
            return
        game_id, app_id = callback_data
        resolution, unlocked_count, total_count, percentage, recent = result.value
        self.handle_resolution_ready(game_id, app_id, resolution)
        self.handle_status_calculated(
            game_id, unlocked_count, total_count, percentage, recent
        )

    def handle_managed_batch_done(self, results: object) -> None:
        self._coordinator.set_batch_in_flight(False)
        if self._suspended:
            return
        total_games = 0
        total_unlocked = 0
        for result in results if isinstance(results, list) else []:
            if result.status != ResourceStatus.READY or not isinstance(result.value, tuple):
                continue
            _resolution, unlocked_count, total_count, _percentage, _recent = result.value
            if total_count > 0:
                total_games += 1
                total_unlocked += unlocked_count
        self._on_batch_finished(total_games, total_unlocked)


__all__ = ["AchievementSyncController"]
