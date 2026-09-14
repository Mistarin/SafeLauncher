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
    ERROR = "error"
    CANCELLED = "cancelled"


# Public plan/API terminology. Keep ResourceStatus as the implementation name
# for backwards compatibility with the first request-manager phases.
ResourceState = ResourceStatus


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
    if isinstance(error, RetryableRequestError):
        return True
    if isinstance(error, (RequestTimeout, TimeoutError, ConnectionError, OSError)):
        return True

    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    response = getattr(error, "response", None)
    if not status and response is not None:
        status = getattr(response, "status_code", None)
    try:
        status = int(status or 0)
    except (TypeError, ValueError, OverflowError):
        status = 0
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
