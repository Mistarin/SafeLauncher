# SafeLauncher Module Locator

Status values: `active` is a normal production path; `compatibility` is retained for manager-less or older callers; `legacy` is historical/fallback; `uncertain` needs verification.

## Core managers and state

| Concept | Source | Main API/symbols | Status |
|---|---|---|---|
| Request scheduling | [`core/request_manager.py`](../core/request_manager.py) | `RequestManager`, `RequestHandle` | active |
| Request contracts | [`core/request_contracts.py`](../core/request_contracts.py) | `RequestKey`, `RequestSpec`, `ResourceResult`, `RetryPolicy` | active |
| Resource cache | [`core/resource_cache.py`](../core/resource_cache.py) | `ResourceCache`, `CacheEntry` | active |
| Qt resource bridge | [`ui/resource_binding.py`](../ui/resource_binding.py) | `ResourceBinding`, `bind_resource` | active |
| Library query/state | [`core/library_controller.py`](../core/library_controller.py), [`core/library_state.py`](../core/library_state.py) | `LibraryController`, `LibraryStateStore` | active |
| Local database | [`database.py`](../database.py) | `GameDatabase`, `GameRecord` | active |
| Cloud sync queue | [`core/cloud_sync_queue.py`](../core/cloud_sync_queue.py) | `PendingCloudSyncQueue` | active |
| Performance measurement | [`core/performance_metrics.py`](../core/performance_metrics.py) | `PerformanceMetrics` | active |

## Transport and account boundaries

| Concept | Source | Main API/symbols | Status |
|---|---|---|---|
| Private cloud transport | [`core/cloud_client.py`](../core/cloud_client.py), [`core/cloud_backend.py`](../core/cloud_backend.py) | `CloudClient`, `ConvexSaveBackend` | active / compatibility |
| Private library reconciliation | [`core/cloud_metadata_sync.py`](../core/cloud_metadata_sync.py) | `CloudMetadataSync` | active |
| Cloud save operations | [`core/cloud_save_sync.py`](../core/cloud_save_sync.py), [`core/cloud_operations.py`](../core/cloud_operations.py) | `CloudSaveSyncEngine`, coordinators | active |
| Central profile authentication | [`core/central_auth.py`](../core/central_auth.py) | `CentralAuthSession` | active |
| Public profile client | [`core/profile_service.py`](../core/profile_service.py) | `ProfileServiceClient` | active |
| Public profile backend | [`services/profile_cloud/convex/http.ts`](../services/profile_cloud/convex/http.ts) | HTTP dispatcher and validation | active |
| Profile gateway | [`services/profile_gateway/api/gateway.ts`](../services/profile_gateway/api/gateway.ts) | gateway proxy | active |

## Game, achievement, and artwork systems

| Concept | Source | Main API/symbols | Status |
|---|---|---|---|
| Steam metadata | [`core/steam_client.py`](../core/steam_client.py), [`core/steam_build_tracker.py`](../core/steam_build_tracker.py), [`core/steam_tags.py`](../core/steam_tags.py) | `SteamClient`, fetchers | active / compatibility |
| Artwork | [`core/artwork_client.py`](../core/artwork_client.py), [`core/steamgriddb_client.py`](../core/steamgriddb_client.py) | `ArtworkClient`, `SteamGridDBClient` | active / compatibility |
| Achievement resolution | [`core/achievement_coordinator.py`](../core/achievement_coordinator.py), [`core/achievement_providers.py`](../core/achievement_providers.py) | provider registry and resolution | active |
| Achievement persistence | [`core/achievement_persistence.py`](../core/achievement_persistence.py), [`database.py`](../database.py) | local schema/unlock persistence | active |
| Achievement file watcher | [`core/achievement_watcher.py`](../core/achievement_watcher.py) | `AchievementWatcher` | active |
| Game launch/session | [`core/game_session.py`](../core/game_session.py), [`core/playtime_tracker.py`](../core/playtime_tracker.py) | session and playtime lifecycle | active |
| Sandbox execution | [`core/firejail_runner.py`](../core/firejail_runner.py), [`core/host_process.py`](../core/host_process.py) | runner/process ownership | active |
| Archive installation | [`core/archive_installer.py`](../core/archive_installer.py), [`core/archive_extractor.py`](../core/archive_extractor.py) | inspection/extraction/install | active |
| Save validation/crypto | [`core/save_validation.py`](../core/save_validation.py), [`core/save_crypto.py`](../core/save_crypto.py) | archive safety and encryption | active |

## UI entrypoints

- Main shell: [`ui/main_window.py`](../ui/main_window.py)
- Library views: [`ui/library_list.py`](../ui/library_list.py), [`ui/components/library_view_host.py`](../ui/components/library_view_host.py), [`ui/components/compact_game_page.py`](../ui/components/compact_game_page.py), [`ui/components/virtual_grid.py`](../ui/components/virtual_grid.py)
- Profile: [`ui/components/profile_page.py`](../ui/components/profile_page.py)
- Account/settings: [`ui/dialogs/account_dialog.py`](../ui/dialogs/account_dialog.py), [`ui/dialogs/settings_dialog.py`](../ui/dialogs/settings_dialog.py)
- Achievements: [`ui/dialogs/achievements_dialog.py`](../ui/dialogs/achievements_dialog.py), [`ui/dialogs/achievement_profile_dialog.py`](../ui/dialogs/achievement_profile_dialog.py)
- Save/cloud UI: [`ui/dialogs/save_manager_dialog.py`](../ui/dialogs/save_manager_dialog.py), [`ui/dialogs/save_conflict_dialog.py`](../ui/dialogs/save_conflict_dialog.py)
- Compatibility worker definitions: [`ui/threads.py`](../ui/threads.py), [`core/safe_thread.py`](../core/safe_thread.py)

## Verification

The generated files under [`generated/`](generated/) provide complete file, symbol, import, connection, service, schema, and test indexes. Run `.ai/tools/build_manifest.py` after structural changes.
