# Core Reference: Request and Resource

## Files

- [`request_contracts.py`](../../../core/request_contracts.py): priorities, keys, result/state values, cancellation, retry, and request specifications.
- [`request_manager.py`](../../../core/request_manager.py): queue, worker lifecycle, deduplication, cache integration, subscriptions, batches, metrics, and shutdown.
- [`resource_cache.py`](../../../core/resource_cache.py): bounded memory/disk cache, serialization, TTL, atomic writes, invalidation, and eviction.
- [`performance_metrics.py`](../../../core/performance_metrics.py): application-facing render/request timing.
- [`runtime_diagnostics.py`](../../../core/runtime_diagnostics.py): explicit
  allowlisted export of numeric request/resource health metrics.
- [`performance_gates.py`](../../../core/performance_gates.py): pure release
  evaluation against explicitly supplied performance baselines.
- [`ui/resource_binding.py`](../../../ui/resource_binding.py): Qt subscription bridge.
- [`remote-error-states.md`](remote-error-states.md): shared remote failure
  categories and user-visible resource-state mapping.
- [`safe_thread.py`](../../../core/safe_thread.py): `WorkerSupervisor` for the
  remaining Qt-thread compatibility paths; it is separate from the Qt-free
  `RequestManager` worker pool.

Cloud status is an application-service consumer of this infrastructure: [`cloud_status_service.py`](../../../core/cloud_status_service.py) owns cloud-specific cache projection, batch lifecycle, and planning, while `RequestManager` owns execution and resource lifecycle. [`cloud_metadata_service.py`](../../../core/cloud_metadata_service.py) owns coalescing for repeated private library reconciliation edits.

Phase 5 resource consumers follow the same boundary: [`achievement_resource_service.py`](../../../core/achievement_resource_service.py), [`steam_resource_service.py`](../../../core/steam_resource_service.py), and [`artwork_resource_service.py`](../../../core/artwork_resource_service.py) construct typed specs and cache policies, while achievement/Steam/artwork transports retain parsing and filesystem/network details.

The manager-backed MainWindow path must not add feature-local executors or a
second QThread registry. `metadata_fetchers` and `auto_fetchers` are retained
only as compatibility indexes for callers that construct the window without a
shared manager. `LibraryMetadataState` owns the remaining local Steam/update
presentation projections; they are not a second remote cache.

Runtime diagnostics are support metadata only. They must never serialize
resource values, game paths, request keys, headers, tokens, or log contents.

`ResourceBindingRegistry` in `ui/resource_binding.py` is only an owner-scoped
lifetime registry. It does not retain resource values, deduplicate requests,
or replace `RequestManager`/`ResourceCache`.

Bindings that opt into cancellation call `RequestManager.cancel_if_unsubscribed`
so closing one shared consumer leaves another consumer's deduplicated request
alive. The virtualized library grid is a delegate-painted view; it exposes
cloud/update descriptions through model tooltip and accessibility roles and
keeps upload available from the selected item with `Ctrl+U`.

## Change guide

Change contracts first when adding request semantics. Add focused Qt-free tests for manager/cache behavior. Add a binding test only when the UI-thread bridge changes. Update [request-resource-manager.md](../../architecture/request-resource-manager.md) if ownership or lifecycle semantics change.
