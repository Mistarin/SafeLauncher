# Phase 0 Baseline: Production-Readiness Refactor

Verified against the repository on 2026-09-14. This document is the baseline for the subsequent cloud-status, cloud-operation, profile, MainWindow, worker, cache, and reliability phases.

## Baseline architecture

The application has an application-scoped request/resource path:

```text
MainWindow
  ├─ RequestManager(max_workers=3)
  ├─ ResourceCache
  ├─ CloudClient / CloudMetadataSync / CloudSaveSyncEngine
  ├─ SteamClient / ArtworkClient
  ├─ GameDatabase
  └─ compatibility workers and feature-local orchestration
```

The request manager is functional and tested, but adoption is incomplete. Some features still maintain their own cache, in-flight, polling, batch, or executor lifecycle.

## Component baseline

| Component | Current state | Production-readiness rating | Next action |
|---|---|---:|---|
| Request contracts/manager | Shared contracts, priorities, deduplication, retries, cancellation, generations, batches, metrics | 8/10 | Maintain API stability and add service-level consumers |
| Resource cache | Bounded memory/disk cache with TTL and stale behavior | 7/10 | Remove duplicate feature caches and define TTL policy |
| ResourceBinding | Queued Qt bridge with cancellation/unsubscription | 7/10 | Extend adoption to remaining profile and dialog paths |
| Private library sync | Local-first merge, revision retry, digest-aware queue | 6.5/10 | Add a dedicated application sync service |
| Cloud save status | Managed paths exist, but polling/cache ownership remains in MainWindow and compatibility workers | 5.5/10 | Build CloudStatusService |
| Cloud save mutations | Domain coordinator and transport exist, but UI lifecycle/progress/conflict ownership is distributed | 5.5/10 | Build CloudOperationService |
| Achievements | Provenance-aware persistence and manager-backed paths with compatibility workers | 7/10 | Move batch ownership out of MainWindow |
| Steam/artwork | Dedicated clients and manager-backed visible paths, with older fetchers/caches retained | 7/10 | Consolidate cache and fallback ownership |
| Public profile | Separate privacy boundary, partially manager-backed, large UI-owned orchestration | 6/10 | Extract ProfileApplicationService |
| MainWindow | Composition root plus substantial domain, worker, polling, cache, and callback logic | 4.5/10 | Extract application services after cloud work |
| Database/local projection | Strong migrations, integrity checks, backup recovery, and local-first writes | 7.5/10 | Preserve authority; split only by domain when needed |
| Thread lifecycle | Strong shutdown protections and supervisors, but two worker models remain | 7/10 | Remove production feature-local executors |
| Testing/diagnostics | Focused tests, smoke phases, full harness, metrics, and security checks | 7/10 | Add service-boundary and cross-device scenarios |

## Measured structural baseline

| Measurement | Baseline |
|---|---:|
| `ui/main_window.py` lines | 7,749 |
| `ui/components/profile_page.py` lines | 3,091 |
| `ui/threads.py` lines | 926 |
| `core/cloud_save_sync.py` lines | 1,203 |
| `core/cloud_metadata_sync.py` lines | 677 |
| `database.py` lines | 1,539 |
| `core/request_manager.py` lines | 792 |
| `core/resource_cache.py` lines | 205 |
| Request manager configured workers | 3 |
| Feature-local `ThreadPoolExecutor` paths | 2 |
| MainWindow cloud/achievement batch/in-flight flags | multiple; owned by UI |
| Profile UI local artwork/in-flight caches | multiple; owned by widget |
| Generated text files indexed | 268 |
| Generated Python symbols indexed | 2,433 |
| Generated connection points indexed | 1,270 |
| Generated request-key sites indexed | 94 |
| Generated database schema statements indexed | 33 |
| Generated tests indexed | 121 |

The generated evidence is stored under [`generated/`](generated/), especially [`connections.json`](generated/connections.json), [`request-keys.json`](generated/request-keys.json), and [`tests.json`](generated/tests.json).

## Current production paths that must not be confused

### Active managed paths

- Main library artwork and metadata requests.
- Steam build/tag lookups from MainWindow.
- Cloud-save status batches where a request manager is supplied.
- Achievement status/schema paths where a request manager is supplied.
- Public profile reads in manager-aware profile paths.

### Compatibility paths

- `SafeQThread` feature workers in `ui/threads.py`.
- Standalone dialogs constructed without a RequestManager.
- Plugin/embedding callers that still expect worker signals.
- Feature-local batch executors in compatibility workers.

Compatibility paths must not be removed until their call sites and tests are verified. They must not remain an accidental second production scheduler.

## Highest-risk seams

1. Cloud save status and polling are split between MainWindow, cloud helpers, state stores, and compatibility workers.
2. Cloud save mutation progress/conflict lifecycle is not represented by one application service.
3. ProfilePageWidget mixes presentation, transport, authentication, cache policy, social operations, and image loading.
4. MainWindow owns synchronization flags, resource binding maps, feature caches, polling timers, and result application.
5. Two feature-local `ThreadPoolExecutor` implementations remain in `ui/threads.py`.
6. Direct UI transport remains in profile artwork and cloud setup/health flows.

## Phase 0 invariants

- SQLite remains authoritative for local library, playtime, achievements, and installation projection.
- Private SafeLauncherCloud and public profile services remain separate.
- Installation status remains private and device-specific; public profile backend changes are out of scope.
- RequestManager owns remote request lifecycle; transport clients own HTTP/parsing.
- Cloud save mutations are explicit operations, not ordinary cacheable resources.
- No raw credentials enter request keys, resource cache keys, `.ai`, logs, or pending queues.
- Offline/local-first behavior must never discard newer local data.
- Conflict resolution must preserve both local and remote save generations until the user decides.
- No forced QThread termination is permitted.
- Late responses must be rejected by request/context generation.

## Phase 0 completion gate

Phase 0 is complete when:

- This baseline is linked from the AI cache status/index.
- The current manager, cloud, profile, database, UI, worker, and test ownership is documented.
- The invariants above are treated as non-regression requirements.
- Baseline tests and smoke checks pass in isolated XDG/offline mode.
- Phase 1 begins with CloudStatusService design rather than another UI-local patch.

## Phase 1 progress

The first Phase 1 boundary is now implemented:

- `CloudContext` creates opaque context-isolated identities and request keys.
- `CloudStatusService` builds typed, manager-backed cloud status specs and batches.
- MainWindow's managed cloud status path delegates key/spec/loader construction to the service.
- Existing `CloudSyncCoordinator`, `CloudSaveSyncEngine`, and compatibility workers remain intact.

Remaining Phase 1 work is to move status polling/cache ownership out of MainWindow in Phase 2 and to add the operation service in Phase 3.
