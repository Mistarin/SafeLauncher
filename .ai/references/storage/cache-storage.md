# Storage Reference: Cache Storage

There are multiple intentional caches: shared `ResourceCache`, SteamGridDB artwork cache, achievement schema/icon cache, cloud listing/status state, and local database state. They have different authority and invalidation rules. Do not merge them merely because they all use disk.

Materialized asset directories use [`core/asset_cache.py`](../../../core/asset_cache.py) for bounded oldest-first eviction. The current budgets are 256 MiB/2048 files for SteamGridDB artwork and 128 MiB/2048 files for achievement schemas/icons. Eviction never follows symlinks and cache failures do not fail a successful resource request.

## Freshness policy ownership

Reusable remote-resource TTLs are named in [`core/cache_policy.py`](../../../core/cache_policy.py). Feature code must use `cache_policy("name")` instead of embedding a new numeric TTL at a call site. The policy is a freshness hint; it never changes the authority of SQLite or an active mutation.

| Resource policy | Fresh for | Stale/offline behavior | Owner |
|---|---:|---|---|
| `private-profile`, `public-profile` | 5 minutes | stale data may render while refresh runs | profile/resource services |
| `profile-social` | 1 minute | stale snapshot is usable | profile/resource services |
| `profile-avatar-catalog` | 24 hours | stale catalog may render | profile/resource service |
| `profile-avatar` | 30 days | cached image remains usable | profile/resource service |
| `profile-artwork`, `profile-background` | 7 days | cached image remains usable | profile/resource service |
| `artwork` | 30 days | cached asset remains usable | artwork resource service |
| `steam-build` | 15 minutes | stale build can be compared offline | Steam resource service |
| `steam-tags` | 7 days | stale tags can render offline | Steam resource service |
| `achievement-schema` | 24 hours | stale schema may be reused | achievement providers |
| `cloud-status`, `cloud-listing` | 2 hours | last usable status is retained offline | cloud status service |

`ResourceCache` remains a bounded optimization layer. It stores only serialized
resource values and metadata, never credentials, tokens, save contents, or
full private payloads. SQLite remains the local library authority, and
mutation/operation state is not cacheable.

When a remote response is stale, the request manager may return it immediately
and schedule a refresh. A failed refresh must preserve a usable stale value and
surface the failure state separately. Context changes (account, backend,
profile, or generation) invalidate or isolate the affected keys.
