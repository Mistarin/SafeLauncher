"""Managed private cloud-save status resources.

This module is the first application-service boundary for cloud work. It
delegates save detection and comparison to the existing cloud coordinator,
while owning context-aware request keys, request specs, retry classification,
and manager-backed batch submission.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Callable, Iterable

from core.cloud_context import CloudContext
from core.cloud_operations import (
    CloudStatusResult,
    CloudSyncCoordinator,
)
from core.cloud_models import SaveStats, SyncStatus
from core.cache_policy import cache_policy
from core.save_state import SaveStateStore
from core.request_contracts import (
    CancellationToken,
    RequestKey,
    RequestPriority,
    RequestSpec,
    ResourceResult,
    ResourceStatus,
    RetryPolicy,
    is_transient_error,
)


class CloudStatusRequestError(RuntimeError):
    """Typed failure raised when a cloud status result is not usable."""

    def __init__(self, result: CloudStatusResult, *, retryable: bool = False):
        message = "Cloud status request failed."
        if result.error is not None:
            message = str(result.error.error or message)
        super().__init__(message)
        self.result = result
        self.retryable = bool(retryable)
        # SaveOperationResult intentionally implements ``__bool__`` as its
        # success flag, so a failed result is false even though it is present.
        self.category = (
            getattr(result.error, "category", "unknown")
            if result.error is not None
            else "unknown"
        )


@dataclass(frozen=True, slots=True)
class CloudStatusTarget:
    """Stable local inputs required to calculate one game's cloud status."""

    game_id: int
    game_name: str
    game_path: str = ""
    steam_id: str = ""


@dataclass(frozen=True, slots=True)
class CloudStatusPlan:
    """The service-owned target selection for one status refresh."""

    targets: tuple[CloudStatusTarget, ...]
    generation: int
    reason: str = ""


class CloudStatusService:
    """Build and submit context-safe cloud status resources."""

    STATUS_TTL_SECONDS = cache_policy("cloud-status").max_age_seconds
    LISTING_TTL_SECONDS = cache_policy("cloud-listing").max_age_seconds

    def __init__(
        self,
        request_manager,
        *,
        coordinator: CloudSyncCoordinator | None = None,
        context_provider: Callable[[int], CloudContext] | None = None,
        status_store: SaveStateStore | None = None,
        cache=None,
        cache_path: str | os.PathLike | None = None,
        legacy_cache_path: str | os.PathLike | None = None,
    ) -> None:
        self.request_manager = request_manager
        self.coordinator = coordinator or CloudSyncCoordinator()
        self._context_provider = context_provider or (
            lambda generation: CloudContext.current(generation=generation)
        )
        self._lock = RLock()
        self._context: CloudContext | None = None
        self.status_store = status_store or SaveStateStore()
        self.resource_cache = cache or getattr(request_manager, "cache", None)
        self.cache_path = Path(cache_path) if cache_path else None
        self._changed_diff_key = None
        self._changed_diff_handle = None
        # Batch lifecycle belongs to the cloud service.  The UI can keep its
        # binding registry, but it must not maintain a second "batch running"
        # flag that can drift from RequestManager state.
        self._active_batches: dict[tuple, list] = {}
        self._load_cache(legacy_cache_path)

    def _cancel_active_batches_locked(self) -> None:
        for handles in self._active_batches.values():
            for handle in handles:
                try:
                    handle.cancel()
                except Exception:
                    pass
        self._active_batches.clear()

    @property
    def status_cache(self) -> SaveStateStore:
        """Compatibility mapping for legacy library consumers."""
        return self.status_store

    def cached_status(self, game_id: int):
        return self.status_store.get(int(game_id))

    def checked_at(self, game_id: int) -> float:
        return self.status_store.checked_at(int(game_id))

    def status_snapshot(self) -> dict[int, tuple]:
        """Return a stable snapshot for a background listing diff."""
        with self._lock:
            return {int(game_id): value for game_id, value in self.status_store.items()}

    def record_status(
        self,
        game_id: int,
        status,
        local_stats=None,
        cloud_stats=None,
        *,
        checked_at: float | None = None,
        generation: int | None = None,
        context_generation: int | None = None,
    ) -> None:
        if generation is not None and context_generation is not None:
            if int(generation) != int(context_generation):
                raise ValueError("generation and context_generation disagree")
        if generation is None:
            generation = context_generation
        with self._lock:
            context = self.current_context()
            self.status_store.set_cloud_status(
                int(game_id),
                status,
                local_stats,
                cloud_stats,
                checked_at=time.time() if checked_at is None else checked_at,
                context_generation=context.generation if generation is None else generation,
            )
            self.save_cache()

    def mark_status(
        self,
        game_ids: Iterable[int],
        status,
        *,
        generation: int | None = None,
    ) -> list[int]:
        """Set an availability verdict without issuing doomed HTTP calls."""
        with self._lock:
            context = self.current_context()
            target_generation = context.generation if generation is None else int(generation)
            changed = []
            checked_at = time.time()
            for game_id in game_ids:
                game_id = int(game_id)
                existing = self.status_store.get(game_id)
                if existing is not None and existing[0] == status:
                    continue
                self.status_store.set_cloud_status(
                    game_id,
                    status,
                    None,
                    None,
                    checked_at=checked_at,
                    context_generation=target_generation,
                )
                changed.append(game_id)
            if changed:
                self.save_cache()
            return changed

    def clear_status(self) -> None:
        with self._lock:
            self.status_store.clear()
            self.save_cache()

    def forget_status(self, game_id: int) -> bool:
        """Remove one game's cached status through the owning service.

        Library lifecycle changes use this instead of mutating the
        compatibility mapping directly.  The mapping remains available for
        older presentation consumers, but cache ownership stays here.
        """
        with self._lock:
            game_id = int(game_id)
            if game_id not in self.status_store:
                return False
            del self.status_store[game_id]
            self.save_cache()
            return True

    def plan_recheck(
        self,
        targets: Iterable[CloudStatusTarget],
        game_ids: Iterable[int] | None,
        *,
        reason: str = "",
        stale_after_seconds: float = 7200,
    ) -> CloudStatusPlan:
        """Select and prioritize targets without depending on UI state."""
        values = list(targets)
        generation = self.current_context().generation
        if game_ids is not None:
            wanted = {int(game_id) for game_id in game_ids}
            selected = [target for target in values if target.game_id in wanted]
            return CloudStatusPlan(tuple(selected), generation, reason)

        now = time.time()
        uncached, offline, stale, fresh = [], [], [], []
        from core.cloud_models import SyncStatus

        for target in values:
            cached = self.cached_status(target.game_id)
            if cached is None:
                uncached.append(target)
            elif cached[0] == SyncStatus.CLOUD_OFFLINE:
                offline.append(target)
            elif now - self.checked_at(target.game_id) > stale_after_seconds:
                stale.append(target)
            else:
                fresh.append(target)
        return CloudStatusPlan(
            tuple(uncached + offline + stale + fresh),
            generation,
            reason,
        )

    def request_changed_diff(
        self,
        targets: Iterable[CloudStatusTarget],
        *,
        generation: int | None = None,
        on_complete: Callable[[list[CloudStatusTarget]], None] | None = None,
    ):
        """Refresh the remote listing and return games whose cloud copy moved."""
        context = self.current_context()
        request_generation = context.generation if generation is None else int(generation)
        values = tuple(targets)
        snapshot = self.status_snapshot()
        key = context.request_key("cloud-save-listing-diff", "library", "v1")
        self._changed_diff_key = key
        with self._lock:
            if (
                self._changed_diff_handle is not None
                and not self._changed_diff_handle.future.done()
                and self._changed_diff_handle.key == key
            ):
                return self._changed_diff_handle

        def load(token: CancellationToken):
            token.raise_if_cancelled()
            changed = self.coordinator.find_changed_games(
                [
                    (target.game_id, target.game_name, target.game_path, target.steam_id)
                    for target in values
                ],
                snapshot,
            )
            token.raise_if_cancelled()
            return [CloudStatusTarget(*item) for item in changed]

        spec = RequestSpec(
            key,
            load,
            priority=RequestPriority.BACKGROUND,
            retry_policy=self._retry_policy(),
            generation=request_generation,
            timeout_seconds=30,
            metadata={
                "cloud_context": context.cache_identity,
                "cloud_generation": request_generation,
                "tag": "poll-diff",
            },
        )
        handle = self._request_listing_cached(spec)
        with self._lock:
            self._changed_diff_handle = handle

        def clear_completed(_future) -> None:
            with self._lock:
                if self._changed_diff_handle is handle:
                    self._changed_diff_handle = None

        handle.future.add_done_callback(clear_completed)
        if on_complete is not None:
            def done(future):
                try:
                    result = future.result()
                    changed = result.value if result.status == ResourceStatus.READY else []
                except Exception:
                    changed = []
                on_complete(changed)
            handle.future.add_done_callback(done)
        return handle

    def current_context(self) -> CloudContext:
        """Return the current context and retire an older configuration."""
        with self._lock:
            candidate = self._context_provider(self.coordinator.generation)
            if self._context is not None and candidate.cache_identity != self._context.cache_identity:
                generation = self.coordinator.invalidate_context()
                if self._changed_diff_handle is not None:
                    try:
                        self._changed_diff_handle.cancel()
                    except Exception:
                        pass
                    self._changed_diff_handle = None
                self._cancel_active_batches_locked()
                candidate = self._context_provider(generation)
            elif candidate.generation != self.coordinator.generation:
                candidate = self._context_provider(self.coordinator.generation)
            self._context = candidate
            return candidate

    def invalidate_context(self) -> CloudContext:
        """Retire all previous-context results and return the new snapshot."""
        with self._lock:
            generation = self.coordinator.invalidate_context()
            self._context = self._context_provider(generation)
            if self._changed_diff_handle is not None:
                try:
                    self._changed_diff_handle.cancel()
                except Exception:
                    pass
            self._changed_diff_handle = None
            self._cancel_active_batches_locked()
            self.status_store.clear()
            self.save_cache()
            return self._context

    def key_for(self, game_id: int, *, context: CloudContext | None = None) -> RequestKey:
        context = context or self.current_context()
        return context.request_key("cloud-save-status", str(int(game_id)), "v1")

    @staticmethod
    def _serialize_stats(value) -> dict | None:
        if value is None:
            return None
        return {
            "exists": bool(getattr(value, "exists", False)),
            "last_modified": float(getattr(value, "last_modified", 0.0) or 0.0),
            "size_bytes": int(getattr(value, "size_bytes", 0) or 0),
            "file_count": int(getattr(value, "file_count", 0) or 0),
            "display_path": str(getattr(value, "display_path", "") or ""),
        }

    @staticmethod
    def _deserialize_stats(value) -> SaveStats | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("Invalid cloud status statistics")
        return SaveStats(
            exists=bool(value.get("exists", False)),
            last_modified=float(value.get("last_modified", 0.0) or 0.0),
            size_bytes=int(value.get("size_bytes", 0) or 0),
            file_count=int(value.get("file_count", 0) or 0),
            display_path=str(value.get("display_path", "") or ""),
        )

    @staticmethod
    def _status_cache_validator(value) -> bool:
        return (
            isinstance(value, CloudStatusResult)
            and value.error is None
            and isinstance(value.status, SyncStatus)
            and isinstance(value.game_name, str)
        )

    @classmethod
    def _status_cache_encoder(cls, value) -> dict:
        if not cls._status_cache_validator(value):
            raise ValueError("Invalid cloud status cache value")
        return {
            "game_name": value.game_name,
            "status": value.status.value,
            "local_stats": cls._serialize_stats(value.local_stats),
            "cloud_stats": cls._serialize_stats(value.cloud_stats),
        }

    @staticmethod
    def _status_cache_decoder(value) -> CloudStatusResult:
        if isinstance(value, CloudStatusResult):
            return value
        if not isinstance(value, dict):
            raise ValueError("Invalid cloud status cache document")
        try:
            status = SyncStatus(str(value["status"]))
            game_name = str(value.get("game_name", ""))
            return CloudStatusResult(
                game_name,
                status,
                CloudStatusService._deserialize_stats(value.get("local_stats")),
                CloudStatusService._deserialize_stats(value.get("cloud_stats")),
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Invalid cloud status cache document") from exc

    @staticmethod
    def _listing_cache_validator(value) -> bool:
        return isinstance(value, (list, tuple)) and all(
            isinstance(target, CloudStatusTarget) for target in value
        )

    @classmethod
    def _listing_cache_encoder(cls, value) -> list[dict]:
        if not cls._listing_cache_validator(value):
            raise ValueError("Invalid cloud listing cache value")
        return [
            {
                "game_id": int(target.game_id),
                "game_name": target.game_name,
                "game_path": target.game_path,
                "steam_id": target.steam_id,
            }
            for target in value
        ]

    @staticmethod
    def _listing_cache_decoder(value) -> list[CloudStatusTarget]:
        if not isinstance(value, list):
            raise ValueError("Invalid cloud listing cache document")
        decoded = []
        try:
            for item in value:
                if isinstance(item, CloudStatusTarget):
                    decoded.append(item)
                    continue
                if not isinstance(item, dict):
                    raise ValueError("Invalid cloud listing target")
                decoded.append(
                    CloudStatusTarget(
                        int(item["game_id"]),
                        str(item.get("game_name", "")),
                        str(item.get("game_path", "")),
                        str(item.get("steam_id", "")),
                    )
                )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Invalid cloud listing cache document") from exc
        return decoded

    def _request_status_cached(self, spec: RequestSpec[CloudStatusResult]):
        if self.resource_cache is None:
            return self.request_manager.submit(spec)
        return self.request_manager.cached_request(
            spec,
            self.resource_cache,
            max_age_seconds=self.STATUS_TTL_SECONDS,
            cache_validator=self._status_cache_validator,
            cache_encoder=self._status_cache_encoder,
            cache_decoder=self._status_cache_decoder,
            stale_while_revalidate=True,
            content_type="application/json",
        )

    def _request_listing_cached(self, spec: RequestSpec[list[CloudStatusTarget]]):
        if self.resource_cache is None:
            return self.request_manager.submit(spec)
        return self.request_manager.cached_request(
            spec,
            self.resource_cache,
            max_age_seconds=self.LISTING_TTL_SECONDS,
            cache_validator=self._listing_cache_validator,
            cache_encoder=self._listing_cache_encoder,
            cache_decoder=self._listing_cache_decoder,
            stale_while_revalidate=True,
            content_type="application/json",
        )

    @staticmethod
    def _retry_policy() -> RetryPolicy:
        return RetryPolicy(
            retry_if=lambda error: bool(getattr(error, "retryable", False))
            or is_transient_error(error),
        )

    def status_spec(
        self,
        target: CloudStatusTarget,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        generation: int | None = None,
        tag: str = "",
    ) -> RequestSpec[CloudStatusResult]:
        """Create one manager request without starting it."""
        context = self.current_context()
        request_generation = context.generation if generation is None else int(generation)
        key = self.key_for(target.game_id, context=context)

        def load(token: CancellationToken) -> CloudStatusResult:
            token.raise_if_cancelled()
            result = self.coordinator.check_status(
                int(target.game_id),
                target.game_name,
                target.game_path,
                target.steam_id,
            )
            token.raise_if_cancelled()
            if result.error is not None:
                retryable = result.error.category == "backend_unavailable"
                raise CloudStatusRequestError(result, retryable=retryable)
            return result

        return RequestSpec(
            key,
            load,
            priority=priority,
            retry_policy=self._retry_policy(),
            generation=request_generation,
            timeout_seconds=30,
            metadata={
                "cloud_context": context.cache_identity,
                "cloud_generation": request_generation,
                "tag": str(tag),
            },
        )

    def request_status(
        self,
        target: CloudStatusTarget,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        generation: int | None = None,
        tag: str = "",
    ):
        """Submit one cloud status resource through RequestManager."""
        return self._request_status_cached(
            self.status_spec(
                target,
                priority=priority,
                generation=generation,
                tag=tag,
            )
        )

    def request_many(
        self,
        targets: Iterable[CloudStatusTarget],
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        generation: int | None = None,
        tag: str = "",
        on_complete: Callable[[list[ResourceResult]], None] | None = None,
    ):
        """Submit a deduplicated batch while retaining individual handles."""
        specs = [
            self.status_spec(
                target,
                priority=priority,
                generation=generation,
                tag=tag,
            )
            for target in targets
        ]
        batch_key = (
            tuple(sorted(spec.key.cache_key() for spec in specs)),
            tuple(sorted(int(spec.generation) for spec in specs)),
        )
        with self._lock:
            active = self._active_batches.get(batch_key)
            if active and any(not handle.future.done() for handle in active):
                return active

        def completed(results):
            with self._lock:
                self._active_batches.pop(batch_key, None)
            if on_complete is not None:
                on_complete(results)

        if self.resource_cache is None:
            handles = self.request_manager.request_many(specs, on_complete=completed)
        else:
            handles = self.request_manager.request_many_cached(
                specs,
                cache=self.resource_cache,
                max_age_seconds=self.STATUS_TTL_SECONDS,
                cache_validator=self._status_cache_validator,
                cache_encoder=self._status_cache_encoder,
                cache_decoder=self._status_cache_decoder,
                stale_while_revalidate=True,
                content_type="application/json",
                on_complete=completed,
            )
        with self._lock:
            self._active_batches[batch_key] = handles
        return handles

    def _load_cache(self, legacy_cache_path: str | os.PathLike | None = None) -> None:
        """Load the shared snapshot, migrating the old combined metadata file."""
        shared = self.resource_cache.get(self._snapshot_key()) if self.resource_cache is not None else None
        if shared is not None and isinstance(shared.value, dict):
            try:
                if self._restore_payload(shared.value):
                    if (
                        self.cache_path is None
                        and legacy_cache_path is not None
                        and self.resource_cache is not None
                        and self.resource_cache.directory is not None
                    ):
                        try:
                            Path(legacy_cache_path).unlink()
                        except OSError:
                            pass
                    return
            except (TypeError, ValueError, KeyError):
                pass
        path = self.cache_path
        source = path if path and path.is_file() else Path(legacy_cache_path) if legacy_cache_path else None
        if source is None or not source.is_file():
            return
        try:
            with source.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if self._restore_payload(data) and self.resource_cache is not None:
                self.resource_cache.put(
                    self._snapshot_key(), data, content_type="application/json"
                )
                if self.cache_path is None and self.resource_cache.directory is not None:
                    try:
                        source.unlink()
                    except OSError:
                        pass
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            # Cache corruption must never prevent cloud status loading.
            return

    def _snapshot_key(self) -> RequestKey:
        return RequestKey("cloud-status-snapshot", self.current_context().cache_identity, "v1")

    def _restore_payload(self, data: dict) -> bool:
        context = self.current_context()
        if not isinstance(data, dict) or data.get("cloud_context") != context.fingerprint:
            return False
        cloud_cache = data.get("cloud_save_status", data.get("statuses", data.get("status", {})))
        if not isinstance(cloud_cache, dict):
            return False
        from core.cloud_models import SaveStats, SyncStatus
        for raw_game_id, entry in cloud_cache.items():
            if not isinstance(entry, dict):
                continue
            status_value = entry.get("status")
            if not status_value:
                continue
            try:
                game_id = int(raw_game_id)
                status = SyncStatus(status_value)
            except (TypeError, ValueError):
                continue
            local_stats = SaveStats(
                exists=entry.get("local_exists", False),
                last_modified=entry.get("local_mtime", 0.0),
                size_bytes=entry.get("local_size", 0),
                file_count=entry.get("local_count", 0),
                display_path=entry.get("display_path", ""),
            )
            cloud_stats = SaveStats(
                exists=entry.get("cloud_exists", False),
                last_modified=entry.get("cloud_mtime", 0.0),
                size_bytes=entry.get("cloud_size", 0),
            )
            self.status_store.set_cloud_status(
                game_id,
                status,
                local_stats,
                cloud_stats,
                checked_at=entry.get("checked_at", time.time()),
                context_generation=context.generation,
            )
        return True

    def save_cache(self) -> None:
        """Persist only cloud status data using an atomic, credential-free file."""
        with self._lock:
            payload = {
                "statuses": {},
                "cloud_context": self.current_context().fingerprint,
                "saved_at": time.time(),
            }
            for game_id, value in self.status_store.items():
                status, local_stats, cloud_stats = value
                payload["statuses"][str(game_id)] = {
                    "status": status.value if hasattr(status, "value") else str(status),
                    "local_exists": getattr(local_stats, "exists", False) if local_stats else False,
                    "local_mtime": getattr(local_stats, "last_modified", 0.0) if local_stats else 0.0,
                    "local_size": getattr(local_stats, "size_bytes", 0) if local_stats else 0,
                    "local_count": getattr(local_stats, "file_count", 0) if local_stats else 0,
                    "display_path": getattr(local_stats, "display_path", "") if local_stats else "",
                    "cloud_exists": getattr(cloud_stats, "exists", False) if cloud_stats else False,
                    "cloud_mtime": getattr(cloud_stats, "last_modified", 0.0) if cloud_stats else 0.0,
                    "cloud_size": getattr(cloud_stats, "size_bytes", 0) if cloud_stats else 0,
                    "checked_at": self.status_store.checked_at(game_id),
                }
            if self.resource_cache is not None:
                self.resource_cache.put(
                    self._snapshot_key(), payload, content_type="application/json"
                )
            if self.cache_path is None:
                return
            directory = self.cache_path.parent
            temporary = None
            try:
                directory.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix=".cloud-status-", suffix=".tmp", dir=str(directory))
                temporary_path = temporary
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, self.cache_path)
                temporary = None
            except (OSError, TypeError, ValueError):
                if temporary:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass


__all__ = [
    "CloudStatusRequestError",
    "CloudStatusPlan",
    "CloudStatusService",
    "CloudStatusTarget",
]
