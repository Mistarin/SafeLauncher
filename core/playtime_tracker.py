import time
import subprocess
import shutil
from PyQt6.QtCore import pyqtSignal
from core.safe_thread import SafeQThread
from core.logger import get_logger

logger = get_logger("PlaytimeTracker")


def _shutdown_firejail_sandbox(sandbox_name: str = None, pid: int = None):
    """Forcefully terminate any background processes/miners left in the Firejail container."""
    if not shutil.which("firejail"):
        return
    try:
        target = sandbox_name if sandbox_name else (str(pid) if pid else None)
        if not target:
            return
        logger.info(f"[SECURITY] Issuing firejail --shutdown={target} to clean up sandbox container.")
        subprocess.run(
            ["firejail", f"--shutdown={target}"],
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
        self.sandbox_name = getattr(process, "safelauncher_sandbox_name", None)

    def stop(self):
        """Interrupt monitoring without terminating the game process."""
        self.requestInterruption()
        if self.isRunning():
            self.wait(3000)

    def safe_run(self):
        """Block until the game process exits, then emit elapsed time and clean up sandbox."""
        logger.info(f"Started monitoring game PID {self.process.pid} (Game ID: {self.game_id}, Sandbox: {self.sandbox_name})")
        start = time.monotonic()

        while self.process.poll() is None:
            if self.isInterruptionRequested():
                logger.info(f"Playtime monitoring interrupted for game {self.game_id}")
                return
            time.sleep(0.25)

        elapsed = int(time.monotonic() - start)
        logger.info(f"Game ID {self.game_id} closed after {elapsed}s of playtime.")

        # [SECURITY] Hard shutdown any remaining background miner processes inside Firejail container
        _shutdown_firejail_sandbox(sandbox_name=self.sandbox_name, pid=self.process.pid)

        if elapsed > 0:
            self.playtime_recorded.emit(self.game_id, elapsed)

