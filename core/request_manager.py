"""Bounded, deduplicated request scheduling for remote resources.

This module is intentionally independent from Qt and transport clients. A
loader receives a cooperative :class:`CancellationToken` and returns a parsed
value. The manager owns scheduling, retries, cancellation, and generation
ordering; callers receive a :class:`ResourceResult` through a Future or a
listener.
"""

from __future__ import annotations

import queue
import random
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, replace
from typing import Callable, Iterable

from core.logger import get_logger
from core.request_contracts import (
    CancellationToken,
    RequestCancelled,
    RequestKey,
    RequestPriority,
    RequestSpec,
    RetryPolicy,
    ResourceResult,
    ResourceStatus,
    resource_status_for_error,
)
from core.resource_cache import ResourceCache


logger = get_logger("RequestManager")


@dataclass(frozen=True, slots=True)
class RequestHandle:
    """The caller-facing handle for one scheduled request."""

    key: RequestKey
    request_id: str
    generation: int
    future: Future
    _cancel: Callable[[], bool]

    def cancel(self) -> bool:
        return self._cancel()


@dataclass
class _RequestRecord:
    spec: RequestSpec
    request_id: str
    token: CancellationToken
    future: Future
    sequence: int
    queued_at: float
    queued: bool = True
    started_at: float = 0.0


class RequestManager:
    """Schedule bounded background work with deduplication and retries."""

    def __init__(
        self,
        max_workers: int = 4,
        *,
        offline_check: Callable[[], bool] | None = None,
        cache: ResourceCache | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self.max_workers = int(max_workers)
        self._offline_check = offline_check or (lambda: False)
        self.cache = cache
        self._sleep = sleep
        self._interruptible_sleep = sleep is time.sleep
        self._random_value = random_value
        self._queue: queue.PriorityQueue[tuple[int, int, _RequestRecord | None]] = queue.PriorityQueue()
        self._condition = threading.Condition()
        self._active: dict[RequestKey, _RequestRecord] = {}
        self._states: dict[RequestKey, ResourceResult] = {}
        self._cache_by_key: dict[RequestKey, ResourceCache] = {}
        self._listeners: dict[RequestKey, dict[str, Callable[[ResourceResult], None]]] = {}
        self._last_generation: dict[RequestKey, int] = {}
        self._metrics = {
            "submitted": 0,
            "deduplicated": 0,
            "completed": 0,
            "errors": 0,
            "retries": 0,
            "cancelled": 0,
            "offline": 0,
            "invalidated": 0,
            "cache_hits": 0,
            "cache_stale": 0,
            "cache_misses": 0,
            "cache_memory_hits": 0,
            "cache_disk_hits": 0,
            "duration_seconds_total": 0.0,
            "duration_seconds_max": 0.0,
            "queue_wait_seconds_total": 0.0,
            "queue_wait_seconds_max": 0.0,
            "active_peak": 0,
            "workers_peak": 0,
            "workers_configured": self.max_workers,
        }
        self._sequence = 0
        self._workers_active = 0
        self._closed = False
        self._workers = [
            threading.Thread(
                target=self._worker_loop,
                name=f"SafeLauncher-Request-{index + 1}",
                daemon=True,
            )
            for index in range(self.max_workers)
        ]
        for worker in self._workers:
            worker.start()

    def submit(self, spec: RequestSpec) -> RequestHandle:
        """Submit work, deduplicating an equal or older active generation."""
        loading_result = None
        with self._condition:
            if self._closed:
                raise RuntimeError("RequestManager is shut down")

            current = self._active.get(spec.key)
            if current is not None:
                if spec.generation <= current.spec.generation:
                    self._metrics["deduplicated"] += 1
                    return self._handle_for(current)
                current.token.cancel()

            # Generation zero is the convenience API's implicit generation.
            # After invalidate(), the manager has advanced the tombstone so a
            # late result from the cancelled request cannot overwrite a new
            # request. Automatically advance the convenience request as well;
            # callers using explicit generations retain full ordering control.
            last_generation = self._last_generation.get(spec.key, -1)
            if current is None and spec.generation == 0 and last_generation >= 0:
                spec = replace(spec, generation=last_generation + 1)

            self._sequence += 1
            record = _RequestRecord(
                spec=spec,
                request_id=uuid.uuid4().hex,
                token=CancellationToken(spec.timeout_seconds, start_immediately=False),
                future=Future(),
                sequence=self._sequence,
                queued_at=time.monotonic(),
            )
            self._active[spec.key] = record
            self._metrics["submitted"] += 1
            self._metrics["active_peak"] = max(
                self._metrics["active_peak"], len(self._active)
            )
            self._last_generation[spec.key] = max(last_generation, spec.generation)
            loading_result = ResourceResult(
                key=spec.key,
                status=ResourceStatus.LOADING,
                request_id=record.request_id,
                generation=spec.generation,
            )
            self._states[spec.key] = loading_result
            self._queue.put((int(spec.priority), record.sequence, record))
            self._condition.notify_all()
            handle = self._handle_for(record)
        self._notify(spec.key, loading_result)
        return handle

    def request(
        self,
        key: RequestKey,
        loader,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        retry_policy=None,
        generation: int = 0,
        metadata: dict | None = None,
        timeout_seconds: float | None = None,
    ) -> RequestHandle:
        """Convenience wrapper for constructing a :class:`RequestSpec`."""
        return self.submit(RequestSpec(
            key=key,
            loader=loader,
            priority=priority,
            retry_policy=retry_policy or RetryPolicy(),
            generation=generation,
            metadata=metadata or {},
            timeout_seconds=timeout_seconds,
        ))

    def request_cached(
        self,
        key: RequestKey,
        loader,
        *,
        max_age_seconds: float,
        cache: ResourceCache | None = None,
        priority: RequestPriority = RequestPriority.NORMAL,
        retry_policy=None,
        generation: int = 0,
        metadata: dict | None = None,
        timeout_seconds: float | None = None,
        stale_while_revalidate: bool = True,
        content_type: str = "application/json",
    ) -> RequestHandle:
        """Request one resource through the manager's shared cache.

        A cache can be supplied for an isolated feature or omitted when the
        manager was constructed with its application-scoped cache.
        """
        target_cache = cache or self.cache
        if target_cache is None:
            raise ValueError("request_cached requires a ResourceCache")
        spec = RequestSpec(
            key=key,
            loader=loader,
            priority=priority,
            retry_policy=retry_policy or RetryPolicy(),
            generation=generation,
            metadata=metadata or {},
            timeout_seconds=timeout_seconds,
        )
        return self.cached_request(
            spec,
            target_cache,
            max_age_seconds=max_age_seconds,
            stale_while_revalidate=stale_while_revalidate,
            content_type=content_type,
        )

    def request_many(
        self,
        specs_or_keys: Iterable[RequestSpec | RequestKey],
        loader=None,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        retry_policy=None,
        generation: int = 0,
        timeout_seconds: float | None = None,
        progress=None,
        on_complete: Callable[[list[ResourceResult]], None] | None = None,
    ) -> list[RequestHandle]:
        """Submit a batch while retaining per-resource deduplication.

        ``progress`` receives ``(completed, total, result)`` as each request
        finishes. It is deliberately a plain callback so Qt and non-Qt
        consumers can choose their own thread-affinity bridge.
        """
        specs_or_keys = list(specs_or_keys)
        if loader is None:
            specs = specs_or_keys
        else:
            policy = retry_policy or RetryPolicy()
            specs = [
                RequestSpec(
                    key,
                    lambda token, key=key: loader(key, token),
                    priority=priority,
                    retry_policy=policy,
                    generation=generation,
                    timeout_seconds=timeout_seconds,
                )
                for key in specs_or_keys
            ]
        handles = [self.submit(spec) for spec in specs]
        self._attach_batch_callbacks(handles, progress, on_complete)
        return handles

    def request_many_cached(
        self,
        specs_or_keys: Iterable[RequestSpec | RequestKey],
        loader=None,
        *,
        max_age_seconds: float,
        cache: ResourceCache | None = None,
        priority: RequestPriority = RequestPriority.NORMAL,
        retry_policy=None,
        generation: int = 0,
        timeout_seconds: float | None = None,
        stale_while_revalidate: bool = True,
        content_type: str = "application/json",
        progress=None,
        on_complete: Callable[[list[ResourceResult]], None] | None = None,
    ) -> list[RequestHandle]:
        """Submit a batch through the cache while preserving per-key dedupe."""
        target_cache = cache or self.cache
        if target_cache is None:
            raise ValueError("request_many_cached requires a ResourceCache")
        values = list(specs_or_keys)
        if loader is None:
            specs = values
        else:
            policy = retry_policy or RetryPolicy()
            specs = [
                RequestSpec(
                    key,
                    lambda token, key=key: loader(key, token),
                    priority=priority,
                    retry_policy=policy,
                    generation=generation,
                    timeout_seconds=timeout_seconds,
                )
                for key in values
            ]
        handles = [
            self.cached_request(
                spec,
                target_cache,
                max_age_seconds=max_age_seconds,
                stale_while_revalidate=stale_while_revalidate,
                content_type=content_type,
            )
            for spec in specs
        ]
        self._attach_batch_callbacks(handles, progress, on_complete)
        return handles

    @staticmethod
    def _attach_batch_callbacks(
        handles: list[RequestHandle],
        progress,
        on_complete: Callable[[list[ResourceResult]], None] | None,
    ) -> None:
        """Attach one consistent progress/completion contract to a batch."""
        if progress is None and on_complete is None:
            return
        total = len(handles)
        if total == 0:
            if on_complete is not None:
                try:
                    on_complete([])
                except Exception:
                    logger.exception("Empty batch completion callback failed")
            return

        completed = 0
        results: list[ResourceResult | None] = [None] * total
        progress_lock = threading.Lock()

        def on_done(index: int, future: Future) -> None:
            nonlocal completed
            try:
                result = future.result()
            except Exception as exc:
                result = ResourceResult(
                    key=handles[index].key,
                    status=ResourceStatus.ERROR,
                    error=exc,
                    request_id=handles[index].request_id,
                    generation=handles[index].generation,
                )
            with progress_lock:
                results[index] = result
                completed += 1
                current = completed
                finished = completed == total
                completed_results = list(results) if finished else None
            if progress is not None:
                try:
                    progress(current, total, result)
                except Exception:
                    logger.exception("Batch progress callback failed")
            if finished and on_complete is not None:
                try:
                    on_complete(completed_results or [])
                except Exception:
                    logger.exception("Batch completion callback failed")

        for index, handle in enumerate(handles):
            handle.future.add_done_callback(
                lambda future, index=index: on_done(index, future)
            )

    def invalidate(self, key: RequestKey) -> bool:
        """Cancel current work and discard the current resource state."""
        with self._condition:
            record = self._active.get(key)
            existed = record is not None or key in self._states
            if existed:
                self._metrics["invalidated"] += 1
            cache = self._cache_by_key.pop(key, None)
            if record is not None:
                record.token.cancel()
                # Remove the cancelled record immediately. Its worker may
                # still be unwinding, but the next request must be allowed to
                # start and its older completion will be suppressed by the
                # generation tombstone below.
                self._active.pop(key, None)
            generation = self._last_generation.get(key, -1) + 1
            self._last_generation[key] = generation
            idle = ResourceResult(
                key=key,
                status=ResourceStatus.IDLE,
                request_id="invalidate-" + uuid.uuid4().hex,
                generation=generation,
            )
            self._states[key] = idle
        if cache is not None:
            cache.invalidate(key)
        elif self.cache is not None:
            self.cache.invalidate(key)
        self._notify(key, idle)
        return existed

    def state(self, key: RequestKey) -> ResourceResult:
        """Return the latest known state, or an explicit idle result."""
        with self._condition:
            return self._states.get(key, ResourceResult(key=key, status=ResourceStatus.IDLE))

    def metrics(self) -> dict[str, int | float]:
        """Return a point-in-time copy of request activity counters."""
        with self._condition:
            snapshot = dict(self._metrics)
            snapshot["active_current"] = len(self._active)
            snapshot["workers_current"] = self._workers_active
        cache_lookups = (
            snapshot.get("cache_hits", 0)
            + snapshot.get("cache_stale", 0)
            + snapshot.get("cache_misses", 0)
        )
        snapshot["cache_hit_rate"] = (
            (snapshot.get("cache_hits", 0) + snapshot.get("cache_stale", 0))
            / cache_lookups
            if cache_lookups
            else 0.0
        )
        return snapshot

    def cached_request(
        self,
        spec: RequestSpec,
        cache: ResourceCache,
        *,
        max_age_seconds: float,
        stale_while_revalidate: bool = True,
        content_type: str = "application/json",
    ) -> RequestHandle:
        """Serve fresh cache data or refresh stale data in the background."""
        with self._condition:
            self._cache_by_key[spec.key] = cache
        cached = cache.get(spec.key)
        if cached is not None and cached.is_fresh(max_age_seconds):
            self._increment_metric("cache_hits")
            self._increment_metric(
                "cache_memory_hits" if cached.source == "memory" else "cache_disk_hits"
            )
            result = ResourceResult(
                key=spec.key,
                status=ResourceStatus.READY,
                value=cached.value,
                request_id="cache-" + uuid.uuid4().hex,
                generation=spec.generation,
                from_cache=True,
                cache_source=cached.source,
                updated_at=cached.stored_at,
            )
            future = Future()
            future.set_result(result)
            self._notify(spec.key, result)
            return RequestHandle(spec.key, result.request_id, spec.generation, future, lambda: False)

        if cached is not None and stale_while_revalidate:
            self._increment_metric("cache_stale")
            self._increment_metric(
                "cache_memory_hits" if cached.source == "memory" else "cache_disk_hits"
            )
            self._notify(spec.key, ResourceResult(
                key=spec.key,
                status=ResourceStatus.STALE,
                value=cached.value,
                request_id="cache-" + uuid.uuid4().hex,
                generation=spec.generation,
                from_cache=True,
                cache_source=cached.source,
                updated_at=cached.stored_at,
            ))

        if cached is None:
            self._increment_metric("cache_misses")

        if cached is not None:
            metadata = dict(spec.metadata)
            metadata["__stale_cache_entry"] = cached
            spec = RequestSpec(
                key=spec.key,
                loader=spec.loader,
                priority=spec.priority,
                retry_policy=spec.retry_policy,
                generation=spec.generation,
                metadata=metadata,
                timeout_seconds=spec.timeout_seconds,
            )

        try:
            offline = self._offline_check() and not spec.metadata.get("allow_offline", False)
        except Exception:
            offline = False
        if offline:
            self._increment_metric("offline")
            offline_result = ResourceResult(
                key=spec.key,
                status=ResourceStatus.OFFLINE,
                request_id="offline-" + uuid.uuid4().hex,
                generation=spec.generation,
            )
            future = Future()
            future.set_result(offline_result)
            self._notify(spec.key, offline_result)
            if cached is not None:
                self._notify(spec.key, ResourceResult(
                    key=spec.key,
                    status=ResourceStatus.STALE,
                    value=cached.value,
                    error=offline_result.error,
                    request_id=offline_result.request_id,
                    generation=spec.generation,
                    from_cache=True,
                    cache_source=cached.source,
                    updated_at=cached.stored_at,
                ))
            return RequestHandle(
                spec.key,
                offline_result.request_id,
                spec.generation,
                future,
                lambda: False,
            )

        handle = self.submit(spec)

        def cache_result(future: Future) -> None:
            try:
                result = future.result()
                if result.status == ResourceStatus.READY:
                    cache.put(spec.key, result.value, content_type=content_type, stored_at=result.updated_at or time.time())
            except Exception:
                pass

        handle.future.add_done_callback(cache_result)
        return handle

    def subscribe(
        self,
        key: RequestKey,
        callback: Callable[[ResourceResult], None],
        *,
        emit_current: bool = True,
    ) -> Callable[[], None]:
        """Subscribe to state changes and return an unsubscribe function."""
        token = uuid.uuid4().hex
        with self._condition:
            self._listeners.setdefault(key, {})[token] = callback
            current = self._states.get(key)
        if emit_current and current is not None:
            try:
                callback(current)
            except Exception:
                logger.exception("Initial request state callback failed for %s", key)

        def unsubscribe() -> None:
            with self._condition:
                listeners = self._listeners.get(key)
                if listeners:
                    listeners.pop(token, None)
                    if not listeners:
                        self._listeners.pop(key, None)

        return unsubscribe

    def cancel(self, key: RequestKey, generation: int | None = None) -> bool:
        with self._condition:
            record = self._active.get(key)
            if record is None or (generation is not None and record.spec.generation != generation):
                return False
            record.token.cancel()
            return True

    def shutdown(self, wait: bool = True) -> None:
        """Stop accepting work and cooperatively stop all queued/running work."""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            for record in self._active.values():
                record.token.cancel()
            for _ in self._workers:
                # Let already-queued records observe cancellation and resolve
                # their futures before a worker exits. No new submissions are
                # accepted after _closed is set, so background sentinels are
                # guaranteed to be last in the existing queue.
                self._queue.put((RequestPriority.BACKGROUND, self._sequence, None))
                self._sequence += 1
            self._condition.notify_all()
        if wait:
            for worker in self._workers:
                worker.join()

    def _handle_for(self, record: _RequestRecord) -> RequestHandle:
        return RequestHandle(
            key=record.spec.key,
            request_id=record.request_id,
            generation=record.spec.generation,
            future=record.future,
            _cancel=lambda record=record: self._cancel_record(record),
        )

    def _cancel_record(self, record: _RequestRecord) -> bool:
        with self._condition:
            current = self._active.get(record.spec.key)
            if current is not record:
                return False
            record.token.cancel()
            return True

    def _worker_loop(self) -> None:
        while True:
            _priority, _sequence, record = self._queue.get()
            if record is None:
                self._queue.task_done()
                return
            try:
                with self._condition:
                    self._workers_active += 1
                    self._metrics["workers_peak"] = max(
                        self._metrics["workers_peak"], self._workers_active
                    )
                self._run_record(record)
            finally:
                with self._condition:
                    self._workers_active = max(0, self._workers_active - 1)
                self._queue.task_done()

    def _run_record(self, record: _RequestRecord) -> None:
        record.started_at = time.monotonic()
        queue_wait = max(0.0, record.started_at - record.queued_at)
        with self._condition:
            self._metrics["queue_wait_seconds_total"] += queue_wait
            self._metrics["queue_wait_seconds_max"] = max(
                self._metrics["queue_wait_seconds_max"], queue_wait
            )
        if queue_wait >= 1.0:
            tag = str(record.spec.metadata.get("tag", "") or "")
            logger.debug(
                "Request %s%s waited %.2fs before execution",
                record.spec.key,
                f" ({tag})" if tag else "",
                queue_wait,
            )
        record.token.start()
        if record.token.cancelled:
            self._finish(record, self._cancelled_result(record))
            return
        try:
            if self._offline_check() and not record.spec.metadata.get("allow_offline", False):
                self._increment_metric("offline")
                self._finish(record, ResourceResult(
                    key=record.spec.key,
                    status=ResourceStatus.OFFLINE,
                    request_id=record.request_id,
                    generation=record.spec.generation,
                ))
                return
        except Exception as exc:
            logger.debug("Offline check failed: %s", exc)

        policy = record.spec.retry_policy
        for attempt in range(policy.max_attempts):
            try:
                record.token.raise_if_cancelled()
                value = record.spec.loader(record.token)
                record.token.raise_if_cancelled()
                self._finish(record, ResourceResult(
                    key=record.spec.key,
                    status=ResourceStatus.READY,
                    value=value,
                    request_id=record.request_id,
                    generation=record.spec.generation,
                    updated_at=time.time(),
                ))
                return
            except RequestCancelled as exc:
                self._finish(record, self._cancelled_result(record, exc))
                return
            except Exception as exc:
                if record.token.cancelled:
                    self._finish(record, self._cancelled_result(record, exc))
                    return
                if record.token.timed_out:
                    self._finish(record, ResourceResult(
                        key=record.spec.key,
                        status=resource_status_for_error(exc),
                        error=exc,
                        request_id=record.request_id,
                        generation=record.spec.generation,
                    ))
                    return
                try:
                    retryable = policy.should_retry(exc)
                except Exception:
                    # A custom classifier belongs to the caller, but a bug in
                    # that hook must not strand the request future forever.
                    logger.exception("Retry classifier failed for %s", record.spec.key)
                    retryable = False
                if attempt + 1 >= policy.max_attempts or not retryable:
                    self._finish(record, ResourceResult(
                        key=record.spec.key,
                        status=resource_status_for_error(exc),
                        error=exc,
                        request_id=record.request_id,
                        generation=record.spec.generation,
                    ))
                    return
                self._increment_metric("retries")
                delay = min(policy.max_delay_seconds, policy.base_delay_seconds * (2 ** attempt))
                if policy.jitter_ratio:
                    delay *= 1 + ((self._random_value() * 2 - 1) * policy.jitter_ratio)
                if delay > 0:
                    if self._interruptible_sleep:
                        cancelled = record.token.wait(delay)
                    else:
                        self._sleep(max(0.0, delay))
                        cancelled = record.token.cancelled
                    if cancelled or record.token.timed_out:
                        if record.token.timed_out and not record.token.cancelled:
                            self._finish(record, ResourceResult(
                                key=record.spec.key,
                                status=resource_status_for_error(RequestTimeout("Request exceeded its timeout during retry backoff")),
                                error=RequestTimeout("Request exceeded its timeout during retry backoff"),
                                request_id=record.request_id,
                                generation=record.spec.generation,
                            ))
                            return
                        self._finish(record, self._cancelled_result(record, RequestCancelled("Request was cancelled during retry backoff")))
                        return

    @staticmethod
    def _cancelled_result(record: _RequestRecord, error: BaseException | None = None) -> ResourceResult:
        return ResourceResult(
            key=record.spec.key,
            status=ResourceStatus.CANCELLED,
            error=error or RequestCancelled("Request was cancelled"),
            request_id=record.request_id,
            generation=record.spec.generation,
        )

    def _finish(self, record: _RequestRecord, result: ResourceResult) -> None:
        duration = max(0.0, time.monotonic() - record.started_at) if record.started_at else 0.0
        with self._condition:
            self._metrics["duration_seconds_total"] += duration
            self._metrics["duration_seconds_max"] = max(
                self._metrics["duration_seconds_max"], duration
            )
        self._increment_metric("completed")
        if result.status == ResourceStatus.ERROR:
            self._increment_metric("errors")
        elif result.status == ResourceStatus.CANCELLED:
            self._increment_metric("cancelled")
        with self._condition:
            current = self._active.get(record.spec.key)
            if current is record:
                self._active.pop(record.spec.key, None)
            last_generation = self._last_generation.get(record.spec.key, -1)
            is_current_generation = result.generation >= last_generation
            listener_result = result
            stale_entry = record.spec.metadata.get("__stale_cache_entry")
            if is_current_generation:
                if stale_entry is not None and result.status in {
                    ResourceStatus.ERROR,
                    ResourceStatus.OFFLINE,
                    ResourceStatus.UNAVAILABLE,
                    ResourceStatus.AUTHENTICATION_REQUIRED,
                    ResourceStatus.PERMISSION_DENIED,
                    ResourceStatus.CONFLICT,
                }:
                    listener_result = ResourceResult(
                        key=record.spec.key,
                        status=ResourceStatus.STALE,
                        value=stale_entry.value,
                        error=result.error,
                        request_id=result.request_id,
                        generation=result.generation,
                        from_cache=True,
                        cache_source=stale_entry.source,
                        updated_at=stale_entry.stored_at,
                    )
                    self._states[record.spec.key] = listener_result
                else:
                    self._states[record.spec.key] = result
            listeners = list(self._listeners.get(record.spec.key, {}).values()) if is_current_generation else []
            if not record.future.done():
                record.future.set_result(result)
        for callback in listeners:
            try:
                callback(listener_result)
            except Exception:
                logger.exception("Request listener failed for %s", result.key)

    def _increment_metric(self, name: str) -> None:
        with self._condition:
            self._metrics[name] = self._metrics.get(name, 0) + 1

    def _notify(self, key: RequestKey, result: ResourceResult) -> None:
        with self._condition:
            last_generation = self._last_generation.get(key, -1)
            if result.generation < last_generation:
                return
            self._last_generation[key] = result.generation
            self._states[key] = result
            listeners = list(self._listeners.get(key, {}).values())
        for callback in listeners:
            try:
                callback(result)
            except Exception:
                logger.exception("Request listener failed for %s", result.key)


ResourceManager = RequestManager

__all__ = ["RequestHandle", "RequestManager", "ResourceManager"]
