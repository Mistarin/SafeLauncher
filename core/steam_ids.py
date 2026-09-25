"""Small, dependency-free helpers for validating Steam AppIDs."""

from __future__ import annotations


def normalize_steam_app_id(value) -> str:
    """Return a canonical numeric Steam AppID, or ``""`` when unset/invalid.

    AppIDs are decimal integers. Treating values such as ``None``, ``0``,
    ``"Not set"`` and arbitrary text as an absent ID prevents malformed URLs
    from reaching Steam services and keeps all callers on the same identity.
    """
    text = str(value or "").strip()
    if not text or text.casefold() in {"0", "none", "null", "not set"}:
        return ""
    if not text.isdigit():
        return ""
    try:
        app_id = int(text, 10)
    except (TypeError, ValueError, OverflowError):
        return ""
    return str(app_id) if app_id > 0 else ""


__all__ = ["normalize_steam_app_id"]
