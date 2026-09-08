"""Fault-tolerant QThread wrapper for SafeLauncher.

Wraps thread run execution in top-level try/except guards to prevent uncaught
background thread exceptions from crashing Qt event loops or destroying thread handles.
"""

import traceback
from typing import Callable, Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from core.logger import get_logger

logger = get_logger("SafeQThread")


class SafeQThread(QThread):
    """QThread subclass that isolates uncaught exceptions during run() and guarantees safe lifecycle teardown."""
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    def safe_run(self):
        """Override this method in subclasses instead of run()."""
        pass

    def run(self):
        try:
            self.safe_run()
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(f"Uncaught exception in background worker {self.__class__.__name__}: {e}\n{tb_str}")
            try:
                self.error_occurred.emit(str(e))
            except Exception:
                pass

    def stop(self, timeout_ms: int = 1500):
        """Gracefully request interruption and wait up to timeout_ms."""
        self.requestInterruption()
        if self.isRunning():
            self.wait(timeout_ms)

    def __del__(self):
        """Ensure native C++ thread is stopped before Python garbage collection destroys the wrapper."""
        try:
            if self.isRunning():
                self.requestInterruption()
                self.wait(1000)
        except Exception:
            pass


class FunctionWorker(SafeQThread):
    """Run one bounded application task with normal QThread ownership.

    Small orchestration tasks previously used ad-hoc daemon threads. They had
    no parent, could not participate in shutdown, and could emit into a window
    that had already closed. This adapter keeps task code simple while giving
    each run a Qt lifetime and a queued completion signal.
    """

    completed = pyqtSignal(object)

    def __init__(self, work: Callable[[], Any], parent=None):
        super().__init__(parent)
        self._work = work

    def safe_run(self):
        result = self._work()
        if not self.isInterruptionRequested():
            self.completed.emit(result)


class TaskSupervisor(QObject):
    """Own one-shot UI tasks for the lifetime of a widget or window.

    A caller supplies only work and a GUI-thread completion callback. The
    supervisor retains QThread instances, logs unexpected failures, and offers
    cooperative shutdown without unsafe ``QThread.terminate()`` calls.
    """

    def __init__(self, owner, task_logger=None):
        super().__init__(owner)
        self._workers: list[FunctionWorker] = []
        self._task_logger = task_logger or logger

    def start(self, name: str, work: Callable[[], Any], on_complete=None) -> FunctionWorker:
        worker = FunctionWorker(work, parent=self)
        worker.setObjectName(name)
        if on_complete is not None:
            worker.completed.connect(on_complete)
        worker.error_occurred.connect(
            lambda error, task=name: self._task_logger.warning(
                "Background task %s failed: %s", task, error
            )
        )

        def _retire(w=worker):
            if w in self._workers:
                self._workers.remove(w)
            w.deleteLater()

        worker.finished.connect(_retire)
        self._workers.append(worker)
        worker.start()
        return worker

    def cancel_all(self, wait_ms: int = 100) -> None:
        for worker in list(self._workers):
            if worker.isRunning():
                worker.requestInterruption()
                worker.wait(wait_ms)

    def has_running_tasks(self) -> bool:
        return any(worker.isRunning() for worker in self._workers)
