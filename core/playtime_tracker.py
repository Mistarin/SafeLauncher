import time
import subprocess
from PyQt6.QtCore import QThread, pyqtSignal


class PlaytimeTrackerThread(QThread):
    """Background thread that monitors a launched game process.

    Waits for the Firejail/wine/umu-run process to terminate, then emits
    ``playtime_recorded`` with the game_id and total elapsed whole seconds.
    The signal is safe to connect to UI slots — PyQt6 automatically delivers
    it on the main thread via the event loop.
    """

    # (game_id, elapsed_seconds)
    playtime_recorded = pyqtSignal(int, int)

    def __init__(self, game_id: int, process: subprocess.Popen, parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.process = process

    def run(self):
        """Block until the game process exits, then emit elapsed time."""
        start = time.monotonic()
        try:
            self.process.wait()          # blocks until Firejail and children exit
        except Exception:
            pass
        elapsed = int(time.monotonic() - start)
        if elapsed > 0:
            self.playtime_recorded.emit(self.game_id, elapsed)
