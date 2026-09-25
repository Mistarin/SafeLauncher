"""Canonical per-game status state and presentation metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.cloud_models import SyncStatus
from core.date_formatting import format_datetime_timestamp


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
    # ``live`` means the comparison was completed against Steam now;
    # ``cached`` means it is the last known comparison and may be stale;
    # ``offline`` means no comparison is available in the current session.
    update_source: str = "unknown"

    @property
    def update_indicator(self) -> StatusIndicator:
        return update_indicator(
            self.update_available,
            self.update_error,
            source=self.update_source,
            checked_at=self.update_checked_at,
        )

    @property
    def cloud_indicator(self) -> StatusIndicator:
        return cloud_indicator(self.cloud_status)


def update_indicator(
    available: bool,
    error: str = "",
    *,
    source: str = "live",
    checked_at: float = 0.0,
) -> StatusIndicator:
    source = str(source or "live").strip().lower()
    if error:
        if source == "offline":
            return StatusIndicator(
                "Game Update: Unavailable offline",
                "No online game-version check is available while offline.",
                "ph.wifi-slash-bold",
                "#6F7682",
                visible=False,
            )
        return StatusIndicator(
            "Game Update: Unavailable",
            f"Game update check failed: {error}",
            "ph.warning-circle-bold",
            "#E5A93D",
        )
    if available:
        if source == "cached":
            checked_text = format_datetime_timestamp(
                int(checked_at or 0), "%H:%M", fallback="an unknown time"
            )
            return StatusIndicator(
                "Game Update: Available · cached",
                "A newer game version was seen previously. "
                f"Last checked online: {checked_text}. Reconnect to verify the current version.",
                "ph.arrow-circle-up-fill",
                "#8493A7",
            )
        return StatusIndicator(
            "Game Update: Available",
            "A newer game version is available.",
            "ph.arrow-circle-up-fill",
            "#3B9FE8",
        )
    if source == "cached":
        checked_text = format_datetime_timestamp(
            int(checked_at or 0), "%H:%M", fallback="an unknown time"
        )
        return StatusIndicator(
            "Game Update: Up to date · cached",
            "No newer version was found in the last online comparison. "
            f"Last checked online: {checked_text}. Reconnect to verify.",
            "ph.check-circle-fill",
            "#8493A7",
            visible=False,
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
        SyncStatus.SYNCING: ("Cloud Save: Syncing…", "SafeLauncher is synchronizing the newest safe save automatically.", "ph.arrows-clockwise-bold", "#3B9FE8"),
        SyncStatus.LOCAL_NEWER: ("Cloud Save: Upload pending", "Local save is newer than cloud; SafeLauncher will upload it automatically when safe.", "ph.cloud-arrow-up-fill", "#3B9FE8"),
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
        SyncStatus.CLOUD_OFFLINE: ("Cloud Save: Offline", "Offline mode is enabled; cloud status will be checked again when online.", "ph.cloud-slash-bold", "#6F7682"),
        SyncStatus.CLOUD_UNAVAILABLE: ("Cloud Save: Unavailable", "Online mode is enabled, but the cloud backend could not be reached.", "ph.cloud-slash-bold", "#E5A93D"),
    }
    label, tooltip, icon, color = values.get(
        status,
        ("Cloud Save: Checking", "Cloud save status has not been checked yet.", "ph.cloud-bold", "#6F7682"),
    )
    return StatusIndicator(label, tooltip, icon, color)


def is_cloud_conflict(status: Any) -> bool:
    """Return whether a cached/managed status requires conflict resolution."""
    if isinstance(status, (tuple, list)):
        status = status[0] if status else None
    if isinstance(status, GameStatusState):
        status = status.cloud_status
    value = getattr(status, "value", status)
    return str(value or "").strip().lower() in {"conflict", "cloud_conflict", "revision_conflict"}


__all__ = [
    "GameStatusState", "StatusIndicator", "cloud_indicator", "is_cloud_conflict",
    "update_indicator",
]
