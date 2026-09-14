"""Managed private library/profile reconciliation resources.

This service schedules the existing ``CloudMetadataSync`` algorithms through
the shared RequestManager.  The synchronizer remains responsible for local
payload construction, merge rules, revision retries, local projection, and
the digest-only offline queue; this layer owns request identity and lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Callable

from core.cloud_context import CloudContext
from core.cloud_metadata_sync import CloudMetadataSync
from core.request_contracts import (
    CancellationToken,
    RequestKey,
    RequestPriority,
    RequestSpec,
    RetryPolicy,
    is_transient_error,
)


@dataclass(frozen=True, slots=True)
class CloudMetadataTarget:
    """Stable identity for one local library record."""

    game_id: int
    game_name: str
    app_id: str = ""


@dataclass(frozen=True, slots=True)
class CloudMetadataResult:
    """Typed outcome for a private reconciliation request."""

    operation: str
    success: bool
    game_id: int | None = None
    queued: bool = False
    error: str = ""


class CloudMetadataService:
    """Submit private profile and library reconciliation through RequestManager."""

    def __init__(
        self,
        request_manager,
        *,
        context_provider: Callable[[int], CloudContext] | None = None,
        db_factory=None,
    ) -> None:
        self.request_manager = request_manager
        self._context_provider = context_provider or (
            lambda generation: CloudContext.current(generation=generation)
        )
        self._context: CloudContext | None = None
        self._db_factory = db_factory
        # Coalescing belongs here, beside the managed reconciliation request,
        # rather than in MainWindow.  A second local edit while a sync is
        # running is remembered as one follow-up request; callers all share
        # the same handle for the active generation.
        self._lock = RLock()
        self._latest_handles: dict[RequestKey, object] = {}
        self._latest_pending: set[RequestKey] = set()

    def _cancel_latest_locked(self) -> None:
        handles = tuple(self._latest_handles.values())
        self._latest_handles.clear()
        self._latest_pending.clear()
        for handle in handles:
            try:
                handle.cancel()
            except (AttributeError, RuntimeError):
                pass

    def current_context(self) -> CloudContext:
        with self._lock:
            candidate = self._context_provider(getattr(self._context, "generation", 0))
            if self._context is not None and candidate.cache_identity != self._context.cache_identity:
                generation = self._context.generation + 1
                candidate = self._context_provider(generation)
                self._cancel_latest_locked()
            self._context = candidate
            return candidate

    def invalidate_context(self) -> CloudContext:
        with self._lock:
            generation = (self._context.generation + 1) if self._context is not None else 1
            self._cancel_latest_locked()
            self._context = self._context_provider(generation)
            return self._context

    @staticmethod
    def _retry_policy() -> RetryPolicy:
        return RetryPolicy(retry_if=lambda error: is_transient_error(error))

    def key_for_profile(self, *, context: CloudContext | None = None) -> RequestKey:
        context = context or self.current_context()
        return context.request_key("cloud-library-profile", "account", "v1")

    def key_for_game(self, target: CloudMetadataTarget, *, context: CloudContext | None = None) -> RequestKey:
        context = context or self.current_context()
        return context.request_key("cloud-game-metadata", str(int(target.game_id)), "v1")

    def _db(self, db_path):
        if self._db_factory is not None:
            return self._db_factory(db_path)
        from database import GameDatabase
        return GameDatabase(db_path) if db_path else GameDatabase()

    def request_profile(
        self,
        db_path=None,
        *,
        force: bool = False,
        priority: RequestPriority = RequestPriority.NORMAL,
        generation: int | None = None,
        tag: str = "",
    ):
        context = self.current_context()
        request_generation = context.generation if generation is None else int(generation)

        def load(token: CancellationToken) -> CloudMetadataResult:
            token.raise_if_cancelled()
            worker_db = self._db(db_path)
            try:
                success = bool(CloudMetadataSync.sync_profile(worker_db, force=force))
                token.raise_if_cancelled()
                return CloudMetadataResult("profile", success, queued=not success)
            finally:
                worker_db.close()

        spec = RequestSpec(
            self.key_for_profile(context=context),
            load,
            priority=priority,
            retry_policy=self._retry_policy(),
            generation=request_generation,
            metadata={
                "cloud_context": context.cache_identity,
                "cloud_generation": request_generation,
                "operation": "profile_sync",
                "allow_offline": True,
                "tag": str(tag),
            },
        )
        return self.request_manager.submit(spec)

    def request_game(
        self,
        target: CloudMetadataTarget,
        db_path=None,
        *,
        drop_legacy_achievements: bool = False,
        priority: RequestPriority = RequestPriority.BACKGROUND,
        generation: int | None = None,
        tag: str = "",
    ):
        context = self.current_context()
        request_generation = context.generation if generation is None else int(generation)

        def load(token: CancellationToken) -> CloudMetadataResult:
            token.raise_if_cancelled()
            worker_db = self._db(db_path)
            try:
                success = bool(CloudMetadataSync.sync_game(
                    worker_db,
                    target.game_id,
                    target.game_name,
                    target.app_id,
                    drop_legacy_achievements=drop_legacy_achievements,
                ))
                token.raise_if_cancelled()
                return CloudMetadataResult("game", success, target.game_id, queued=not success)
            finally:
                worker_db.close()

        spec = RequestSpec(
            self.key_for_game(target, context=context),
            load,
            priority=priority,
            retry_policy=self._retry_policy(),
            generation=request_generation,
            metadata={
                "cloud_context": context.cache_identity,
                "cloud_generation": request_generation,
                "operation": "game_metadata_sync",
                "game_id": int(target.game_id),
                "allow_offline": True,
                "tag": str(tag),
            },
        )
        return self.request_manager.submit(spec)

    def request_profile_and_game(
        self,
        target: CloudMetadataTarget,
        db_path=None,
        *,
        force_profile: bool = False,
        priority: RequestPriority = RequestPriority.BACKGROUND,
        generation: int | None = None,
        tag: str = "",
    ):
        """Reconcile account profile first, then the legacy game record."""
        context = self.current_context()
        request_generation = context.generation if generation is None else int(generation)

        def load(token: CancellationToken) -> CloudMetadataResult:
            token.raise_if_cancelled()
            worker_db = self._db(db_path)
            try:
                profile_synced = bool(CloudMetadataSync.sync_profile(worker_db, force=force_profile))
                token.raise_if_cancelled()
                game_synced = bool(CloudMetadataSync.sync_game(
                    worker_db,
                    target.game_id,
                    target.game_name,
                    target.app_id,
                    drop_legacy_achievements=profile_synced,
                ))
                token.raise_if_cancelled()
                return CloudMetadataResult(
                    "profile_and_game",
                    profile_synced and game_synced,
                    target.game_id,
                    queued=not (profile_synced and game_synced),
                )
            finally:
                worker_db.close()

        spec = RequestSpec(
            context.request_key("cloud-library-reconcile", str(int(target.game_id)), "v1"),
            load,
            priority=priority,
            retry_policy=self._retry_policy(),
            generation=request_generation,
            metadata={
                "cloud_context": context.cache_identity,
                "cloud_generation": request_generation,
                "operation": "profile_and_game_sync",
                "game_id": int(target.game_id),
                "allow_offline": True,
                "tag": str(tag),
            },
        )
        return self.request_manager.submit(spec)

    def request_latest_game(
        self,
        target: CloudMetadataTarget,
        db_path=None,
        *,
        force_profile: bool = False,
        priority: RequestPriority = RequestPriority.BACKGROUND,
        tag: str = "",
    ):
        """Request the newest reconciliation for a game, coalescing repeats.

        This is the application-facing API for local metadata changes.  When
        another request for the same context/game is active, the current
        handle is returned and one follow-up run is scheduled after it
        settles.  The follow-up is discarded if the cloud context changed.
        """
        context = self.current_context()
        key = context.request_key(
            "cloud-library-reconcile", str(int(target.game_id)), "v1"
        )
        with self._lock:
            active = self._latest_handles.get(key)
            if active is not None and not active.future.done():
                self._latest_pending.add(key)
                return active

            handle = self.request_profile_and_game(
                target,
                db_path,
                force_profile=force_profile,
                priority=priority,
                generation=context.generation,
                tag=tag,
            )
            self._latest_handles[key] = handle

            def _settled(_future, *, request_key=key, request_handle=handle,
                         request_context=context, request_target=target):
                with self._lock:
                    if self._latest_handles.get(request_key) is not request_handle:
                        return
                    self._latest_handles.pop(request_key, None)
                    rerun = request_key in self._latest_pending
                    self._latest_pending.discard(request_key)
                    current = self._context
                    if not rerun or current is None:
                        return
                    if (
                        current.cache_identity != request_context.cache_identity
                        or current.generation != request_context.generation
                    ):
                        return
                    next_handle = self.request_profile_and_game(
                        request_target,
                        db_path,
                        force_profile=force_profile,
                        priority=priority,
                        generation=current.generation,
                        tag=tag,
                    )
                    self._latest_handles[request_key] = next_handle
                    next_handle.future.add_done_callback(
                        lambda future: _settled(future, request_handle=next_handle)
                    )

            handle.future.add_done_callback(_settled)
            return handle


__all__ = ["CloudMetadataResult", "CloudMetadataService", "CloudMetadataTarget"]
