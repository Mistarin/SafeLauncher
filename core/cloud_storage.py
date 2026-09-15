"""Local private-cloud storage configuration.

This module owns the local-folder location used by the cloud save engine and
its UI surfaces.  Keeping this small configuration helper separate prevents
dialogs from importing the low-level save engine merely to display a path.
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QSettings

from database import _APP_DATA_DIR


DEFAULT_CLOUD_SAVES_DIR = os.path.join(_APP_DATA_DIR, "cloud_saves")


def get_cloud_root(*, create: bool = True) -> str:
    """Return the configured local cloud root without touching cloud APIs."""
    settings = QSettings("SafeLauncher", "SafeLauncher")
    root = str(
        settings.value("cloud_saves_dir", DEFAULT_CLOUD_SAVES_DIR, type=str)
        or DEFAULT_CLOUD_SAVES_DIR
    ).strip()
    if not root:
        root = DEFAULT_CLOUD_SAVES_DIR
    root = os.path.abspath(os.path.expanduser(root))
    if create:
        os.makedirs(root, exist_ok=True)
    return root


__all__ = ["DEFAULT_CLOUD_SAVES_DIR", "get_cloud_root"]
