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
_LOCAL_IDENTITY_RE = re.compile(r"^local:[a-z0-9]+(?:-[a-z0-9]+)*$", re.IGNORECASE)


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


def is_identity_placeholder_name(value: object) -> bool:
    """Return whether a local profile identity was accidentally shown as a title."""
    return bool(_LOCAL_IDENTITY_RE.fullmatch(clean_game_name(value)))


def meaningful_game_name(value: object, app_id: object = "") -> str:
    """Return a usable title, excluding generated Steam placeholders."""
    name = clean_game_name(value)
    return "" if is_placeholder_game_name(name, app_id) or is_identity_placeholder_name(name) else name


def fallback_game_name(app_id: object, identity: object = "") -> str:
    """Return the final offline-safe title for a cloud-only game."""
    normalized = str(app_id or "").strip()
    if normalized and normalized not in {"0", "None"}:
        return f"Steam App {normalized}"[:MAX_GAME_NAME_LENGTH]
    identity_text = clean_game_name(identity)
    if identity_text.casefold().startswith("local:"):
        slug = identity_text.split(":", 1)[1].replace("-", " ").replace("_", " ")
        while slug.casefold().startswith("local "):
            slug = slug[6:]
        return clean_game_name(slug).title() or "Unnamed Game"
    return identity_text or "Unnamed Game"


def local_profile_identity(name: object) -> str:
    """Build the canonical identity for a non-Steam title or legacy identity."""
    raw_name = clean_game_name(name)
    if raw_name.casefold().startswith("local:"):
        raw_name = raw_name.split(":", 1)[1]
    while raw_name.casefold().startswith("local-"):
        raw_name = raw_name[6:]
    normalized = re.sub(r"[^a-z0-9]+", "-", raw_name.casefold()).strip("-")
    return f"local:{normalized or 'unnamed-game'}"


def preferred_game_name(app_id: object, *values: object) -> str:
    """Choose the first meaningful title, otherwise return an empty string."""
    for value in values:
        candidate = meaningful_game_name(value, app_id)
        if candidate:
            return candidate
    return ""


def display_name_key(value: object, app_id: object = "") -> str:
    """Return a conservative comparison key for matching display aliases.

    This is intentionally a presentation comparison, never a replacement for
    the portable identity.  It is used only when a non-Steam archived
    cloud-only placeholder can be reconciled with a later Steam identity.
    """
    candidate = meaningful_game_name(value, app_id)
    if not candidate and not str(app_id or "").strip():
        candidate = fallback_game_name("", value)
    return clean_game_name(candidate).casefold()


__all__ = [
    "MAX_GAME_NAME_LENGTH",
    "clean_game_name",
    "display_name_key",
    "fallback_game_name",
    "is_placeholder_game_name",
    "is_identity_placeholder_name",
    "local_profile_identity",
    "meaningful_game_name",
    "preferred_game_name",
]
