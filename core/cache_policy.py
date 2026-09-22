"""Central freshness policies for reusable remote resources.

The request manager owns cache mechanics, while this module owns the product
decision of how long each resource may be considered fresh.  Keeping those
decisions named and centralized prevents feature code from quietly drifting
to different TTLs for the same remote resource.

These policies contain no account data, credentials, or resource values.  They
are safe to use from both UI and headless service code.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CachePolicy:
    """Freshness and fallback behavior for one reusable resource class."""

    name: str
    max_age_seconds: float
    stale_while_revalidate: bool = True
    offline_usable: bool = True
    content_type: str = "application/json"


_POLICIES = {
    # Local-library metadata is authoritative in SQLite; these values only
    # govern reusable remote responses and their refresh cadence.
    "private-profile": CachePolicy("private-profile", 5 * 60),
    "public-profile": CachePolicy("public-profile", 5 * 60),
    "profile-social": CachePolicy("profile-social", 60),
    "profile-avatar-catalog": CachePolicy("profile-avatar-catalog", 24 * 60 * 60),
    "profile-avatar": CachePolicy(
        "profile-avatar", 30 * 24 * 60 * 60, content_type="image/png"
    ),
    "profile-artwork": CachePolicy(
        "profile-artwork", 7 * 24 * 60 * 60, content_type="image/jpeg"
    ),
    "profile-background": CachePolicy(
        "profile-background", 7 * 24 * 60 * 60, content_type="image/jpeg"
    ),
    "artwork": CachePolicy("artwork", 30 * 24 * 60 * 60),
    "steam-build": CachePolicy("steam-build", 15 * 60),
    "steam-tags": CachePolicy("steam-tags", 7 * 24 * 60 * 60),
    "steam-app-details": CachePolicy("steam-app-details", 7 * 24 * 60 * 60),
    "achievement-schema": CachePolicy("achievement-schema", 24 * 60 * 60),
    # The icon files remain materialized outputs; the shared value is their
    # validated path map, so its envelope is JSON rather than image bytes.
    "achievement-icons": CachePolicy("achievement-icons", 30 * 24 * 60 * 60),
    "cloud-status": CachePolicy("cloud-status", 2 * 60 * 60),
    "cloud-listing": CachePolicy("cloud-listing", 2 * 60 * 60),
    "library-update": CachePolicy("library-update", 15 * 60),
    "artwork-search": CachePolicy("artwork-search", 24 * 60 * 60),
}


def cache_policy(name: str) -> CachePolicy:
    """Return the named policy, failing loudly for an unclassified resource."""

    try:
        return _POLICIES[str(name).strip()]
    except KeyError as exc:
        raise KeyError(f"No cache policy registered for {name!r}") from exc


def cache_policies() -> tuple[CachePolicy, ...]:
    """Return an immutable snapshot for diagnostics and documentation tools."""

    return tuple(_POLICIES.values())


__all__ = ["CachePolicy", "cache_policies", "cache_policy"]
