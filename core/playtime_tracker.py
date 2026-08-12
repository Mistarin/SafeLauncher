import time
import subprocess
import shutil
from PyQt6.QtCore import pyqtSignal
from core.safe_thread import SafeQThread
from core.logger import get_logger

logger = get_logger("PlaytimeTracker")


def _shutdown_firejail_sandbox(pid: int):
    """Forcefully terminate any background processes/miners left in the Firejail container."""
    if not pid or not shutil.which("firejail"):
        return
    try:
        logger.info(f"[SECURITY] Issuing firejail --shutdown={pid} to clean up background processes.")
        subprocess.run(
            ["firejail", f"--shutdown={pid}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception as e:
        logger.debug(f"Firejail shutdown cleanup notice: {e}")


class PlaytimeTrackerThread(SafeQThread):
    """Background thread that monitors a launched game process."""

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

    def safe_run(self):
        """Block until the game process exits, then emit elapsed time and clean up sandbox."""
        logger.info(f"Started monitoring game PID {self.process.pid} (Game ID: {self.game_id})")
        deadline = time.monotonic() + 10.0
        while True:
            if self.isInterruptionRequested():
                logger.info(f"Playtime monitoring interrupted for game {self.game_id}")
                return
            if self.process.poll() is not None:
                logger.info(f"Game process {self.game_id} exited before timing deadline.")
                _shutdown_firejail_sandbox(self.process.pid)
                return
            if time.monotonic() >= deadline:
                logger.warning(f"Process {self.game_id} startup check timed out after 10s.")
                return
            time.sleep(0.25)
            if self.process.poll() is None:
                break

        start = time.monotonic()
        while self.process.poll() is None:
            if self.isInterruptionRequested():
                logger.info(f"Playtime monitoring interrupted for game {self.game_id}")
                return
            time.sleep(0.25)

        elapsed = int(time.monotonic() - start)
        logger.info(f"Game ID {self.game_id} closed after {elapsed}s of playtime.")

        # [SECURITY] Hard shutdown any remaining background miner processes inside Firejail container
        _shutdown_firejail_sandbox(self.process.pid)

        if elapsed > 0:
            self.playtime_recorded.emit(self.game_id, elapsed)
