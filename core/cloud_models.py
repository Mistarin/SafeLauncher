"""Cloud-domain value objects shared by services and presentation adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SyncStatus(Enum):
    IN_SYNC = "in_sync"
    LOCAL_NEWER = "local_newer"
    CLOUD_ONLY = "cloud_only"
    CLOUD_NEWER = "cloud_newer"
    CONFLICT = "conflict"
    NO_SAVES = "no_saves"
    CLOUD_AUTH_REQUIRED = "cloud_auth_required"
    CLOUD_OFFLINE = "cloud_offline"
    CLOUD_UNAVAILABLE = "cloud_unavailable"


@dataclass
class SaveStats:
    """Redacted local/cloud save metadata used for comparison and display."""

    exists: bool
    last_modified: float = 0.0
    size_bytes: int = 0
    file_count: int = 0
    display_path: str = ""
    snapshot: Optional[object] = None


__all__ = ["SaveStats", "SyncStatus"]
