"""Transport-independent helpers for presenting save history provenance."""

from __future__ import annotations

import re
from typing import Any


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_MAX_DEVICE_NAME = 80


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


__all__ = ["history_device_metadata", "history_device_text"]
