"""Single source of truth for SafeLauncher application and cloud versioning."""

from __future__ import annotations

import re
from typing import Tuple

APP_VERSION = "0.5.5"
__version__ = APP_VERSION

MIN_CONVEX_BACKEND_VERSION = "1.2.0"
GITHUB_REPO = "Mistarin/SafeLauncher"
BACKEND_GITHUB_REPO = "Mistarin/SafeLauncherCloud"


def parse_version(v: str) -> Tuple[int, ...]:
    """Extract numeric version components from a version string (e.g. 'v0.5.5' -> (0, 5, 5))."""
    if not v:
        return (0,)
    # Remove leading 'v' or 'V'
    clean = v.strip().lstrip("vV")
    # Match integer segments
    parts = re.findall(r"\d+", clean)
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts)


def compare_versions(v1: str, v2: str) -> int:
    """Compare two version strings. Returns -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2."""
    p1 = parse_version(v1)
    p2 = parse_version(v2)

    # Pad with zeros to equal length
    max_len = max(len(p1), len(p2))
    p1_padded = p1 + (0,) * (max_len - len(p1))
    p2_padded = p2 + (0,) * (max_len - len(p2))

    if p1_padded < p2_padded:
        return -1
    elif p1_padded > p2_padded:
        return 1
    return 0


def is_version_outdated(current: str, required_or_latest: str) -> bool:
    """Return True if current version is strictly older than required_or_latest."""
    return compare_versions(current, required_or_latest) < 0


def set_version(new_version: str, backend_version: str = "") -> None:
    """Convenience helper to update version definitions directly in this file."""
    import sys
    from pathlib import Path

    file_path = Path(__file__).resolve()
    content = file_path.read_text(encoding="utf-8")

    clean_v = new_version.strip().lstrip("vV")
    content = re.sub(r'APP_VERSION\s*=\s*"[^"]*"', f'APP_VERSION = "{clean_v}"', content)

    if backend_version:
        clean_b = backend_version.strip().lstrip("vV")
        content = re.sub(r'MIN_CONVEX_BACKEND_VERSION\s*=\s*"[^"]*"', f'MIN_CONVEX_BACKEND_VERSION = "{clean_b}"', content)

    file_path.write_text(content, encoding="utf-8")
    print(f"✔ Updated SafeLauncher APP_VERSION to: {clean_v}")
    if backend_version:
        print(f"✔ Updated MIN_CONVEX_BACKEND_VERSION to: {backend_version}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        arg = sys.argv[1].strip()
        if arg in ("--help", "-h"):
            print("Usage:")
            print("  python3 -m core.version           # Print current version")
            print("  python3 -m core.version 0.5.6     # Set new app version")
            print("  python3 -m core.version 0.5.6 1.2.0  # Set app and backend version")
        else:
            backend_v = sys.argv[2].strip() if len(sys.argv) > 2 else ""
            set_version(arg, backend_v)
    else:
        print(f"SafeLauncher APP_VERSION: {APP_VERSION}")
        print(f"Minimum Convex Backend:   {MIN_CONVEX_BACKEND_VERSION}")

