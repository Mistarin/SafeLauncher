"""Canonical cloud-save identity and listing boundary.

All save mutations and reads use this small repository instead of inventing a
key locally.  The transport remains in ``cloud_backend``; this module owns
context isolation, TTL policy, legacy-key reuse, and in-flight listing
coalescing.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from core.cache_policy import cache_policy
from core.request_contracts import RequestKey


@dataclass(frozen=True, slots=True)
class CloudGameRef:
    """Resolved cloud identity shared by status, history, and mutations."""

    name_key: str
    display_name: str
    context_identity: str
    listing_metadata: dict
    requested_name: str

    @property
    def legacy_name_key(self) -> str:
        from core.cloud_backend import legacy_name_key

        return legacy_name_key(self.requested_name)


class CloudSaveRepository:
    """One process-wide listing/resolver for private cloud saves."""

    _instance = None
    _instance_lock = threading.RLock()

    def __init__(self) -> None:
        self._listing = None
        self._stored_at = 0.0
        self._context = ""
        self._epoch = 0
        self._listing_lock = threading.RLock()
        self._fetch_lock = threading.Lock()
        self._resource_cache = None

    @classmethod
    def shared(cls) -> "CloudSaveRepository":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def configure_cache(self, resource_cache) -> None:
        """Attach the application's persistent manager cache when available."""
        if resource_cache is None:
            return
        with self._listing_lock:
            self._resource_cache = resource_cache

    def _context_identity(self) -> str:
        from core.cloud_save_sync import cloud_context_fingerprint

        return cloud_context_fingerprint()

    def listing(self, *, force_refresh: bool = False) -> dict:
        """Return the named-policy listing, coalescing concurrent fetches."""
        from core.cloud_save_sync import (
            _backend,
            _cloud_auth_configured,
            backend_active,
        )
        if not backend_active():
            return {"games": []}
        if not _cloud_auth_configured():
            from core.cloud_backend import CloudBackendError

            raise CloudBackendError("Cloud authentication is not configured.", "auth_required", 401)
        ttl = cache_policy("cloud-listing").max_age_seconds
        context = self._context_identity()
        now = time.monotonic()
        # AccountService is the manager-backed owner of the authenticated
        # listing.  Reuse its persisted snapshot when it is still within the
        # same named listing policy, so low-level save workers do not create a
        # second persistent listing resource.
        with self._listing_lock:
            resource_cache = self._resource_cache
        if not force_refresh and resource_cache is not None:
            try:
                key = RequestKey("cloud-account-snapshot", f"{context}:account", "v1")
                cached = resource_cache.get(key)
                value = cached.value if cached is not None else None
                listing = getattr(value, "listing", None)
                if listing is None and isinstance(value, dict):
                    listing = value.get("listing")
                if isinstance(listing, dict) and cached.is_fresh(ttl):
                    self.adopt_listing(listing)
                    return listing
            except Exception:
                pass
        with self._listing_lock:
            if (
                not force_refresh
                and self._listing is not None
                and self._context == context
                and now - self._stored_at < ttl
            ):
                return self._listing
            request_epoch = self._epoch
        with self._fetch_lock:
            context = self._context_identity()
            with self._listing_lock:
                if (
                    not force_refresh
                    and self._listing is not None
                    and self._context == context
                    and time.monotonic() - self._stored_at < ttl
                ):
                    return self._listing
                request_epoch = self._epoch
            data = _backend().list_games()
            with self._listing_lock:
                if request_epoch == self._epoch and context == self._context_identity():
                    self._listing = data
                    self._stored_at = time.monotonic()
                    self._context = context
            return data

    def invalidate(self) -> None:
        from core.cloud_save_sync import _backend_singleton

        with self._listing_lock:
            self._epoch += 1
            self._listing = None
            self._stored_at = 0.0
            self._context = ""
        if _backend_singleton is not None:
            _backend_singleton.invalidate_key_cache()

    def adopt_listing(self, listing: dict) -> dict:
        """Publish a manager-backed listing into the canonical resolver."""
        if not isinstance(listing, dict):
            return listing
        with self._listing_lock:
            self._listing = listing
            self._stored_at = time.monotonic()
            self._context = self._context_identity()
        return listing

    @staticmethod
    def _slug(value: str) -> str:
        from core.cloud_save_sync import _clean_game_slug

        return _clean_game_slug(str(value or ""))

    def resolve(self, game_name: str, *, force_refresh: bool = False, listing: dict | None = None) -> CloudGameRef:
        from core.cloud_backend import legacy_name_key, normalize_name_key

        requested = str(game_name or "")
        canonical = normalize_name_key(requested)
        context = self._context_identity()
        if not self._is_remote_active():
            return CloudGameRef(canonical, requested, context, {}, requested)
        data = listing if listing is not None else self.listing(force_refresh=force_refresh)
        games = data.get("games", []) if isinstance(data, dict) else []
        selected = next((item for item in games if item.get("nameKey") == canonical), None)
        legacy = legacy_name_key(requested)
        if selected is None and legacy and legacy != canonical:
            selected = next((item for item in games if item.get("nameKey") == legacy), None)
        if selected is None:
            requested_lower = requested.strip().lower()
            selected = next(
                (
                    item
                    for item in games
                    if str(item.get("displayName", "")).strip().lower() == requested_lower
                    or str(item.get("nameKey", "")).strip().lower() == canonical.lower()
                ),
                None,
            )
        if selected is None:
            target = self._slug(requested)
            selected = next(
                (
                    item
                    for item in games
                    if target and target in {
                        self._slug(item.get("nameKey", "")),
                        self._slug(item.get("displayName", "")),
                    }
                ),
                None,
            )
        selected = selected or {}
        return CloudGameRef(
            str(selected.get("nameKey") or canonical),
            str(selected.get("displayName") or requested),
            context,
            dict(selected),
            requested,
        )

    def _is_remote_active(self) -> bool:
        from core.cloud_save_sync import backend_active

        return bool(backend_active())


__all__ = ["CloudGameRef", "CloudSaveRepository"]
