"""Shared contracts for remote-resource loading.

Phase 0 deliberately contains no executor, cache, or Qt integration.  These
small value objects define the vocabulary used by the Phase 1 request manager
and by future transport adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from threading import Event
from time import time
from typing import Any, Callable, Generic, Mapping, TypeVar


T = TypeVar("T")


class RequestPriority(IntEnum):
    """Lower values are scheduled before higher-value background work."""

    CRITICAL = 0
    NORMAL = 10
    BACKGROUND = 20


class ResourceStatus(StrEnum):
    """State exposed by a remote-resource subscription."""

    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    STALE = "stale"
    OFFLINE = "offline"
    UNAVAILABLE = "unavailable"
    AUTHENTICATION_REQUIRED = "authentication-required"
    PERMISSION_DENIED = "permission-denied"
    CONFLICT = "conflict"
    ERROR = "error"
    CANCELLED = "cancelled"


# Public plan/API terminology. Keep ResourceStatus as the implementation name
# for backwards compatibility with the first request-manager phases.
ResourceState = ResourceStatus


class RemoteErrorCategory(StrEnum):
    """Transport-neutral failure categories shared by all remote services."""

    OFFLINE = "offline"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    TRANSIENT = "transient"
    AUTHENTICATION_REQUIRED = "authentication_required"
    PERMISSION_DENIED = "permission_denied"
    CONFLICT = "conflict"
    VALIDATION = "validation"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True, slots=True)
class RequestKey:
    """Stable identity used for deduplication and cache lookup."""

    resource: str
    identity: str
    variant: str = ""

    def __post_init__(self) -> None:
        if not self.resource.strip():
            raise ValueError("RequestKey.resource must not be empty")
        if not self.identity.strip():
            raise ValueError("RequestKey.identity must not be empty")

    def cache_key(self) -> str:
        parts = (self.resource.strip(), self.identity.strip(), self.variant.strip())
        return ":".join(part.replace(":", "%3A") for part in parts if part)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry limits for a single request."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 8.0
    jitter_ratio: float = 0.2
    retry_if: Callable[[BaseException], bool] | None = None

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("RetryPolicy.max_attempts must be at least 1")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("Retry delays must not be negative")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be >= base_delay_seconds")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")

    def should_retry(self, error: BaseException) -> bool:
        """Return whether ``error`` represents a transient failure.

        A caller can provide ``retry_if`` for a transport-specific policy. The
        default deliberately retries only network/time-limit failures and
        HTTP 429/5xx responses; permanent parsing, auth, and validation
        failures should reach the consumer immediately.
        """
        if self.retry_if is not None:
            return bool(self.retry_if(error))
        return is_transient_error(error)


class RequestCancelled(Exception):
    """Raised by cooperative loaders when their request is cancelled."""


class RequestTimeout(Exception):
    """Raised when a cooperative loader exceeds its manager deadline."""


class RetryableRequestError(RuntimeError):
    """Transport-neutral marker for a failure that is safe to retry."""


def _http_status(error: BaseException) -> int:
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    response = getattr(error, "response", None)
    if not status and response is not None:
        status = getattr(response, "status_code", None)
    try:
        return int(status or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def classify_remote_error(error: BaseException | str | None) -> RemoteErrorCategory:
    """Return the stable category used by UI, diagnostics, and retry policy.

    Adapters may expose ``category`` or ``code`` without importing this
    module.  HTTP status is used as a fallback, keeping this contract free of
    a dependency on requests or any particular cloud backend.
    """
    if error is None:
        return RemoteErrorCategory.UNEXPECTED
    if isinstance(error, RequestCancelled):
        return RemoteErrorCategory.CANCELLED
    if isinstance(error, RequestTimeout) or isinstance(error, TimeoutError):
        return RemoteErrorCategory.TIMEOUT

    raw = str(
        error
        if isinstance(error, str)
        else getattr(error, "category", "") or getattr(error, "code", "") or ""
    )
    normalized = raw.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "offline": RemoteErrorCategory.OFFLINE,
        "timeout": RemoteErrorCategory.TIMEOUT,
        "timed_out": RemoteErrorCategory.TIMEOUT,
        "rate_limit": RemoteErrorCategory.RATE_LIMITED,
        "rate_limited": RemoteErrorCategory.RATE_LIMITED,
        "too_many_requests": RemoteErrorCategory.RATE_LIMITED,
        "transient": RemoteErrorCategory.TRANSIENT,
        "retryable": RemoteErrorCategory.TRANSIENT,
        "backend_unavailable": RemoteErrorCategory.UNAVAILABLE,
        "unavailable": RemoteErrorCategory.UNAVAILABLE,
        "not_found": RemoteErrorCategory.UNAVAILABLE,
        "endpoint_missing": RemoteErrorCategory.UNAVAILABLE,
        "unreachable": RemoteErrorCategory.TRANSIENT,
        "network": RemoteErrorCategory.TRANSIENT,
        "authentication": RemoteErrorCategory.AUTHENTICATION_REQUIRED,
        "authentication_required": RemoteErrorCategory.AUTHENTICATION_REQUIRED,
        "unauthorized": RemoteErrorCategory.AUTHENTICATION_REQUIRED,
        "not_signed_in": RemoteErrorCategory.AUTHENTICATION_REQUIRED,
        "owner_token_missing": RemoteErrorCategory.AUTHENTICATION_REQUIRED,
        "permission": RemoteErrorCategory.PERMISSION_DENIED,
        "permission_denied": RemoteErrorCategory.PERMISSION_DENIED,
        "forbidden": RemoteErrorCategory.PERMISSION_DENIED,
        "conflict": RemoteErrorCategory.CONFLICT,
        "revision_conflict": RemoteErrorCategory.CONFLICT,
        "validation": RemoteErrorCategory.VALIDATION,
        "invalid": RemoteErrorCategory.VALIDATION,
        "invalid_handle": RemoteErrorCategory.VALIDATION,
        "invalid_operation": RemoteErrorCategory.VALIDATION,
        "invalid_response": RemoteErrorCategory.VALIDATION,
        "invalid_profile": RemoteErrorCategory.VALIDATION,
        "invalid_avatar": RemoteErrorCategory.VALIDATION,
        "invalid_avatar_response": RemoteErrorCategory.VALIDATION,
        "invalid_action": RemoteErrorCategory.VALIDATION,
        "invalid_token": RemoteErrorCategory.AUTHENTICATION_REQUIRED,
        "unconfigured": RemoteErrorCategory.UNAVAILABLE,
        "payload_too_large": RemoteErrorCategory.VALIDATION,
        "save_too_large": RemoteErrorCategory.VALIDATION,
        "cancelled": RemoteErrorCategory.CANCELLED,
    }
    if normalized in aliases:
        return aliases[normalized]

    status = _http_status(error)
    if status == 401:
        return RemoteErrorCategory.AUTHENTICATION_REQUIRED
    if status == 403:
        return RemoteErrorCategory.PERMISSION_DENIED
    if status == 404:
        return RemoteErrorCategory.UNAVAILABLE
    if status == 409:
        return RemoteErrorCategory.CONFLICT
    if status == 429:
        return RemoteErrorCategory.RATE_LIMITED
    if 400 <= status <= 499:
        return RemoteErrorCategory.VALIDATION
    if 500 <= status <= 599:
        return RemoteErrorCategory.TRANSIENT
    if isinstance(error, RetryableRequestError) or isinstance(error, (ConnectionError, OSError)):
        return RemoteErrorCategory.TRANSIENT
    return RemoteErrorCategory.UNEXPECTED


def resource_status_for_error(error: BaseException | None) -> ResourceStatus:
    """Map a categorized failure to the user-visible resource state."""
    category = classify_remote_error(error)
    return {
        RemoteErrorCategory.OFFLINE: ResourceStatus.OFFLINE,
        RemoteErrorCategory.AUTHENTICATION_REQUIRED: ResourceStatus.AUTHENTICATION_REQUIRED,
        RemoteErrorCategory.PERMISSION_DENIED: ResourceStatus.PERMISSION_DENIED,
        RemoteErrorCategory.CONFLICT: ResourceStatus.CONFLICT,
        RemoteErrorCategory.UNAVAILABLE: ResourceStatus.UNAVAILABLE,
        RemoteErrorCategory.CANCELLED: ResourceStatus.CANCELLED,
    }.get(category, ResourceStatus.ERROR)


def is_transient_error(error: BaseException) -> bool:
    """Classify common transient transport failures without owning HTTP.

    The request manager stays independent of a particular HTTP client. HTTP
    adapters can expose ``status_code``/``response.status_code`` or raise the
    explicit :class:`RetryableRequestError` marker; requests-style timeout and
    connection exception names are also recognized without importing requests
    into this Qt-free contracts module.
    """
    if isinstance(error, (RequestCancelled, ValueError, TypeError, KeyError)):
        return False
    category = classify_remote_error(error)
    if category in {
        RemoteErrorCategory.TRANSIENT,
        RemoteErrorCategory.RATE_LIMITED,
        RemoteErrorCategory.TIMEOUT,
    }:
        return True
    if category != RemoteErrorCategory.UNEXPECTED:
        return False
    if isinstance(error, RetryableRequestError):
        return True
    if isinstance(error, (RequestTimeout, TimeoutError, ConnectionError, OSError)):
        return True

    status = _http_status(error)
    if status == 429 or 500 <= status <= 599:
        return True

    module = type(error).__module__
    name = type(error).__name__
    if module.startswith("requests.exceptions") and name in {
        "Timeout",
        "ConnectTimeout",
        "ReadTimeout",
        "ConnectionError",
        "ProxyError",
        "ChunkedEncodingError",
    }:
        return True
    return False


class CancellationToken:
    """Thread-safe cooperative cancellation primitive passed to loaders."""

    def __init__(self, timeout_seconds: float | None = None) -> None:
        self._event = Event()
        self._deadline = time() + timeout_seconds if timeout_seconds is not None else None

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def timed_out(self) -> bool:
        return self._deadline is not None and time() >= self._deadline

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise RequestCancelled("Request was cancelled")
        if self.timed_out:
            raise RequestTimeout("Request exceeded its timeout")

    def wait(self, seconds: float) -> bool:
        """Wait for a duration, returning early when cancellation is set."""
        wait_seconds = max(0.0, float(seconds))
        if self._deadline is not None:
            wait_seconds = min(wait_seconds, max(0.0, self._deadline - time()))
        return self._event.wait(wait_seconds)


Loader = Callable[[CancellationToken], T]


@dataclass(frozen=True, slots=True)
class RequestSpec(Generic[T]):
    """Immutable work description accepted by the future request manager."""

    key: RequestKey
    loader: Loader[T]
    priority: RequestPriority = RequestPriority.NORMAL
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    generation: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("RequestSpec.timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class ResourceResult(Generic[T]):
    """Value delivered to consumers after a resource request completes."""

    key: RequestKey
    status: ResourceStatus
    value: T | None = None
    error: BaseException | None = None
    request_id: str = ""
    generation: int = 0
    from_cache: bool = False
    cache_source: str = ""
    updated_at: float = 0.0
    checked_at: float = field(default_factory=time)

    @property
    def usable(self) -> bool:
        return self.value is not None and self.status in {
            ResourceStatus.READY,
            ResourceStatus.STALE,
        }

    @property
    def error_category(self) -> str:
        """Stable machine-readable category for the result's failure."""
        if self.status == ResourceStatus.OFFLINE:
            return RemoteErrorCategory.OFFLINE.value
        if self.status == ResourceStatus.CANCELLED:
            return RemoteErrorCategory.CANCELLED.value
        if self.status == ResourceStatus.AUTHENTICATION_REQUIRED:
            return RemoteErrorCategory.AUTHENTICATION_REQUIRED.value
        if self.status == ResourceStatus.PERMISSION_DENIED:
            return RemoteErrorCategory.PERMISSION_DENIED.value
        if self.status == ResourceStatus.CONFLICT:
            return RemoteErrorCategory.CONFLICT.value
        if self.status == ResourceStatus.UNAVAILABLE:
            return RemoteErrorCategory.UNAVAILABLE.value
        return classify_remote_error(self.error).value
