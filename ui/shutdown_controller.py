"""Cooperative, nonblocking application shutdown state machine."""

from enum import Enum
import time

from PyQt6.QtCore import QObject, QTimer


class ShutdownState(Enum):
    RUNNING = "running"
    DRAINING = "draining"
    CANCELLED = "cancelled"
    CLOSED = "closed"


class ShutdownController(QObject):
    """Own deadlines/re-entry, while injected hooks own feature-specific work."""

    def __init__(self, *, begin, cancel_work, pending, finish, resume, request_close,
                 show_waiting, parent=None, timeout_seconds=12.0, clock=time.monotonic,
                 defer=None):
        super().__init__(parent)
        self._begin, self._cancel = begin, cancel_work
        self._pending, self._finish, self._resume = pending, finish, resume
        self._request_close, self._show_waiting = request_close, show_waiting
        self._timeout, self._clock = timeout_seconds, clock
        self._defer = defer if defer is not None else QTimer.singleShot
        self._epoch = 0
        self.state = ShutdownState.RUNNING
        self.deadline = 0.0

    def request(self) -> bool:
        if self.state == ShutdownState.CLOSED:
            return True
        if self.state != ShutdownState.DRAINING:
            self.state = ShutdownState.DRAINING
            self._epoch += 1
            self.deadline = self._clock() + self._timeout
            self._begin()
        self._cancel()
        waiting = self._pending()
        if waiting:
            self._show_waiting(waiting)
            if self._clock() >= self.deadline:
                self.abort()
            else:
                epoch = self._epoch
                self._defer(100, lambda: self._retry(epoch))
            return False
        self._finish()
        self.state = ShutdownState.CLOSED
        self.deadline = 0.0
        return True

    def _retry(self, epoch):
        if self.state == ShutdownState.DRAINING and self._epoch == epoch:
            self._request_close()

    def abort(self):
        if self.state != ShutdownState.DRAINING:
            return
        self._epoch += 1
        self.state = ShutdownState.CANCELLED
        self.deadline = 0.0
        self._resume()
