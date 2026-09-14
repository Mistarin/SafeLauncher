# Ownership and Boundaries

| Owner | Owns | Must not own |
|---|---|---|
| `GameDatabase` | Local records, projections, playtime, achievements, schema migrations | HTTP scheduling or widget state |
| `RequestManager` | Queueing, priority, deduplication, cancellation, retry, generation, request notifications | HTTP parsing, SQLite authority, widget mutation |
| `ResourceCache` | Memory/disk values, TTL, stale values, invalidation, eviction | Credentials or domain reconciliation |
| Transport clients | HTTP sessions, response parsing, transport-specific auth/encryption | Global request lifecycle |
| Coordinators | Domain workflows and merge/conflict decisions | Per-widget worker pools |
| UI | Presentation, subscription lifetime, user actions | Direct cross-thread widget updates |
| Compatibility workers | Legacy/standalone entrypoints | Becoming a second production scheduler |

New remote resources should have a stable key, a transport loader, a cache policy, and a UI binding only if a view consumes them.

Sources: [`core/request_manager.py`](../../core/request_manager.py), [`core/resource_cache.py`](../../core/resource_cache.py), [`ui/resource_binding.py`](../../ui/resource_binding.py).
