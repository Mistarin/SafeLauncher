"""Application boundary for SafeLauncherCloud account and setup probes.

This service owns account/listing/device/history transport calls used by
dialogs and settings.  ``CloudClient`` remains the HTTP/encryption transport;
UI code receives plain domain dictionaries/results and never constructs the
client directly.

Explicit setup credentials are accepted only for the duration of a probe. They
are never placed in request keys, cache entries, operation records, or logs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable

from core.cloud_backend import (
    CloudBackendError,
    check_backend_health,
    describe_cloud_error,
)
from core.cloud_client import CloudClient
from core.cloud_context import CloudContext
from core.cache_policy import cache_policy
from core.request_contracts import (
    CancellationToken,
    RequestKey,
    RequestPriority,
    RequestSpec,
    RetryPolicy,
    is_transient_error,
)
from core.version import MIN_CONVEX_BACKEND_VERSION


@dataclass(frozen=True, slots=True)
class CloudAccountSnapshot:
    """Combined account overview and cloud-save listing."""

    listing: dict[str, Any]
    overview: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON-safe shared-cache representation."""
        return {
            "listing": dict(self.listing),
            "overview": dict(self.overview),
        }

    @classmethod
    def from_payload(cls, payload: Any) -> "CloudAccountSnapshot":
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, dict):
            raise ValueError("Invalid cloud account snapshot")
        listing = payload.get("listing")
        overview = payload.get("overview")
        if not isinstance(listing, dict) or not isinstance(overview, dict):
            raise ValueError("Invalid cloud account snapshot")
        return cls(dict(listing), dict(overview))


@dataclass(frozen=True, slots=True)
class CloudConnectionProbe:
    """Safe setup result containing no credentials or response payloads."""

    success: bool
    message: str
    version: str = ""
    health: dict[str, Any] | None = None


class CloudAccountService:
    """Own SafeLauncherCloud account, setup, and account-admin operations."""

    def __init__(
        self,
        *,
        client_factory: Callable[..., CloudClient] = CloudClient,
        request_manager=None,
        context_provider: Callable[[int], CloudContext] | None = None,
        read_invalidator: Callable[[int | None], None] | None = None,
    ):
        self._client_factory = client_factory
        self.request_manager = request_manager
        self._context_provider = context_provider or (
            lambda generation: CloudContext.current(generation=generation)
        )
        self._context: CloudContext | None = None
        self._generation = 0
        self._context_lock = RLock()
        self._read_invalidator = read_invalidator

    SNAPSHOT_TTL_SECONDS = cache_policy("cloud-account-snapshot").max_age_seconds

    def set_read_invalidator(
        self,
        callback: Callable[[int | None], None] | None,
    ) -> None:
        """Attach the application-owned read-cache invalidation callback."""
        self._read_invalidator = callback

    # UI-facing cloud administration stays behind this service. These small
    # configuration/matching adapters delegate to the existing domain code so
    # dialogs never import the low-level save engine directly.
    @staticmethod
    def mode() -> str:
        from core.cloud_save_sync import cloud_mode

        return cloud_mode()

    @staticmethod
    def set_mode(mode: str) -> None:
        from core.cloud_save_sync import set_cloud_mode

        set_cloud_mode(mode)

    @staticmethod
    def reset_backend() -> None:
        from core.cloud_save_sync import reset_cloud_backend

        reset_cloud_backend()

    @staticmethod
    def match_game_to_library(name_key: str, display_name: str, all_games: list):
        from core.cloud_save_sync import match_cloud_game_to_library

        return match_cloud_game_to_library(name_key, display_name, all_games)

    def current_context(self) -> CloudContext:
        """Return the current account context and retire changed identities."""
        with self._context_lock:
            candidate = self._context_provider(self._generation)
            if self._context is not None and candidate.cache_identity != self._context.cache_identity:
                self._generation += 1
                candidate = self._context_provider(self._generation)
            elif candidate.generation != self._generation:
                self._generation = int(candidate.generation)
            self._context = candidate
            return candidate

    def invalidate_context(self) -> CloudContext:
        """Retire account-admin requests from the previous cloud identity."""
        with self._context_lock:
            previous = self._context
            self._generation += 1
            self._context = self._context_provider(self._generation)
            current = self._context
        if self.request_manager is not None and previous is not None:
            self.request_manager.invalidate(self.snapshot_key(previous))
        return current

    @staticmethod
    def request_key(
        resource: str,
        identity: str = "",
        variant: str = "v1",
        *,
        site_url: str | None = None,
        secret_key: str | None = None,
    ) -> RequestKey:
        """Build a context-safe key without embedding endpoint or credentials."""
        if site_url is None and secret_key is None:
            return CloudContext.current().request_key(resource, identity, variant)
        from core.cloud_backend import normalize_site_url

        endpoint = normalize_site_url(site_url)
        secret_digest = hashlib.sha256(str(secret_key or "").encode("utf-8")).hexdigest()
        context = hashlib.sha256(
            f"{endpoint}\0{secret_digest}".encode("utf-8")
        ).hexdigest()[:32]
        return RequestKey(resource, f"{context}:{str(identity or '').strip()}", variant)

    def _client(self, site_url: str | None = None, secret_key: str | None = None):
        kwargs = {}
        if site_url is not None:
            kwargs["site_url"] = site_url
        if secret_key is not None:
            kwargs["secret_key"] = secret_key
        return self._client_factory(**kwargs)

    def snapshot(self) -> CloudAccountSnapshot:
        """Load the account overview and save listing using one transport."""
        client = self._client()
        try:
            return CloudAccountSnapshot(
                listing=client.list_games(),
                overview=client.account(),
            )
        finally:
            client.close()

    def snapshot_key(self, context: CloudContext | None = None) -> RequestKey:
        """Return the opaque, context-isolated account snapshot key."""
        context = context or self.current_context()
        return context.request_key("cloud-account-snapshot", "account", "v1")

    @staticmethod
    def _snapshot_validator(value: object) -> bool:
        try:
            CloudAccountSnapshot.from_payload(value)
            return True
        except (TypeError, ValueError, KeyError):
            return False

    @staticmethod
    def _snapshot_encoder(value: object) -> dict[str, Any]:
        return CloudAccountSnapshot.from_payload(value).to_payload()

    @staticmethod
    def _snapshot_decoder(value: object) -> CloudAccountSnapshot:
        return CloudAccountSnapshot.from_payload(value)

    def request_snapshot(
        self,
        *,
        force: bool = False,
        priority: RequestPriority = RequestPriority.NORMAL,
        tag: str = "",
    ):
        """Request one shared account/listing snapshot through the manager.

        All account, quota, device, and compact history projections use this
        resource.  The persisted envelope contains only response data; the
        context key remains opaque and no credentials are retained.
        """
        if self.request_manager is None:
            raise RuntimeError("CloudAccountService has no RequestManager")
        context = self.current_context()
        key = self.snapshot_key(context)
        if force:
            self.request_manager.invalidate(key)
        request_generation = max(
            int(context.generation),
            int(self.request_manager.state(key).generation),
        )

        def load(token: CancellationToken) -> CloudAccountSnapshot:
            token.raise_if_cancelled()
            if not context.remote_requests_allowed:
                # Do not replace a stale authenticated snapshot with an empty
                # setup/offline value. RequestManager will expose stale data
                # when available and otherwise surface this as unavailable.
                raise RuntimeError("Cloud account is not configured")
            client = self._client()
            try:
                listing = client.list_games()
                token.raise_if_cancelled()
                overview = client.account()
                token.raise_if_cancelled()
                if not isinstance(listing, dict) or not isinstance(overview, dict):
                    raise ValueError("Cloud account returned an invalid snapshot")
                return CloudAccountSnapshot(dict(listing), dict(overview))
            finally:
                client.close()

        spec = RequestSpec(
            key,
            load,
            priority=priority,
            retry_policy=RetryPolicy(retry_if=is_transient_error),
            generation=request_generation,
            timeout_seconds=20,
            metadata={
                "cloud_context": context.cache_identity,
                "cloud_generation": request_generation,
                "operation": "cloud-account-snapshot",
                "tag": str(tag),
            },
        )
        if getattr(self.request_manager, "cache", None) is None:
            return self.request_manager.submit(spec)
        return self.request_manager.cached_request(
            spec,
            self.request_manager.cache,
            max_age_seconds=self.SNAPSHOT_TTL_SECONDS,
            cache_validator=self._snapshot_validator,
            cache_encoder=self._snapshot_encoder,
            cache_decoder=self._snapshot_decoder,
            stale_while_revalidate=True,
            content_type="application/json",
        )

    def account(self) -> dict[str, Any]:
        """Load the authenticated account/quota overview."""
        client = self._client()
        try:
            return client.account()
        finally:
            client.close()

    def list_games(self) -> dict[str, Any]:
        """Load the cloud save-generation listing."""
        client = self._client()
        try:
            return client.list_games()
        finally:
            client.close()

    def revoke_device(self, device_id: str) -> bool:
        """Revoke one device through the cloud transport."""
        client = self._client()
        try:
            return bool(client.revoke_device(str(device_id)))
        finally:
            client.close()

    def delete_generation(self, name_key: str, version: int) -> bool:
        """Delete one explicitly selected cloud-save generation."""
        client = self._client()
        try:
            return bool(client.delete_generation(str(name_key), int(version)))
        finally:
            client.close()

    @staticmethod
    def _admin_identity(value: str) -> str:
        """Return an opaque identity for keys without exposing private names."""
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:24]

    def _managed_mutation(
        self,
        resource: str,
        identity: str,
        loader: Callable[[CancellationToken], bool],
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        timeout_seconds: float = 60.0,
        tag: str = "",
    ):
        """Submit one account mutation through the shared request manager."""
        if self.request_manager is None:
            raise RuntimeError("CloudAccountService has no RequestManager")
        context = self.current_context()
        key = context.request_key(resource, identity, "v1")
        return self.request_manager.request(
            key,
            loader,
            priority=priority,
            retry_policy=RetryPolicy(retry_if=is_transient_error),
            generation=context.generation,
            metadata={
                "cloud_context": context.cache_identity,
                "cloud_generation": context.generation,
                "operation": resource,
                "tag": str(tag),
            },
            timeout_seconds=timeout_seconds,
        )

    def request_revoke_device(
        self,
        device_id: str,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        tag: str = "",
    ):
        """Request device revocation with deduplication and cancellation."""
        device_id = str(device_id)
        identity = self._admin_identity(device_id)

        def load(token: CancellationToken) -> bool:
            token.raise_if_cancelled()
            return_value = self.revoke_device(device_id)
            token.raise_if_cancelled()
            return return_value

        handle = self._managed_mutation(
            "cloud-device-revoke",
            identity,
            load,
            priority=priority,
            tag=tag,
        )
        self._attach_read_invalidation(handle)
        return handle

    def request_delete_generation(
        self,
        name_key: str,
        version: int,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        tag: str = "",
    ):
        """Request explicit generation deletion without exposing the game key."""
        name_key = str(name_key)
        version = int(version)
        identity = f"{self._admin_identity(name_key)}:{version}"

        def load(token: CancellationToken) -> bool:
            token.raise_if_cancelled()
            return_value = self.delete_generation(name_key, version)
            token.raise_if_cancelled()
            return return_value

        handle = self._managed_mutation(
            "cloud-generation-delete",
            identity,
            load,
            priority=priority,
            tag=tag,
        )
        self._attach_read_invalidation(handle)
        return handle

    def _attach_read_invalidation(self, handle) -> None:
        callback = self._read_invalidator
        if callback is None:
            return

        def done(future) -> None:
            try:
                result = future.result()
            except Exception:
                return
            status = getattr(getattr(result, "status", None), "value", "")
            if status != "ready" or not result.value:
                return
            try:
                callback(None)
            except Exception:
                # Cache invalidation is a consistency enhancement and must
                # never turn a successful admin mutation into a failed one.
                return

        handle.future.add_done_callback(done)

    @staticmethod
    def health(
        site_url: str | None = None,
        secret_key: str | None = None,
        *,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Probe backend health without creating a long-lived client."""
        return check_backend_health(site_url, secret_key, timeout=timeout)

    def verify_connection(
        self,
        site_url: str,
        secret_key: str = "",
        *,
        timeout: float = 6.0,
    ) -> CloudConnectionProbe:
        """Run the wizard's health and authenticated quota verification."""
        health = self.health(site_url, secret_key, timeout=timeout)
        status = str(health.get("status", "unreachable"))
        version = str(health.get("version") or "1.0.0").strip()

        if status == "legacy":
            return CloudConnectionProbe(
                False,
                "This is a legacy backend: /api/health is missing. "
                "Redeploy the backend with npm install and npx convex deploy --yes first.",
                version,
                health,
            )
        if not bool(health.get("healthy")):
            if status == "unauthorized":
                message = (
                    "The backend requires a valid Secret Access Key. "
                    "Enter the key configured in Convex."
                )
            else:
                message = str(health.get("error") or "Backend health check failed.")
            return CloudConnectionProbe(False, message, version, health)

        if bool(health.get("is_outdated")):
            return CloudConnectionProbe(
                False,
                f"Backend v{version} is outdated; SafeLauncher requires "
                f"v{MIN_CONVEX_BACKEND_VERSION}. Redeploy it with npm install "
                "and npx convex deploy --yes.",
                version,
                health,
            )

        client = self._client(site_url, secret_key)
        try:
            overview = client.account()
        except Exception as exc:
            status_code = getattr(exc, "status_code", 0) or getattr(exc, "status", 0)
            if (
                status_code in (401, 403)
                or (
                    isinstance(exc, CloudBackendError)
                    and getattr(exc, "code", "") == "auth"
                )
            ):
                message = (
                    "The backend requires a valid Secret Access Key. "
                    "Enter the key configured in Convex."
                )
            else:
                message = f"Backend is reachable, but account verification failed: {describe_cloud_error(exc)}"
            return CloudConnectionProbe(False, message, version, health)
        finally:
            # The client is intentionally short-lived for setup probes.
            client.close()

        quota_mb = float(overview.get("quotaBytes", 0) or 0) / (1024 * 1024)
        return CloudConnectionProbe(
            True,
            f"Connected! Available quota: {quota_mb:.0f} MB",
            version,
            health,
        )


__all__ = ["CloudAccountService", "CloudAccountSnapshot", "CloudConnectionProbe"]
