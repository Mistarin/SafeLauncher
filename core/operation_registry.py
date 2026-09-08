"""Session-scoped tracking for user-visible background operations."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional
from uuid import uuid4

from PyQt6.QtCore import QObject, pyqtSignal


@dataclass
class Operation:
    operation_id: str
    label: str
    category: str = "General"
    state: str = "running"
    progress: Optional[int] = None
    error: str = ""
    guidance: str = ""
    detail: str = ""
    game_id: Optional[int] = None
    game_name: str = ""
    log_path: str = ""
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    retry: Optional[Callable[[], object]] = None
    cancel: Optional[Callable[[], object]] = None

    @property
    def active(self) -> bool:
        return self.state in {"queued", "running", "stopping"}


class OperationRegistry(QObject):
    """Own the lifecycle of operations that should be visible to users.

    This is deliberately independent of worker implementation. Existing
    QThreads can report into it, and future async backends can use the same
    user-facing contract.
    """

    operation_added = pyqtSignal(object)
    operation_updated = pyqtSignal(object)
    operation_failed = pyqtSignal(object)
    unread_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._operations: dict[str, Operation] = {}
        self._unread_failures: set[str] = set()

    def start(
        self,
        label: str,
        category: str = "General",
        *,
        retry: Optional[Callable[[], object]] = None,
        cancel: Optional[Callable[[], object]] = None,
        log_path: str = "",
        guidance: str = "",
        detail: str = "",
        game_id: Optional[int] = None,
        game_name: str = "",
    ) -> Operation:
        operation = Operation(
            operation_id=uuid4().hex,
            label=label,
            category=category,
            retry=retry,
            cancel=cancel,
            log_path=log_path,
            guidance=guidance,
            detail=detail,
            game_id=game_id,
            game_name=game_name,
        )
        self._operations[operation.operation_id] = operation
        self.operation_added.emit(operation)
        return operation

    def update(self, operation_id: str, *, progress: Optional[int] = None, label: Optional[str] = None) -> None:
        operation = self._operations.get(operation_id)
        if operation is None:
            return
        if progress is not None:
            operation.progress = max(0, min(100, int(progress)))
        if label:
            operation.label = label
        self.operation_updated.emit(operation)

    def finish(self, operation_id: str, state: str = "completed") -> None:
        operation = self._operations.get(operation_id)
        if operation is None:
            return
        if operation.state == "stopping" and state == "completed":
            state = "cancelled"
        operation.state = state
        operation.progress = 100 if state == "completed" else operation.progress
        operation.finished_at = datetime.now()
        self.operation_updated.emit(operation)

    def fail(self, operation_id: str, error: str, log_path: str = "", guidance: str = "") -> None:
        operation = self._operations.get(operation_id)
        if operation is None:
            return
        operation.state = "failed"
        operation.error = str(error)
        if guidance:
            operation.guidance = guidance
        if log_path:
            operation.log_path = log_path
        operation.finished_at = datetime.now()
        self.operation_updated.emit(operation)
        self.operation_failed.emit(operation)
        self._unread_failures.add(operation_id)
        self.unread_changed.emit(self.unread_count())

    def cancel(self, operation_id: str) -> None:
        operation = self._operations.get(operation_id)
        if operation is None or not operation.active:
            return
        if operation.cancel is not None:
            operation.cancel()
        operation.state = "stopping"
        self.operation_updated.emit(operation)

    def retry_operation(self, operation_id: str) -> Optional[Operation]:
        operation = self._operations.get(operation_id)
        if operation is None or operation.retry is None:
            return None
        callback = operation.retry
        operation.state = "retried"
        operation.finished_at = datetime.now()
        self.operation_updated.emit(operation)
        callback()
        return operation

    def mark_all_read(self) -> None:
        if not self._unread_failures:
            return
        self._unread_failures.clear()
        self.unread_changed.emit(0)

    def finish_result(self, operation_id: str, result) -> None:
        """Finish an operation from a worker result or mark it failed.

        Save/cloud result objects intentionally remain UI-neutral. The registry
        only relies on the small ``success/error/guidance/log_path`` contract.
        """
        if isinstance(result, tuple) and result:
            # Some legacy Qt signals carry ``(result, auxiliary_value)``.
            result = result[0]
        if getattr(result, "success", True) is False:
            self.fail(
                operation_id,
                getattr(result, "error", "Operation failed."),
                getattr(result, "log_path", ""),
                getattr(result, "guidance", ""),
            )
            return
        if isinstance(result, dict) and result.get("error"):
            self.fail(
                operation_id,
                result.get("error", "Operation failed."),
                result.get("log_path", ""),
                result.get("guidance", ""),
            )
            return
        self.finish(operation_id)

    def get(self, operation_id: str) -> Optional[Operation]:
        return self._operations.get(operation_id)

    def all(self) -> list[Operation]:
        return list(self._operations.values())

    def active_count(self) -> int:
        return sum(1 for operation in self._operations.values() if operation.active)

    def unread_count(self) -> int:
        return len(self._unread_failures)
