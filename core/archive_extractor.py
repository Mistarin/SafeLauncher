import os
import shlex
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
    """Extract game archive securely in a Firejail sandbox."""
    if not os.path.exists(archive_path):
        return False
    
    os.makedirs(dest_dir, exist_ok=True)
    
    archive_abs = os.path.abspath(archive_path)
    dest_abs = os.path.abspath(dest_dir)
    
    q_arc = shlex.quote(archive_abs)
    q_dest = shlex.quote(dest_abs)
    
    lower_arc = archive_abs.lower()
    
    if lower_arc.endswith(".zip"):
        cmd = f"firejail --net=none --whitelist={q_arc} --whitelist={q_dest} unzip -q {q_arc} -d {q_dest}"
    elif lower_arc.endswith(".7z"):
        cmd = f"firejail --net=none --whitelist={q_arc} --whitelist={q_dest} 7z x {q_arc} -o{q_dest} > /dev/null"
    elif lower_arc.endswith(".tar.gz") or lower_arc.endswith(".tgz"):
        cmd = f"firejail --net=none --whitelist={q_arc} --whitelist={q_dest} tar -xzf {q_arc} -C {q_dest}"
    elif lower_arc.endswith(".tar"):
        cmd = f"firejail --net=none --whitelist={q_arc} --whitelist={q_dest} tar -xf {q_arc} -C {q_dest}"
    else:
        return False

    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return res.returncode == 0
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
