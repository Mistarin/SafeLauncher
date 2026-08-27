"""Terminal-based setup wizard for SafeLauncher private cloud saves."""

import sys
import requests
from PyQt6.QtCore import QSettings

from core.cloud_detector import discover_local_cloud_backend, detect_local_cloud_installation


def deploy_convex_backend(existing_path: Optional[str] = None) -> Optional[str]:
    """Interactively build and deploy Convex backend functions."""
    import shutil
    import subprocess
    from pathlib import Path

    root_dir = Path(__file__).resolve().parent.parent
    parent_dir = root_dir.parent

    server_dir = Path(existing_path) if existing_path else None
    if not server_dir or not server_dir.is_dir():
        candidates = [
            parent_dir / "SafeLauncherCloud",
            parent_dir / "SafeLauncherDatabase",
            Path.home() / "Main" / "Programming" / "SafeLauncherCloud",
            Path.home() / "Main" / "Programming" / "SafeLauncherDatabase",
        ]
        for c in candidates:
            if c.is_dir():
                server_dir = c
                break

    if not server_dir or not server_dir.is_dir():
        server_dir = parent_dir / "SafeLauncherCloud"
        print(f"  Cloning SafeLauncherCloud repository into {server_dir}...")
        try:
            res = subprocess.run(["git", "clone", "https://github.com/Mistarin/SafeLauncherCloud.git", str(server_dir)])
            if res.returncode != 0:
                print("  [✖] Git clone failed.")
                return None
        except Exception as e:
            print(f"  [✖] Git clone failed: {e}")
            return None

    if not shutil.which("npm"):
        print("  [✖] Node.js & npm are required. Please install Node.js (e.g. sudo apt install nodejs npm).")
        return None

    print(f"\n  [Deploy] Installing dependencies in {server_dir}...")
    try:
        subprocess.run(["npm", "install"], cwd=str(server_dir), check=True)
        print("  [Deploy] Running 'npx convex deploy'...")
        subprocess.run(["npx", "convex", "deploy"], cwd=str(server_dir), check=True)
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

    # Step 1: Deployment & Auto-Detection
    local_info = detect_local_cloud_installation()
    if local_info:
        banner("[1/3] Backend Auto-Detection", CYAN)
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
        banner("[1/3] Backend Deployment", CYAN)
        print("  SafeLauncher stores encrypted game saves on your private Convex cloud.")
        print("  Convex provides 1 GB free cloud storage without monthly fees.\n")
        redeploy = input(f"  {CYAN}{BOLD}➜{RESET} Deploy a new private Convex backend now? [y/N]: ").strip().lower()
        if redeploy in ("y", "yes"):
            deployed_url = deploy_convex_backend()
            if deployed_url:
                local_info = {"site_url": deployed_url}
        else:
            print(f"\n  {BOLD}Manual deployment steps:{RESET}")
            print(f"   {DIM}1.{RESET} Clone:  {YELLOW}git clone https://github.com/Mistarin/SafeLauncherCloud.git{RESET}")
            print(f"   {DIM}2.{RESET} Deploy: {YELLOW}cd SafeLauncherCloud && npm install && npx convex deploy{RESET}")
            print(f"   {DIM}3.{RESET} Copy your project's {BOLD}.convex.site{RESET} URL from the terminal output.")
        footer(CYAN)

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

    key_prompt = f"  {CYAN}{BOLD}➜{RESET} Secret Access Key [{current_key}]: " if current_key else f"  {CYAN}{BOLD}➜{RESET} Secret Access Key (optional, press Enter to skip): "
    entered_key = input(key_prompt).strip()
    secret_key = entered_key if entered_key else (current_key if entered_url == "" else "")
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
        resp_me = requests.get(f"{site_url}/api/me", headers=headers, timeout=6)
        if resp_me.status_code == 200:
            data = resp_me.json()
            quota_mb = data.get("quotaBytes", 0) / (1024 * 1024)
            used_mb = data.get("bytesUsed", 0) / (1024 * 1024)
            print(f"  {GREEN}✔ Quota verification:{RESET} {used_mb:.1f} MB used of {quota_mb:.0f} MB")
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
