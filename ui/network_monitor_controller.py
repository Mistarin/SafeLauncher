"""Connectivity probing and transient network gating for the main window."""

from __future__ import annotations

import random
import time
from typing import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtNetwork import QNetworkInformation

from core.network_probe import probe_internet
from core.request_contracts import RequestKey, RequestPriority, ResourceStatus

from core.logger import get_logger

logger = get_logger("network_monitor_controller")


class NetworkMonitorController(QObject):
    """Own periodic connectivity probes, transition state, and request gating.

    User-facing policy and dialogs stay with the window. This controller only
    decides whether the shared request manager should treat connectivity as
    transiently unavailable and reports probe transitions to its owner.
    """

    result_ready = pyqtSignal(object)
    _probe_finished = pyqtSignal(int, object)
    request_failed = pyqtSignal()

    def __init__(
        self,
        request_manager,
        *,
        network_allowed: Callable[[], bool],
        offline_test_mode: Callable[[], bool] | bool = False,
        interval_ms: int = 5 * 60 * 1000,
        initial_delay_ms: int = 0,
        parent=None,
    ):
        super().__init__(parent)
        self.request_manager = request_manager
        self.network_allowed = network_allowed
        self.offline_test_mode = offline_test_mode
        self.initial_delay_ms = max(0, int(initial_delay_ms))
        self.reachability_known = False
        self.reachable = False
        # This is only the local internet indicator. Backend requests still
        # run so each service can establish its own reachability.
        self.transient_unavailable = True
        self._generation = 0
        self._probe_in_flight = False
        self._probe_again = False
        self._probe_handle = None
        self._failure_backoff_seconds = 15.0
        self._next_failure_probe_at = 0.0
        self._network_information = None
        try:
            if QNetworkInformation.instance() is None:
                # Prefer the GLib backend: Qt's NetworkManager backend fails to
                # wire its D-Bus signals in PyQt6 builds and spams QtDBus warnings.
                if not QNetworkInformation.loadBackendByName("glib"):
                    QNetworkInformation.loadDefaultBackend()
            self._network_information = QNetworkInformation.instance()
            if self._network_information is not None:
                self._network_information.reachabilityChanged.connect(self._on_reachability_hint)
        except (AttributeError, RuntimeError):
            self._network_information = None

        self.timer = QTimer(self)
        self.timer.setInterval(max(250, int(interval_ms)))
        self.timer.timeout.connect(self.probe_now)
        self._probe_finished.connect(self._on_probe_finished)
        self.request_failed.connect(self.probe_now)

    def notify_request_failed(self) -> None:
        """Schedule a jittered connectivity check after transient failures."""
        now = time.monotonic()
        if now < self._next_failure_probe_at:
            return
        self._next_failure_probe_at = now + self._failure_backoff_seconds * random.uniform(0.8, 1.2)
        self._failure_backoff_seconds = min(300.0, self._failure_backoff_seconds * 2.0)
        self.request_failed.emit()

    def _test_mode(self) -> bool:
        value = self.offline_test_mode
        return bool(value() if callable(value) else value)

    def start(self) -> None:
        """Watch OS connectivity hints and use a five-minute fallback probe."""
        if self._test_mode() or not self.network_allowed():
            self.stop()
            return
        self.transient_unavailable = True
        self.timer.start()
        generation = self._generation
        QTimer.singleShot(
            self.initial_delay_ms,
            lambda: self.probe_now() if generation == self._generation else None,
        )

    def _on_reachability_hint(self, reachability) -> None:
        """Treat OS reachability as a local hint; verify recovery with a probe."""
        if self._test_mode() or not self.network_allowed():
            return
        disconnected = reachability == QNetworkInformation.Reachability.Disconnected
        if disconnected:
            previous = self.reachable
            had_previous = self.reachability_known
            was_transient = self.transient_unavailable
            self.reachability_known = True
            self.reachable = False
            self.transient_unavailable = True
            self.result_ready.emit({
                "reachable": False,
                "reason": "The operating system reports no network connection.",
                "had_previous": had_previous,
                "previous_reachable": previous,
                "was_transient_unavailable": was_transient,
                "source": "os_hint",
            })
        elif reachability in {
            QNetworkInformation.Reachability.Local,
            QNetworkInformation.Reachability.Site,
            QNetworkInformation.Reachability.Online,
        }:
            # A connectivity event is only a hint. Do not reopen remote
            # requests until an actual internet probe succeeds.
            self.probe_now()

    def stop(self) -> None:
        """Stop scheduling and invalidate any completion from an old policy."""
        self.timer.stop()
        self._generation += 1
        self._probe_in_flight = False
        self._probe_again = False
        handle = self._probe_handle
        self._probe_handle = None
        if handle is not None:
            try:
                handle.cancel()
            except Exception:
                logger.debug("Ignoring non-fatal error in best-effort step", exc_info=True)
        self.transient_unavailable = False

    def probe_now(self) -> None:
        if self._test_mode() or not self.network_allowed():
            return
        if self._probe_in_flight:
            self._probe_again = True
            return
        self._probe_in_flight = True
        generation = self._generation
        handle = self.request_manager.request(
            RequestKey("internet-connectivity", "public", "v1"),
            lambda token: (
                token.raise_if_cancelled(),
                probe_internet(timeout=3.0),
                token.raise_if_cancelled(),
            )[1],
            priority=RequestPriority.BACKGROUND,
            metadata={"allow_offline": True, "connectivity_probe": True},
            timeout_seconds=5,
        )
        self._probe_handle = handle
        handle.future.add_done_callback(
            lambda future: self._probe_finished.emit(generation, future)
        )

    def _on_probe_finished(self, generation: int, future) -> None:
        if generation != self._generation:
            return
        self._probe_in_flight = False
        self._probe_handle = None
        if not self.network_allowed():
            self.stop()
            return

        reachable = False
        reason = "The internet connection could not be reached."
        try:
            result = future.result()
            if result.status == ResourceStatus.READY and isinstance(result.value, tuple):
                reachable = bool(result.value[0])
                reason = str(result.value[1] or reason)
        except Exception:
            # Connectivity failures are intentionally reported as a safe,
            # user-facing state rather than leaking transport details.
            logger.debug("Ignoring non-fatal error in best-effort step", exc_info=True)

        previous_reachable = self.reachable
        had_previous = self.reachability_known
        was_transient = self.transient_unavailable
        self.reachability_known = True
        self.reachable = reachable
        self.transient_unavailable = not reachable
        if reachable:
            self._failure_backoff_seconds = 15.0
            self._next_failure_probe_at = 0.0
        self.result_ready.emit({
            "reachable": reachable,
            "reason": reason,
            "had_previous": had_previous,
            "previous_reachable": previous_reachable,
            "was_transient_unavailable": was_transient,
        })
        if self._probe_again and self.network_allowed():
            self._probe_again = False
            QTimer.singleShot(0, self.probe_now)
