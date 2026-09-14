# Phase 1: Cloud application-service boundary

Status: implemented on 2026-09-14.

## Scope completed

Phase 1 establishes the first production-facing service boundary for private cloud status loading without rewriting the existing save detection, archive, or merge algorithms.

- `core.cloud_context.CloudContext` owns the opaque cloud identity used for request keys and cache isolation.
- `core.cloud_status_service.CloudStatusService` builds typed, manager-compatible status requests.
- `CloudStatusTarget` carries the game-specific input required by the existing coordinator.
- `CloudStatusResult` remains the typed result returned by the existing cloud operation layer.
- MainWindow now obtains cloud status keys, loaders, retry classification, and request metadata from the service.
- Context changes invalidate the coordinator generation and create new context-isolated request keys.
- Existing tuple result handling remains as a migration compatibility path for already-submitted callers.
- Account-level UI transport is now behind `CloudAccountService`; setup health/quota probes accept explicit credentials only in memory and use opaque identities for any managed request keys.

## Boundary rules

The service does not own UI, Qt objects, HTTP transport, SQLite authority, or save merge algorithms. The existing `CloudSyncCoordinator` remains the domain coordinator for those operations. `RequestManager` remains responsible for scheduling, deduplication, retries, cancellation, and resource state delivery.

Cloud request keys contain only an opaque context fingerprint and game identity. They do not contain endpoint URLs, access tokens, deployment keys, or other credential material.

## Verification

- Focused cloud service, request manager, and contract tests: 32 passed.
- Full unit suite: 111 passed.
- Smoke suite: core, cloud, achievements, and UI passed.
- `.ai` manifest and cache validation passed.
- `git diff --check` and Python compilation passed.

## Remaining work

Phase 2 is documented in [phase-2-status.md](phase-2-status.md). Save restore/upload lifecycle remains in `CloudOperationService`; manager-less account-dialog restore is retained only as a compatibility fallback. Remaining cloud work is to migrate setup/deployment-only compatibility workers after their standalone callers are retired.
