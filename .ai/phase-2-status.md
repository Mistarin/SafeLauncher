# Phase 2: Cloud status cache and polling ownership

Status: implemented on 2026-09-14.

## Scope completed

- `CloudStatusService` now owns the `SaveStateStore` projection used by cloud-save indicators and Save Manager compatibility consumers.
- Cloud status persistence moved to a dedicated `cloud_status_cache.json` file beside the existing metadata cache.
- Existing combined metadata caches are read once for migration; Steam build, achievement, and attempted-tag data remain in `metadata_cache.json`.
- Dedicated cache writes are atomic and contain only opaque cloud context identity, status values, save statistics, and timestamps.
- Context invalidation clears status projection data and retires the old changed-listing request generation.
- Recheck planning is service-owned: uncached, offline, stale, and fresh entries are prioritized consistently.
- Changed-listing polling is manager-backed, deduplicated, generation-aware, and no longer uses MainWindow's generic managed-task executor.
- Periodic polling lifecycle now belongs to the Qt-free `CloudStatusPollingService`; MainWindow only publishes detached target snapshots and receives queued change notifications.
- Offline and authentication-required verdicts are written through the service rather than directly into the UI-owned mapping.
- MainWindow remains responsible for Qt bindings, rendering, and user notifications only; it no longer owns a cloud polling QTimer.

## Compatibility

`cloud_save_status_cache` and `save_state_store` remain aliases to the service-owned store so existing library and Save Manager readers continue to work. The low-level `CloudSyncCoordinator` and save algorithms are unchanged.

## Verification

- Focused cloud/request/cache/polling tests: 38 passed.
- Full unit suite: 162 passed.
- Smoke suite: core, cloud, achievements, and UI passed.
- Python compilation, diff check, and `.ai` cache validation passed.

## Remaining work

Phase 3 should move upload, restore, delete, archive, and conflict mutation lifecycle into a managed cloud operation service. That service should reuse the same context, generation, status store, and request-manager contracts before MainWindow mutation code is reduced further.
