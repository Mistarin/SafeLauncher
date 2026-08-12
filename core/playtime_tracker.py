import time
import subprocess
from PyQt6.QtCore import QThread, pyqtSignal


class PlaytimeTrackerThread(QThread):
    """Background thread that monitors a launched game process.

    Tracking accuracy notes:
    - Start time is recorded after the Firejail process has confirmed startup
      (first successful poll), reducing setup overhead from the count.
    - End time is recorded the moment process.wait() returns, which is when
      Firejail's parent process exits after the game closes.
    - With shell=False + exec, our tracked PID *is* Firejail itself, so there
      is no bash wrapper layer adding latency.

    The signal is safe to connect to UI slots — PyQt6 automatically delivers
    it on the main thread via the event loop.
    """

    # (game_id, elapsed_seconds)
    playtime_recorded = pyqtSignal(int, int)

    def __init__(self, game_id: int, process: subprocess.Popen, parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.process = process

    def stop(self):
        """Interrupt monitoring without terminating the game process."""
        self.requestInterruption()
        if self.isRunning():
            self.wait(3000)

    def run(self):
        """Block until the game process exits, then emit elapsed time."""
        # Wait until the process is confirmed alive before starting the clock.
        # poll() returns None while the process is running; we spin briefly
        # (max 10s) until it has either started properly or already exited.
        deadline = time.monotonic() + 10.0
        while True:
            if self.isInterruptionRequested():
                return
            if self.process.poll() is not None:
                # Already exited before we even started timing — skip.
                return
            if time.monotonic() >= deadline:
                return
            time.sleep(0.25)
            if self.process.poll() is None:
                break

        start = time.monotonic()
        # Poll instead of blocking forever in wait(), so application shutdown
        # can interrupt this worker safely without killing the game.
        while self.process.poll() is None:
            if self.isInterruptionRequested():
                return
            time.sleep(0.25)
        elapsed = int(time.monotonic() - start)
        if elapsed > 0:
            self.playtime_recorded.emit(self.game_id, elapsed)
