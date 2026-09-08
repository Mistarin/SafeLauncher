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
    log_path: str = ""
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    retry: Optional[Callable[[], object]] = None
    cancel: Optional[Callable[[], object]] = None

    @property
    def active(self) -> bool:
        return self.state in {"queued", "running"}


class OperationRegistry(QObject):
    """Own the lifecycle of operations that should be visible to users.

    This is deliberately independent of worker implementation. Existing
    QThreads can report into it, and future async backends can use the same
    user-facing contract.
    """

    operation_added = pyqtSignal(object)
    operation_updated = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._operations: dict[str, Operation] = {}

    def start(
        self,
        label: str,
        category: str = "General",
        *,
        retry: Optional[Callable[[], object]] = None,
        cancel: Optional[Callable[[], object]] = None,
        log_path: str = "",
    ) -> Operation:
        operation = Operation(
            operation_id=uuid4().hex,
            label=label,
            category=category,
            retry=retry,
            cancel=cancel,
            log_path=log_path,
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
        operation.state = state
        operation.progress = 100 if state == "completed" else operation.progress
        operation.finished_at = datetime.now()
        self.operation_updated.emit(operation)

    def fail(self, operation_id: str, error: str, log_path: str = "") -> None:
        operation = self._operations.get(operation_id)
        if operation is None:
            return
        operation.state = "failed"
        operation.error = str(error)
        if log_path:
            operation.log_path = log_path
        operation.finished_at = datetime.now()
        self.operation_updated.emit(operation)

    def cancel(self, operation_id: str) -> None:
        operation = self._operations.get(operation_id)
        if operation is None or not operation.active:
            return
        if operation.cancel is not None:
            operation.cancel()
        operation.state = "cancelled"
        operation.finished_at = datetime.now()
        self.operation_updated.emit(operation)

    def retry_operation(self, operation_id: str) -> Optional[Operation]:
        operation = self._operations.get(operation_id)
        if operation is None or operation.retry is None:
            return None
        callback = operation.retry
        operation.state = "queued"
        operation.error = ""
        operation.finished_at = None
        self.operation_updated.emit(operation)
        callback()
        return operation

    def get(self, operation_id: str) -> Optional[Operation]:
        return self._operations.get(operation_id)

    def all(self) -> list[Operation]:
        return list(self._operations.values())

    def active_count(self) -> int:
        return sum(1 for operation in self._operations.values() if operation.active)
