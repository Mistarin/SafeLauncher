# Point 3: Feature-local cache and in-flight state audit

Status: implemented for the current migration slice on 2026-09-14. The final
cleanup also removed the redundant profile-background memory cache and added
an explicit shared remote-error/state contract.

## Ownership changes

| Previous MainWindow state | Current owner | Classification |
|---|---|---|
| `_metadata_sync_in_flight`, `_metadata_sync_tokens`, `_metadata_sync_pending` | `CloudMetadataService._latest_handles` and `_latest_pending` | removed from UI; managed reconciliation lifecycle |
| `_auto_fetch_attempted` | `LibraryArtworkCoordinator._attempted["auto"]` | removed from UI on the managed path |
| `metadata_attempted_builds`, `metadata_attempted_tags` | `LibraryMetadataState` | local compatibility/presentation projection |
| `steam_check_results` | `LibraryMetadataState` | local presentation projection, not remote cache |
| `local_version_by_game_id` | `LibraryMetadataState` | local installed-build projection |
| `update_status_by_game_id`, `game_status_by_id` | `LibraryMetadataState` | derived library presentation state |
| `_steam_build_checked_ts` | `LibraryMetadataState.steam_build_checked_at` | local freshness hint for legacy UI policy |
| old combined `metadata_cache.json` writer | `LibraryMetadataState.save_legacy_cache()` | compatibility migration snapshot only |
| cloud status cache | `CloudStatusService` / `SaveStateStore` | active cloud resource projection |
| remote Steam/artwork/profile caches | `ResourceCache` through resource services | active reusable remote cache |

`MainWindow` retains read-compatible properties for the metadata projections so
older dialogs and presentation code do not need a flag-day rewrite. The
properties delegate to `LibraryMetadataState`; they do not create duplicate
maps.

## Intentionally retained state

- `metadata_fetchers`, `auto_fetchers`, and `_pending_auto_fetchers` are
  compatibility indexes for manager-less QThread callers. They are not request
  schedulers and their lifetimes remain owned by `WorkerSupervisor`.
- `_cloud_status_bindings` and `_cloud_status_callbacks` are UI binding
  registries. They map managed request keys to presentation callbacks and are
  cleared when the view/context closes; they do not cache resource values.
- `_managed_task_callbacks` is a short-lived callback registry for explicit
  operations, not a resource cache or deduplication system.
- `_size_fetch_scheduled` deduplicates local filesystem size work. It remains
  beside the local `DiskSizeFetcherThread` because it is not a remote request.
- Specialized artwork/avatar/achievement disk caches are formally isolated
  materialization layers. Active manager paths use `ResourceCache` for
  reusable response data; legacy disk files remain only to load or write
  validated bytes for manager-less integrations and local asset paths. The
  retained SteamGridDB and achievement materializations have explicit bounded
  file/byte eviction; they must not be used as request deduplication or
  freshness state.

## Final state-policy cleanup

- `ResourceBindingRegistry` owns page/window binding lifetimes; it is not a
  cache or an in-flight request map.
- Profile background bytes no longer have a second page-local memory cache.
  The manager cache is checked first, followed by the validated legacy disk
  materialization for compatibility.
- `_artwork_cache` and `_avatar_pixmaps` are bounded UI presentation caches
  of already-decoded bytes/pixmaps. They are intentionally distinct from
  remote data caching and never decide freshness or retry behavior.
- `_artwork_inflight`, `_avatar_inflight`, and `_profile_background_inflight`
  are short-lived view binding guards. RequestManager remains the source of
  truth for active request deduplication and cancellation.

## Context and invalidation rules

- Repeated metadata edits for one game share the active cloud reconciliation
  handle. A second edit schedules one follow-up after the active request
  settles.
- Cloud context invalidation cancels and clears pending reconciliation and
  cloud-status batches. Late results are rejected by request generation and
  binding context checks.
- Automatic artwork attempts are marked by the artwork coordinator, so rows
  sharing an AppID do not create repeated attempts while a library refresh is
  rebuilding widgets.
- The legacy metadata snapshot contains only local presentation data. Cloud
  save status is never loaded or written by that adapter; `CloudStatusService`
  owns its dedicated context-scoped cache.
- No cache key, projection snapshot, or pending state contains credentials,
  tokens, save contents, or raw private cloud payloads.

## Remaining cleanup

Compatibility worker references are implemented by `CompatibilityWorkerIndex`;
they remain indexes only, while `WorkerSupervisor` owns QThread lifetimes.
Decoded artwork and avatar values use bounded `PresentationCache` instances;
they do not own freshness, retries, request deduplication, or remote state.

The remaining MainWindow dictionaries are presentation adapters, not obsolete
remote caches. They can be removed after `GamePropertiesDialog` and other
older consumers read `LibraryMetadataState` directly. The compatibility worker
lists remain until manager-less callers are retired; specialized asset caches
are bounded materialization layers with no request or freshness authority.
