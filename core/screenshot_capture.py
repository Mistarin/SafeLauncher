import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from typing import Optional, List, Tuple
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import pyqtSignal

from core.logger import get_logger
from core.host_process import host_process_env
from core.safe_thread import SafeQThread
from database import _APP_DATA_DIR

logger = get_logger("ScreenshotCapture")


class ScreenshotCaptureWorker(SafeQThread):
    """Run potentially slow desktop capture commands away from the GUI thread."""

    completed = pyqtSignal(object)

    def __init__(self, game_id: int, target_screen: str, game_name: str, target_geometry=None, parent=None):
        super().__init__(parent)
        self.game_id = int(game_id)
        self.target_screen = str(target_screen or "current")
        self.game_name = str(game_name or "screenshot")
        self.target_geometry = target_geometry

    def safe_run(self):
        try:
            result = capture_desktop_screenshot(
                self.game_id,
                target_screen=self.target_screen,
                game_name=self.game_name,
                target_geometry=self.target_geometry,
            )
        except Exception:
            logger.exception("Screenshot capture worker failed")
            result = None
        self.completed.emit(result)


def _normalise_name(name: str) -> str:
    """Convert a game name to a safe filename prefix, e.g. 'Hell is Us' -> 'hell_is_us'."""
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)   # replace non-alphanumeric runs with _
    name = name.strip("_")
    return name or "screenshot"


def _make_filename(game_name: str, ext: str = "png") -> str:
    """Build a timestamped filename: gamename_YYYYMMDD_HHMMSS.ext"""
    prefix = _normalise_name(game_name)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{ts}.{ext}"


def get_game_screenshots_dir(game_id: int) -> str:
    """Return the absolute path to the screenshot gallery folder for a game."""
    target_dir = os.path.join(_APP_DATA_DIR, "screenshots", str(game_id))
    os.makedirs(target_dir, exist_ok=True)
    return target_dir


def get_available_screens() -> List[Tuple[str, str]]:
    """List available physical monitors and capture target modes."""
    targets = [
        ("current", "Current Active Display (Where Game is Focused)"),
        ("primary", "Primary Display"),
    ]
    try:
        app = QApplication.instance()
        if app:
            for i, screen in enumerate(app.screens()):
                geo = screen.geometry()
                name = screen.name() or f"Screen {i}"
                targets.append((name, f"{name} ({geo.width()}x{geo.height()})"))
    except Exception:
        pass
    targets.append(("all", "All Displays (Combined Desktop)"))
    return targets


def capture_desktop_screenshot(
    game_id: int,
    target_screen: str = "current",
    game_name: str = "screenshot",
    *,
    target_geometry: tuple[int, int, int, int] | None = None,
) -> Optional[str]:
    """Capture 1 single screen or designated monitor screenshot and save to game gallery."""
    target_dir = get_game_screenshots_dir(game_id)
    filename = _make_filename(game_name)
    target_path = os.path.join(target_dir, filename)

    spectacle = shutil.which("spectacle")
    ffmpeg = shutil.which("ffmpeg")

    # 1. Capture specific monitor geometry if requested
    if target_screen not in ("current", "all"):
        app = QApplication.instance() if target_geometry is None else None
        target_geo = target_geometry
        if app:
            screen = app.primaryScreen() if target_screen == "primary" else next(
                (item for item in app.screens() if item.name() == target_screen), None
            )
            if screen is not None:
                geometry = screen.geometry()
                target_geo = (geometry.x(), geometry.y(), geometry.width(), geometry.height())

        if ffmpeg and target_geo:
            x, y, width, height = target_geo
            try:
                cmd = [
                    ffmpeg, "-y", "-f", "x11grab", "-draw_mouse", "0",
                    "-video_size", f"{width}x{height}",
                    "-i", f":0.0+{x},{y}",
                    "-vframes", "1", target_path
                ]
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4, env=host_process_env())
                if res.returncode == 0 and os.path.exists(target_path) and os.path.getsize(target_path) > 1024:
                    logger.info(f"Captured single monitor ({target_screen}) screenshot via ffmpeg: {target_path}")
                    return target_path
            except Exception as e:
                logger.debug(f"ffmpeg monitor capture failed: {e}")

    # 2. Spectacle current active monitor or fullscreen
    if spectacle:
        try:
            # -m captures the current active monitor (1 single screen where game is active)
            # -f captures all screens combined
            flag = "-f" if target_screen == "all" else "-m"
            res = subprocess.run(
                [spectacle, flag, "-b", "-n", "-o", target_path],
                capture_output=True,
                timeout=4,
                env=host_process_env()
            )
            if res.returncode == 0 and os.path.exists(target_path) and os.path.getsize(target_path) > 1024:
                logger.info(f"Captured single screen screenshot via spectacle ({flag}): {target_path}")
                return target_path
        except Exception as e:
            logger.debug(f"Spectacle capture failed: {e}")

    # 3. Universal single screen fallback via ffmpeg
    if ffmpeg:
        try:
            cmd = [
                ffmpeg, "-y", "-f", "x11grab", "-draw_mouse", "0",
                "-i", ":0.0", "-vframes", "1", target_path
            ]
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4, env=host_process_env())
            if res.returncode == 0 and os.path.exists(target_path) and os.path.getsize(target_path) > 1024:
                logger.info(f"Captured screenshot via ffmpeg fallback: {target_path}")
                return target_path
        except Exception as e:
            logger.debug(f"ffmpeg fallback capture failed: {e}")

    return None
