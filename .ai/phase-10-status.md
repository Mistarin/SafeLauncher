# Phase 10: Test, performance, and release hardening

Status: client-side release gate implemented and passing on 2026-09-14.

## Completed

- Focused unit coverage exists for request lifecycle, cache freshness,
  context isolation, cloud operations, cross-device projection, profile
  privacy, worker shutdown, and Cloud Center operations.
- Cross-device matrix covers cloud-only materialization, installation identity
  reuse, shared playtime/achievements, archived records, and account/context
  switching while requests are active.
- Failure coverage includes offline startup, stale refresh success/failure,
  retries, timeout/cancellation, conflict preservation, and shutdown with
  active work.
- `ci/release_readiness.py` is the canonical isolated release check. It runs
  compilation, unit tests, smoke phases, the full harness, security and worker
  audits, the offline performance guard, `.ai` regeneration/validation, and
  diff checks.
- The 600-game/100-request offline performance baseline passes with bounded
  worker concurrency and zero synthetic errors.
- CI uses explicit workspace paths and no invalid `runner` expression in
  job-level environment configuration.

## Current gate result

The complete local release-readiness gate passes. Real-library artwork timing,
live backend latency, and production deployment verification remain staging
measurements rather than values that can be safely fabricated in CI.
