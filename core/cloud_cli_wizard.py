"""Terminal-based setup wizard for SafeLauncher private cloud saves."""

import sys
import requests
from PyQt6.QtCore import QSettings


def run_cloud_setup_wizard() -> int:
    """Run interactive terminal setup wizard for private cloud backend."""
    print("=" * 68)
    print("           SafeLauncher Private Cloud Save Setup Wizard")
    print("=" * 68)
    print("SafeLauncher Cloud Saves are 100% private and self-hosted on Convex.")
    print("You can deploy your free backend (1 GB storage) from GitHub:")
    print("  -> https://github.com/Mistarin/SafeLauncherCloud.git")
    print("-" * 68)

    settings = QSettings("SafeLauncher", "SafeLauncher")
    current_url = settings.value("convex_site_url", "", type=str).strip()

    prompt_url = f"Enter your Convex Site URL [{current_url}]: " if current_url else "Enter your Convex Site URL (e.g. https://your-project.convex.site): "
    site_url = input(prompt_url).strip()
    if not site_url and current_url:
        site_url = current_url

    if not site_url:
        print("[ERROR] Site URL cannot be empty. Aborted.")
        return 1

    site_url = site_url.rstrip("/")
    if not site_url.startswith("http://") and not site_url.startswith("https://"):
        site_url = "https://" + site_url

    secret_key = input("Enter your Cloud Secret Key (optional, press Enter if none): ").strip()

    print(f"\nTesting connection to {site_url}/api/health...")
    try:
        headers = {}
        if secret_key:
            headers["Authorization"] = f"Bearer {secret_key}"
            headers["X-SafeLauncher-Key"] = secret_key

        resp = requests.get(f"{site_url}/api/health", headers=headers, timeout=6)
        if resp.status_code != 200:
            print(f"[ERROR] Health check failed with HTTP {resp.status_code}.")
            return 1

        print("[OK] Health probe successful!")

        # Verify authentication/API route
        resp_me = requests.get(f"{site_url}/api/me", headers=headers, timeout=6)
        if resp_me.status_code == 200:
            data = resp_me.json()
            quota_mb = data.get("quotaBytes", 0) / (1024 * 1024)
            print(f"[OK] Authenticated successfully! Cloud storage quota: {quota_mb:.0f} MB")
        else:
            print(f"[WARNING] /api/me returned HTTP {resp_me.status_code}. (Check your secret key if required).")

        # Save settings
        settings.setValue("cloud_mode", "convex")
        settings.setValue("convex_site_url", site_url)
        if secret_key:
            settings.setValue("cloud_secret_key", secret_key)
        else:
            settings.remove("cloud_secret_key")

        print("\n" + "=" * 68)
        print(" SUCCESS: Cloud Saves configured and enabled in SafeLauncher!")
        print("=" * 68)
        return 0

    except Exception as e:
        print(f"[ERROR] Could not connect to {site_url}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(run_cloud_setup_wizard())
