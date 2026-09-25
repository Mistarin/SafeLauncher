"""Lifecycle owner for periodic private cloud-save listing checks.

The service is deliberately Qt-free.  It keeps a thread-safe snapshot of the
library targets and asks :class:`CloudStatusService` to perform one managed
listing-diff request at a time.  UI code supplies the target snapshot and a
callback; it does not own a second timer or polling state machine.
"""

from __future__ import annotations

import threading
from threading import RLock
from typing import Callable, Iterable

from core.cloud_status_service import CloudStatusService, CloudStatusTarget


class CloudStatusPollingService:
    """Own periodic cloud listing polling and its cooperative lifecycle."""

    def __init__(
        self,
        status_service: CloudStatusService,
        *,
        interval_seconds: float = 5 * 60,
        on_changed: Callable[[list[CloudStatusTarget]], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.status_service = status_service
        self.interval_seconds = float(interval_seconds)
        self._on_changed = on_changed
        self._on_error = on_error
        self._lock = RLock()
        self._targets: tuple[CloudStatusTarget, ...] = ()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._handle = None
        self._running = False

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def set_targets(self, targets: Iterable[CloudStatusTarget]) -> None:
        """Replace the immutable library snapshot used by future polls."""
        normalized = tuple(
            target if isinstance(target, CloudStatusTarget) else CloudStatusTarget(*target)
            for target in targets
        )
        with self._lock:
            self._targets = normalized

    def targets(self) -> tuple[CloudStatusTarget, ...]:
        with self._lock:
            return self._targets

    def poll_once(self, *, reason: str = "poll"):
        """Submit one changed-listing request using the current target snapshot."""
        with self._lock:
            if self._stop_event.is_set():
                return None
            targets = self._targets

        if not targets:
            return None

        context = self.status_service.current_context()
        if not context.remote_requests_allowed:
            return None
        generation = context.generation

        def complete(changed: list[CloudStatusTarget]) -> None:
            with self._lock:
                if self._stop_event.is_set():
                    return
            current = self.status_service.current_context()
            if current.generation != generation:
                return
            if changed and self._on_changed is not None:
                try:
                    self._on_changed(changed)
                except Exception as exc:
                    self._report_error(exc)

        try:
            try:
                handle = self.status_service.request_changed_diff(
                    targets,
                    generation=generation,
                    on_complete=complete,
                    force=True,
                )
            except TypeError as error:
                # Keep lightweight embedders written against the pre-force
                # polling contract usable. The built-in service always takes
                # the forced revalidation path above.
                if "force" not in str(error):
                    raise
                handle = self.status_service.request_changed_diff(
                    targets,
                    generation=generation,
                    on_complete=complete,
                )
        except Exception as exc:
            self._report_error(exc)
            return None
        with self._lock:
            self._handle = handle
        return handle

    def start(self, *, poll_immediately: bool = False) -> bool:
        """Start periodic polling; return whether a new loop was created."""
        with self._lock:
            if self._running:
                return False
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._run,
                name="SafeLauncher-CloudStatusPoll",
                daemon=True,
            )
            thread = self._thread
            thread.start()
        if poll_immediately:
            self.poll_once(reason="startup")
        return True

    def stop(self, *, wait: bool = True) -> None:
        """Cooperatively stop polling and cancel the current diff request."""
        with self._lock:
            self._stop_event.set()
            handle = self._handle
            thread = self._thread
            self._running = False
            self._thread = None
            self._handle = None
        if handle is not None:
            try:
                handle.cancel()
            except Exception:
                pass
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            self.poll_once(reason="poll")

    def _report_error(self, error: BaseException) -> None:
        if self._on_error is None:
            return
        try:
            self._on_error(error)
        except Exception:
            pass


__all__ = ["CloudStatusPollingService"]
