# Phase 6: MainWindow application-service extraction

Status: library projection, artwork, achievement, Steam metadata, launch/session, and achievement-state slices implemented.

## Implemented

- Added [`core/library_service.py`](../core/library_service.py).
- `LibraryService` owns SQLite library reads, `LibraryStateStore` snapshot derivation, collection-count projection, collection mutations, and archive/restore transitions.
- `LibraryService` now owns selection replacement/toggling/pruning and status-map reconciliation before rendering.
- MainWindow now uses the service for refresh reads, collection operations, archive/restore actions, update scans, and sandbox discovery reads.
- MainWindow retains only compatibility selection adapters for manager-less/lightweight hosts.
- MainWindow remains responsible for widget destruction/rendering, navigation, dialogs, and compatibility fallback behavior.
- The service preserves SQLite as the authoritative local projection and does not add a second cache.
- Added [`core/library_artwork_coordinator.py`](../core/library_artwork_coordinator.py), a Qt-free boundary for library artwork target preparation, per-kind attempt deduplication, shared resource grouping, and opaque UI binding ownership.
- MainWindow now keeps `ResourceBinding` delivery and widget updates at the UI edge while the coordinator owns hero/icon/automatic-artwork request groups and game-ID fan-out.
- Artwork transport, caching, priorities, and request-manager lifecycle remain owned by `ArtworkResourceService`; the coordinator does not duplicate them.
- Added [`core/library_achievement_coordinator.py`](../core/library_achievement_coordinator.py), a Qt-free boundary for achievement request keys, callback identity, batch lifecycle, and opaque UI binding ownership.
- MainWindow continues to own achievement cache projection, local persistence callbacks, watcher lifecycle, and compatibility workers; managed achievement request registration and binding grouping now live in the coordinator.
- Added [`core/library_steam_metadata_coordinator.py`](../core/library_steam_metadata_coordinator.py), a Qt-free boundary for grouped Steam build/tag targets and opaque UI binding ownership.
- MainWindow continues to own update-status projection and detail-panel rendering; `SteamResourceService` still owns transport/cache requests and compatibility fetchers remain available for manager-less hosts.
- Added [`core/launch_session_coordinator.py`](../core/launch_session_coordinator.py), which owns process launch registration, playtime-session creation through the injected local session store, tracker creation/attachment, active tracker lookup, and terminal session finalization.
- Added [`core/cloud_exit_sync_service.py`](../core/cloud_exit_sync_service.py), which owns exit-sync request construction, neutral result normalization, and typed outcome-to-presentation instructions; MainWindow retains cloud preflight dialogs, watcher setup, recorder/Discord presentation, tracker signal wiring, and the final Qt toast/detail application.
- Added [`core/launch_policy.py`](../core/launch_policy.py), which owns the pure archived/running/configured-mode launch-entry decision. MainWindow now only performs local filesystem checks, opens mode/dependency dialogs, and dispatches the chosen action.
- Added [`core/achievement_state_store.py`](../core/achievement_state_store.py), which owns the transient achievement status/resolution/freshness/pending-unlock projection; SQLite remains authoritative for durable achievement rows.
- Extended [`core/library_service.py`](../core/library_service.py) to own launch-adjacent local mutations: game upsert/edit, runtime settings, build references, artwork/AppID identity, tags, playtime completion/checkpoints, and library-row removal. MainWindow now uses these methods instead of direct database mutation calls for those paths.
- Added [`core/achievement_persistence_service.py`](../core/achievement_persistence_service.py), which owns achievement unlock claiming, watcher snapshots, schemas, resolver snapshots, and durable projection reads. MainWindow retains notification, profile-sync, and inspector presentation only.
- LibraryService now also owns favorite toggles, launch environment reads, and playtime-session creation, so MainWindow and LaunchSessionCoordinator do not perform direct local database mutations or launch-setting reads.
- Compatibility lifecycle tests still work when a lightweight host has no `library_service` attribute.

## Remaining

- Preserve the service-owned selection and status preparation while migrating remaining callers away from compatibility aliases.
- Keep cloud-exit toast/detail rendering and security-sensitive prelaunch dialogs at the UI edge; the result/policy shaping is now service-owned.
- Archived/restore fallbacks remain only for lightweight compatibility hosts and still delegate through `LibraryService`; production MainWindow has no direct archive/restore database writes.

## Verification

- `tests/test_library_service.py` covers snapshot projection, collection counts, and archive/restore identity preservation.
- `tests/test_library_artwork_coordinator.py` covers shared identity grouping, attempted-game deduplication, kind isolation, and binding cleanup.
- `tests/test_library_achievement_coordinator.py` covers stable callback identity, binding deduplication, batch state, and cleanup.
- `tests/test_library_steam_metadata_coordinator.py` covers AppID/name grouping, local-build fan-out, and binding cleanup.
- `tests/test_launch_session_coordinator.py` covers process/session/tracker registration, duplicate launch suppression, and terminal cleanup.
- `tests/test_achievement_state_store.py` covers freshness, pending unlocks, and lifecycle cleanup.
- `tests/test_achievement_persistence_service.py` covers schema/unlock persistence, one-shot notification claiming, watcher snapshots, and resolver projection persistence.
- Existing profile/lifecycle coverage remains green.
