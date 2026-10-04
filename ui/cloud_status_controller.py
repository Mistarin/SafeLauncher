"""UI-facing orchestration for managed per-game cloud status requests."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from core.cloud_models import SyncStatus
from core.cloud_operations import CloudStatusResult, CloudSyncCoordinator
from core.cloud_status_service import CloudStatusService, CloudStatusTarget
from core.logger import get_logger
from core.request_contracts import (
    RequestKey,
    RequestPriority,
    ResourceStatus,
)
from core.request_manager import RequestManager
from ui.resource_binding import ResourceBindingRegistry, bind_resource


logger = get_logger("UI.CloudStatus")


class CloudStatusController(QObject):
    """Own status-request subscriptions and context-aware result delivery.

    The controller schedules and fans out status resources. The composing
    window supplies explicit policy and presentation callbacks; it remains the
    owner of widgets and decides whether a resulting status starts a save
    operation.
    """

    _batch_done = pyqtSignal(object)

    def __init__(
        self,
        *,
        request_manager: RequestManager | None,
        status_service: CloudStatusService,
        coordinator: CloudSyncCoordinator,
        games_provider: Callable[[], list],
        request_allowed: Callable[[], bool],
        network_allowed: Callable[[], bool],
        mark_offline: Callable[[object], None],
        mark_auth_required: Callable[[object], None],
        on_status_calculated: Callable[..., None],
        on_poll_changed: Callable[[list], None],
        on_batch_finished: Callable[[list, list], None],
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._request_manager = request_manager
        self._status_service = status_service
        self._coordinator = coordinator
        self._games_provider = games_provider
        self._request_allowed = request_allowed
        self._network_allowed = network_allowed
        self._mark_offline = mark_offline
        self._mark_auth_required = mark_auth_required
        self._on_status_calculated = on_status_calculated
        self._on_poll_changed = on_poll_changed
        self._on_batch_finished = on_batch_finished
        self._bindings = ResourceBindingRegistry()
        self._callbacks: dict[RequestKey, list[tuple[int, int, object]]] = {}
        self._target_ids: dict[RequestKey, int] = {}
        self._batch_done.connect(self.handle_batch_done)

    def request_recheck(self, game_ids=None, reason: str = "", *, auto_sync: bool = False) -> None:
        """Apply the shared cloud-status refresh policy to requested games."""
        if not self._request_allowed():
            logger.debug("Ignoring cloud recheck during launcher shutdown")
            return
        context = self._status_service.current_context()
        if not context.network_allowed:
            if game_ids is None or game_ids:
                self._mark_offline(game_ids)
            return
        if not context.backend_active:
            return
        if not context.authentication_configured:
            if game_ids is None:
                self._mark_auth_required(None)
            elif game_ids:
                self._mark_auth_required(game_ids)
            return

        tag = f" ({reason})" if reason else ""
        force = reason not in {"", "startup", "poll", "listing-diff"}
        generation = context.generation
        targets_snapshot = self.targets_snapshot()

        if game_ids is None:
            plan = self._status_service.plan_recheck(targets_snapshot, None, reason=reason)
            targets = [
                (item.game_id, item.game_name, item.game_path, item.steam_id)
                for item in plan.targets
            ]
            if not targets:
                logger.info("Cloud recheck%s: nothing to scan.", tag)
                return
            self.request_statuses(
                targets,
                lambda gid, status, local, cloud, expected=generation, sync=auto_sync:
                self._deliver_if_current(
                    expected, gid, status, local, cloud, auto_sync=sync
                ),
                tag,
                generation=generation,
                force=force,
                on_batch_complete=lambda results, expected=generation: self._batch_done.emit(
                    (expected, results)
                ),
            )
            return

        if game_ids:
            by_id = {target.game_id for target in targets_snapshot}
            plan = self._status_service.plan_recheck(
                targets_snapshot, game_ids, reason=reason
            )
            callback = self._deliver_status
            if auto_sync:
                callback = lambda gid, status, local, cloud: self._deliver_status(
                    gid, status, local, cloud, auto_sync=True
                )
            self.request_statuses(
                [
                    (target.game_id, target.game_name, target.game_path, target.steam_id)
                    for target in plan.targets
                    if target.game_id in by_id
                ],
                callback,
                tag,
                generation=generation,
                force=force,
            )
            return

        def finish_diff(changed, expected_generation=generation):
            if not self._coordinator.accepts(expected_generation):
                logger.debug(
                    "Discarded cloud listing diff from retired context %s",
                    expected_generation,
                )
                return
            if changed:
                self._on_poll_changed([
                    (item.game_id, item.game_name, item.game_path, item.steam_id)
                    for item in changed
                ])

        self._status_service.request_changed_diff(
            targets_snapshot,
            generation=generation,
            on_complete=finish_diff,
            force=True,
        )

    def targets_snapshot(self) -> tuple[CloudStatusTarget, ...]:
        return tuple(
            CloudStatusTarget(
                int(game[0]),
                str(game[1] or ""),
                str(game[2] or "") if len(game) > 2 else "",
                str(game[6] or "") if len(game) > 6 else "",
            )
            for game in list(self._games_provider())
        )

    def request_statuses(
        self,
        targets: list,
        on_result,
        tag: str = "",
        *,
        generation=None,
        on_batch_complete=None,
        force: bool = False,
    ) -> None:
        """Subscribe to per-game statuses through the shared request manager."""
        if not self._request_allowed() or not self._network_allowed():
            return
        if generation is None:
            generation = self._coordinator.generation
        if self._request_manager is None:
            logger.error("Cloud status refresh requested without the application RequestManager")
            return

        status_targets = []
        priority = RequestPriority.CRITICAL if "detail" in tag.lower() else RequestPriority.NORMAL
        for item in targets:
            if isinstance(item, CloudStatusTarget):
                target = item
            else:
                game_id, name, path, steam_id = item
                target = CloudStatusTarget(
                    int(game_id), str(name), str(path or ""), str(steam_id or "")
                )
            status_targets.append(target)
            spec = self._status_service.status_spec(
                target, priority=priority, generation=generation, tag=tag
            )
            key = spec.key
            self._callbacks.setdefault(key, []).append(
                (generation, target.game_id, on_result)
            )
            self._target_ids[key] = target.game_id
            if key not in self._bindings:
                self._bindings[key] = bind_resource(
                    self._request_manager,
                    key,
                    lambda result, key=key: self.handle_managed_state(key, result),
                    self,
                    cancel_on_close=True,
                )

        try:
            self._status_service.request_many(
                status_targets,
                priority=priority,
                generation=generation,
                tag=tag,
                force=force,
                on_complete=on_batch_complete,
            )
        except RuntimeError as error:
            if "shut down" in str(error).lower() or not self._request_allowed():
                logger.debug("Cloud status request ignored during shutdown: %s", error)
                return
            raise

    def handle_managed_state(self, key: RequestKey, result) -> None:
        callbacks = list(self._callbacks.get(key, ()))
        if not callbacks or result.status in {ResourceStatus.IDLE, ResourceStatus.LOADING}:
            return
        if result.status == ResourceStatus.STALE and result.error is None:
            return
        if result.status != ResourceStatus.READY:
            self._deliver_managed_status(
                key,
                callbacks,
                self._failure_status(result),
                None,
                None,
                error=result.error or result.status.value,
            )
            return

        status = local_stats = cloud_stats = None
        if isinstance(result.value, CloudStatusResult):
            if result.value.error is not None:
                logger.debug(
                    "Managed cloud status result failed for game %s: %s",
                    self._target_ids.get(key, "unknown"),
                    result.value.error.error,
                )
                self._deliver_managed_status(
                    key,
                    callbacks,
                    self._failure_status(result, domain_error=result.value.error),
                    None,
                    None,
                    error=result.value.error.error,
                )
                return
            status = result.value.status
            local_stats = result.value.local_stats
            cloud_stats = result.value.cloud_stats
        elif isinstance(result.value, tuple) and len(result.value) == 3:
            status, local_stats, cloud_stats = result.value
        if status is None:
            self._deliver_managed_status(
                key,
                callbacks,
                self._failure_status(result),
                None,
                None,
                error="invalid cloud status payload",
            )
            return
        self._deliver_managed_status(key, callbacks, status, local_stats, cloud_stats)

    def _deliver_managed_status(
        self, key, callbacks, status, local_stats, cloud_stats, *, error=None
    ) -> None:
        try:
            for generation, game_id, callback in callbacks:
                if not self._coordinator.accepts(generation):
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
                    logger.exception("Cloud status consumer failed for game %s", game_id)
        finally:
            current = self._callbacks.get(key, [])
            if current[:len(callbacks)] == callbacks:
                remaining = current[len(callbacks):]
            else:
                remaining = [item for item in current if item not in callbacks]
            if remaining:
                self._callbacks[key] = remaining
            else:
                self._callbacks.pop(key, None)

    def _failure_status(self, result, *, domain_error=None):
        if result.status == ResourceStatus.OFFLINE or not self._network_allowed():
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

    def handle_batch_done(self, payload: object) -> None:
        generation, results = payload
        if not self._coordinator.accepts(generation):
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
            game_id = self._target_ids.get(result.key)
            if game_id is None:
                continue
            name = next(
                (
                    str(game[1] or "")
                    for game in self._games_provider()
                    if game and int(game[0]) == game_id
                ),
                "",
            )
            if status == SyncStatus.LOCAL_NEWER:
                uploaded.append(name)
            elif status in {SyncStatus.CLOUD_NEWER, SyncStatus.CLOUD_ONLY}:
                newer_in_cloud.append(name)
        self._on_batch_finished(uploaded, newer_in_cloud)

    def close(self) -> None:
        for binding in self._bindings.take_all():
            binding.close()
            binding.deleteLater()
        self._callbacks.clear()
        self._target_ids.clear()

    def _deliver_if_current(
        self, generation, game_id, status, local_stats, cloud_stats, *, auto_sync
    ):
        if self._coordinator.accepts(generation):
            self._deliver_status(
                game_id, status, local_stats, cloud_stats, auto_sync=auto_sync
            )

    def _deliver_status(self, game_id, status, local_stats, cloud_stats, *, auto_sync=False):
        self._on_status_calculated(
            game_id, status, local_stats, cloud_stats, auto_sync=auto_sync
        )


__all__ = ["CloudStatusController"]
