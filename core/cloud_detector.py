"""Helper to auto-discover local development SafeLauncherCloud instances and inspect system compatibility."""

import os
import shutil
import subprocess
from typing import Optional, Dict, Any


def detect_local_cloud_installation() -> Optional[Dict[str, Any]]:
    """Scan the system for a local SafeLauncherDatabase / SafeLauncherCloud directory."""
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "Main", "Programming", "SafeLauncherDatabase"),
        os.path.join(home, "Main", "Programming", "SafeLauncherCloud"),
        os.path.join(home, "SafeLauncherDatabase"),
        os.path.join(home, "SafeLauncherCloud"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "SafeLauncherDatabase"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "SafeLauncherCloud"),
    ]

    for c in candidates:
        if not os.path.isdir(c):
            continue
        marker = os.path.join(c, "ImHereJustToExist.txt")
        schema_file = os.path.join(c, "convex", "schema.ts")
        if os.path.isfile(marker) or os.path.isfile(schema_file):
            site_url = ""
            deployment = ""
            env_file = os.path.join(c, ".env.local")
            if os.path.isfile(env_file):
                try:
                    with open(env_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("CONVEX_SITE_URL="):
                                site_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                            elif line.startswith("CONVEX_URL=") and not site_url:
                                raw_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                                if ".convex.cloud" in raw_url:
                                    site_url = raw_url.replace(".convex.cloud", ".convex.site")
                            elif line.startswith("CONVEX_DEPLOYMENT="):
                                deployment = line.split("=", 1)[1].strip().strip('"').strip("'")
                except Exception:
                    pass

            return {
                "path": c,
                "site_url": site_url,
                "deployment": deployment,
                "has_env": bool(site_url),
            }
    return None


def discover_local_cloud_backend() -> Optional[str]:
    """Retrieve discovered Convex site URL if present on the system."""
    info = detect_local_cloud_installation()
    if info and info.get("site_url"):
        return info["site_url"]
    return None


def inspect_system_compatibility(
    os_release_path: str = "/etc/os-release",
    dmi_product_path: Optional[str] = "/sys/devices/virtual/dmi/id/product_name",
) -> Dict[str, Any]:
    """Inspect host operating system to determine deployment capabilities (SteamOS, immutable rootfs, Node/npm)."""
    is_steamos = False
    is_steam_deck = False
    is_immutable = False
    distro_name = "Linux"

    if os.path.isfile(os_release_path):
        try:
            with open(os_release_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        k, v = line.split("=", 1)
                        v = v.strip("\"'")
                        if k == "ID" and v.lower() == "steamos":
                            is_steamos = True
                        elif k == "ID_LIKE" and "steamos" in v.lower():
                            is_steamos = True
                        elif k == "VARIANT_ID" and "steamdeck" in v.lower():
                            is_steamos = True
                            is_steam_deck = True
                        elif k == "NAME":
                            distro_name = v
        except Exception:
            pass

    # Hardware check for Steam Deck (Jupiter / Galileo)
    if dmi_product_path and os.path.isfile(dmi_product_path):
        try:
            with open(dmi_product_path, "r", encoding="utf-8") as f:
                pname = f.read().strip().lower()
                if any(x in pname for x in ("jupiter", "galileo", "steam deck")):
                    is_steam_deck = True
                    is_steamos = True
        except Exception:
            pass

    # Immutable filesystem detection (OSTree, SteamOS A/B system partition, or ro rootfs)
    if os.path.exists("/run/ostree-booted") or (is_steamos and not os.access("/", os.W_OK)):
        is_immutable = True
    elif is_steamos:
        # SteamOS default state is immutable rootfs
        is_immutable = True

    # Node.js and npm presence
    node_bin = shutil.which("node")
    npm_bin = shutil.which("npm")
    has_node = bool(node_bin)
    has_npm = bool(npm_bin)
    node_version = ""

    if has_node:
        try:
            res = subprocess.run(
                [node_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0:
                node_version = res.stdout.strip()
        except Exception:
            pass

    can_deploy_locally = bool(has_node and has_npm)

    if can_deploy_locally:
        recommended_mode = "cli"
        reason = f"Node.js ({node_version or 'detected'}) and npm are available for automated deployment."
    elif is_steamos or is_immutable:
        recommended_mode = "web"
        reason = "Steam Deck / Immutable OS detected without Node.js. 1-Click web deployment is recommended."
    else:
        recommended_mode = "web"
        reason = "Node.js and npm not found on host PATH. 1-Click web deployment is recommended."

    return {
        "distro_name": distro_name,
        "is_steamos": is_steamos,
        "is_steam_deck": is_steam_deck,
        "is_immutable": is_immutable,
        "has_node": has_node,
        "has_npm": has_npm,
        "node_version": node_version,
        "can_deploy_locally": can_deploy_locally,
        "recommended_mode": recommended_mode,
        "reason": reason,
    }
