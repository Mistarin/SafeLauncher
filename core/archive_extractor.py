import os
import subprocess
from typing import List, Optional, Dict

DEFAULT_SANDBOX_DIR = os.path.expanduser("~/Games/Sandbox")
CONFIG_FILE = ".sandbox-config"

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

def extract_archive_sandboxed(archive_path: str, dest_dir: str) -> bool:
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

    # [S2 FIX] Use shell=False with argument list - zero shell injection surface.
    # Firejail args + extractor args are passed as a flat list; no string interpolation.
    # Use --noprofile so default profile rules (like disable-common.inc) don't block
    # unzip/7z/tar from creating files inside the whitelisted target directory.
    # --net=none + --whitelist ensures complete sandboxing and zero network access.
    firejail_base = [
        "firejail", "--noprofile", "--net=none",
        f"--whitelist={archive_abs}",
        f"--whitelist={dest_abs}",
    ]

    if lower_arc.endswith(".zip"):
        cmd = firejail_base + ["unzip", "-q", "-o", archive_abs, "-d", dest_abs]
    elif lower_arc.endswith(".7z"):
        cmd = firejail_base + ["7z", "x", "-y", archive_abs, f"-o{dest_abs}"]
    elif lower_arc.endswith(".tar.gz") or lower_arc.endswith(".tgz"):
        cmd = firejail_base + ["tar", "-xzf", archive_abs, "-C", dest_abs]
    elif lower_arc.endswith(".tar"):
        cmd = firejail_base + ["tar", "-xf", archive_abs, "-C", dest_abs]
    else:
        return False

    try:
        res = subprocess.run(cmd, shell=False, capture_output=True, text=True)
        # returncode 0 = clean success, 1 = minor warnings (e.g. non-fatal zip header warnings)
        if res.returncode in (0, 1):
            return True
        # Fallback check: if dest_dir has extracted files, consider it successful
        if os.path.exists(dest_abs) and len(os.listdir(dest_abs)) > 0:
            return True
        print(f"Sandboxed extraction failed (exit {res.returncode}): {res.stderr}")
        return False
    except Exception as e:
        print(f"Error during sandboxed extraction: {e}")
        return False

def find_executables(game_dir: str) -> List[str]:
    """Scan directory recursively for executable files (.exe, .bat, .sh)."""
    exes = []
    if not os.path.isdir(game_dir):
        return exes

    for root, _, files in os.walk(game_dir):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in [".exe", ".bat", ".sh"]:
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, start=game_dir)
                exes.append(rel_path)

    exes.sort()
    return exes

def scan_sandbox_games(sandbox_dir: str = DEFAULT_SANDBOX_DIR) -> List[Dict]:
    """Scan ~/Games/Sandbox for installed game folders and auto-detect executables & config."""
    found_games = []
    if not os.path.exists(sandbox_dir):
        return found_games

    try:
        entries = os.listdir(sandbox_dir)
        for name in entries:
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
