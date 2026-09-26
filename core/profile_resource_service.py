"""Application/transport boundary for profile resources.

The profile page owns presentation only.  This module keeps profile-service
HTTP, bounded image downloads, and the small owner/social workflows out of
Qt widgets.  RequestManager remains responsible for scheduling and caching;
callers use these methods as synchronous loaders from a managed request (or
as compatibility workers while older integrations are being retired).
"""

from __future__ import annotations

import hashlib
from typing import Any

import requests

from core.profile_models import steam_hero_urls
from core.profile_service import (
    ProfileServiceClient,
    ProfileServiceError,
    get_profile_service_url,
)
from core.request_contracts import (
    RequestKey,
    RequestPriority,
    RequestSpec,
)


class ProfileResourceService:
    """Profile resource and operation service shared by profile UI paths."""

    def __init__(self, auth_session=None, *, client_factory=ProfileServiceClient):
        self.auth_session = auth_session
        self._client_factory = client_factory
        self._context_generation = 0

    @property
    def context_generation(self) -> int:
        return self._context_generation

    def invalidate_context(self) -> int:
        """Advance the profile context after account/backend/view identity changes."""
        self._context_generation += 1
        return self._context_generation

    def request_key(
        self,
        resource: str,
        identity: str,
        variant: str = "",
        service_url: str | None = None,
    ) -> RequestKey:
        """Build a context-isolated profile resource key.

        Endpoint text is never placed in a key.  The endpoint fingerprint
        keeps local development/profile-service overrides from sharing cached
        data with another deployment.
        """
        endpoint = self.endpoint_fingerprint(service_url)
        return RequestKey(resource, f"{endpoint}:{str(identity or '').strip()}", variant)

    def request_spec(
        self,
        key: RequestKey,
        loader,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        timeout_seconds: float | None = 20,
        tag: str = "",
    ) -> RequestSpec:
        """Create a generation-bound request without owning scheduling."""
        return RequestSpec(
            key,
            loader,
            priority=priority,
            generation=self._context_generation,
            timeout_seconds=timeout_seconds,
            metadata={
                "profile_context": self.endpoint_fingerprint(),
                "profile_generation": self._context_generation,
                "tag": str(tag),
            },
        )

    @staticmethod
    def endpoint_fingerprint(service_url: str | None = None) -> str:
        """Return a non-sensitive endpoint identity suitable for request keys."""
        value = str(service_url or get_profile_service_url()).strip().rstrip("/")
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    def _client(self, service_url: str | None = None):
        return self._client_factory(
            service_url or get_profile_service_url(),
            auth_session=self.auth_session,
        )

    def configured(self, service_url: str | None = None) -> bool:
        """Check endpoint configuration without exposing a client to callers."""
        with self._client(service_url) as client:
            return bool(client.configured)

    def fetch_public(self, handle: str, service_url: str | None = None) -> dict[str, Any]:
        with self._client(service_url) as client:
            return client.fetch(handle)

    def current_profile(self, service_url: str | None = None) -> dict[str, Any] | None:
        with self._client(service_url) as client:
            return client.current_profile()

    def check_handle_availability(self, handle: str, service_url: str | None = None) -> bool:
        with self._client(service_url) as client:
            return client.check_handle_availability(handle)

    def get_social(self, handle: str | None = None, service_url: str | None = None) -> dict[str, Any]:
        with self._client(service_url) as client:
            return client.get_social(handle)

    def get_owner_social(self, service_url: str | None = None) -> dict[str, Any]:
        """Return authoritative owner profile state together with its friends.

        The local ``profile_published`` setting is only a UI cache. Resolve the
        Auth0-owned profile first so stale private-cloud metadata cannot make
        Friends claim that an actually published profile does not exist.
        """
        with self._client(service_url) as client:
            owner = client.current_profile()
            if owner is None:
                raise ProfileServiceError(
                    "Create a public profile first.",
                    "profile_required",
                )
            handle = str(owner.get("handle", "") or "").strip().lower()
            if not handle:
                raise ProfileServiceError("The public profile has no username yet.", "invalid_profile")
            return {"profile": owner, "social": client.get_social(handle)}

    def social_operation(
        self,
        operation: str,
        owner_handle: str,
        *,
        target_handle: str = "",
        request_id: str = "",
        action: str = "",
        service_url: str | None = None,
    ) -> dict[str, Any]:
        """Execute one explicitly named social mutation.

        Using an operation name instead of passing a client into the widget
        prevents UI code from growing another transport abstraction.
        """
        with self._client(service_url) as client:
            if operation == "send_friend_request":
                return client.send_friend_request(owner_handle, target_handle)
            if operation == "respond_friend_request":
                return client.respond_friend_request(owner_handle, request_id, action)
            if operation == "remove_friend":
                return client.remove_friend(owner_handle, target_handle)
            if operation == "block_user":
                return client.block_user(owner_handle, target_handle)
            if operation == "unblock_user":
                return client.unblock_user(owner_handle, target_handle)
        raise ProfileServiceError("The social operation is invalid.", "invalid_operation")

    def list_avatar_catalog(self, service_url: str | None = None) -> list[dict[str, Any]]:
        with self._client(service_url) as client:
            return client.list_avatar_catalog()

    def fetch_avatar_batch(
        self,
        avatar_ids: Any,
        service_url: str | None = None,
        *,
        max_items: int = 128,
        max_total_bytes: int = 12 * 1024 * 1024,
    ) -> dict[str, bytes]:
        with self._client(service_url) as client:
            return client.fetch_avatar_batch(
                avatar_ids,
                max_items=max_items,
                max_total_bytes=max_total_bytes,
            )

    @staticmethod
    def download_image(
        url: str,
        timeout: tuple[int, int] = (5, 12),
        *,
        max_bytes: int = 4 * 1024 * 1024,
    ) -> bytes:
        """Download one bounded image from an already validated public URL."""
        response = None
        try:
            response = requests.get(
                url,
                headers={"Accept": "image/jpeg,image/*;q=0.8", "User-Agent": "SafeLauncher/1"},
                timeout=timeout,
                stream=True,
            )
            if response.status_code != 200:
                return b""
            content_type = response.headers.get("Content-Type", "").lower()
            if not content_type.startswith("image/"):
                return b""
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                return b""
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    return b""
                chunks.append(chunk)
            return b"".join(chunks)
        except (requests.RequestException, TypeError, ValueError, OverflowError):
            return b""
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    @classmethod
    def download_steam_artwork(cls, app_id: str, token=None, timeout: tuple[int, int] = (2, 6)) -> bytes:
        for candidate in steam_hero_urls(app_id):
            if token is not None:
                token.raise_if_cancelled()
            data = cls.download_image(candidate, timeout, max_bytes=4 * 1024 * 1024)
            if data:
                return data
        return b""

    def reconcile_authenticated_profile(
        self,
        handle: str,
        legacy_token: str = "",
        service_url: str | None = None,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Load the identity-owned profile and perform one legacy claim if needed."""
        with self._client(service_url) as client:
            remote = client.current_profile()
            if remote is not None:
                return remote, False
            token = str(legacy_token or "").strip()
            if not handle or not token:
                return None, False
            try:
                client.claim_legacy_profile(handle, token)
            except ProfileServiceError as exc:
                if exc.code not in {"not_found", "unauthorized", "claimed", "identity_has_profile"}:
                    raise
                return None, False
            return client.current_profile(), True

    def publish(
        self,
        document: dict[str, Any],
        handle: str,
        *,
        legacy_token: str = "",
        service_url: str | None = None,
    ) -> dict[str, Any]:
        """Create/update the owner profile with revision conflict recovery."""
        with self._client(service_url) as client:
            def write_with_schema_compatibility(writer):
                """Prefer schema 4, with one safe retry for older deployments."""
                try:
                    return writer(document)
                except ProfileServiceError as exc:
                    # The public gateway may lag the desktop release during a
                    # rolling deployment. Only downgrade for the exact schema
                    # validation response; never hide handle, avatar, or
                    # arbitrary profile validation failures.
                    if exc.code != "invalid_profile" or "schema" not in str(exc).casefold():
                        raise
                    compatible = dict(document)
                    compatible["schema_version"] = 3
                    response = writer(compatible)
                    document.clear()
                    document.update(compatible)
                    return response

            remote = client.current_profile()
            claimed = False
            token = str(legacy_token or "").strip()
            if remote is None and token:
                try:
                    client.claim_legacy_profile(handle, token)
                except ProfileServiceError as exc:
                    if exc.code not in {"not_found", "unauthorized", "claimed", "identity_has_profile"}:
                        raise
                else:
                    claimed = True
                remote = client.current_profile()

            if remote is None:
                try:
                    response = write_with_schema_compatibility(client.create_profile)
                except ProfileServiceError as exc:
                    if exc.code not in {"exists", "profile_exists", "profile_exists_for_identity"}:
                        raise
                    remote = client.current_profile()
                    if remote is None:
                        # The identity lookup above is authoritative for an
                        # owner profile. If it is still empty after a create
                        # conflict, the requested handle belongs to another
                        # public profile (or the deployment has a stale
                        # owner index). Do not expose an ambiguous raw 409 to
                        # the UI: retain the original code in ``extra`` while
                        # giving callers a stable, recoverable category.
                        if exc.code in {"exists", "profile_exists"}:
                            raise ProfileServiceError(
                                "That username is already in use. Choose a different handle (username) and try again.",
                                "handle_taken",
                                exc.status or 409,
                                {"original_code": exc.code},
                            ) from exc
                        raise
                    response = write_with_schema_compatibility(
                        lambda payload: client.update_profile(payload, int(remote.get("revision", 0) or 0))
                    )
            else:
                revision = int(remote.get("revision", 0) or 0)
                try:
                    response = write_with_schema_compatibility(
                        lambda payload: client.update_profile(payload, revision)
                    )
                except ProfileServiceError as exc:
                    if exc.code != "conflict":
                        raise
                    fresh = client.current_profile()
                    if fresh is None:
                        raise
                    response = write_with_schema_compatibility(
                        lambda payload: client.update_profile(payload, int(fresh.get("revision", 0) or 0))
                    )
            if isinstance(response, dict) and response.get("handle"):
                document["handle"] = str(response["handle"])
            return {"response": response, "document": document, "legacy_claimed": claimed}

    def unpublish(
        self,
        handle: str,
        *,
        legacy_token: str = "",
        service_url: str | None = None,
    ) -> dict[str, Any]:
        with self._client(service_url) as client:
            claimed = False
            if client.current_profile() is None and legacy_token and handle:
                try:
                    client.claim_legacy_profile(handle, legacy_token)
                except ProfileServiceError as exc:
                    if exc.code not in {"not_found", "unauthorized", "claimed", "identity_has_profile"}:
                        raise
                else:
                    claimed = True
            return {"response": client.delete_profile(), "legacy_claimed": claimed}


__all__ = ["ProfileResourceService"]
