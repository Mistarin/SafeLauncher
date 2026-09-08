"""Fault-tolerant QThread wrapper for SafeLauncher.

Wraps thread run execution in top-level try/except guards to prevent uncaught
background thread exceptions from crashing Qt event loops or destroying thread handles.
"""

import traceback
from typing import Callable, Any

from PyQt6.QtCore import QThread, pyqtSignal
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
