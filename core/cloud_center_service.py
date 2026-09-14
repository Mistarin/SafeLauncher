"""Application facade for the private-cloud management experience.

The facade deliberately composes the existing cloud services instead of
creating a second transport or save implementation.  It returns compact,
serializable overview data for the UI and submits all work through the shared
request manager.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from core.cloud_account_service import CloudAccountService
from core.cloud_context import CloudContext
from core.cloud_metadata_service import CloudMetadataService
from core.cloud_status_service import CloudStatusService
from core.cloud_sync_queue import PendingCloudSyncQueue
from core.request_contracts import (
    CancellationToken,
    RequestKey,
    RequestPriority,
    RetryPolicy,
    ResourceStatus,
    is_transient_error,
)


@dataclass(frozen=True, slots=True)
class CloudDeviceSummary:
    name: str
    platform: str = ""
    online: bool = False
    last_seen_at: float = 0.0


class CloudConnectionState(StrEnum):
    """Stable connection vocabulary shared by Cloud Center and its clients."""

    IDLE = "idle"
    READY = "ready"
    SYNCING = "syncing"
    STALE = "stale"
    SETUP_REQUIRED = "setup_required"
    LOCAL = "local"
    OFFLINE = "offline"
    UNAVAILABLE = "unavailable"
    AUTHENTICATION_REQUIRED = "authentication_required"
    CONFLICT = "conflict"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CloudSyncSummary:
    pending_changes: int = 0
    conflict_count: int = 0
    last_sync_at: float = 0.0


@dataclass(frozen=True, slots=True)
class CloudQuotaSummary:
    used_bytes: int = 0
    total_bytes: int = 0


@dataclass(frozen=True, slots=True)
class CloudConflictSummary:
    count: int = 0
    game_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class CloudOverview:
    """Redacted cloud-management data suitable for UI presentation/cache."""

    connection: str
    message: str
    account_label: str = ""
    endpoint: str = ""
    quota_used: int = 0
    quota_total: int = 0
    game_count: int = 0
    device_count: int = 0
    online_device_count: int = 0
    pending_changes: int = 0
    conflict_count: int = 0
    devices: tuple[CloudDeviceSummary, ...] = ()
    last_sync_at: float = 0.0

    @property
    def connection_state(self) -> CloudConnectionState:
        try:
            return CloudConnectionState(self.connection)
        except ValueError:
            return CloudConnectionState.ERROR

    @property
    def sync_summary(self) -> CloudSyncSummary:
        return CloudSyncSummary(self.pending_changes, self.conflict_count, self.last_sync_at)

    @property
    def quota_summary(self) -> CloudQuotaSummary:
        return CloudQuotaSummary(self.quota_used, self.quota_total)

    @property
    def conflict_summary(self) -> CloudConflictSummary:
        return CloudConflictSummary(self.conflict_count)

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-safe payload; never include credentials or saves."""
        return {
            "connection": self.connection,
            "message": self.message,
            "account_label": self.account_label,
            "endpoint": self.endpoint,
            "quota_used": int(self.quota_used),
            "quota_total": int(self.quota_total),
            "game_count": int(self.game_count),
            "device_count": int(self.device_count),
            "online_device_count": int(self.online_device_count),
            "pending_changes": int(self.pending_changes),
            "conflict_count": int(self.conflict_count),
            "last_sync_at": float(self.last_sync_at),
            "devices": [
                {
                    "name": device.name,
                    "platform": device.platform,
                    "online": bool(device.online),
                    "last_seen_at": float(device.last_seen_at),
                }
                for device in self.devices
            ],
        }

    @classmethod
    def from_payload(cls, payload: Any) -> "CloudOverview":
        if isinstance(payload, cls):
            return payload
        payload = payload if isinstance(payload, dict) else {}
        devices = tuple(
            CloudDeviceSummary(
                str(item.get("name") or "Device"),
                str(item.get("platform") or ""),
                bool(item.get("online")),
                float(item.get("last_seen_at", 0) or 0),
            )
            for item in payload.get("devices", ())
            if isinstance(item, dict)
        )
        return cls(
            connection=str(payload.get("connection") or "error"),
            message=str(payload.get("message") or "Cloud status unavailable."),
            account_label=str(payload.get("account_label") or ""),
            endpoint=str(payload.get("endpoint") or ""),
            quota_used=int(payload.get("quota_used", 0) or 0),
            quota_total=int(payload.get("quota_total", 0) or 0),
            game_count=int(payload.get("game_count", 0) or 0),
            device_count=int(payload.get("device_count", len(devices)) or 0),
            online_device_count=int(payload.get("online_device_count", 0) or 0),
            pending_changes=int(payload.get("pending_changes", 0) or 0),
            conflict_count=int(payload.get("conflict_count", 0) or 0),
            devices=devices,
            last_sync_at=float(payload.get("last_sync_at", 0) or 0),
        )


class CloudCenterService:
    """Single application entry point for private-cloud overview and sync."""

    CACHE_TTL_SECONDS = 30.0

    def __init__(
        self,
        request_manager,
        *,
        account_service: CloudAccountService | None = None,
        status_service: CloudStatusService | None = None,
        metadata_service: CloudMetadataService | None = None,
        settings=None,
    ) -> None:
        self.request_manager = request_manager
        self.account_service = account_service or CloudAccountService(
            request_manager=request_manager
        )
        self.status_service = status_service
        self.metadata_service = metadata_service
        self.settings = settings

    def current_context(self) -> CloudContext:
        if self.status_service is not None:
            return self.status_service.current_context()
        return self.account_service.current_context()

    def overview_key(self, context: CloudContext | None = None) -> RequestKey:
        context = context or self.current_context()
        return context.request_key("cloud-center-overview", "account", "v1")

    def _local_summary(self, context: CloudContext) -> tuple[int, int]:
        pending = 0
        conflicts = 0
        if self.status_service is not None:
            from core.cloud_save_sync import SyncStatus

            for value in self.status_service.status_snapshot().values():
                if not value:
                    continue
                status = value[0]
                if status in (SyncStatus.LOCAL_NEWER, SyncStatus.CLOUD_NEWER):
                    pending += 1
                elif status == SyncStatus.CONFLICT:
                    pending += 1
                    conflicts += 1
        try:
            pending += len(PendingCloudSyncQueue().pending(context.cache_identity))
        except Exception:
            pass
        return pending, conflicts

    def _local_overview(self, context: CloudContext) -> CloudOverview:
        pending, conflicts = self._local_summary(context)
        if not context.network_allowed and context.backend_active:
            return CloudOverview(
                "offline",
                "Offline mode is enabled. Cached cloud data remains available.",
                pending_changes=pending,
                conflict_count=conflicts,
            )
        if context.backend_active and not context.authentication_configured:
            return CloudOverview(
                "setup_required",
                "Add the Secret Access Key to connect this device.",
                pending_changes=pending,
                conflict_count=conflicts,
            )
        if not context.backend_active:
            return CloudOverview(
                "local",
                "Local folder sync is active. Private cloud is not connected.",
                pending_changes=pending,
                conflict_count=conflicts,
            )
        return CloudOverview(
            "offline",
            "Cloud data is temporarily unavailable.",
            pending_changes=pending,
            conflict_count=conflicts,
        )

    def _load_overview(self, token: CancellationToken, context: CloudContext) -> dict[str, Any]:
        token.raise_if_cancelled()
        local = self._local_overview(context)
        if not context.remote_requests_allowed:
            return local.to_payload()

        snapshot = self.account_service.snapshot()
        token.raise_if_cancelled()
        overview = snapshot.overview or {}
        listing = snapshot.listing or {}
        devices = tuple(
            CloudDeviceSummary(
                str(item.get("deviceName") or "Device"),
                str(item.get("platform") or ""),
                bool(item.get("isOnline")),
                float(item.get("lastSeenAt", 0) or 0) / 1000.0,
            )
            for item in overview.get("devices", ())
            if isinstance(item, dict)
        )
        pending, conflicts = self._local_summary(context)
        total_devices = int(overview.get("totalDevices", len(devices)) or len(devices))
        online_devices = sum(1 for device in devices if device.online)
        result = CloudOverview(
            "ready",
            "Private cloud is connected.",
            account_label=str(overview.get("email") or "Private Cloud"),
            endpoint=context.endpoint,
            quota_used=int(listing.get("bytesUsed", 0) or 0),
            quota_total=int(listing.get("quotaBytes", overview.get("quotaBytes", 0)) or 0),
            game_count=len(listing.get("games", ()) or ()),
            device_count=max(total_devices, len(devices)),
            online_device_count=online_devices,
            pending_changes=pending,
            conflict_count=conflicts,
            devices=devices,
        )
        return result.to_payload()

    def request_overview(
        self,
        *,
        force: bool = False,
        priority: RequestPriority = RequestPriority.NORMAL,
    ):
        context = self.current_context()
        key = self.overview_key(context)
        if force:
            self.request_manager.invalidate(key)
        loader = lambda token: self._load_overview(token, context)
        if getattr(self.request_manager, "cache", None) is not None:
            return self.request_manager.request_cached(
                key,
                loader,
                max_age_seconds=self.CACHE_TTL_SECONDS,
                priority=priority,
                generation=context.generation,
                retry_policy=RetryPolicy(retry_if=is_transient_error),
                timeout_seconds=20,
                stale_while_revalidate=True,
            )
        return self.request_manager.request(
            key,
            loader,
            priority=priority,
            generation=context.generation,
            retry_policy=RetryPolicy(retry_if=is_transient_error),
            timeout_seconds=20,
        )

    def request_sync(self, db_path: str | None = None):
        """Reconcile the private profile through the existing managed service."""
        if self.metadata_service is None:
            raise RuntimeError("CloudCenterService has no metadata service")
        return self.metadata_service.request_profile(
            db_path,
            force=True,
            priority=RequestPriority.CRITICAL,
            tag="cloud_center_sync",
        )

    def sync_now(self, db_path: str | None = None):
        """Start the primary Cloud Center synchronization operation."""
        return self.request_sync(db_path)

    def refresh_overview(self, *, priority: RequestPriority = RequestPriority.NORMAL):
        """Force a fresh overview while retaining stale data semantics."""
        return self.request_overview(force=True, priority=priority)

    def request_devices(self, *, force: bool = False, priority: RequestPriority = RequestPriority.NORMAL):
        """Request the device projection through its own managed resource key."""
        context = self.current_context()
        key = context.request_key("cloud-devices", "account", "v1")
        if force:
            self.request_manager.invalidate(key)

        def loader(token: CancellationToken) -> dict[str, Any]:
            token.raise_if_cancelled()
            if not context.remote_requests_allowed:
                return {"devices": [], "offline": True}
            snapshot = self.account_service.snapshot()
            token.raise_if_cancelled()
            devices = []
            for item in (snapshot.overview or {}).get("devices", ()):
                if not isinstance(item, dict):
                    continue
                devices.append({
                    "name": str(item.get("deviceName") or "Device"),
                    "platform": str(item.get("platform") or ""),
                    "online": bool(item.get("isOnline")),
                    "last_seen_at": float(item.get("lastSeenAt", 0) or 0) / 1000.0,
                })
            return {"devices": devices}

        kwargs = dict(
            priority=priority,
            generation=context.generation,
            retry_policy=RetryPolicy(retry_if=is_transient_error),
            timeout_seconds=20,
        )
        if getattr(self.request_manager, "cache", None) is not None:
            return self.request_manager.request_cached(
                key, loader, max_age_seconds=self.CACHE_TTL_SECONDS,
                stale_while_revalidate=True, **kwargs,
            )
        return self.request_manager.request(key, loader, **kwargs)

    def request_save_history(
        self,
        game_id: int,
        *,
        force: bool = False,
        priority: RequestPriority = RequestPriority.NORMAL,
    ):
        """Load one game's history through the account service without exposing its key."""
        context = self.current_context()
        key = context.request_key("cloud-save-history", str(int(game_id)), "v1")
        if force:
            self.request_manager.invalidate(key)

        def loader(token: CancellationToken) -> dict[str, Any]:
            token.raise_if_cancelled()
            if not context.remote_requests_allowed:
                return {"game_id": int(game_id), "games": [], "offline": True}
            listing = self.account_service.list_games()
            token.raise_if_cancelled()
            return {"game_id": int(game_id), "games": listing.get("games", [])}

        request = self.request_manager.request(
            key,
            loader,
            priority=priority,
            generation=context.generation,
            retry_policy=RetryPolicy(retry_if=is_transient_error),
            timeout_seconds=20,
        )
        return request

    def request_connection_probe(self, *, priority: RequestPriority = RequestPriority.NORMAL):
        """Probe connectivity and return only redacted health metadata."""
        context = self.current_context()
        key = context.request_key("cloud-connection-probe", "account", "v1")

        def loader(token: CancellationToken) -> dict[str, Any]:
            token.raise_if_cancelled()
            if not context.network_allowed:
                return {"healthy": False, "status": "offline", "message": "Offline mode is enabled."}
            if not context.backend_active or not context.authentication_configured:
                return {"healthy": False, "status": "setup_required", "message": "Cloud setup is incomplete."}
            health = self.account_service.health(timeout=5.0)
            token.raise_if_cancelled()
            return {
                "healthy": bool(health.get("healthy")),
                "status": str(health.get("status") or "unavailable"),
                "version": str(health.get("version") or ""),
                "is_outdated": bool(health.get("is_outdated")),
                "latency_ms": int(health.get("latency_ms", 0) or 0),
            }

        return self.request_manager.request(
            key,
            loader,
            priority=priority,
            generation=context.generation,
            retry_policy=RetryPolicy(retry_if=is_transient_error, max_attempts=2),
            timeout_seconds=8,
        )

    def invalidate_context(self) -> CloudContext:
        """Invalidate overview and status resources for the current context."""
        context = (
            self.status_service.invalidate_context()
            if self.status_service is not None
            else self.account_service.invalidate_context()
        )
        self.request_manager.invalidate(self.overview_key(context))
        for resource in ("cloud-devices", "cloud-connection-probe"):
            self.request_manager.invalidate(context.request_key(resource, "account", "v1"))
        return context


__all__ = [
    "CloudCenterService", "CloudOverview", "CloudDeviceSummary",
    "CloudConnectionState", "CloudSyncSummary", "CloudQuotaSummary",
    "CloudConflictSummary",
]
