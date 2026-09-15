"""Transport-independent save-history normalization and presentation helpers.

Cloud history contains two different kinds of records: retained remote
generations and local safety forks.  Keeping their ordering and display rules
here prevents each dialog from inventing a slightly different interpretation
of timestamps, provenance, or duplicate records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Iterable


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_MAX_DEVICE_NAME = 80


def _timestamp(value: Any) -> float:
    """Return a unix timestamp, accepting seconds or millisecond wire values."""
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if value > 100_000_000_000:
        value /= 1000.0
    return max(0.0, value)


def _first_timestamp(entry: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = _timestamp(entry.get(key))
        if value:
            return value
    return 0.0


def _safe_device_name(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("deviceName") or value.get("label")
    value = _CONTROL_CHARS.sub(" ", str(value or ""))
    return " ".join(value.split()).strip()[:_MAX_DEVICE_NAME]


def _first_name(entry: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _safe_device_name(entry.get(key))
        if value:
            return value
    return ""


def history_device_metadata(entry: Any) -> dict[str, str]:
    """Normalize device names without ever using opaque IDs as labels."""
    if not isinstance(entry, dict):
        return {"created": "", "uploaded": ""}
    created = _first_name(
        entry, "created_device_name", "createdDeviceName", "created_device",
        "createdDevice", "source_device_name", "sourceDeviceName",
    )
    uploaded = _first_name(
        entry, "uploaded_device_name", "uploadedDeviceName", "uploaded_device",
        "uploadedDevice",
    )
    # Compatibility responses may only have one device field.
    single = _first_name(entry, "device_name", "deviceName", "device")
    return {"created": created or single, "uploaded": uploaded or single}


def history_device_text(entry: Any) -> str:
    """Return the compact device provenance text for a history row."""
    if isinstance(entry, dict) and entry.get("source") == "fork":
        return "This PC (local backup)"
    devices = history_device_metadata(entry)
    created, uploaded = devices["created"], devices["uploaded"]
    if created and uploaded and created.casefold() != uploaded.casefold():
        return f"Created on {created} · uploaded from {uploaded}"
    if created:
        return f"Created and uploaded on {created}"
    if uploaded:
        return f"Uploaded from {uploaded}"
    return "Unavailable (older cloud generation)"


@dataclass(frozen=True)
class HistoryEntry:
    """Normalized, display-safe representation of one history record."""

    raw: dict[str, Any]
    source: str
    identity: str
    version: str
    event_at: float
    created_at: float
    uploaded_at: float
    size_bytes: int
    file_count: int
    is_active: bool
    has_conflict: bool
    device_text: str
    title: str

    @property
    def source_label(self) -> str:
        return "Cloud" if self.source == "cloud" else "This PC"

    @property
    def date_key(self) -> str:
        if not self.event_at:
            return "undated"
        return datetime.fromtimestamp(self.event_at).strftime("%Y-%m-%d")

    @property
    def date_label(self) -> str:
        if not self.event_at:
            return "Date unavailable"
        return datetime.fromtimestamp(self.event_at).strftime("%A, %d %B %Y")

    @property
    def time_label(self) -> str:
        if not self.event_at:
            return "Time unavailable"
        return datetime.fromtimestamp(self.event_at).strftime("%H:%M")

    @property
    def version_label(self) -> str:
        if self.source == "cloud":
            return f"Generation v{self.version}"
        return "Local safety backup"


def _entry_identity(entry: dict[str, Any], source: str, event_at: float) -> str:
    version = entry.get("version")
    if source == "cloud" and version is not None:
        return f"cloud:version:{version}"
    path = str(entry.get("path") or "")
    # Fork names are stable across directory scans.  If a legacy response has
    # no path, its timestamp/size pair still gives a deterministic fallback.
    if path:
        return f"fork:path:{path}"
    return f"{source}:content:{event_at:.3f}:{int(entry.get('size_bytes', entry.get('sizeBytes', 0)) or 0)}"


def normalize_history_entries(entries: Iterable[Any] | None) -> list[HistoryEntry]:
    """Normalize, deduplicate, and newest-first sort history records.

    Upload/creation metadata is preferred over content mtime because the
    latter describes the save contents, not when the retained generation was
    created.  All values retained in ``raw`` come from the existing history
    response and are only used for restore operations; callers should display
    the normalized fields instead.
    """
    normalized: list[HistoryEntry] = []
    seen: set[str] = set()
    for candidate in entries or ():
        if not isinstance(candidate, dict):
            continue
        raw = dict(candidate)
        source = "cloud" if str(raw.get("source") or "cloud").casefold() == "cloud" else "fork"
        created_at = _first_timestamp(raw, "created_at", "createdAt")
        uploaded_at = _first_timestamp(raw, "uploaded_at", "uploadedAt", "updatedAt")
        content_at = _first_timestamp(raw, "sourceMaxMtime", "source_max_mtime", "mtime")
        event_at = uploaded_at or created_at or content_at
        identity = _entry_identity(raw, source, event_at)
        if identity in seen:
            continue
        seen.add(identity)
        version_value = raw.get("version")
        version = str(version_value) if version_value is not None else ""
        try:
            size_bytes = int(raw.get("size_bytes", raw.get("sizeBytes", 0)) or 0)
        except (TypeError, ValueError):
            size_bytes = 0
        try:
            file_count = int(raw.get("file_count", raw.get("fileCount", 0)) or 0)
        except (TypeError, ValueError):
            file_count = 0
        title = str(raw.get("display_name") or raw.get("displayName") or (
            f"Cloud Generation v{version}" if source == "cloud" else "Local safety backup"
        ))
        normalized.append(HistoryEntry(
            raw=raw,
            source=source,
            identity=identity,
            version=version,
            event_at=event_at,
            created_at=created_at,
            uploaded_at=uploaded_at,
            size_bytes=max(0, size_bytes),
            file_count=max(0, file_count),
            is_active=bool(raw.get("is_active", raw.get("isActive", False))),
            has_conflict=bool(raw.get("has_conflict", raw.get("hasConflict", False))),
            device_text=history_device_text(raw),
            title=title,
        ))
    normalized.sort(key=lambda item: (
        0 if item.event_at else 1,
        -item.event_at,
        0 if item.source == "cloud" else 1,
        item.version,
        item.identity,
    ))
    return normalized


__all__ = [
    "HistoryEntry",
    "history_device_metadata",
    "history_device_text",
    "normalize_history_entries",
]
