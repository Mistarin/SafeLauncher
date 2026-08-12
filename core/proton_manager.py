"""GE-Proton Manager & GitHub Auto-Downloader for SafeLauncher.

Queries GitHub API (GloriousEggroll/proton-ge-custom) for available GE-Proton releases,
checks local installations in ~/.local/share/umu/ or Steam compatibility tools, and provides
a safe background thread worker (SafeQThread) to download and extract tarballs.
"""

import os
import sys
import tarfile
import urllib.request
import json
from pathlib import Path
from PyQt6.QtCore import pyqtSignal
from core.safe_thread import SafeQThread
from core.logger import get_logger

logger = get_logger("ProtonManager")

_HOME = os.path.expanduser("~")
UMU_DIR = os.path.join(_HOME, ".local", "share", "umu")
STEAM_COMPAT_DIR = os.path.join(_HOME, ".local", "share", "Steam", "compatibilitytools.d")

GITHUB_RELEASES_API = "https://api.github.com/repos/GloriousEggroll/proton-ge-custom/releases"


def get_default_install_dir() -> str:
    """Ensure destination directory ~/.local/share/umu exists and return path."""
    os.makedirs(UMU_DIR, mode=0o755, exist_ok=True)
    return UMU_DIR


def list_installed_ge_proton() -> list[dict]:
    """Scan local directories for installed GE-Proton builds."""
    installed = []
    seen = set()

    for base_dir in (UMU_DIR, STEAM_COMPAT_DIR):
        if not os.path.isdir(base_dir):
            continue
        try:
            for item in os.listdir(base_dir):
                full_path = os.path.join(base_dir, item)
                if os.path.isdir(full_path) and ("GE-Proton" in item or "Proton-GE" in item):
                    if item not in seen:
                        seen.add(item)
                        installed.append({
                            "name": item,
                            "path": full_path,
                            "location": "umu" if base_dir == UMU_DIR else "steam"
                        })
        except Exception as e:
            logger.warning(f"Error scanning GE-Proton directory {base_dir}: {e}")

    installed.sort(key=lambda x: x["name"], reverse=True)
    return installed


def fetch_online_ge_proton_releases(max_results: int = 10) -> list[dict]:
    """Query GitHub API for available GE-Proton releases."""
    logger.info("Querying GitHub API for GE-Proton releases...")
    req = urllib.request.Request(
        GITHUB_RELEASES_API,
        headers={
            "User-Agent": "SafeLauncher/1.0 (Linux Game Sandbox Manager)",
            "Accept": "application/vnd.github.v3+json"
        }
    )
    releases = []
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                for release in data[:max_results]:
                    tag = release.get("tag_name", "")
                    name = release.get("name", tag)
                    published_at = release.get("published_at", "")[:10]
                    body = release.get("body", "")

                    tarball_url = ""
                    size_mb = 0.0
                    for asset in release.get("assets", []):
                        asset_name = asset.get("name", "")
                        if asset_name.endswith(".tar.gz") or asset_name.endswith(".tar.xz"):
                            tarball_url = asset.get("browser_download_url", "")
                            size_mb = round(asset.get("size", 0) / (1024 * 1024), 1)
                            break

                    if tarball_url:
                        releases.append({
                            "tag": tag,
                            "name": name,
                            "url": tarball_url,
                            "size_mb": size_mb,
                            "published": published_at,
                            "body": body
                        })
    except Exception as e:
        logger.error(f"Failed to fetch GE-Proton releases from GitHub: {e}")

    logger.info(f"Retrieved {len(releases)} GE-Proton releases from GitHub.")
    return releases


class GEProtonDownloader(SafeQThread):
    """Background worker thread to download and extract a GE-Proton release tarball."""
    # (downloaded_bytes, total_bytes, percentage)
    progress_changed = pyqtSignal(int, int, int)
    status_text = pyqtSignal(str)
    download_complete = pyqtSignal(str, str)  # (tag_name, installed_path)
    download_failed = pyqtSignal(str)

    def __init__(self, release_url: str, tag_name: str, dest_dir: str = None, parent=None):
        super().__init__(parent)
        self.release_url = release_url
        self.tag_name = tag_name
        self.dest_dir = dest_dir or get_default_install_dir()

    def safe_run(self):
        logger.info(f"Starting download of {self.tag_name} from {self.release_url}")
        self.status_text.emit(f"Connecting to GitHub ({self.tag_name})…")

        os.makedirs(self.dest_dir, mode=0o755, exist_ok=True)
        tar_filename = os.path.basename(self.release_url)
        tar_filepath = os.path.join(self.dest_dir, tar_filename)

        try:
            req = urllib.request.Request(
                self.release_url,
                headers={"User-Agent": "SafeLauncher/1.0 (Linux Game Sandbox Manager)"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                total_bytes = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 128 * 1024

                self.status_text.emit(f"Downloading {self.tag_name}…")
                with open(tar_filepath, "wb") as f:
                    while True:
                        if self.isInterruptionRequested():
                            logger.info(f"Download of {self.tag_name} was interrupted by user.")
                            f.close()
                            if os.path.exists(tar_filepath):
                                os.remove(tar_filepath)
                            self.download_failed.emit("Download cancelled by user.")
                            return

                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        percent = int((downloaded / total_bytes) * 100) if total_bytes > 0 else 0
                        self.progress_changed.emit(downloaded, total_bytes, percent)

            if self.isInterruptionRequested():
                if os.path.exists(tar_filepath):
                    os.remove(tar_filepath)
                return

            self.status_text.emit(f"Extracting {self.tag_name} to ~/.local/share/umu/…")
            logger.info(f"Extracting {tar_filepath} into {self.dest_dir}...")

            with tarfile.open(tar_filepath, "r:*") as tar:
                tar.extractall(path=self.dest_dir)

            # Cleanup downloaded archive tarball after extraction
            if os.path.exists(tar_filepath):
                os.remove(tar_filepath)

            expected_path = os.path.join(self.dest_dir, self.tag_name)
            if not os.path.exists(expected_path):
                # Check if extracted folder has slightly different casing or name
                for entry in os.listdir(self.dest_dir):
                    if self.tag_name.lower() in entry.lower():
                        expected_path = os.path.join(self.dest_dir, entry)
                        break

            logger.info(f"GE-Proton {self.tag_name} installed successfully at {expected_path}")
            self.download_complete.emit(self.tag_name, expected_path)
        except Exception as e:
            logger.error(f"Failed to download/extract {self.tag_name}: {e}")
            if os.path.exists(tar_filepath):
                try:
                    os.remove(tar_filepath)
                except Exception:
                    pass
            self.download_failed.emit(str(e))
