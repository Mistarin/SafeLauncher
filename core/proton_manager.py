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
import platform
import struct
from pathlib import Path
from PyQt6.QtCore import pyqtSignal
from core.safe_thread import SafeQThread
from core.logger import get_logger

logger = get_logger("ProtonManager")

_HOME = os.path.expanduser("~")
UMU_DIR = os.path.join(_HOME, ".local", "share", "umu")
STEAM_COMPAT_DIR = os.path.join(_HOME, ".local", "share", "Steam", "compatibilitytools.d")

GITHUB_RELEASES_API = "https://api.github.com/repos/GloriousEggroll/proton-ge-custom/releases"


def _host_architecture() -> str:
    """Return the normalized CPU family used for Proton asset selection."""
    machine = platform.machine().lower()
    if machine in {"aarch64", "arm64", "armv8l"}:
        return "aarch64"
    if machine in {"x86_64", "amd64", "x64"}:
        return "x86_64"
    return machine


def _asset_architecture_score(asset_name: str) -> int:
    """Score a release asset for this host; negative means incompatible."""
    name = asset_name.lower()
    has_arm = any(token in name for token in ("aarch64", "arm64", "armv8"))
    has_x86 = any(token in name for token in ("x86_64", "amd64", "x64"))
    host = _host_architecture()

    if host == "x86_64":
        if has_arm:
            return -1
        return 3 if has_x86 else 2
    if host == "aarch64":
        if has_x86 or not has_arm:
            return -1
        return 3
    return -1 if (has_arm or has_x86) else 1


def _proton_path_is_compatible(path: str) -> bool:
    """Reject explicitly ARM/x86 Proton installs that cannot run on this host."""
    name = os.path.basename(os.path.normpath(path)).lower()
    host = _host_architecture()
    if host == "x86_64" and any(token in name for token in ("aarch64", "arm64", "armv8")):
        return False
    if host == "aarch64" and any(token in name for token in ("x86_64", "amd64", "x64")):
        return False

    # Names are normally sufficient, but inspect the bundled pressure-vessel
    # executable when a build has no architecture suffix.
    for relative in (
        os.path.join("pressure-vessel", "bin", "pressure-vessel-wrap"),
        os.path.join("pressure-vessel", "bin", "pv-verify"),
    ):
        binary = os.path.join(path, relative)
        try:
            with open(binary, "rb") as stream:
                header = stream.read(20)
            if header[:4] != b"\x7fELF" or len(header) < 20:
                continue
            machine = struct.unpack("<H", header[18:20])[0]
            if host == "x86_64" and machine == 183:  # EM_AARCH64
                return False
            if host == "aarch64" and machine == 62:  # EM_X86_64
                return False
        except OSError:
            continue
    return True


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
                # Only list actual GE-Proton builds. UMU runtime folders such
                # as steamrt4 also contain toolmanifest.vdf but are not Proton
                # tools and may contain a different architecture.
                is_ge_name = item.lower().startswith(("ge-proton", "proton-ge"))
                if (is_ge_name and os.path.isdir(valid_path)
                        and os.path.exists(os.path.join(valid_path, "toolmanifest.vdf"))
                        and _proton_path_is_compatible(valid_path)):
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
                candidates = []
                for asset in release.get("assets", []):
                    asset_name = asset.get("name", "")
                    if asset_name.endswith(".tar.gz") or asset_name.endswith(".tar.xz"):
                        score = _asset_architecture_score(asset_name)
                        if score >= 0:
                            candidates.append((score, asset))
                if candidates:
                    _, asset = max(candidates, key=lambda item: item[0])
                    tarball_url = asset.get("browser_download_url", "")
                    size_mb = round(asset.get("size", 0) / (1024 * 1024), 1)

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
                    # Proton packages legitimately contain internal symlinks
                    # and hardlinks. Allow them only when their resolved target
                    # remains inside the destination; reject device nodes.
                    if member.isdev():
                        raise ValueError(f"unsafe archive member: {member.name}")
                    target = os.path.realpath(os.path.join(dest_root, member.name))
                    if target != dest_root and not target.startswith(dest_root + os.sep):
                        raise ValueError(f"archive path escapes install directory: {member.name}")

                    if member.issym():
                        link_target = os.path.realpath(os.path.join(
                            dest_root, os.path.dirname(member.name), member.linkname
                        ))
                        if link_target != dest_root and not link_target.startswith(dest_root + os.sep):
                            raise ValueError(f"unsafe symlink target: {member.name} -> {member.linkname}")
                    elif member.islnk():
                        link_target = os.path.realpath(os.path.join(dest_root, member.linkname))
                        if link_target != dest_root and not link_target.startswith(dest_root + os.sep):
                            raise ValueError(f"unsafe hardlink target: {member.name} -> {member.linkname}")

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
            has_manifest = os.path.exists(os.path.join(valid_proton_path, "toolmanifest.vdf"))
            is_compatible = _proton_path_is_compatible(valid_proton_path)
            if not has_manifest or not is_compatible:
                logger.error(
                    f"Rejected extracted Proton folder {valid_proton_path}: "
                    f"manifest={has_manifest}, compatible={is_compatible}"
                )
                self.download_failed.emit(
                    "Downloaded Proton archive is missing its manifest or is incompatible with this host architecture."
                )
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
