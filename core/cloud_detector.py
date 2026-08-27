"""Helper to auto-discover local development SafeLauncherCloud instances."""

import os
from typing import Optional


def discover_local_cloud_backend() -> Optional[str]:
    """Look for SafeLauncherDatabase with ImHereJustToExist.txt on the host system."""
    home = os.path.expanduser("~")
    candidates = [
        "/home/martin/Main/Programming/SafeLauncherDatabase",
        os.path.join(home, "Main", "Programming", "SafeLauncherDatabase"),
        os.path.join(home, "Main", "Programming", "SafeLauncherCloud"),
        os.path.join(home, "SafeLauncherDatabase"),
        os.path.join(home, "SafeLauncherCloud"),
    ]
    for c in candidates:
        if not os.path.isdir(c):
            continue
        marker = os.path.join(c, "ImHereJustToExist.txt")
        env_file = os.path.join(c, ".env.local")
        if os.path.isfile(marker):
            # Parse .env.local for CONVEX_SITE_URL or CONVEX_URL
            if os.path.isfile(env_file):
                try:
                    with open(env_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("CONVEX_SITE_URL="):
                                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                                if url:
                                    return url
                            elif line.startswith("CONVEX_URL="):
                                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                                if ".convex.cloud" in url:
                                    return url.replace(".convex.cloud", ".convex.site")
                except Exception:
                    pass
    return None
