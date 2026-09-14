"""Bounded, deterministic display-name rules for library game records.

Steam AppIDs are the portable identity.  A title is presentation metadata and
may be unavailable while a cloud-only record is being materialized.  Keeping
the placeholder test here prevents profile merges and metadata fallbacks from
mistaking ``Steam App <ID>`` for a user-meaningful title.
"""

from __future__ import annotations

import re


MAX_GAME_NAME_LENGTH = 120
_PLACEHOLDER_RE = re.compile(r"^steam\s+app\s+(\d{1,16})$", re.IGNORECASE)


def clean_game_name(value: object, *, limit: int = MAX_GAME_NAME_LENGTH) -> str:
    """Return a bounded, single-line display name."""
    text = " ".join(str(value or "").split())
    return text[:max(1, int(limit))].strip()


def is_placeholder_game_name(value: object, app_id: object = "") -> bool:
    """Return whether ``value`` is the generated Steam fallback title."""
    name = clean_game_name(value)
    match = _PLACEHOLDER_RE.fullmatch(name)
    if not match:
        return False
    requested = str(app_id or "").strip()
    return not requested or requested in {"0", "None"} or match.group(1) == requested


def meaningful_game_name(value: object, app_id: object = "") -> str:
    """Return a usable title, excluding generated Steam placeholders."""
    name = clean_game_name(value)
    return "" if is_placeholder_game_name(name, app_id) else name


def fallback_game_name(app_id: object, identity: object = "") -> str:
    """Return the final offline-safe title for a cloud-only game."""
    normalized = str(app_id or "").strip()
    if normalized and normalized not in {"0", "None"}:
        return f"Steam App {normalized}"[:MAX_GAME_NAME_LENGTH]
    return clean_game_name(identity) or "Unnamed Game"


def preferred_game_name(app_id: object, *values: object) -> str:
    """Choose the first meaningful title, otherwise return an empty string."""
    for value in values:
        candidate = meaningful_game_name(value, app_id)
        if candidate:
            return candidate
    return ""


__all__ = [
    "MAX_GAME_NAME_LENGTH",
    "clean_game_name",
    "fallback_game_name",
    "is_placeholder_game_name",
    "meaningful_game_name",
    "preferred_game_name",
]
