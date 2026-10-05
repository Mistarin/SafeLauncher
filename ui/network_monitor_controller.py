"""Connectivity probing and transient network gating for the main window."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.network_probe import probe_internet
from core.request_contracts import RequestKey, RequestPriority, ResourceStatus


class NetworkMonitorController(QObject):
    """Own periodic connectivity probes, transition state, and request gating.

    User-facing policy and dialogs stay with the window. This controller only
    decides whether the shared request manager should treat connectivity as
    transiently unavailable and reports probe transitions to its owner.
    """

    result_ready = pyqtSignal(object)
    _probe_finished = pyqtSignal(int, object)

    def __init__(
        self,
        request_manager,
        *,
        network_allowed: Callable[[], bool],
        offline_test_mode: Callable[[], bool] | bool = False,
        interval_ms: int = 5_000,
        initial_delay_ms: int = 1_500,
        parent=None,
    ):
        super().__init__(parent)
        self.request_manager = request_manager
        self.network_allowed = network_allowed
        self.offline_test_mode = offline_test_mode
        self.initial_delay_ms = max(0, int(initial_delay_ms))
        self.reachability_known = False
        self.reachable = False
        self.transient_unavailable = False
        self._generation = 0
        self._probe_in_flight = False

        self.timer = QTimer(self)
        self.timer.setInterval(max(250, int(interval_ms)))
        self.timer.timeout.connect(self.probe_now)
        self._probe_finished.connect(self._on_probe_finished)

    def _test_mode(self) -> bool:
        value = self.offline_test_mode
        return bool(value() if callable(value) else value)

    def start(self) -> None:
        """Start recurring checks and schedule an initial delayed check."""
        if self._test_mode() or not self.network_allowed():
            self.stop()
            return
        self.timer.start()
        generation = self._generation
        QTimer.singleShot(
            self.initial_delay_ms,
            lambda: self.probe_now() if generation == self._generation else None,
        )

    def stop(self) -> None:
        """Stop scheduling and invalidate any completion from an old policy."""
        self.timer.stop()
        self._generation += 1
        self._probe_in_flight = False
        self.transient_unavailable = False

    def probe_now(self) -> None:
        if self._test_mode() or not self.network_allowed() or self._probe_in_flight:
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
        handle.future.add_done_callback(
            lambda future: self._probe_finished.emit(generation, future)
        )

    def _on_probe_finished(self, generation: int, future) -> None:
        if generation != self._generation:
            return
        self._probe_in_flight = False
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
            pass

        previous_reachable = self.reachable
        had_previous = self.reachability_known
        was_transient = self.transient_unavailable
        self.reachability_known = True
        self.reachable = reachable
        self.transient_unavailable = not reachable
        if not reachable:
            self.request_manager.cancel_matching(
                lambda spec: not bool(spec.metadata.get("allow_offline", False)),
                reason="policy",
            )
        self.result_ready.emit({
            "reachable": reachable,
            "reason": reason,
            "had_previous": had_previous,
            "previous_reachable": previous_reachable,
            "was_transient_unavailable": was_transient,
        })
