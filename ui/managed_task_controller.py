"""Owned one-shot tasks and GUI-thread completion delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from core.logger import get_logger
from core.request_contracts import RequestKey, RequestPriority, ResourceStatus


logger = get_logger("ManagedTasks")


@dataclass
class _PendingTask:
    name: str
    operation: Any
    handle: Any
    callback: Callable | None
    on_error: Callable | None = None


class ManagedTaskController(QObject):
    """Use the shared RequestManager; never create a feature-local executor.

    A controller is a subscription lifetime: disposing it cancels its own
    tasks and suppresses late delivery without affecting other subscribers.
    """

    _done = pyqtSignal(object)

    def __init__(self, manager, registry, *, parent=None, category="Background", game_id=None, game_name=""):
        super().__init__(parent)
        self._manager = manager
        self._registry = registry
        self._category = category
        self._game_id, self._game_name = game_id, game_name
        self._pending: dict[str, _PendingTask] = {}
        self._closed = False
        self._draining_handles = []
        self._done.connect(self._deliver)

    def start(self, name, work, on_complete=None, *, allow_offline=False, on_error=None):
        if self._closed:
            return None
        operation = self._registry.start(
            name.replace("_", " ").strip().title(), category=self._category,
            game_id=self._game_id, game_name=self._game_name,
        )
        try:
            handle = self._manager.request(
                RequestKey("application-task", f"{name}:{uuid4().hex}"),
                lambda token: (token.raise_if_cancelled(), work(), token.raise_if_cancelled())[1],
                priority=RequestPriority.NORMAL,
                metadata={"allow_offline": bool(allow_offline)}, timeout_seconds=120,
            )
        except Exception as error:
            self._registry.fail(operation.operation_id, str(error))
            if on_error is not None:
                on_error(str(error))
            return None
        operation.cancel = handle.cancel
        operation.retry = lambda: self.start(name, work, on_complete, allow_offline=allow_offline, on_error=on_error)
        self._pending[handle.request_id] = _PendingTask(name, operation, handle, on_complete, on_error)
        handle.future.add_done_callback(
            lambda future, request_id=handle.request_id: self._emit_done(request_id, future)
        )
        return handle

    def _emit_done(self, request_id, future):
        try:
            self._done.emit((request_id, future))
        except RuntimeError:
            # The controller's QObject may already have been destroyed.
            pass

    @pyqtSlot(object)
    def _deliver(self, payload):
        request_id, future = payload
        task = self._pending.pop(request_id, None)
        if task is None or self._closed:
            return
        try:
            result = future.result()
            if result.status == ResourceStatus.CANCELLED:
                self._registry.finish(task.operation.operation_id, state="cancelled")
            elif result.status == ResourceStatus.READY:
                self._registry.finish_result(task.operation.operation_id, result.value)
                if task.callback is not None:
                    task.callback(result.value)
            else:
                self._registry.fail(task.operation.operation_id, str(result.error or result.status.value))
                if task.on_error is not None:
                    task.on_error(str(result.error or result.status.value))
        except Exception as error:
            logger.exception("Background task %s failed", task.name)
            self._registry.fail(task.operation.operation_id, str(error))

    def dispose(self):
        if self._closed:
            return
        self._closed = True
        for task in self._pending.values():
            task.handle.cancel()
            self._registry.finish(task.operation.operation_id, state="cancelled")
            task.operation.retry = None
            self._draining_handles.append(task.handle)
        self._pending.clear()

    def has_pending_work(self):
        """A cancelled loader may still be committing or rolling back files."""
        self._draining_handles = [handle for handle in self._draining_handles if not handle.future.done()]
        return bool(self._pending or self._draining_handles)
