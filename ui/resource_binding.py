"""Qt bridge for subscribing widgets to request-manager resource state."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, pyqtSignal

from core.request_contracts import RequestKey, ResourceResult
from core.request_manager import RequestManager


class ResourceBindingRegistry:
    """Owner-scoped registry for bindings without a parallel request cache.

    Registries are deliberately limited to binding lifetime. They do not
    deduplicate requests or retain resource values; those concerns remain in
    RequestManager and ResourceCache respectively.
    """

    def __init__(self) -> None:
        self._bindings: dict[RequestKey, "ResourceBinding"] = {}

    def __contains__(self, key: RequestKey) -> bool:
        return key in self._bindings

    def __getitem__(self, key: RequestKey):
        return self._bindings[key]

    def __setitem__(self, key: RequestKey, binding: "ResourceBinding") -> None:
        self._bindings[key] = binding

    def get(self, key: RequestKey, default=None):
        return self._bindings.get(key, default)

    def pop(self, key: RequestKey, default=None):
        return self._bindings.pop(key, default)

    def values(self):
        return self._bindings.values()

    def keys(self):
        return self._bindings.keys()

    def clear(self) -> None:
        self._bindings.clear()

    def take_all(self) -> tuple["ResourceBinding", ...]:
        """Detach and return all bindings for explicit UI-thread cleanup."""
        values = tuple(self._bindings.values())
        self._bindings.clear()
        return values


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
        request_id: str | None = None,
        generation: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.request_manager = request_manager
        self.key = key
        self.cancel_on_close = bool(cancel_on_close)
        self.request_id = request_id
        self.generation = generation
        self._closed = False
        self._state_received.connect(
            self._deliver_state,
            Qt.ConnectionType.QueuedConnection,
        )
        self._unsubscribe = request_manager.subscribe(key, self._receive_state)

    def _receive_state(self, result: ResourceResult) -> None:
        # A request may finish after a parent dialog has already scheduled
        # this QObject for deletion.  The manager is intentionally Qt-free and
        # can still hold the plain Python listener for a short time, so check
        # both our logical lifecycle and the native QObject wrapper before
        # touching a signal owned by C++.
        if self._closed or sip.isdeleted(self):
            return
        if self.request_id is not None and result.request_id != self.request_id:
            return
        if self.generation is not None and result.generation != self.generation:
            return
        # The manager may call this method on a worker thread. Emitting a
        # signal queued to this QObject's thread keeps all consumer callbacks
        # on the GUI thread without making the core manager depend on Qt.
        try:
            self._state_received.emit(result)
        except RuntimeError:
            # QObject destruction can race a worker callback at the native
            # boundary.  Treat it as an ordinary late result; never let a UI
            # lifecycle race become a request-manager error.
            self._closed = True

    def _deliver_state(self, result: ResourceResult) -> None:
        if self._closed or sip.isdeleted(self):
            return
        try:
            self.state_changed.emit(result)
        except RuntimeError:
            self._closed = True

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
    request_id: str | None = None,
    generation: int | None = None,
) -> ResourceBinding:
    """Create a binding and connect a GUI-thread resource callback."""
    binding = ResourceBinding(
        request_manager,
        key,
        parent,
        cancel_on_close=cancel_on_close,
        request_id=request_id,
        generation=generation,
    )
    binding.state_changed.connect(callback, Qt.ConnectionType.QueuedConnection)
    return binding


def bind_request(
    request_manager: RequestManager,
    handle,
    callback: Callable[[ResourceResult], None],
    parent: QObject | None = None,
    *,
    cancel_on_close: bool = True,
) -> ResourceBinding:
    """Bind one submitted request while rejecting older same-key results.

    This is the preferred bridge for dialogs and short-lived panels that own
    one request handle. The request manager remains Qt-free, while the
    binding delivers only the handle's request ID and generation to the UI.
    """
    return bind_resource(
        request_manager,
        handle.key,
        callback,
        parent,
        cancel_on_close=cancel_on_close,
        request_id=handle.request_id,
        generation=handle.generation,
    )


__all__ = [
    "ResourceBinding",
    "ResourceBindingRegistry",
    "bind_resource",
    "bind_request",
]
