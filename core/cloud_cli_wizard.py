"""Terminal-based setup wizard for SafeLauncher private cloud saves."""

import sys
import requests
from PyQt6.QtCore import QSettings

from core.cloud_detector import discover_local_cloud_backend


def run_cloud_setup_wizard() -> int:
    """Run interactive terminal setup wizard for private cloud save backend."""
    settings = QSettings("SafeLauncher", "SafeLauncher")
    current_url = settings.value("convex_site_url", "", type=str).strip()
    discovered_url = discover_local_cloud_backend()
    active_url = current_url or discovered_url or ""
    current_key = settings.value("cloud_secret_key", "", type=str).strip()

    # ANSI color formatting
    GREEN = "\033[92m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    # Fast probe: if already configured and reachable, show green checkmark
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
                    quota_info = f" ({used_mb:.1f} MB of {quota_mb:.0f} MB used · {game_count} game(s) synced)"

                print("\n" + "=" * 64)
                print(f" {GREEN}{BOLD}[✓] SafeLauncher Cloud Saves are already set up and active!{RESET}")
                print("=" * 64)
                print(f"  • Endpoint: {active_url}")
                if quota_info:
                    print(f"  • Storage: {quota_info.strip(' ()')}")
                if current_key:
                    print(f"  • Secret Key: {'*' * len(current_key)}")
                print("-" * 64)

                reconf = input("\nWould you like to reconfigure or change endpoints? [y/N]: ").strip().lower()
                if reconf not in ("y", "yes"):
                    print(f"{GREEN}[✓] Existing cloud configuration preserved.{RESET}\n")
                    return 0
                print("")
        except Exception:
            pass

    print("\n" + "=" * 64)
    print("      SafeLauncher Cloud Saves Setup")
    print("=" * 64)
    print("SafeLauncher stores encrypted game saves on your own private Convex backend.")
    print("Convex offers 1 GB free storage without monthly fees.\n")
    print("If you haven't deployed your backend yet:")
    print("  1. Clone: git clone https://github.com/Mistarin/SafeLauncherCloud.git")
    print("  2. Deploy: cd SafeLauncherCloud && npm install && npx convex deploy")
    print("  3. Copy your project's .convex.site URL from the deployment output")
    print("-" * 64)

    default_url = current_url or discovered_url or ""

    if discovered_url and not current_url:
        print(f"\n[Detected] Found local backend deployment: {discovered_url}")
        use_detected = input("Use this detected endpoint? [Y/n]: ").strip().lower()
        if use_detected not in ("n", "no"):
            default_url = discovered_url

    prompt = f"Convex Site URL [{default_url}]: " if default_url else "Convex Site URL (e.g. https://my-saves.convex.site): "
    entered_url = input(prompt).strip()
    site_url = entered_url if entered_url else default_url

    if not site_url:
        print("[!] Site URL cannot be empty. Setup aborted.")
        return 1

    site_url = site_url.rstrip("/")
    if not site_url.startswith("http://") and not site_url.startswith("https://"):
        site_url = "https://" + site_url

    current_key = settings.value("cloud_secret_key", "", type=str).strip()
    key_prompt = f"Secret Access Key [{current_key}]: " if current_key else "Secret Access Key (optional, press Enter to skip): "
    entered_key = input(key_prompt).strip()
    secret_key = entered_key if entered_key else (current_key if entered_url == "" else "")

    print(f"\nVerifying connection to {site_url}...")
    try:
        headers = {}
        if secret_key:
            headers["Authorization"] = f"Bearer {secret_key}"
            headers["X-SafeLauncher-Key"] = secret_key

        # 1. Health probe
        resp = requests.get(f"{site_url}/api/health", headers=headers, timeout=6)
        if resp.status_code != 200:
            print(f"[x] Health check failed (HTTP {resp.status_code}). Check the URL and deployment status.")
            return 1
        print("[✓] Backend is online.")

        # 2. Account overview probe
        resp_me = requests.get(f"{site_url}/api/me", headers=headers, timeout=6)
        if resp_me.status_code == 200:
            data = resp_me.json()
            quota_mb = data.get("quotaBytes", 0) / (1024 * 1024)
            used_mb = data.get("bytesUsed", 0) / (1024 * 1024)
            print(f"[✓] Storage quota: {used_mb:.1f} MB used of {quota_mb:.0f} MB")
        else:
            print(f"[!] Warning: /api/me returned HTTP {resp_me.status_code}. (Check secret key if configured).")

        # Save to local configuration
        settings.setValue("cloud_mode", "convex")
        settings.setValue("convex_site_url", site_url)
        if secret_key:
            settings.setValue("cloud_secret_key", secret_key)
        else:
            settings.remove("cloud_secret_key")

        print("\n" + "=" * 64)
        print("Setup complete. SafeLauncher will now sync game saves to your cloud backend.")
        print("=" * 64 + "\n")
        return 0

    except Exception as err:
        print(f"[x] Connection failed: {err}")
        return 1


if __name__ == "__main__":
    sys.exit(run_cloud_setup_wizard())
