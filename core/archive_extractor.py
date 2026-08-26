import os
import shutil
import subprocess
import tempfile
import threading
import time
from typing import List, Optional, Dict
from core.host_process import host_process_env

DEFAULT_SANDBOX_DIR = os.path.expanduser("~/Games/Sandbox")
CONFIG_FILE = ".sandbox-config"


def executable_sort_key(relative_path: str) -> tuple:
    """Rank likely game binaries ahead of installers and helper programs."""
    lower = relative_path.lower().replace("\\", "/")
    name = os.path.basename(lower)
    excluded_terms = (
        "redist/", "redistributable", "directx", "dxwebsetup",
        "vcredist", "physx", "crashhandler", "crashsender",
        "uninstall", "setup.exe",
    )
    penalty = 1 if any(term in lower or term in name for term in excluded_terms) else 0
    extension_penalty = 0 if name.endswith(".exe") else 1
    return (penalty, extension_penalty, len(relative_path.split(os.sep)), len(relative_path), lower)

def ensure_sandbox_dir(path: str = DEFAULT_SANDBOX_DIR) -> str:
    """Ensure base sandbox directory exists and setup ignore files for media crawlers."""
    os.makedirs(path, exist_ok=True)
    for ignore_file in [".nomedia", ".trackerignore"]:
        ignore_path = os.path.join(path, ignore_file)
        if not os.path.exists(ignore_path):
            try:
                with open(ignore_path, "w") as f:
                    pass
            except Exception:
                pass
    return path

def save_sandbox_config(game_dir: str, executable: str):
    """Write .sandbox-config file for 100% interoperability with bash sandbox scripts."""
    config_path = os.path.join(game_dir, CONFIG_FILE)
    try:
        with open(config_path, "w") as f:
            f.write(f'EXECUTABLE="{executable}"\n')
    except Exception as e:
        print(f"Error saving {CONFIG_FILE}: {e}")

def load_sandbox_config(game_dir: str) -> Optional[str]:
    """Read EXECUTABLE from .sandbox-config if present."""
    config_path = os.path.join(game_dir, CONFIG_FILE)
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r") as f:
                for line in f:
                    if line.startswith("EXECUTABLE="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        return val
        except Exception as e:
            print(f"Error reading {CONFIG_FILE}: {e}")
    return None

def extract_archive_sandboxed(archive_path: str, dest_dir: str, cancel_callback=None, progress_callback=None) -> bool:
    """Extract game archive securely in a Firejail sandbox.
    
    Uses shell=False (argument lists) to eliminate all shell injection surface.
    Firejail whitelist strictly scopes the extractor process to only the archive
    and destination directory.
    """
    # [H2 FIX] Validate archive path is a real, regular file before proceeding.
    if not os.path.isfile(archive_path):
        return False

    os.makedirs(dest_dir, exist_ok=True)

    archive_abs = os.path.abspath(archive_path)
    dest_abs = os.path.abspath(dest_dir)

    lower_arc = archive_abs.lower()

    # Extract into a staging directory beside the destination. The live game
    # folder is only merged once the whole archive extracted cleanly, so a
    # cancelled or failed install never leaves a half-extracted mix behind.
    # The whitelist scopes the extractor to the staging dir instead of the
    # destination itself; both live under the same parent.
    dest_parent = os.path.dirname(dest_abs) or "."
    os.makedirs(dest_parent, exist_ok=True)

    archive_type = None
    if lower_arc.endswith(".zip"):
        archive_type = "zip"
    elif lower_arc.endswith(".7z"):
        archive_type = "7z"
    elif lower_arc.endswith((".tar.gz", ".tgz")):
        archive_type = "tar.gz"
    elif lower_arc.endswith(".tar"):
        archive_type = "tar"

    if archive_type is None:
        return False

    staging_dir = tempfile.mkdtemp(prefix=".safelauncher-extract-", dir=dest_parent)

    def _transfer_staged() -> None:
        """Move every staged entry onto the destination, overwriting in place."""
        for root, _, files in os.walk(staging_dir):
            for file in files:
                staged_file = os.path.join(root, file)
                rel = os.path.relpath(staged_file, start=staging_dir)
                final_path = os.path.join(dest_abs, rel)
                os.makedirs(os.path.dirname(final_path), exist_ok=True)
                shutil.move(staged_file, final_path)
            for directory in os.listdir(root):
                full_dir = os.path.join(root, directory)
                if os.path.isdir(full_dir) and not os.listdir(full_dir):
                    rel = os.path.relpath(full_dir, start=staging_dir)
                    os.makedirs(os.path.join(dest_abs, rel), exist_ok=True)

    def _discard_staged() -> None:
        shutil.rmtree(staging_dir, ignore_errors=True)

    firejail_base = [
        "firejail", "--noprofile", "--net=none",
        f"--whitelist={archive_abs}",
        f"--whitelist={staging_dir}",
    ]

    if archive_type == "zip":
        cmd = firejail_base + ["unzip", "-q", "-o", archive_abs, "-d", staging_dir]
    elif archive_type == "7z":
        cmd = firejail_base + ["7z", "x", "-y", archive_abs, f"-o{staging_dir}"]
    elif archive_type == "tar.gz":
        cmd = firejail_base + ["tar", "-xzf", archive_abs, "-C", staging_dir]
    else:
        cmd = firejail_base + ["tar", "-xf", archive_abs, "-C", staging_dir]

    try:
        # Drain stderr continuously. Progress-heavy extractors (e.g. 7z prints
        # a line per file) otherwise fill the pipe buffer (~64 KB) and block
        # forever while this loop waits on poll().
        stderr_lines: List[str] = []

        def _drain_stderr(pipe) -> None:
            try:
                for line in iter(pipe.readline, ""):
                    stderr_lines.append(line)
            except Exception:
                pass

        process = subprocess.Popen(
            cmd,
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=host_process_env(),
        )
        drainer = threading.Thread(target=_drain_stderr, args=(process.stderr,), daemon=True)
        drainer.start()
        while process.poll() is None:
            if cancel_callback and cancel_callback():
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                _discard_staged()
                return False
            if progress_callback:
                progress_callback(-1)
            time.sleep(0.25)
        drainer.join(timeout=5)
        # Treat only a zero exit status as success. Exit code 1 means the
        # extractor reported an error or warning and must not be silently
        # accepted as a complete installation.
        if process.returncode == 0:
            _transfer_staged()
            return True
        print(f"Sandboxed extraction failed (exit {process.returncode}): {''.join(stderr_lines)}")
        _discard_staged()
        return False
    except Exception as e:
        print(f"Error during sandboxed extraction: {e}")
        _discard_staged()
        return False

def find_executables(game_dir: str) -> List[str]:
    """Scan directory recursively for executable files (.exe, .bat, .sh)."""
    exes = []
    if not os.path.isdir(game_dir):
        return exes

    for root, _, files in os.walk(game_dir):
        # A Wine prefix contains hundreds of helper .exe files.  They are
        # never valid game launch candidates and can otherwise outrank the
        # real binary during auto-detection.
        if "prefix" in root.split(os.sep):
            continue
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in [".exe", ".bat", ".sh"]:
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, start=game_dir)
                exes.append(rel_path)

    exes.sort(key=executable_sort_key)
    return exes

def scan_sandbox_games(sandbox_dir: str = DEFAULT_SANDBOX_DIR) -> List[Dict]:
    """Scan ~/Games/Sandbox for installed game folders and auto-detect executables & config."""
    found_games = []
    if not os.path.exists(sandbox_dir):
        return found_games

    try:
        entries = os.listdir(sandbox_dir)
        for name in entries:
            if name.startswith("."):
                # Hidden folders hold temp/staging leftovers, not games.
                continue
            full_path = os.path.join(sandbox_dir, name)
            if os.path.isdir(full_path):
                cfg_exe = load_sandbox_config(full_path)
                exes = find_executables(full_path)

                exe = cfg_exe if cfg_exe else (exes[0] if exes else "")

                # Determine mode based on executable extension
                if exe.lower().endswith(".sh"):
                    mode = "linux"
                else:
                    mode = "umu"

                if exe:
                    found_games.append({
                        'name': name,
                        'path': full_path,
                        'executable': exe,
                        'mode': mode
                    })
    except Exception as e:
        print(f"Error scanning sandbox games: {e}")

    return found_games
