# Request and Resource Manager

## Responsibilities

`RequestManager` is application-scoped and Qt-free. It provides bounded worker concurrency, priority scheduling, active-request deduplication, cooperative cancellation, per-request timeout behavior, transient retry/backoff, offline short-circuiting, generation ordering, state listeners, batch progress, and graceful shutdown.

`ResourceCache` is optional but normally shared by the application. It provides bounded memory LRU storage and optional disk storage with TTL, stale-while-revalidate behavior, atomic writes, invalidation, and offline stale reads.

## Contract

```python
RequestKey(resource, identity, variant)
RequestManager.request(key, loader, priority=...)
RequestManager.request_many(specs, ...)
RequestManager.request_cached(key, loader, ttl_seconds=..., ...)
RequestManager.subscribe(key, listener)
RequestManager.cancel(key)
RequestManager.cancel_if_unsubscribed(key, request_id=..., generation=...)
RequestManager.cancel_matching(predicate)
RequestManager.invalidate(key)
RequestManager.shutdown()
```

`cancel_if_unsubscribed()` is the lifecycle-safe path used by
`ResourceBinding.close()`: closing one consumer cannot cancel a deduplicated
request while another listener remains subscribed. `cancel_matching()` is
used by the transient connectivity circuit breaker to cooperatively cancel
queued/running requests that are not marked `allow_offline`; connectivity
probes and explicitly local managed tasks may opt out of that cancellation.

Loaders should accept a cancellation token when they support cooperative cancellation. They should raise `RetryableRequestError` or use the shared retry policy for transient failures. Authentication, validation, and permanent domain failures should not be retried blindly.

## State flow

```text
idle → loading → ready
             ↘ error
ready → stale → loading → ready
                   ↘ stale/error with stale value preserved
offline → loading after connectivity returns
```

`ResourceBinding` converts manager callbacks into queued Qt signals. Widgets render a usable cached/stale value immediately and must reject obsolete account/game generations.

Sources: [`core/request_contracts.py`](../../core/request_contracts.py), [`core/request_manager.py`](../../core/request_manager.py), [`core/resource_cache.py`](../../core/resource_cache.py), [`ui/resource_binding.py`](../../ui/resource_binding.py), [`docs/request_manager.md`](../../docs/request_manager.md).
