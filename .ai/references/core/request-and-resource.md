# Core Reference: Request and Resource

## Files

- [`request_contracts.py`](../../../core/request_contracts.py): priorities, keys, result/state values, cancellation, retry, and request specifications.
- [`request_manager.py`](../../../core/request_manager.py): queue, worker lifecycle, deduplication, cache integration, subscriptions, batches, metrics, and shutdown.
- [`resource_cache.py`](../../../core/resource_cache.py): bounded memory/disk cache, serialization, TTL, atomic writes, invalidation, and eviction.
- [`performance_metrics.py`](../../../core/performance_metrics.py): application-facing render/request timing.
- [`ui/resource_binding.py`](../../../ui/resource_binding.py): Qt subscription bridge.

## Change guide

Change contracts first when adding request semantics. Add focused Qt-free tests for manager/cache behavior. Add a binding test only when the UI-thread bridge changes. Update [request-resource-manager.md](../../architecture/request-resource-manager.md) if ownership or lifecycle semantics change.
