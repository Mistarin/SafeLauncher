"""Terminal-based setup wizard for SafeLauncher private cloud saves."""

from __future__ import annotations

import os
import sys
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

import requests
from PyQt6.QtCore import QSettings

from core.host_process import host_process_env
from core.cloud_detector import discover_local_cloud_backend, detect_local_cloud_installation


def download_server_repository(target_dir: Optional[Path] = None) -> Optional[Path]:
    """Download or clone the SafeLauncherDatabase/SafeLauncherCloud backend repository."""
    import shutil
    import subprocess
    import tempfile
    import zipfile
    from pathlib import Path

    if target_dir:
        target = target_dir
    else:
        root_dir = Path(__file__).resolve().parent.parent
        parent_dir = root_dir.parent
        # Avoid downloading into /tmp when running inside AppImage (_MEIPASS)
        if getattr(sys, "frozen", False) or str(parent_dir).startswith(("/tmp", "/var/tmp")):
            target = Path.home() / "SafeLauncherCloud"
        else:
            target = parent_dir / "SafeLauncherDatabase"

    print(f"  Downloading server files into {target}...")

    # Method 1: git clone using host environment
    if shutil.which("git"):
        try:
            res = subprocess.run(
                ["git", "clone", "https://github.com/Mistarin/SafeLauncherCloud.git", str(target)],
                capture_output=True,
                text=True,
                env=host_process_env(),
            )
            if res.returncode == 0 and target.is_dir():
                marker = target / "ImHereJustToExist.txt"
                if not marker.exists():
                    marker.touch()
                return target
        except Exception:
            pass

    # Method 2: HTTP ZIP download fallback
    try:
        zip_url = "https://github.com/Mistarin/SafeLauncherCloud/archive/refs/heads/main.zip"
        resp = requests.get(zip_url, timeout=30)
        resp.raise_for_status()

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf:
            tf.write(resp.content)
            tmp_zip = tf.name

        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            for member in zf.infolist():
                parts = member.filename.split("/", 1)
                if len(parts) > 1 and parts[1]:
                    dest_file = target / parts[1]
                    if member.is_dir():
                        dest_file.mkdir(parents=True, exist_ok=True)
                    else:
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(dest_file, "wb") as dst:
                            dst.write(src.read())

        Path(tmp_zip).unlink(missing_ok=True)
        marker = target / "ImHereJustToExist.txt"
        if not marker.exists():
            marker.touch()
        return target
    except Exception as e:
        print(f"  [✖] Download failed: {e}")
        return None


def deploy_convex_backend(existing_path: Optional[str] = None) -> Optional[str]:
    """Interactively build, connect, and deploy Convex backend functions."""
    import shutil
    import subprocess
    from pathlib import Path

    root_dir = Path(__file__).resolve().parent.parent
    parent_dir = root_dir.parent

    server_dir = Path(existing_path) if existing_path else None
    if not server_dir or not server_dir.is_dir():
        candidates = [
            parent_dir / "SafeLauncherDatabase",
            parent_dir / "SafeLauncherCloud",
            Path.home() / "Main" / "Programming" / "SafeLauncherDatabase",
            Path.home() / "Main" / "Programming" / "SafeLauncherCloud",
            Path.home() / "SafeLauncherDatabase",
            Path.home() / "SafeLauncherCloud",
        ]
        for c in candidates:
            if c.is_dir():
                server_dir = c
                break

    if not server_dir or not server_dir.is_dir():
        downloaded = download_server_repository()
        if not downloaded:
            return None
        server_dir = downloaded

    if not shutil.which("npm"):
        print("  [✖] Node.js & npm are required. Please install Node.js (e.g. sudo pacman -S nodejs npm or sudo apt install nodejs npm).")
        return None

    clean_env = host_process_env()

    print(f"\n  [Deploy] Installing dependencies in {server_dir}...")
    try:
        subprocess.run(["npm", "install"], cwd=str(server_dir), check=True, env=clean_env)
        
        env_local = server_dir / ".env.local"
        if not env_local.is_file():
            print("\n  [Convex] First-time setup: Linking your Convex project...")
            print("  (A browser window or terminal prompt will open to authenticate with Convex)")
        
        print("  [Deploy] Deploying backend functions with 'npx convex deploy'...")
        subprocess.run(["npx", "convex", "deploy"], cwd=str(server_dir), check=True, env=clean_env)
    except Exception as e:
        print(f"  [✖] Deployment encountered an error: {e}")
        return None

    from core.cloud_detector import detect_local_cloud_installation
    info = detect_local_cloud_installation()
    if info and info.get("site_url"):
        print(f"\n  [✔] Deployment complete! Found site URL: {info['site_url']}")
        return info["site_url"]
    return None


def run_cloud_setup_wizard() -> int:
    """Run interactive terminal setup wizard for private cloud save backend."""
    # Terminal ANSI styling
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    def banner(title: str, color=CYAN):
        width = 66
        line = "─" * (width - len(title) - 6)
        print(f"\n{color}{BOLD}┌── {title} {line}┐{RESET}")

    def footer(color=CYAN):
        print(f"{color}{BOLD}└{'─' * 64}┘{RESET}")

    settings = QSettings("SafeLauncher", "SafeLauncher")
    current_url = settings.value("convex_site_url", "", type=str).strip()
    discovered_url = discover_local_cloud_backend()
    active_url = current_url or discovered_url or ""
    current_key = settings.value("cloud_secret_key", "", type=str).strip()

    # Fast probe: if already configured and reachable, show active status banner
    if active_url:
        try:
            headers = {}
            if current_key:
                headers["Authorization"] = f"Bearer {current_key}"
                headers["X-SafeLauncher-Key"] = current_key

            resp_health = requests.get(f"{active_url}/api/health", headers=headers, timeout=3)
            if resp_health.status_code == 200:
                resp_me = requests.get(f"{active_url}/api/me", headers=headers, timeout=3)
                quota_info = ""
                if resp_me.status_code == 200:
                    data = resp_me.json()
                    used_mb = data.get("bytesUsed", 0) / (1024 * 1024)
                    quota_mb = data.get("quotaBytes", 0) / (1024 * 1024)
                    game_count = len(data.get("games", []))
                    quota_info = f"{used_mb:.1f} MB used of {quota_mb:.0f} MB · {game_count} game(s) synced"

                banner("Active Cloud Save Backend", GREEN)
                print(f"  {GREEN}{BOLD}✔ Status:{RESET}     Connected & Synchronizing")
                print(f"  {BOLD}• Endpoint:{RESET}   {active_url}")
                if quota_info:
                    print(f"  {BOLD}• Storage:{RESET}    {quota_info}")
                if current_key:
                    print(f"  {BOLD}• Security:{RESET}   Secret Key Configured ({'*' * len(current_key)})")
                footer(GREEN)

                reconf = input(f"\n{CYAN}{BOLD}➜{RESET} Reconfigure or change backend settings? [y/N]: ").strip().lower()
                if reconf not in ("y", "yes"):
                    print(f"{GREEN}✔ Existing cloud configuration preserved.{RESET}\n")
                    return 0
                print("")
        except Exception:
            pass

    # Step 1: Choose Setup Mode
    banner("[1/3] Choose Setup Mode", CYAN)
    print("  SafeLauncher stores game saves encrypted on your personal Convex cloud (1 GB free storage).\n")
    print(f"  {BOLD}1) Connect to an already created cloud database{RESET}  {GREEN}(Recommended){RESET}")
    print(f"     {DIM}• For secondary devices (such as a laptop, Steam Deck, or another PC).{RESET}")
    print(f"     {DIM}• You do NOT need Node.js, npm, git, or server files on this machine!{RESET}")
    print(f"     {DIM}• Simply enter your .convex.site URL and start syncing immediately.{RESET}\n")
    print(f"  {BOLD}2) Set up a new private cloud database from scratch{RESET}")
    print(f"     {DIM}• First-time setup: Downloads server repository and guides 'npx convex deploy'.{RESET}")
    footer(CYAN)

    mode_choice = input(f"  {CYAN}{BOLD}➜{RESET} Choose setup mode [1/2] (default: 1): ").strip()
    is_new_setup = mode_choice in ("2", "new", "deploy")

    local_info = None
    if is_new_setup:
        # Deployment & Auto-Detection
        local_info = detect_local_cloud_installation()
        if local_info:
            banner("[1b/3] Backend Auto-Detection", CYAN)
            print(f"  {GREEN}{BOLD}✔ Found local server files:{RESET} {local_info['path']}")
            if local_info.get("site_url"):
                print(f"  {GREEN}{BOLD}✔ Extracted from .env.local:{RESET}  {local_info['site_url']}")
                if local_info.get("deployment"):
                    print(f"  {DIM}• Convex deployment:{RESET}        {local_info['deployment']}")
            else:
                print(f"  {YELLOW}● Server folder found, but .env.local is not initialized yet.{RESET}")
                redeploy = input(f"\n  {CYAN}{BOLD}➜{RESET} Deploy Convex backend now? [Y/n]: ").strip().lower()
                if redeploy not in ("n", "no"):
                    deployed_url = deploy_convex_backend(local_info["path"])
                    if deployed_url:
                        local_info["site_url"] = deployed_url
            footer(CYAN)
        else:
            banner("[1b/3] Backend Setup & Download", CYAN)
            print("  SafeLauncher stores encrypted game saves on your private Convex cloud.")
            print("  Convex provides 1 GB free cloud storage without monthly fees.\n")
            print(f"  {YELLOW}● SafeLauncherDatabase files not found on this system.{RESET}")
            download_choice = input(f"  {CYAN}{BOLD}➜{RESET} Download SafeLauncherDatabase now? [Y/n]: ").strip().lower()
            if download_choice not in ("n", "no"):
                downloaded_dir = download_server_repository()
                if downloaded_dir:
                    local_info = detect_local_cloud_installation() or {"path": str(downloaded_dir), "site_url": ""}
                    print(f"  {GREEN}{BOLD}✔ Server files ready in:{RESET} {downloaded_dir}")
                    deploy_choice = input(f"\n  {CYAN}{BOLD}➜{RESET} Link and deploy Convex backend now? [Y/n]: ").strip().lower()
                    if deploy_choice not in ("n", "no"):
                        deployed_url = deploy_convex_backend(str(downloaded_dir))
                        if deployed_url:
                            local_info["site_url"] = deployed_url
            else:
                print(f"\n  {BOLD}Manual deployment steps:{RESET}")
                print(f"   {DIM}1.{RESET} Clone:  {YELLOW}git clone https://github.com/Mistarin/SafeLauncherCloud.git SafeLauncherCloud{RESET}")
                print(f"   {DIM}2.{RESET} Deploy: {YELLOW}cd SafeLauncherCloud && npm install && npx convex deploy{RESET}")
                print(f"   {DIM}3.{RESET} Copy your project's {BOLD}.convex.site{RESET} URL from the terminal output.")
            footer(CYAN)
    else:
        local_info = detect_local_cloud_installation()

    default_url = current_url or (local_info.get("site_url") if local_info else "") or ""

    # Step 2: Connection settings
    banner("[2/3] Connection Configuration", CYAN)
    prompt = f"  {CYAN}{BOLD}➜{RESET} Convex Site URL [{default_url}]: " if default_url else f"  {CYAN}{BOLD}➜{RESET} Convex Site URL (e.g. https://my-saves.convex.site): "
    entered_url = input(prompt).strip()
    site_url = entered_url if entered_url else default_url

    if not site_url:
        print(f"\n  {RED}✖ Site URL cannot be empty. Setup aborted.{RESET}\n")
        return 1

    site_url = site_url.rstrip("/")
    if not site_url.startswith("http://") and not site_url.startswith("https://"):
        site_url = "https://" + site_url

    print(f"\n  {BOLD}🔐 Secret Access Key (Recommended):{RESET}")
    print(f"     {DIM}Acts as a private password for your server endpoint. It stops anyone else{RESET}")
    print(f"     {DIM}who finds your public .convex.site URL from uploading files and filling{RESET}")
    print(f"     {DIM}up your 1 GB free Convex storage quota.{RESET}")
    print(f"     {DIM}• If you configured a secret key on your server, enter it below.{RESET}")
    print(f"     {DIM}• If this is a new setup, enter a passphrase to configure it on Convex now.{RESET}")
    print(f"     {DIM}• Press Enter to skip (leaves the server open to anyone with the URL).{RESET}\n")

    key_prompt = f"  {CYAN}{BOLD}➜{RESET} Secret Access Key [{current_key}]: " if current_key else f"  {CYAN}{BOLD}➜{RESET} Secret Access Key (press Enter to skip): "
    entered_key = input(key_prompt).strip()
    secret_key = entered_key if entered_key else (current_key if entered_url == "" else "")

    if is_new_setup and secret_key and local_info and local_info.get("path") and shutil.which("npx"):
        try:
            print(f"\n  {DIM}Configuring SAFELAUNCHER_SECRET_KEY on your Convex deployment...{RESET}")
            subprocess.run(
                ["npx", "convex", "env", "set", "SAFELAUNCHER_SECRET_KEY", secret_key],
                cwd=local_info["path"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=25,
            )
            print(f"  {GREEN}✔ Configured SAFELAUNCHER_SECRET_KEY in Convex environment!{RESET}")
        except Exception as e:
            print(f"  {YELLOW}⚠ Could not auto-set secret in Convex: {e}{RESET}")

    footer(CYAN)

    # Step 3: Verification
    banner("[3/3] Live Verification", CYAN)
    print(f"  Testing connection to {site_url}...")
    try:
        headers = {}
        if secret_key:
            headers["Authorization"] = f"Bearer {secret_key}"
            headers["X-SafeLauncher-Key"] = secret_key

        # 1. Health probe
        resp = requests.get(f"{site_url}/api/health", headers=headers, timeout=6)
        if resp.status_code != 200:
            print(f"\n  {RED}✖ Health probe failed (HTTP {resp.status_code}). Check your Convex deployment.{RESET}\n")
            return 1
        print(f"  {GREEN}✔ Backend health probe passed.{RESET}")

        # 2. Account overview probe
        def _fmt_bytes(n: float) -> str:
            n = float(n)
            for unit in ("B", "KB", "MB", "GB"):
                if n < 1024 or unit == "GB":
                    return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
                n /= 1024
            return f"{n:.1f} GB"

        resp_me = requests.get(f"{site_url}/api/me", headers=headers, timeout=6)
        if resp_me.status_code in (401, 403):
            print(f"\n  {RED}✖ Quota verification failed: the backend rejected the credentials "
                  f"(HTTP {resp_me.status_code}). The Secret Key is missing or wrong — "
                  f"cloud sync will not work until it matches.{RESET}\n")
            return 1
        if resp_me.status_code == 200:
            data = resp_me.json()
            print(f"  {GREEN}✔ Quota verification:{RESET} "
                  f"{_fmt_bytes(data.get('bytesUsed', 0))} used of {_fmt_bytes(data.get('quotaBytes', 0))} · "
                  f"max {_fmt_bytes(data.get('maxSaveBytes', 0))} per save · "
                  f"keeping last {data.get('keepVersions', '?')} generations")

            # 3. Data key probe — save payloads are encrypted client-side with it.
            try:
                resp_key = requests.get(f"{site_url}/api/key", headers=headers, timeout=6)
                if resp_key.status_code == 200 and resp_key.json().get("dataKeyB64"):
                    print(f"  {GREEN}✔ Data encryption key available.{RESET}")
                else:
                    print(f"  {YELLOW}● Warning: /api/key returned HTTP {resp_key.status_code}; "
                          f"save uploads will fail until it succeeds.{RESET}")
            except requests.RequestException as key_err:
                print(f"  {YELLOW}● Warning: data key probe failed ({key_err}).{RESET}")
        else:
            print(f"  {YELLOW}● Warning: /api/me returned HTTP {resp_me.status_code}. (Check secret key if configured).{RESET}")

        # Save to local configuration
        settings.setValue("cloud_mode", "convex")
        settings.setValue("convex_site_url", site_url)
        if secret_key:
            settings.setValue("cloud_secret_key", secret_key)
        else:
            settings.remove("cloud_secret_key")

        footer(CYAN)

        banner("Setup Complete", GREEN)
        print(f"  {GREEN}{BOLD}✔ SafeLauncher is now connected to your private cloud backend.{RESET}")
        print("  Game saves will sync automatically with AES-256-GCM encryption.")
        footer(GREEN)
        print("")
        return 0

    except Exception as err:
        print(f"\n  {RED}✖ Connection failed: {err}{RESET}\n")
        return 1


if __name__ == "__main__":
    sys.exit(run_cloud_setup_wizard())
