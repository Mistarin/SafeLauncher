# Phase 8: Performance and reliability pass

Status: complete for the current client architecture on 2026-09-14; live
staging measurements remain a release-environment task.

## Scope completed

- Added the centralized `core.cache_policy` registry for reusable remote
  resource freshness, stale-while-revalidate defaults, offline usability, and
  safe content types. Existing Steam, artwork, profile, public-profile, and
  artwork-search TTLs now use named policies instead of scattered literals.
- Extended `ResourcePerformanceTracker` to expose the full request-manager
  operational set: completion, errors, retries, cancellations, offline
  short-circuits, invalidations, fresh/stale/missed cache reads, memory/disk
  cache sources, concurrency, and timing.
- Added large-library batch coverage with an explicit worker-bound assertion.
- Added duplicate-identity coverage proving equal request keys share one
  loader even when submitted through a batch.
- Added shutdown coverage for an active cooperative request plus a queued
  batch, proving queued work is cancelled and does not execute late.
- Added metadata-only runtime diagnostics export with an explicit metric
  allowlist, atomic writes, and `0600` permissions.
- Added the Settings action for exporting runtime diagnostics without game
  paths, credentials, resource values, or log contents.
- Added deterministic account-switch and rapid-filter stress coverage using
  synthetic data and opaque cloud contexts.
- Added configurable performance-gate evaluation for CI/release baselines;
  thresholds are supplied explicitly instead of being guessed in production.
- Added [`ci/performance_baseline.py`](../ci/performance_baseline.py), a
  repeatable offline baseline runner for representative library and request
  counts.
- Kept the metrics layer observation-only; it does not become a second state or
  scheduling system.
- Added [`core/asset_cache.py`](../core/asset_cache.py) with bounded,
  symlink-safe eviction for retained achievement and SteamGridDB materialized
  assets.

## Current measurement surface

`MainWindow.performance_metrics()` combines user-visible milestones with
`RequestManager.metrics()`. The resulting snapshot can now answer the Phase 8
questions about first usable render, visible artwork, duplicate requests,
cache behavior, failures/retries, cancellations, peak workers, and refresh
frequency.

## Reliability matrix covered

| Scenario | Coverage |
|---|---|
| Large library / bounded concurrency | `test_large_batch_stays_within_worker_bound` |
| Duplicate game/resource identity | `test_duplicate_keys_in_a_batch_share_one_loader` |
| Active request shutdown | existing manager shutdown test plus Phase 8 test |
| Queued request shutdown | `test_shutdown_cancels_queued_large_batch` |
| Stale cache success/failure/offline | resource-cache tests |
| Retry/backoff/cancellation | request-manager tests |
| Generation race protection | request-manager and binding tests |
| Rapid account/context switch | `test_rapid_account_switch_isolates_old_cloud_context` |
| Rapid library filtering | `test_rapid_filtering_keeps_snapshot_and_selection_consistent` |
| Release threshold evaluation | `tests/test_performance_gates.py` |
| Baseline collection | `ci/performance_baseline.py`, `tests/test_performance_baseline.py` |

An initial local 600-game / 100-request / 5-repetition run measured a median
library snapshot of approximately 1.06 ms and a median managed batch of
approximately 2.53 ms, with zero errors. These are engineering observations,
not release thresholds; hardware- and library-specific baselines must be
collected before enforcing them.

## Release-environment follow-up

- Kept the remaining validated legacy achievement/icon and SteamGridDB disk
  materializations isolated, and added explicit file/byte budgets with
  symlink-safe oldest-first eviction. Active achievement schema/resource paths
  already use `ResourceCache`.
- Persistent automatic collection remains intentionally out of scope; explicit
  user-triggered support export is complete.
- Run the baseline runner against representative real-library sizes and record
  versioned release thresholds in a controlled staging/release environment.
- Feed measured baselines into `PerformanceGateThresholds` for release CI.

These measurements cannot be honestly synthesized by the offline test gate:
they require the target hardware, real library shape, and controlled network
services. The local gate intentionally verifies deterministic behavior and
regression safety without requiring credentials or live service access.
