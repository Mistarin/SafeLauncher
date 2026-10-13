import os
import re
import shutil
import subprocess
import uuid
import threading
from datetime import datetime
from typing import Optional, List, Tuple
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import pyqtSignal

from core.logger import get_logger
from core.host_process import host_process_env
from core.safe_thread import SafeQThread
from database import _APP_DATA_DIR

logger = get_logger("ScreenshotCapture")
_CAPTURE_LOCK = threading.Lock()


class ScreenshotCaptureWorker(SafeQThread):
    """Run potentially slow desktop capture commands away from the GUI thread."""

    completed = pyqtSignal(object)

    def __init__(self, game_id: int, target_screen: str, game_name: str, target_geometry=None,
                 avoid_focus_stealing: bool = False, target_output: str = "", parent=None):
        super().__init__(parent)
        self.game_id = int(game_id)
        self.target_screen = str(target_screen or "current")
        self.game_name = str(game_name or "screenshot")
        self.target_geometry = target_geometry
        self.avoid_focus_stealing = bool(avoid_focus_stealing)
        self.target_output = str(target_output or "")

    def safe_run(self):
        try:
            result = capture_desktop_screenshot(
                self.game_id,
                target_screen=self.target_screen,
                game_name=self.game_name,
                target_geometry=self.target_geometry,
                avoid_focus_stealing=self.avoid_focus_stealing,
                target_output=self.target_output,
            )
        except Exception:
            logger.exception("Screenshot capture worker failed")
            result = None
        self.completed.emit(result)


def _normalise_name(name: str) -> str:
    """Convert a game name to a safe filename prefix, e.g. 'Hell is Us' -> 'hell_is_us'."""
    name = str(name or "").strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)   # replace non-alphanumeric runs with _
    name = name.strip("_")[:100].strip("_")
    return name or "screenshot"


def _make_filename(game_name: str, ext: str = "png") -> str:
    """Build a collision-resistant, sortable screenshot filename."""
    prefix = _normalise_name(game_name)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return f"{prefix}_{ts}_{uuid.uuid4().hex[:8]}.{ext}"


def get_game_screenshots_dir(game_id: int) -> str:
    """Return the absolute path to the screenshot gallery folder for a game."""
    game_id = int(game_id)
    if game_id < 0:
        raise ValueError("game_id must be a non-negative integer")
    target_dir = os.path.join(_APP_DATA_DIR, "screenshots", str(game_id))
    os.makedirs(target_dir, exist_ok=True)
    return target_dir


def get_available_screens() -> List[Tuple[str, str]]:
    """List available physical monitors and capture target modes."""
    targets = [
        ("current", "Current Display (Under Pointer)"),
        ("primary", "Primary Display"),
    ]
    try:
        app = QApplication.instance()
        if app:
            screens = app.screens()
            names = [screen.name() or f"Screen {i + 1}" for i, screen in enumerate(screens)]
            duplicates = {name for name in names if names.count(name) > 1}
            for i, (screen, name) in enumerate(zip(screens, names)):
                geo = screen.geometry()
                value = f"screen:{i}" if name in duplicates or not screen.name() else name
                label_name = f"{name} (Display {i + 1})" if name in duplicates else name
                targets.append((value, f"{label_name} ({geo.width()}x{geo.height()})"))
    except Exception:
        logger.debug("Ignoring non-fatal error in best-effort step", exc_info=True)
    targets.append(("all", "All Displays (Combined Desktop)"))
    return targets


def capture_desktop_screenshot(
    game_id: int,
    target_screen: str = "current",
    game_name: str = "screenshot",
    *,
    target_geometry: tuple[int, int, int, int] | None = None,
    avoid_focus_stealing: bool = False,
    target_output: str = "",
) -> Optional[str]:
    """Capture at most one desktop frame at a time across all app entry points."""
    if not _CAPTURE_LOCK.acquire(blocking=False):
        logger.debug("Skipping overlapping screenshot request")
        return None
    try:
        return _capture_desktop_screenshot(
            game_id, target_screen, game_name, target_geometry=target_geometry,
            avoid_focus_stealing=avoid_focus_stealing, target_output=target_output,
        )
    finally:
        _CAPTURE_LOCK.release()


def _capture_desktop_screenshot(
    game_id: int,
    target_screen: str = "current",
    game_name: str = "screenshot",
    *,
    target_geometry: tuple[int, int, int, int] | None = None,
    avoid_focus_stealing: bool = False,
    target_output: str = "",
) -> Optional[str]:
    """Capture 1 single screen or designated monitor screenshot and save to game gallery."""
    target_dir = get_game_screenshots_dir(game_id)
    filename = _make_filename(game_name)
    target_path = os.path.join(target_dir, filename)

    spectacle = shutil.which("spectacle")
    ffmpeg = shutil.which("ffmpeg")
    grim = shutil.which("grim")
    env = host_process_env()
    display_name = env.get("DISPLAY")
    is_wayland = bool(env.get("WAYLAND_DISPLAY"))
    use_x11_capture = bool(display_name) and not is_wayland

    def _valid_file() -> bool:
        try:
            if not os.path.isfile(target_path) or os.path.getsize(target_path) < 45:
                return False
            with open(target_path, "rb") as image_file:
                header = image_file.read(24)
                image_file.seek(-12, os.SEEK_END)
                trailer = image_file.read(12)
            return (
                header[:8] == b"\x89PNG\r\n\x1a\n"
                and header[12:16] == b"IHDR"
                and int.from_bytes(header[16:20], "big") > 0
                and int.from_bytes(header[20:24], "big") > 0
                and trailer[4:8] == b"IEND"
            )
        except (OSError, ValueError):
            return False

    def _discard_partial() -> None:
        try:
            if os.path.exists(target_path):
                os.remove(target_path)
        except OSError:
            logger.debug("Could not remove incomplete screenshot file")

    # grim is a native Wayland screencopy client and does not map or activate
    # a screenshot application. Do not let an unspecified output degrade to
    # an all-desktop capture when a single monitor was requested.
    if grim and is_wayland and (target_screen == "all" or target_output):
        try:
            cmd = [grim]
            if target_screen != "all":
                cmd.extend(["-o", target_output])
            cmd.append(target_path)
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 timeout=5, env=env)
            if res.returncode == 0 and _valid_file():
                logger.info(f"Captured screenshot via grim ({target_screen}): {target_path}")
                return target_path
        except Exception as e:
            logger.debug(f"grim capture failed: {e}")
        _discard_partial()

    # Capture the selected monitor or X11 desktop directly when geometry is
    # known. In-game requests can require this path exclusively so no desktop
    # screenshot application is launched while a game owns focus.
    target_geo = target_geometry
    if ffmpeg and use_x11_capture and target_screen != "all" and target_geo:
        try:
            if len(target_geo) != 4:
                raise ValueError("target_geometry must contain x, y, width, height")
            x, y, width, height = (int(value) for value in target_geo)
            if width <= 0 or height <= 0:
                raise ValueError("target_geometry dimensions must be positive")
            cmd = [
                ffmpeg, "-y", "-f", "x11grab", "-draw_mouse", "0",
                "-video_size", f"{width}x{height}",
                "-i", f"{display_name}+{x},{y}",
                "-vframes", "1", target_path
            ]
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 timeout=5, env=env)
            if res.returncode == 0 and _valid_file():
                logger.info(f"Captured single monitor ({target_screen}) screenshot via ffmpeg: {target_path}")
                return target_path
        except Exception as e:
            logger.debug(f"ffmpeg monitor capture failed: {e}")
        _discard_partial()

    if ffmpeg and use_x11_capture and target_screen == "all":
        try:
            if target_geo is None or len(target_geo) != 4:
                raise ValueError("combined desktop geometry is unavailable")
            x, y, width, height = (int(value) for value in target_geo)
            if width <= 0 or height <= 0:
                raise ValueError("combined desktop dimensions must be positive")
            cmd = [ffmpeg, "-y", "-f", "x11grab", "-draw_mouse", "0",
                   "-video_size", f"{width}x{height}", "-i", f"{display_name}+{x},{y}",
                   "-vframes", "1", target_path]
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 timeout=5, env=env)
            if res.returncode == 0 and _valid_file():
                logger.info(f"Captured combined desktop screenshot via ffmpeg: {target_path}")
                return target_path
        except Exception as e:
            logger.debug(f"ffmpeg full desktop capture failed: {e}")
        _discard_partial()

    # Named targets may not degrade to another display; current/all can use
    # the desktop backend only when explicitly allowed by the caller.
    if target_screen not in ("current", "all"):
        return None
    if avoid_focus_stealing:
        return None

    # 2. Spectacle current active monitor or fullscreen
    if spectacle:
        try:
            # -m captures the current active monitor (1 single screen where game is active)
            # -f captures all screens combined
            flag = "-f" if target_screen == "all" else "-m"
            res = subprocess.run(
                [spectacle, flag, "-b", "-n", "-o", target_path],
                capture_output=True,
                timeout=6,
                env=env
            )
            if res.returncode == 0 and _valid_file():
                logger.info(f"Captured single screen screenshot via spectacle ({flag}): {target_path}")
                return target_path
        except Exception as e:
            logger.debug(f"Spectacle capture failed: {e}")
        _discard_partial()

    return None
