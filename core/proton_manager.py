"""GE-Proton Manager & GitHub Auto-Downloader for SafeLauncher.

Queries GitHub API (GloriousEggroll/proton-ge-custom) for available GE-Proton releases,
checks local installations in ~/.local/share/umu/ or Steam compatibility tools, and provides
a robust background thread worker (SafeQThread) to download and extract tarballs.
"""

import os
import sys
import tarfile
import requests
import shutil
import subprocess
import inspect
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


def find_valid_proton_dir(target_dir: str) -> str:
    """Check if target_dir or any of its subdirectories contain toolmanifest.vdf."""
    if not os.path.isdir(target_dir):
        return target_dir
    if os.path.exists(os.path.join(target_dir, "toolmanifest.vdf")):
        return target_dir
    for root, dirs, files in os.walk(target_dir):
        if "toolmanifest.vdf" in files:
            return root
    return target_dir


def list_installed_ge_proton() -> list[dict]:
    """Scan local directories for installed GE-Proton builds containing toolmanifest.vdf."""
    installed = []
    seen = set()

    for base_dir in (UMU_DIR, STEAM_COMPAT_DIR):
        if not os.path.isdir(base_dir):
            continue
        try:
            for item in os.listdir(base_dir):
                full_path = os.path.join(base_dir, item)
                valid_path = find_valid_proton_dir(full_path)
                if os.path.isdir(valid_path) and os.path.exists(os.path.join(valid_path, "toolmanifest.vdf")):
                    if item not in seen:
                        seen.add(item)
                        installed.append({
                            "name": item,
                            "path": valid_path,
                            "location": "umu" if base_dir == UMU_DIR else "steam"
                        })
        except Exception as e:
            logger.warning(f"Error scanning GE-Proton directory {base_dir}: {e}")

    installed.sort(key=lambda x: x["name"], reverse=True)
    return installed


def fetch_online_ge_proton_releases(max_results: int = 12) -> list[dict]:
    """Query GitHub API for available GE-Proton releases."""
    logger.info("Querying GitHub API for GE-Proton releases...")
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) SafeLauncher/1.0",
        "Accept": "application/vnd.github.v3+json"
    }
    releases = []
    try:
        response = requests.get(GITHUB_RELEASES_API, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
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
    progress_changed = pyqtSignal(int, int, int)
    progress_details = pyqtSignal(str, float, float, int)
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

        success = False
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "*/*"
            })

            resp = session.get(self.release_url, stream=True, timeout=(15, 60))
            resp.raise_for_status()

            total_bytes = int(resp.headers.get("content-length", 0))
            total_mb = round(total_bytes / (1024 * 1024), 1) if total_bytes > 0 else 0.0
            downloaded = 0
            chunk_size = 256 * 1024  # 256 KB

            self.status_text.emit(f"Downloading {self.tag_name}…")
            with open(tar_filepath, "wb") as f:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if self.isInterruptionRequested():
                        logger.info(f"Download of {self.tag_name} was interrupted by user.")
                        f.close()
                        if os.path.exists(tar_filepath):
                            os.remove(tar_filepath)
                        self.download_failed.emit("Download cancelled by user.")
                        return

                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        downloaded_mb = round(downloaded / (1024 * 1024), 1)
                        percent = int((downloaded / total_bytes) * 100) if total_bytes > 0 else 0
                        self.progress_changed.emit(downloaded, total_bytes, percent)
                        self.progress_details.emit(self.tag_name, downloaded_mb, total_mb, percent)

            success = True
        except Exception as primary_err:
            logger.warning(f"Python requests download failed for {self.tag_name}: {primary_err}. Attempting curl fallback...")
            if os.path.exists(tar_filepath):
                try:
                    os.remove(tar_filepath)
                except Exception:
                    pass

            if shutil.which("curl"):
                try:
                    self.status_text.emit(f"Downloading {self.tag_name} via curl…")
                    cmd = ["curl", "-L", "--retry", "3", "-o", tar_filepath, self.release_url]
                    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    while proc.poll() is None:
                        if self.isInterruptionRequested():
                            proc.terminate()
                            if os.path.exists(tar_filepath):
                                os.remove(tar_filepath)
                            self.download_failed.emit("Download cancelled by user.")
                            return
                        self.sleep(1)

                    if proc.returncode == 0 and os.path.exists(tar_filepath):
                        success = True
                except Exception as curl_err:
                    logger.error(f"Curl fallback failed: {curl_err}")

        if not success or not os.path.exists(tar_filepath):
            self.download_failed.emit("Network connection dropped during download. Please retry.")
            return

        if self.isInterruptionRequested():
            if os.path.exists(tar_filepath):
                os.remove(tar_filepath)
            return

        try:
            self.status_text.emit(f"Extracting {self.tag_name} to ~/.local/share/umu/…")
            logger.info(f"Extracting {tar_filepath} into {self.dest_dir}...")

            with tarfile.open(tar_filepath, "r:*") as tar:
                dest_root = os.path.realpath(self.dest_dir)
                for member in tar.getmembers():
                    # Reject links and device nodes: they can escape the
                    # install directory or create unsafe filesystem objects.
                    if member.issym() or member.islnk() or member.isdev():
                        raise ValueError(f"unsafe archive member: {member.name}")
                    target = os.path.realpath(os.path.join(dest_root, member.name))
                    if target != dest_root and not target.startswith(dest_root + os.sep):
                        raise ValueError(f"archive path escapes install directory: {member.name}")

                extract_params = inspect.signature(tar.extractall).parameters
                if "filter" in extract_params:
                    tar.extractall(path=self.dest_dir, filter="data")
                else:
                    tar.extractall(path=self.dest_dir)

            if os.path.exists(tar_filepath):
                os.remove(tar_filepath)

            expected_path = os.path.join(self.dest_dir, self.tag_name)
            if not os.path.exists(expected_path):
                for entry in os.listdir(self.dest_dir):
                    if self.tag_name.lower() in entry.lower():
                        expected_path = os.path.join(self.dest_dir, entry)
                        break

            # Resolve valid directory with toolmanifest.vdf
            valid_proton_path = find_valid_proton_dir(expected_path)
            if not os.path.exists(os.path.join(valid_proton_path, "toolmanifest.vdf")):
                logger.error(f"Extracted folder {valid_proton_path} missing toolmanifest.vdf")
                self.download_failed.emit("Extracted archive is missing toolmanifest.vdf. Download may be corrupt.")
                return

            logger.info(f"GE-Proton {self.tag_name} installed successfully at {valid_proton_path}")
            self.download_complete.emit(self.tag_name, valid_proton_path)
        except Exception as extract_err:
            logger.error(f"Failed to extract {self.tag_name}: {extract_err}")
            if os.path.exists(tar_filepath):
                try:
                    os.remove(tar_filepath)
                except Exception:
                    pass
            self.download_failed.emit(f"Extraction failed: {extract_err}")
