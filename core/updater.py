"""Safe AppImage and Git updater logic for SafeLauncher."""

from __future__ import annotations

import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, Callable

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from core.logger import get_logger
from core.version import (
    APP_VERSION,
    GITHUB_REPO,
    compare_versions,
    is_version_outdated,
)

logger = get_logger("Updater")

# Minimum expected size for a complete SafeLauncher AppImage (~30 MB)
MIN_APPIMAGE_SIZE_BYTES = 10 * 1024 * 1024


def is_appimage() -> bool:
    """Return True if SafeLauncher is currently executing from an AppImage."""
    appimage_env = os.environ.get("APPIMAGE")
    return bool(appimage_env and os.path.exists(appimage_env))


def get_appimage_path() -> Optional[str]:
    """Return the absolute path of the running AppImage, or None."""
    if is_appimage():
        return os.environ.get("APPIMAGE")
    return None


def is_git_repo() -> bool:
    """Check if the application root contains a .git directory."""
    root_dir = Path(__file__).resolve().parent.parent
    return (root_dir / ".git").is_dir()


def get_git_commit_or_tag() -> Optional[str]:
    """Return current git commit hash or tag if running from source checkout."""
    if not is_git_repo():
        return None
    root_dir = Path(__file__).resolve().parent.parent
    head_file = root_dir / ".git" / "HEAD"
    if head_file.is_file():
        try:
            content = head_file.read_text(encoding="utf-8").strip()
            if content.startswith("ref:"):
                ref_path = root_dir / ".git" / content.split(" ", 1)[1].strip()
                if ref_path.is_file():
                    return ref_path.read_text(encoding="utf-8").strip()[:8]
            else:
                return content[:8]
        except Exception:
            pass

    if shutil.which("git"):
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(root_dir),
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0:
                return res.stdout.strip()
        except Exception:
            pass
    return None


def validate_appimage_header(file_path: str) -> bool:
    """Verify that a file starts with the standard Linux ELF magic bytes."""
    if not os.path.isfile(file_path):
        return False
    try:
        with open(file_path, "rb") as f:
            header = f.read(16)
        # Standard ELF magic: 0x7F, 'E', 'L', 'F'
        return header.startswith(b"\x7fELF")
    except Exception as e:
        logger.warning(f"Error checking binary header for {file_path}: {e}")
        return False


def check_for_updates(
    repo: str = GITHUB_REPO,
    current_version: str = APP_VERSION,
    timeout: float = 8.0,
) -> Dict[str, Any]:
    """Check GitHub Releases API for new versions of SafeLauncher."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": f"SafeLauncher/{current_version}",
    }

    result: Dict[str, Any] = {
        "update_available": False,
        "latest_version": current_version,
        "current_version": current_version,
        "release_name": "",
        "release_notes": "",
        "release_url": "",
        "appimage_asset": None,
        "is_appimage": is_appimage(),
        "is_git": is_git_repo(),
        "git_commit": get_git_commit_or_tag(),
        "error": None,
    }

    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 404:
            # No releases published yet
            return result
        resp.raise_for_status()
        data = resp.json()

        tag = data.get("tag_name", "").strip()
        if tag:
            result["latest_version"] = tag
        result["release_name"] = data.get("name", "") or tag or current_version
        result["release_notes"] = data.get("body", "")
        result["release_url"] = data.get("html_url", "")

        # Compare version
        if tag and is_version_outdated(current_version, tag):
            result["update_available"] = True

        # Find x86_64 AppImage asset, falling back to generic AppImage
        assets = data.get("assets", [])
        chosen_asset = None
        for asset in assets:
            name = asset.get("name", "")
            if ("x86_64" in name or "amd64" in name) and name.endswith(".AppImage"):
                chosen_asset = asset
                break
        if not chosen_asset:
            for asset in assets:
                name = asset.get("name", "")
                if name.endswith(".AppImage"):
                    chosen_asset = asset
                    break

        if chosen_asset:
            result["appimage_asset"] = {
                "name": chosen_asset.get("name", ""),
                "download_url": chosen_asset.get("browser_download_url"),
                "size": chosen_asset.get("size", 0),
            }

    except Exception as e:
        logger.debug(f"GitHub release update check failed: {e}")
        result["error"] = str(e)

    return result


def download_and_apply_appimage_update(
    asset_url: str,
    target_appimage_path: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    min_size_bytes: int = MIN_APPIMAGE_SIZE_BYTES,
) -> str:
    """Stream download new AppImage to temporary file, validate header, and atomically replace."""
    dest_path = target_appimage_path or get_appimage_path()
    if not dest_path:
        raise ValueError("Cannot update AppImage: not running from an AppImage environment.")

    dest_dir = os.path.dirname(os.path.abspath(dest_path))
    if not os.access(dest_dir, os.W_OK):
        raise PermissionError(f"Directory {dest_dir} is not writable.")

    temp_path = dest_path + ".download"

    try:
        resp = requests.get(asset_url, stream=True, timeout=30)
        resp.raise_for_status()

        total_bytes = int(resp.headers.get("content-length", 0))
        downloaded = 0

        with open(temp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total_bytes)

        # Guard against truncated downloads BEFORE any validation: ELF magic
        # lives at the file start, so a truncated AppImage passes header
        # validation and only fails when launched. The advertised size (when
        # the server sends one) must match exactly.
        if total_bytes and downloaded < total_bytes:
            raise ValueError(
                f"Download truncated: got {downloaded} of {total_bytes} bytes.")
        if total_bytes and downloaded > total_bytes:
            # Can happen with transparent decompression (content-length counts
            # encoded bytes, iter_content yields decoded ones) — log it, the
            # ELF and size checks below still gate what we accept.
            logger.warning(
                f"Downloaded {downloaded} bytes exceeds advertised {total_bytes}.")

        # Make file executable
        os.chmod(temp_path, 0o755)

        # Validate binary header
        if not validate_appimage_header(temp_path):
            raise ValueError("Downloaded file is not a valid Linux ELF / AppImage executable.")

        # Validate plausible size: a real SafeLauncher AppImage is tens of MB.
        actual_size = os.path.getsize(temp_path)
        if actual_size < min_size_bytes:
            raise ValueError(
                f"Downloaded file size ({actual_size} bytes) is below the "
                f"minimum plausible AppImage size ({min_size_bytes} bytes).")

        # Atomic replacement
        os.replace(temp_path, dest_path)
        logger.info(f"Successfully updated AppImage in place at {dest_path}")
        return dest_path

    except Exception:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        raise


def restart_application() -> None:
    """Flush stdio and re-execute SafeLauncher process cleanly."""
    sys.stdout.flush()
    sys.stderr.flush()

    if is_appimage():
        appimage_path = os.environ["APPIMAGE"]
        os.execl(appimage_path, appimage_path, *sys.argv[1:])
    else:
        os.execl(sys.executable, sys.executable, *sys.argv)


class UpdateCheckWorker(QThread):
    """Background worker thread to query GitHub Releases API."""

    check_finished = pyqtSignal(dict)

    def __init__(self, current_version: str = APP_VERSION, parent=None):
        super().__init__(parent)
        self.current_version = current_version

    def run(self):
        info = check_for_updates(current_version=self.current_version)
        self.check_finished.emit(info)


class UpdateDownloadWorker(QThread):
    """Background worker thread to download AppImage with progress reports."""

    progress = pyqtSignal(int, int)  # (downloaded_bytes, total_bytes)
    finished = pyqtSignal(str)       # target_path
    failed = pyqtSignal(str)         # error message

    def __init__(self, asset_url: str, target_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.asset_url = asset_url
        self.target_path = target_path

    def run(self):
        try:
            target = download_and_apply_appimage_update(
                self.asset_url,
                target_appimage_path=self.target_path,
                progress_callback=lambda d, t: self.progress.emit(d, t),
            )
            self.finished.emit(target)
        except Exception as e:
            logger.error(f"AppImage update download failed: {e}")
            self.failed.emit(str(e))
