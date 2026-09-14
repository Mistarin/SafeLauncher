# Phase 4: Private library/profile reconciliation service

Status: implemented on 2026-09-14.

## Scope completed

- Added the Qt-free `CloudMetadataService` and `CloudMetadataTarget` boundary.
- Account-wide private profile reconciliation now runs through RequestManager with context-isolated keys and typed outcomes.
- Per-game launcher metadata reconciliation now runs through the same service.
- Combined profile-plus-game synchronization preserves the existing ordering: account-wide achievements/playtime/profile merge first, then legacy per-game metadata migration.
- SQLite remains the authoritative local projection; worker database connections are opened and closed inside the service loader.
- Existing `CloudMetadataSync` retains payload construction, normalization, merge precedence, revision retry, local projection, and digest-only offline queue behavior.
- Offline/auth/backend failures continue to enqueue retryable digest metadata rather than leaking secrets into request state.
- MainWindow no longer constructs private metadata loaders or cloud-context request keys directly.
- UI generation checks and pending per-game coalescing remain intact at the presentation boundary.

## Compatibility

`CloudMetadataSync` remains a tested domain/transport implementation for direct integrations and older callers. The new service is the production MainWindow path; no public-profile publication behavior or installation-state exposure was added.

## Verification

- Added 3 focused metadata-service tests.
- Full unit suite: 122 passed.
- Smoke suite: core, cloud, achievements, and UI passed.
- Python compilation, cache/index validation, and `git diff --check` passed.

## Remaining work

The next phase should unify remaining achievement/cloud-save status workers and artwork/Steam metadata adapters under the same service conventions, then remove redundant feature-local concurrency and compatibility paths only after their callers are migrated.
