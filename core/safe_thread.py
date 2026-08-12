"""Fault-tolerant QThread wrapper for SafeLauncher.

Wraps thread run execution in top-level try/except guards to prevent uncaught
background thread exceptions from crashing Qt event loops or destroying thread handles.
"""

import traceback
from PyQt6.QtCore import QThread, pyqtSignal
from core.logger import get_logger

logger = get_logger("SafeQThread")


class SafeQThread(QThread):
    """QThread subclass that isolates uncaught exceptions during run()."""
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
            self.error_occurred.emit(str(e))
