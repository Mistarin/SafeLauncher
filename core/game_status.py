"""Canonical per-game status state and presentation metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.cloud_save_sync import SyncStatus


@dataclass(frozen=True)
class StatusIndicator:
    """View-neutral metadata shared by every library presentation."""

    label: str
    tooltip: str
    icon: str
    color: str
    visible: bool = True


@dataclass(frozen=True)
class GameStatusState:
    """The complete status contract for one game."""

    update_available: bool = False
    update_error: str = ""
    update_build_id: str = ""
    update_build_date: int = 0
    cloud_status: SyncStatus | None = None
    local_stats: Any = None
    cloud_stats: Any = None
    cloud_checked_at: float = 0.0
    update_checked_at: float = 0.0

    @property
    def update_indicator(self) -> StatusIndicator:
        return update_indicator(self.update_available, self.update_error)

    @property
    def cloud_indicator(self) -> StatusIndicator:
        return cloud_indicator(self.cloud_status)


def update_indicator(available: bool, error: str = "") -> StatusIndicator:
    if error:
        return StatusIndicator(
            "Game Update: Unavailable",
            f"Game update check failed: {error}",
            "ph.warning-circle-bold",
            "#E5A93D",
        )
    if available:
        return StatusIndicator(
            "Game Update: Available",
            "A newer game version is available.",
            "ph.arrow-circle-up-fill",
            "#3B9FE8",
        )
    return StatusIndicator(
        "Game Update: Up to date",
        "No newer game version was found.",
        "ph.check-circle-fill",
        "#35C98A",
        visible=False,
    )


def cloud_indicator(status: SyncStatus | None) -> StatusIndicator:
    values = {
        SyncStatus.IN_SYNC: ("Cloud Save: Synced", "Cloud save is up to date.", "ph.cloud-check-fill", "#35C98A"),
        SyncStatus.LOCAL_NEWER: ("Cloud Save: Ready to upload", "Local save is newer than cloud and is ready to upload.", "ph.cloud-arrow-up-fill", "#3B9FE8"),
        SyncStatus.CLOUD_NEWER: ("Cloud Save: Newer in cloud", "A newer save exists in the cloud.", "ph.cloud-arrow-down-fill", "#E5A93D"),
        SyncStatus.CLOUD_ONLY: ("Cloud Save: Available", "A cloud save is available to restore.", "ph.cloud-arrow-down-fill", "#3B9FE8"),
        SyncStatus.CONFLICT: ("Cloud Save: Conflict", "Local and cloud saves conflict and require a choice.", "ph.warning-circle-bold", "#E5A93D"),
        SyncStatus.NO_SAVES: ("Cloud Save: No save detected", "No local or cloud save files were found.", "ph.cloud-slash-bold", "#F05D6C"),
        SyncStatus.CLOUD_AUTH_REQUIRED: (
            "Cloud Save: Setup required",
            "Add the Secret Access Key in Settings → Cloud to check cloud saves.",
            "ph.key-bold",
            "#E5A93D",
        ),
        SyncStatus.CLOUD_OFFLINE: ("Cloud Save: Offline", "Cloud status is unknown because the backend is unavailable.", "ph.cloud-slash-bold", "#6F7682"),
    }
    label, tooltip, icon, color = values.get(
        status,
        ("Cloud Save: Checking", "Cloud save status has not been checked yet.", "ph.cloud-bold", "#6F7682"),
    )
    return StatusIndicator(label, tooltip, icon, color)


__all__ = ["GameStatusState", "StatusIndicator", "cloud_indicator", "update_indicator"]
