"""Managed private cloud-save mutation resources.

The service owns remote operation lifecycle while the existing coordinator
continues to own save detection, archive packaging, encryption, transport,
and conflict-safe domain behavior.  It is deliberately Qt-free so dialogs
and launch flows can subscribe through whatever UI bridge they need.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import RLock
from typing import Callable

from core.cloud_context import CloudContext
from core.cloud_operations import (
    CloudOperationResult,
    CloudPreflightResult,
    CloudStatusResult,
    CloudSyncCoordinator,
)
from core.cloud_models import SyncStatus
from core.cloud_operation_records import CloudOperationRecord, CloudOperationState
from core.request_contracts import (
    CancellationToken,
    RemoteErrorCategory,
    RequestKey,
    RequestPriority,
    RequestSpec,
    ResourceResult,
    RetryPolicy,
    classify_remote_error,
    is_transient_error,
)


class _ProgressReporter:
    """Bind stage progress to a manager request without carrying save data."""

    def __init__(self, service: "CloudOperationService") -> None:
        self._service = service
        self._operation_id = ""
        self._pending: float | None = None

    def bind(self, operation_id: str) -> None:
        self._operation_id = str(operation_id)
        if self._pending is not None:
            self(self._pending)
            self._pending = None

    def __call__(self, progress: float) -> None:
        bounded = max(0.0, min(1.0, float(progress)))
        if not self._operation_id:
            self._pending = bounded
            return
        self._service.update_progress(self._operation_id, bounded)


def _scaled_progress(reporter: _ProgressReporter, start: float, end: float):
    """Map a nested engine's 0..1 progress into an outer operation stage."""
    span = float(end) - float(start)
    return lambda value: reporter(float(start) + span * max(0.0, min(1.0, float(value))))


@dataclass(frozen=True, slots=True)
class CloudOperationTarget:
    """Stable local inputs required for one cloud mutation."""

    game_id: int
    game_name: str
    game_path: str = ""
    steam_id: str = ""


class CloudOperationService:
    """Submit context-safe cloud operations through RequestManager."""

    def __init__(
        self,
        request_manager,
        *,
        coordinator: CloudSyncCoordinator | None = None,
        context_provider: Callable[[int], CloudContext] | None = None,
        read_invalidator: Callable[[CloudOperationTarget, str, object], None] | None = None,
    ) -> None:
        self.request_manager = request_manager
        self.coordinator = coordinator or CloudSyncCoordinator()
        self._context_provider = context_provider or (
            lambda generation: CloudContext.current(generation=generation)
        )
        self._context: CloudContext | None = None
        self._records: dict[str, CloudOperationRecord] = {}
        self._handles = {}
        self._read_invalidation_notified: set[str] = set()
        self._records_lock = RLock()
        self._read_invalidator = read_invalidator

    def set_read_invalidator(
        self,
        callback: Callable[[CloudOperationTarget, str, object], None] | None,
    ) -> None:
        """Attach the application-owned cloud-read invalidation callback."""
        self._read_invalidator = callback

    def current_context(self) -> CloudContext:
        candidate = self._context_provider(self.coordinator.generation)
        if self._context is not None and candidate.cache_identity != self._context.cache_identity:
            generation = self.coordinator.invalidate_context()
            candidate = self._context_provider(generation)
        elif candidate.generation != self.coordinator.generation:
            candidate = self._context_provider(self.coordinator.generation)
        self._context = candidate
        return candidate

    def invalidate_context(self, *, generation: int | None = None) -> CloudContext:
        generation = self.coordinator.invalidate_context() if generation is None else int(generation)
        self._context = self._context_provider(generation)
        return self._context

    @staticmethod
    def _retry_policy() -> RetryPolicy:
        return RetryPolicy(retry_if=lambda error: is_transient_error(error))

    def key_for(self, target: CloudOperationTarget, operation: str, *, context=None) -> RequestKey:
        context = context or self.current_context()
        return context.request_key(
            "cloud-save-operation",
            f"{int(target.game_id)}:{str(operation).strip()}",
            "v1",
        )

    def _spec(
        self,
        target: CloudOperationTarget,
        operation: str,
        loader,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        generation: int | None = None,
        tag: str = "",
        timeout_seconds: float | None = None,
    ) -> RequestSpec:
        context = self.current_context()
        request_generation = context.generation if generation is None else int(generation)
        return RequestSpec(
            self.key_for(target, operation, context=context),
            loader,
            priority=priority,
            retry_policy=self._retry_policy(),
            generation=request_generation,
            timeout_seconds=timeout_seconds,
            metadata={
                "cloud_context": context.cache_identity,
                "cloud_generation": request_generation,
                "operation": str(operation),
                "tag": str(tag),
            },
        )

    def _request(self, target, operation, loader, *, progress_hook=None, **kwargs):
        spec = self._spec(target, operation, loader, **kwargs)
        handle = self.request_manager.submit(spec)
        now = time.time()
        record = CloudOperationRecord(
            operation_id=handle.request_id,
            key=handle.key,
            game_id=int(target.game_id),
            operation=str(operation),
            context_fingerprint=self.current_context().fingerprint,
            generation=int(handle.generation),
            state=CloudOperationState.QUEUED,
            started_at=now,
        )
        with self._records_lock:
            # A deduplicated handle points at the existing request. Do not
            # replace its original lifecycle record with a second consumer.
            self._records.setdefault(handle.request_id, record)
            self._handles.setdefault(handle.request_id, handle)
        if progress_hook is not None:
            progress_hook.bind(handle.request_id)

        def _finish(future, operation_id=handle.request_id):
            try:
                result = future.result()
            except Exception as exc:
                self._finish_record(
                    operation_id,
                    CloudOperationState.FAILED,
                    error=str(exc),
                    error_category=classify_remote_error(exc).value,
                )
                return
            status = getattr(result, "status", None)
            status_value = getattr(status, "value", str(status or ""))
            if status_value == "cancelled":
                state = CloudOperationState.CANCELLED
            elif status_value in {"ready", "stale"}:
                value = getattr(result, "value", None)
                operation_failed = getattr(value, "success", True) is False
                if isinstance(value, dict):
                    operation_failed = operation_failed or value.get("outcome") == "failed"
                if isinstance(value, tuple) and len(value) > 1:
                    operation_failed = operation_failed or value[1] is not None
                state = CloudOperationState.FAILED if operation_failed else CloudOperationState.COMPLETED
            else:
                state = CloudOperationState.FAILED
            error = getattr(result, "error", None)
            value = getattr(result, "value", None)
            if error is None and getattr(value, "success", True) is False:
                error = getattr(value, "error", None)
            legacy_category = getattr(value, "category", "") if value is not None else ""
            if status_value == "cancelled":
                error_category = RemoteErrorCategory.CANCELLED.value
            elif state == CloudOperationState.FAILED:
                error_category = classify_remote_error(legacy_category or error).value
            else:
                error_category = ""
            self._finish_record(
                operation_id,
                state,
                result_status=status_value,
                error=str(error or ""),
                error_category=error_category,
            )
            notify_read_invalidator = False
            if state == CloudOperationState.COMPLETED and self._read_invalidator is not None:
                with self._records_lock:
                    if operation_id not in self._read_invalidation_notified:
                        self._read_invalidation_notified.add(operation_id)
                        notify_read_invalidator = True
            if notify_read_invalidator:
                try:
                    self._read_invalidator(target, operation, value)
                except Exception:
                    # A cache invalidation hook must never change the outcome
                    # of a completed cloud mutation.
                    pass

        handle.future.add_done_callback(_finish)
        return handle

    def _finish_record(
        self,
        operation_id: str,
        state: CloudOperationState,
        *,
        result_status: str = "",
        error_category: str = "",
        error: str = "",
    ) -> None:
        with self._records_lock:
            current = self._records.get(operation_id)
            if current is None:
                return
            self._records[operation_id] = CloudOperationRecord(
                operation_id=current.operation_id,
                key=current.key,
                game_id=current.game_id,
                operation=current.operation,
                context_fingerprint=current.context_fingerprint,
                generation=current.generation,
                state=state,
                progress=current.progress,
                started_at=current.started_at,
                finished_at=time.time(),
                error_category=error_category,
                error=error,
                result_status=result_status,
            )

    def operation(self, operation_id: str) -> CloudOperationRecord | None:
        with self._records_lock:
            return self._records.get(str(operation_id))

    def operations(self) -> tuple[CloudOperationRecord, ...]:
        with self._records_lock:
            return tuple(self._records.values())

    @staticmethod
    def active_save_version(game_name: str):
        """Return the locally selected generation for presentation metadata.

        The setting is local operation state, not a remote resource. Keeping
        this accessor here means UI code does not need to import the archive
        engine just to mark the active history row.
        """
        from core.cloud_save_sync import get_active_save_version

        return get_active_save_version(str(game_name or ""))

    def active_operations(self) -> tuple[CloudOperationRecord, ...]:
        return tuple(
            record for record in self.operations()
            if record.state in {CloudOperationState.QUEUED, CloudOperationState.RUNNING}
        )

    def update_progress(self, operation_id: str, progress: float | None) -> bool:
        """Update metadata-only progress without accepting save contents."""
        with self._records_lock:
            current = self._records.get(str(operation_id))
            if current is None or current.state not in {
                CloudOperationState.QUEUED,
                CloudOperationState.RUNNING,
            }:
                return False
            bounded = None if progress is None else max(0.0, min(1.0, float(progress)))
            self._records[str(operation_id)] = CloudOperationRecord(
                operation_id=current.operation_id,
                key=current.key,
                game_id=current.game_id,
                operation=current.operation,
                context_fingerprint=current.context_fingerprint,
                generation=current.generation,
                state=CloudOperationState.RUNNING,
                progress=bounded,
                started_at=current.started_at,
                finished_at=current.finished_at,
                error_category=current.error_category,
                error=current.error,
                result_status=current.result_status,
            )
            return True

    def cancel(self, operation_id: str) -> bool:
        with self._records_lock:
            handle = self._handles.get(str(operation_id))
        if handle is None:
            return False
        return bool(handle.cancel())

    def request_preflight(self, target: CloudOperationTarget, *, priority=RequestPriority.CRITICAL, generation=None, tag=""):
        progress = _ProgressReporter(self)

        def load(token: CancellationToken) -> CloudPreflightResult:
            token.raise_if_cancelled()
            progress(0.05)
            result = self.coordinator.preflight(
                target.game_id, target.game_name, target.game_path, target.steam_id
            )
            token.raise_if_cancelled()
            progress(0.95)
            return result

        return self._request(
            target,
            "preflight",
            load,
            priority=priority,
            generation=generation,
            tag=tag,
            timeout_seconds=30,
            progress_hook=progress,
        )

    def request_upload(
        self,
        target: CloudOperationTarget,
        *,
        priority=RequestPriority.NORMAL,
        generation=None,
        tag="",
        snapshot=None,
        locations=None,
    ):
        progress = _ProgressReporter(self)

        def load(token: CancellationToken) -> CloudOperationResult:
            token.raise_if_cancelled()
            progress(0.05)
            kwargs = {
                "steam_id": target.steam_id,
                "cancel_check": lambda: token.cancelled,
                "progress_callback": progress,
            }
            if snapshot is not None:
                kwargs["snapshot"] = snapshot
            if locations is not None:
                kwargs["locations"] = locations
            result = self.coordinator.upload_local_save(
                target.game_id,
                target.game_name,
                target.game_path,
                **kwargs,
            )
            token.raise_if_cancelled()
            progress(1.0)
            return result

        return self._request(
            target,
            "upload",
            load,
            priority=priority,
            generation=generation,
            tag=tag,
            timeout_seconds=None,
            progress_hook=progress,
        )

    def request_restore(
        self,
        target: CloudOperationTarget,
        *,
        priority: RequestPriority = RequestPriority.CRITICAL,
        generation=None,
        tag="",
        target_version=None,
    ):
        progress = _ProgressReporter(self)

        def load(token: CancellationToken) -> CloudOperationResult:
            token.raise_if_cancelled()
            progress(0.05)
            result = self.coordinator.restore_cloud_save(
                target.game_id,
                target.game_name,
                target.game_path,
                steam_id=target.steam_id,
                target_version=target_version,
                cancel_check=lambda: token.cancelled,
                progress_callback=progress,
            )
            token.raise_if_cancelled()
            progress(1.0)
            return result

        return self._request(
            target,
            "restore" if target_version is None else f"restore:{int(target_version)}",
            load,
            priority=priority,
            generation=generation,
            tag=tag,
            timeout_seconds=None,
            progress_hook=progress,
        )

    def request_restore_with_preflight(
        self,
        target: CloudOperationTarget,
        *,
        priority: RequestPriority = RequestPriority.CRITICAL,
        generation=None,
        tag="",
    ):
        """Preflight and restore as one deduplicated UI operation."""
        progress = _ProgressReporter(self)

        def load(token: CancellationToken) -> CloudOperationResult:
            token.raise_if_cancelled()
            progress(0.05)
            preflight = self.coordinator.preflight(
                target.game_id, target.game_name, target.game_path, target.steam_id
            )
            token.raise_if_cancelled()
            progress(0.25)
            if preflight.error is not None:
                progress(1.0)
                return preflight.error
            if not preflight.cloud_stats or not preflight.cloud_stats.exists:
                progress(1.0)
                return CloudOperationResult(
                    False,
                    "Cloud restore",
                    target.game_name,
                    error="No cloud save is available to restore.",
                    category="cloud_missing",
                    guidance="Upload a local save first, then try restoring again.",
                )
            result = self.coordinator.restore_cloud_save(
                target.game_id,
                target.game_name,
                target.game_path,
                steam_id=target.steam_id,
                cancel_check=lambda: token.cancelled,
                progress_callback=_scaled_progress(progress, 0.25, 0.95),
            )
            token.raise_if_cancelled()
            progress(1.0)
            return result

        return self._request(
            target,
            "restore-with-preflight",
            load,
            priority=priority,
            generation=generation,
            tag=tag,
            timeout_seconds=None,
            progress_hook=progress,
        )

    def request_exit_sync(
        self,
        target: CloudOperationTarget,
        *,
        settle_seconds: float = 0.5,
        priority: RequestPriority = RequestPriority.BACKGROUND,
        generation=None,
        tag="game_exit",
    ):
        """Check and conditionally upload a game's save after exit."""
        progress = _ProgressReporter(self)

        def load(token: CancellationToken) -> dict:
            if settle_seconds > 0 and token.wait(settle_seconds):
                token.raise_if_cancelled()
            token.raise_if_cancelled()
            progress(0.1)
            status_result: CloudStatusResult = self.coordinator.check_status(
                target.game_id,
                target.game_name,
                target.game_path,
                target.steam_id,
            )
            if status_result.error is not None:
                progress(1.0)
                return {
                    "game": target.game_name,
                    "game_id": target.game_id,
                    "outcome": "failed",
                    "reason": status_result.error.error,
                    "error": status_result.error.error,
                    "guidance": status_result.error.guidance,
                    "cloud_result": status_result.error,
                }
            progress(0.4)
            if status_result.status is not None and status_result.status.value in {"local_newer", "in_sync"}:
                result = self.coordinator.upload_local_save(
                    target.game_id,
                    target.game_name,
                    target.game_path,
                    steam_id=target.steam_id,
                    cancel_check=lambda: token.cancelled,
                    progress_callback=_scaled_progress(progress, 0.4, 0.95),
                )
                progress(1.0)
                return {
                    "game": target.game_name,
                    "game_id": target.game_id,
                    "outcome": "uploaded" if result.success else "failed",
                    "reason": status_result.status.value,
                    "error": result.error if not result.success else "",
                    "guidance": result.guidance if not result.success else "",
                    "cloud_result": result,
                }
            reason = status_result.status.value if status_result.status is not None else "unknown"
            progress(1.0)
            return {
                "game": target.game_name,
                "game_id": target.game_id,
                "outcome": "skipped",
                "reason": reason,
            }

        return self._request(
            target,
            "exit-sync",
            load,
            priority=priority,
            generation=generation,
            tag=tag,
            timeout_seconds=None,
            progress_hook=progress,
        )

    def request_prelaunch_resolution(
        self,
        target: CloudOperationTarget,
        *,
        auto_prefer_newer: bool = False,
        auto_prefer_local: bool = False,
        priority: RequestPriority = RequestPriority.CRITICAL,
        generation=None,
        tag="prelaunch",
    ):
        """Preflight a launch and perform only explicitly allowed auto-actions."""
        progress = _ProgressReporter(self)

        def load(token: CancellationToken) -> dict:
            token.raise_if_cancelled()
            progress(0.05)
            preflight = self.coordinator.preflight(
                target.game_id, target.game_name, target.game_path, target.steam_id
            )
            token.raise_if_cancelled()
            progress(0.25)
            payload = {
                "preflight": preflight,
                "status": preflight.status,
                "local_stats": preflight.local_stats,
                "cloud_stats": preflight.cloud_stats,
            }
            if preflight.error is not None:
                payload["cloud_error"] = preflight.error
                progress(1.0)
                return payload

            status = preflight.status
            if status in (SyncStatus.CLOUD_ONLY, SyncStatus.CLOUD_NEWER) and auto_prefer_newer:
                result = self.coordinator.restore_cloud_save(
                    target.game_id,
                    target.game_name,
                    target.game_path,
                    steam_id=target.steam_id,
                    cancel_check=lambda: token.cancelled,
                    progress_callback=_scaled_progress(progress, 0.25, 0.95),
                )
                token.raise_if_cancelled()
                payload["cloud_result"] = result
            elif status == SyncStatus.CLOUD_ONLY:
                payload["needs_cloud_only_prompt"] = True
            elif status == SyncStatus.CLOUD_NEWER:
                payload["needs_conflict"] = True
            elif status == SyncStatus.LOCAL_NEWER:
                if not preflight.cloud_stats or not preflight.cloud_stats.exists:
                    payload["local_ready"] = True
                elif auto_prefer_local:
                    payload["local_preferred"] = True
                else:
                    payload["needs_conflict"] = True
            progress(1.0)
            return payload

        return self._request(
            target,
            "prelaunch",
            load,
            priority=priority,
            generation=generation,
            tag=tag,
            timeout_seconds=None,
            progress_hook=progress,
        )

    def request_history(self, target: CloudOperationTarget, *, priority=RequestPriority.NORMAL, generation=None, tag=""):
        progress = _ProgressReporter(self)

        def load(token: CancellationToken):
            token.raise_if_cancelled()
            progress(0.1)
            result = self.coordinator.load_history(
                target.game_id, target.game_name, target.game_path, target.steam_id
            )
            token.raise_if_cancelled()
            progress(0.95)
            return result

        return self._request(
            target,
            "history",
            load,
            priority=priority,
            generation=generation,
            tag=tag,
            timeout_seconds=30,
            progress_hook=progress,
        )

    def request_restore_preflight(
        self,
        target: CloudOperationTarget,
        *,
        priority: RequestPriority = RequestPriority.CRITICAL,
        generation=None,
        tag="restore_preflight",
    ):
        """Load history and current cloud availability before a restore prompt."""
        progress = _ProgressReporter(self)

        def load(token: CancellationToken) -> dict:
            token.raise_if_cancelled()
            progress(0.1)
            versions, history_error = self.coordinator.load_history(
                target.game_id, target.game_name, target.game_path, target.steam_id
            )
            token.raise_if_cancelled()
            progress(0.45)
            if history_error is not None:
                return {"kind": "error", "error": history_error}
            cloud_versions = [version for version in versions if version.get("source") == "cloud"]
            if len(cloud_versions) > 1:
                return {
                    "kind": "history",
                    "display_path": "Latest cloud save version",
                    "history_entry": cloud_versions[0],
                }
            preflight = self.coordinator.preflight(
                target.game_id, target.game_name, target.game_path, target.steam_id
            )
            token.raise_if_cancelled()
            progress(0.9)
            if preflight.error is not None:
                return {"kind": "error", "error": preflight.error}
            cloud_stats = preflight.cloud_stats
            return {
                "kind": "ready",
                "display_path": cloud_stats.display_path if cloud_stats else "Unavailable",
                "cloud_exists": bool(cloud_stats and cloud_stats.exists),
                # Keep the newest version metadata available to UI callers so
                # a restore confirmation can explain exactly what will replace
                # the local save. The raw entry is still treated as transport
                # data and normalized by the presentation layer.
                "history_entry": cloud_versions[0] if cloud_versions else None,
            }

        return self._request(
            target,
            "restore-preflight",
            load,
            priority=priority,
            generation=generation,
            tag=tag,
            timeout_seconds=30,
            progress_hook=progress,
        )


__all__ = ["CloudOperationService", "CloudOperationTarget"]
