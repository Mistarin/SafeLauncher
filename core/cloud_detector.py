"""Helper to auto-discover local development SafeLauncherCloud instances."""

import os
from typing import Optional, Dict, Any


def detect_local_cloud_installation() -> Optional[Dict[str, Any]]:
    """Scan the system for a local SafeLauncherDatabase / SafeLauncherCloud directory."""
    home = os.path.expanduser("~")
    candidates = [
        "/home/martin/Main/Programming/SafeLauncherDatabase",
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
