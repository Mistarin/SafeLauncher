# Request and resource loading

SafeLauncher has one application-scoped `RequestManager` created by
`MainWindow`. It owns bounded scheduling, priorities, active-request
deduplication, cooperative cancellation, retries, generation ordering, cache
state notifications, and shutdown.

`ResourceManager` is an alias for the same implementation for code that uses
resource-oriented terminology. The application manager is constructed with a
shared `ResourceCache`; feature code may pass an isolated cache to
`cached_request()` when it needs a separate retention policy.

Transport code stays separate:

- `core/cloud_client.py` owns SafeLauncherCloud/Convex HTTP and encrypted-save
  transport through the compatibility implementation in `cloud_backend.py`.
- `core/steam_client.py` owns Steam Store and Steam build metadata requests.
- `core/artwork_client.py` owns artwork transport and the existing safe local
  artwork cache through `SteamGridDBClient`.
- `ProfileServiceClient` owns central profile-service HTTP and authentication
  remains in `CentralAuthSession`.

## Request keys

Use a stable `RequestKey(resource, identity, variant)` for every remote
resource. The identity must include the account/backend context whenever the
same resource can differ between accounts or deployments. Do not use a widget
instance, object ID, or a transient callback as the resource identity.

Examples:

```python
RequestKey("steam-build", app_id)
RequestKey("profile-artwork", canonical_artwork_url)
RequestKey("cloud-save-status", f"{cloud_context}:{game_id}")
```

## UI integration

UI code should submit work through the shared manager and marshal results back
to Qt with a signal. It should render a usable cached/stale value immediately,
show loading only when no value is available, and cancel page-specific keys
when the page is hidden or destroyed. Generation IDs must be used when a
newer navigation or account context supersedes an older request.

For widget-facing subscriptions, `ui.resource_binding.ResourceBinding` (or
`bind_resource()`) converts the manager's worker-thread listener into a queued
Qt signal. Bindings are page-owned and can cancel their page-scoped request on
close; they must not directly mutate widgets from a manager callback.

`TaskSupervisor` and the original feature workers remain compatibility
fallbacks for standalone dialogs, plugins, and tests. New production call
sites should not create feature-local executors or request pools.

The main library uses direct manager subscriptions for hero/icon prefetches,
automatic cover/icon resolution, cloud-save status, achievement status,
Steam build/tag metadata, and profile artwork. The bulk Steam update action
uses one cached batch request per distinct AppID. Add Game cover
search/download uses the same binding; its legacy banner workers remain only
for callers that construct the dialog without a manager. Achievement and
Steam metadata QThread classes remain as compatibility paths for manager-less
embedding and older integrations.

For data that can be reused, use `request_cached()` with a TTL. It returns a
completed future for fresh values, emits stale values immediately while a
refresh is scheduled, and preserves stale state if the refresh fails. Calling
`invalidate()` also removes the associated shared cache entry.

`request_many()` and `request_many_cached()` continue to return individual
handles for per-item cancellation. They also accept `progress(completed,
total, result)` and `on_complete(results)` callbacks so feature code does not
need to rebuild batch completion bookkeeping.

The default `RetryPolicy` retries transient connection/time-limit failures and
HTTP 429/5xx-style errors only. Transport-specific temporary failures can use
`RetryableRequestError` or provide `RetryPolicy(retry_if=...)`; permanent
validation and authentication errors are returned without wasting retry
attempts.

## Cache and failure behavior

`ResourceCache` is thread-safe and supports bounded memory, optional bounded
disk storage, bounded reads, durable atomic writes, JSON/bytes values, TTL
checks, and LRU eviction.
Disk persistence degrades to the bounded memory cache if its directory becomes
read-only or unavailable, so cache I/O cannot fail an otherwise successful
resource request.
`cached_request()` emits stale data before refreshing it. If refresh ends
offline or fails, the last stale value remains the manager's usable state while
the request future still reports the original failure for diagnostics/retry
logic.

## Diagnostics

`RequestManager.metrics()` returns counters for submitted, deduplicated,
completed, retried, cancelled, offline, invalidated, cache-hit, stale, and
cache-miss requests. It also separates memory and disk cache hits, reports a
derived cache hit rate, and includes total/max duration plus peak active
requests. The main window logs this snapshot during shutdown.
`MainWindow.performance_metrics()` adds time to first usable library render,
time to first visible artwork, library refresh count/rate, and visible artwork
updates/rate, allowing startup/render performance to be compared with the
request counters without adding metrics logic to widgets. The request snapshot
also reports current active requests and the configured worker bound.

Private profile reconciliation is local-first. If the configured cloud backend
is offline, unauthenticated, or temporarily fails, `CloudMetadataSync` records
one coalesced pending operation in the private
`pending_cloud_sync.json` queue. The payload is not stored there; the next
successful attempt rebuilds it from SQLite and acknowledges only the digest it
actually synchronized, so a newer local edit cannot be lost.
