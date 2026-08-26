"""
App-managed Ludusavi integration.

SafeLauncher treats the host `ludusavi` binary as a bundled component: when the
executable is neither on PATH nor already downloaded into the app data
directory, it is fetched automatically (pinned release, no pip/sudo) into

    ~/.local/share/safelauncher/bin/ludusavi

Downloads are opt-outable via SAFELAUNCHER_NO_LUDUSAVI=1 and never run on the
GUI thread (callers should invoke ensure_ludusavi() from a worker).
"""

import os
import shutil
import stat
import sys
import tarfile
import tempfile
import zipfile
from typing import Optional

from core.logger import get_logger

logger = get_logger("LudusaviInstall")

# Pinned for reproducibility/supply-chain reasons; bump deliberately.
LUDUSAVI_VERSION = "0.31.0"

_RELEASE_BASE = (
    f"https://github.com/mtkennerly/ludusavi/releases/"
    f"download/v{LUDUSAVI_VERSION}"
)

_DOWNLOAD_TIMEOUT = 30


def get_app_bin_dir() -> str:
    """Directory where SafeLauncher keeps managed external binaries."""
    from database import _APP_DATA_DIR
    return os.path.join(_APP_DATA_DIR, "bin")


def get_managed_ludusavi_path() -> str:
    return os.path.join(get_app_bin_dir(), "ludusavi")


def _asset_name_for_platform() -> str:
    if sys.platform == "win32":
        return f"ludusavi-v{LUDUSAVI_VERSION}-win64.zip"
    if sys.platform == "darwin":
        return f"ludusavi-v{LUDUSAVI_VERSION}-mac.tar.gz"
    return f"ludusavi-v{LUDUSAVI_VERSION}-linux.tar.gz"


def _extract_binary(archive_path: str, dest_dir: str) -> str:
    """Extract the ludusavi executable out of a release archive."""
    exe_out = os.path.join(dest_dir, "ludusavi")

    if archive_path.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            members = [m for m in zf.infolist()
                       if os.path.basename(m.filename).lower() == "ludusavi.exe"]
            if not members:
                raise ValueError("ludusavi.exe not found in release archive")
            extracted = zf.extract(members[0], dest_dir)
            final = exe_out + ".exe"
            if os.path.exists(final):
                os.unlink(final)
            os.rename(extracted, final)
            return final

    # tar.gz archives contain a single top-level `ludusavi` binary
    with tarfile.open(archive_path, "r:*") as tf:
        member = next(
            (m for m in tf.getmembers()
             if m.isfile() and os.path.basename(m.name) == "ludusavi"),
            None,
        )
        if member is None:
            raise ValueError("ludusavi binary not found in release archive")
        member.name = "ludusavi"  # flatten any directory prefix
        tf.extract(member, dest_dir)

    os.chmod(exe_out, os.stat(exe_out).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return exe_out


def ensure_ludusavi() -> Optional[str]:
    """Guarantee a usable ludusavi binary and return its path, or None.

    Order of preference:
      1. SAFELAUNCHER_LUDUSAVI env var (explicit override)
      2. Managed copy in the app bin dir (downloading it on first use)
      3. System PATH
    """
    override = os.environ.get("SAFELAUNCHER_LUDUSAVI", "").strip()
    if override and os.path.isfile(override):
        return override

    managed = get_managed_ludusavi_path()
    if os.path.isfile(managed):
        return managed

    if os.environ.get("SAFELAUNCHER_NO_LUDUSAVI", "").strip() == "1":
        logger.info("Skipping ludusavi download (SAFELAUNCHER_NO_LUDUSAVI=1).")
        return shutil.which("ludusavi")

    try:
        return _download_ludusavi(managed)
    except Exception as e:
        logger.warning(f"Ludusavi download failed ({e}); falling back to heuristics-only save detection.")
        return shutil.which("ludusavi")


def _download_ludusavi(managed_path: str) -> str:
    """Fetch the pinned release and stage the binary next to its final home."""
    import requests

    url = f"{_RELEASE_BASE}/{_asset_name_for_platform()}"
    logger.info(f"Downloading ludusavi v{LUDUSAVI_VERSION} ...")

    dest_dir = os.path.dirname(managed_path)
    os.makedirs(dest_dir, mode=0o700, exist_ok=True)

    resp = requests.get(url, timeout=_DOWNLOAD_TIMEOUT, stream=True)
    resp.raise_for_status()

    fd, tmp_archive = tempfile.mkstemp(prefix=".ludusavi-", suffix=".tmp", dir=dest_dir)
    os.close(fd)
    staged_binary = None
    try:
        total = 0
        max_bytes = 64 * 1024 * 1024
        with open(tmp_archive, "wb") as out:
            for chunk in resp.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("Release archive exceeds expected size cap")
                out.write(chunk)
        staged_binary = _extract_binary(tmp_archive, dest_dir)
        final = managed_path + (".exe" if sys.platform == "win32" else "")
        if os.path.exists(final):
            os.unlink(final)
        os.rename(staged_binary, final)
        staged_binary = None
        os.chmod(final, os.stat(final).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        logger.info(f"Ludusavi {LUDUSAVI_VERSION} installed at {final}")
        return final
    finally:
        for leftover in (tmp_archive, staged_binary):
            if leftover and os.path.exists(leftover):
                try:
                    os.unlink(leftover)
                except OSError:
                    pass
