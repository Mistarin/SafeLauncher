"""Qt bridge for subscribing widgets to request-manager resource state."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, Qt, pyqtSignal

from core.request_contracts import RequestKey, ResourceResult
from core.request_manager import RequestManager


class ResourceBinding(QObject):
    """Deliver manager state changes on the owning Qt object's thread.

    ``RequestManager`` is intentionally Qt-free and invokes listeners from
    request worker threads. Widgets should use this bridge instead of
    attaching a Future callback directly and then mutating controls from a
    worker thread. Closing the binding removes the manager listener before the
    widget is destroyed, so queued late results are ignored safely.
    """

    state_changed = pyqtSignal(object)
    _state_received = pyqtSignal(object)

    def __init__(
        self,
        request_manager: RequestManager,
        key: RequestKey,
        parent: QObject | None = None,
        *,
        cancel_on_close: bool = False,
    ) -> None:
        super().__init__(parent)
        self.request_manager = request_manager
        self.key = key
        self.cancel_on_close = bool(cancel_on_close)
        self._closed = False
        self._state_received.connect(
            self._deliver_state,
            Qt.ConnectionType.QueuedConnection,
        )
        self._unsubscribe = request_manager.subscribe(key, self._receive_state)

    def _receive_state(self, result: ResourceResult) -> None:
        if self._closed:
            return
        # The manager may call this method on a worker thread. Emitting a
        # signal queued to this QObject's thread keeps all consumer callbacks
        # on the GUI thread without making the core manager depend on Qt.
        self._state_received.emit(result)

    def _deliver_state(self, result: ResourceResult) -> None:
        if not self._closed:
            self.state_changed.emit(result)

    def close(self) -> None:
        """Detach from the manager and optionally cancel active work."""
        if self._closed:
            return
        self._closed = True
        try:
            self._unsubscribe()
        finally:
            self._unsubscribe = lambda: None
            if self.cancel_on_close:
                self.request_manager.cancel(self.key)

    def __del__(self) -> None:
        # QObject destruction is not guaranteed to happen on the GUI thread;
        # only detach the plain Python callback here. Request cancellation is
        # deliberately left to explicit owner lifecycle methods.
        try:
            self.close()
        except Exception:
            pass


def bind_resource(
    request_manager: RequestManager,
    key: RequestKey,
    callback: Callable[[ResourceResult], None],
    parent: QObject | None = None,
    *,
    cancel_on_close: bool = False,
) -> ResourceBinding:
    """Create a binding and connect a GUI-thread resource callback."""
    binding = ResourceBinding(
        request_manager,
        key,
        parent,
        cancel_on_close=cancel_on_close,
    )
    binding.state_changed.connect(callback, Qt.ConnectionType.QueuedConnection)
    return binding


__all__ = ["ResourceBinding", "bind_resource"]
