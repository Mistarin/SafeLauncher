"""Context and identity contracts for private cloud resources.

The context deliberately stores only an opaque fingerprint of the cloud
configuration. Raw site secrets never enter request keys, cache keys, logs,
or resource state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.network_policy import automatic_network_allowed
from core.request_contracts import RequestKey


@dataclass(frozen=True, slots=True)
class CloudContext:
    """Immutable identity and availability snapshot for private cloud work."""

    mode: str
    endpoint: str
    fingerprint: str
    generation: int = 0
    network_allowed: bool = True
    backend_active: bool = False
    authentication_configured: bool = False

    @classmethod
    def current(cls, settings: Any | None = None, *, generation: int = 0) -> "CloudContext":
        """Read the current cloud configuration without exposing credentials."""
        # Imports stay inside the factory so the context contract does not
        # create a module cycle with the legacy cloud-save dispatch module.
        from core.cloud_save_sync import (
            backend_active,
            cloud_context_fingerprint,
            cloud_mode,
            _cloud_auth_configured,
        )
        from core.cloud_backend import get_site_url

        return cls(
            mode=str(cloud_mode()),
            endpoint=str(get_site_url() or "").strip().rstrip("/"),
            fingerprint=str(cloud_context_fingerprint()),
            generation=int(generation),
            network_allowed=bool(automatic_network_allowed(settings)),
            backend_active=bool(backend_active()),
            authentication_configured=bool(_cloud_auth_configured()),
        )

    @property
    def cache_identity(self) -> str:
        """Opaque identity safe to use in request/cache keys."""
        return self.fingerprint

    @property
    def remote_requests_allowed(self) -> bool:
        return self.network_allowed and self.backend_active and self.authentication_configured

    def request_key(self, resource: str, identity: str, variant: str = "") -> RequestKey:
        """Build a context-isolated request key without raw credentials."""
        return RequestKey(
            resource,
            f"{self.cache_identity}:{str(identity).strip()}",
            variant,
        )


__all__ = ["CloudContext"]
