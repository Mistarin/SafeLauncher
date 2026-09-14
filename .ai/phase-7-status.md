# Phase 7: Compatibility-worker and lifecycle cleanup

Status: complete and audited in the current migration slice on 2026-09-14.

## Scope completed

- Removed `MainWindow._background_workers`, which duplicated the authoritative
  `WorkerSupervisor` registry and could drift from the real QThread set.
- Simplified `_register_worker()` so every compatibility QThread is registered
  only with `WorkerSupervisor` for shutdown ownership.
- Kept `metadata_fetchers`, `auto_fetchers`, and the pending artwork queue as
  feature indexes only. They are compatibility paths for manager-less callers,
  not competing production schedulers.
- Removed duplicate initializations and redundant shutdown cleanup in the
  MainWindow resource setup.
- Preserved the compatibility worker classes for older integrations and tests;
  their manager-less fallbacks now run on the owning SafeQThread or serial
  caller and do not create feature-local executors.
- Changed the manager-backed branches of `CloudSaveStatusFetcherThread`,
  `CloudSaveBatchQueueWorker`, `AchievementStatusFetcherThread`, and
  `AchievementBatchQueueWorker` to delegate to `CloudStatusService` and
  `AchievementResourceService`. They now share the production request keys,
  retry/cancellation contracts, and typed result handling instead of rebuilding
  feature-local `RequestSpec` loaders.
- Audited every `ThreadPoolExecutor`, direct `threading.Thread`, `SafeQThread`,
  `FunctionWorker`, `TaskSupervisor`, and `WorkerSupervisor` call site. The
  full classification is in [phase-7-worker-audit.md](phase-7-worker-audit.md).
- Added [`ci/worker_audit.py`](../ci/worker_audit.py), which fails whenever a
  feature-local executor appears under `core/` or `ui/`.

## Verified ownership boundary

| Concern | Production owner | Compatibility fallback |
|---|---|---|
| Remote resource scheduling | `RequestManager` | individual `SafeQThread` workers |
| QThread shutdown | `WorkerSupervisor` | same supervisor when attached to MainWindow |
| Artwork/Steam/achievement deduplication | resource services and manager | feature-local worker lists |
| Local library authority | SQLite | unchanged |

The remaining compatibility lists and worker wrappers must not be deleted
until manager-less embedding callers are removed or migrated. They should not
receive new production features, but they no longer contain a second request
scheduler.

## Next slice

The former manager-less batch and icon pools now run on their owning worker or
serial caller. Embedded callers remain compatibility paths, and the production
MainWindow path continues to pass the shared manager.

## Verification

- Python compilation and the complete unit suite must pass after this slice.
- `python ci/worker_audit.py` passes with no feature-local executor findings.
- The manager-backed application path remains unchanged.
- Shutdown continues to wait on the single `WorkerSupervisor` registry.
