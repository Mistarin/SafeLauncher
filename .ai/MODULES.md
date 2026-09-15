# SafeLauncher Module Locator

Status values: `active` is a normal production path; `compatibility` is retained for manager-less or older callers; `legacy` is historical/fallback; `uncertain` needs verification.

## Core managers and state

| Concept | Source | Main API/symbols | Status |
|---|---|---|---|
| Request scheduling | [`core/request_manager.py`](../core/request_manager.py) | `RequestManager`, `RequestHandle` | active |
| Request contracts | [`core/request_contracts.py`](../core/request_contracts.py) | `RequestKey`, `RequestSpec`, `ResourceResult`, `RetryPolicy` | active |
| Resource cache | [`core/resource_cache.py`](../core/resource_cache.py) | `ResourceCache`, `CacheEntry` | active |
| Qt resource bridge | [`ui/resource_binding.py`](../ui/resource_binding.py) | `ResourceBinding`, `ResourceBindingRegistry`, `bind_resource`, `bind_request` | active |
| Remote error/state contract | [`core/request_contracts.py`](../core/request_contracts.py) | `RemoteErrorCategory`, `classify_remote_error`, `resource_status_for_error` | active |
| Qt worker shutdown | [`core/safe_thread.py`](../core/safe_thread.py) | `WorkerSupervisor` | active; sole MainWindow QThread registry |
| Worker/executor audit | [`.ai/phase-7-worker-audit.md`](phase-7-worker-audit.md) | managed scheduler, compatibility workers, local/process workers | verified; compatibility allowlist |
| Compatibility worker indexes | [`core/compatibility_worker_index.py`](../core/compatibility_worker_index.py) | `CompatibilityWorkerIndex` | active; references only, no scheduling/lifecycle ownership |
| UI presentation cache | [`core/presentation_cache.py`](../core/presentation_cache.py) | `PresentationCache` | active; bounded decoded-value LRU only |
| Library query/state | [`core/library_controller.py`](../core/library_controller.py), [`core/library_state.py`](../core/library_state.py) | `LibraryController`, `LibraryStateStore` | active |
| Local database | [`database.py`](../database.py) | `GameDatabase`, `GameRecord` | active |
| Portable game naming | [`core/game_names.py`](../core/game_names.py) | `meaningful_game_name`, `preferred_game_name`, `local_profile_identity`, `display_name_key`, placeholder/fallback rules | active |
| Library application service | [`core/library_service.py`](../core/library_service.py) | `LibraryService`, `LibraryProjection`, archive/remove/purge actions | active |
| Library metadata presentation state | [`core/library_metadata_state.py`](../core/library_metadata_state.py) | `LibraryMetadataState` | active; local projection and legacy migration adapter |
| Cloud sync queue | [`core/cloud_sync_queue.py`](../core/cloud_sync_queue.py) | `PendingCloudSyncQueue` | active |
| Cloud context/status service | [`core/cloud_context.py`](../core/cloud_context.py), [`core/cloud_status_service.py`](../core/cloud_status_service.py) | `CloudContext`, `CloudStatusService`, `CloudStatusTarget`, `CloudStatusPlan` | active |
| Cloud status polling lifecycle | [`core/cloud_status_polling_service.py`](../core/cloud_status_polling_service.py) | `CloudStatusPollingService` | active; owns periodic listing diff lifecycle and target snapshots |
| Private cloud center facade | [`core/cloud_center_service.py`](../core/cloud_center_service.py) | `CloudCenterService`, `CloudOverview`, `CloudConnectionState`, `CloudSyncSummary`, `CloudQuotaSummary`, `CloudConflictSummary` | active; single overview/sync/history/probe and operation-delegation entry point composed from existing cloud services |
| Cloud operation service | [`core/cloud_operation_service.py`](../core/cloud_operation_service.py) | `CloudOperationService`, `CloudOperationTarget` | active |
| Cloud operation lifecycle records | [`core/cloud_operation_records.py`](../core/cloud_operation_records.py) | `CloudOperationRecord`, `CloudOperationState` | active; metadata-only, no save payloads |
| Cloud exit synchronization | [`core/cloud_exit_sync_service.py`](../core/cloud_exit_sync_service.py) | `CloudExitSyncService`, `CloudExitSyncResult`, `CloudExitPresentation` | active; exit request/result/presentation boundary |
| Cloud metadata service | [`core/cloud_metadata_service.py`](../core/cloud_metadata_service.py) | `CloudMetadataService`, `CloudMetadataTarget`, `CloudMetadataResult`, `request_latest_game` | active; managed reconciliation/coalescing |
| Achievement resource service | [`core/achievement_resource_service.py`](../core/achievement_resource_service.py) | `AchievementResourceService`, `AchievementTarget` | active |
| Achievement persistence service | [`core/achievement_persistence_service.py`](../core/achievement_persistence_service.py) | `AchievementPersistenceService`, `AchievementProjection` | active; SQLite-backed local authority |
| Library achievement coordination | [`core/library_achievement_coordinator.py`](../core/library_achievement_coordinator.py) | `LibraryAchievementCoordinator`, `AchievementRequestPlan` | active; Qt-free MainWindow orchestration boundary |
| Steam resource service | [`core/steam_resource_service.py`](../core/steam_resource_service.py) | `SteamResourceService`, cached `request_app_details` | active |
| Library Steam metadata coordination | [`core/library_steam_metadata_coordinator.py`](../core/library_steam_metadata_coordinator.py) | `LibrarySteamMetadataCoordinator`, build/tag plans | active; Qt-free MainWindow orchestration boundary |
| Achievement presentation state | [`core/achievement_state_store.py`](../core/achievement_state_store.py) | `AchievementStateStore` | active; local UI projection only |
| Launch/session coordination | [`core/launch_session_coordinator.py`](../core/launch_session_coordinator.py) | `LaunchSessionCoordinator`, `LaunchSessionContext` | active; process/session registration boundary |
| Launch entry policy | [`core/launch_policy.py`](../core/launch_policy.py) | `LaunchPolicy`, `LaunchDecision`, `LaunchAction` | active; Qt-free archived/running/mode decision |
| Artwork resource service | [`core/artwork_resource_service.py`](../core/artwork_resource_service.py) | `ArtworkResourceService`, `ArtworkTarget` | active |
| Library artwork coordination | [`core/library_artwork_coordinator.py`](../core/library_artwork_coordinator.py) | `LibraryArtworkCoordinator`, `ArtworkRequestPlan` | active; Qt-free MainWindow orchestration boundary |
| Performance measurement | [`core/performance_metrics.py`](../core/performance_metrics.py) | `PerformanceMetrics` | active |
| Runtime diagnostics export | [`core/runtime_diagnostics.py`](../core/runtime_diagnostics.py) | `build_runtime_diagnostics`, `export_runtime_diagnostics` | active; metadata-only and redacted by allowlist |
| Performance release gates | [`core/performance_gates.py`](../core/performance_gates.py) | `PerformanceGateThresholds`, `evaluate_performance_gates` | active; thresholds supplied by measured CI baselines |
| Offline performance baseline | [`ci/performance_baseline.py`](../ci/performance_baseline.py) | `collect_baseline` | active; synthetic records, no network/database access |

## Transport and account boundaries

| Concept | Source | Main API/symbols | Status |
|---|---|---|---|
| Private cloud transport | [`core/cloud_client.py`](../core/cloud_client.py), [`core/cloud_backend.py`](../core/cloud_backend.py) | `CloudClient`, `ConvexSaveBackend` | active / compatibility |
| Private backend checkout/deployment | [`core/cloud_detector.py`](../core/cloud_detector.py), [`core/cloud_cli_wizard.py`](../core/cloud_cli_wizard.py) | discovers/downloads/deploys external `SafeLauncherCloud`; default local path is `../SafeLauncherDatabase` | external operational dependency |
| Private library reconciliation | [`core/cloud_metadata_sync.py`](../core/cloud_metadata_sync.py) | `CloudMetadataSync` | active |
| Cloud save operations | [`core/cloud_save_sync.py`](../core/cloud_save_sync.py), [`core/cloud_operations.py`](../core/cloud_operations.py) | `CloudSaveSyncEngine`, coordinators | active |
| Central profile authentication | [`core/central_auth.py`](../core/central_auth.py) | `CentralAuthSession` | active |
| Public profile client | [`core/profile_service.py`](../core/profile_service.py) | `ProfileServiceClient` | active |
| Public profile resource boundary | [`core/profile_resource_service.py`](../core/profile_resource_service.py) | `ProfileResourceService` | active |
| Public profile backend | [`services/profile_cloud/convex/http.ts`](../services/profile_cloud/convex/http.ts) | HTTP dispatcher and validation | active |
| Profile gateway | [`services/profile_gateway/api/gateway.ts`](../services/profile_gateway/api/gateway.ts) | gateway proxy | active |

## Game, achievement, and artwork systems

| Concept | Source | Main API/symbols | Status |
|---|---|---|---|
| Steam metadata | [`core/steam_client.py`](../core/steam_client.py), [`core/steam_resource_service.py`](../core/steam_resource_service.py), [`core/steam_build_tracker.py`](../core/steam_build_tracker.py), [`core/steam_tags.py`](../core/steam_tags.py) | `SteamClient`, `SteamResourceService`, fetchers | active / compatibility |
| Artwork | [`core/artwork_client.py`](../core/artwork_client.py), [`core/artwork_resource_service.py`](../core/artwork_resource_service.py), [`core/steamgriddb_client.py`](../core/steamgriddb_client.py) | `ArtworkClient`, `ArtworkResourceService`, `SteamGridDBClient`; archived records use neutral icons and skip artwork work | active / compatibility |
| Achievement resolution | [`core/achievement_coordinator.py`](../core/achievement_coordinator.py), [`core/achievement_providers.py`](../core/achievement_providers.py) | provider registry and resolution | active |
| Achievement persistence | [`core/achievement_persistence.py`](../core/achievement_persistence.py), [`database.py`](../database.py) | local schema/unlock persistence | active |
| Achievement file watcher | [`core/achievement_watcher.py`](../core/achievement_watcher.py) | `AchievementWatcher` | active |
| Game launch/session | [`core/game_session.py`](../core/game_session.py), [`core/playtime_tracker.py`](../core/playtime_tracker.py) | session and playtime lifecycle | active |
| Sandbox execution | [`core/firejail_runner.py`](../core/firejail_runner.py), [`core/host_process.py`](../core/host_process.py) | runner/process ownership | active |
| Archive installation | [`core/archive_installer.py`](../core/archive_installer.py), [`core/archive_extractor.py`](../core/archive_extractor.py) | inspection/extraction/install | active |
| Save validation/crypto | [`core/save_validation.py`](../core/save_validation.py), [`core/save_crypto.py`](../core/save_crypto.py) | archive safety and encryption | active |

## UI entrypoints

- Main shell: [`ui/main_window.py`](../ui/main_window.py)
- Shared sort control: [`ui/components/sort_combo.py`](../ui/components/sort_combo.py) (`SortComboBox` uses a platform-independent chevron and native popup)
- Library views: [`ui/library_list.py`](../ui/library_list.py), [`ui/components/library_view_host.py`](../ui/components/library_view_host.py), [`ui/components/compact_game_page.py`](../ui/components/compact_game_page.py), [`ui/components/virtual_grid.py`](../ui/components/virtual_grid.py)
- Profile: [`ui/components/profile_page.py`](../ui/components/profile_page.py)
- Account/settings: [`ui/dialogs/account_dialog.py`](../ui/dialogs/account_dialog.py), [`ui/dialogs/settings_dialog.py`](../ui/dialogs/settings_dialog.py)
- Unified private cloud management: [`ui/dialogs/cloud_center_dialog.py`](../ui/dialogs/cloud_center_dialog.py) (simple overview by default; setup, connection settings, and detailed history are advanced paths)
- Achievements: [`ui/dialogs/achievements_dialog.py`](../ui/dialogs/achievements_dialog.py), [`ui/dialogs/achievement_profile_dialog.py`](../ui/dialogs/achievement_profile_dialog.py)
- Save/cloud UI: [`ui/dialogs/save_manager_dialog.py`](../ui/dialogs/save_manager_dialog.py), [`ui/dialogs/save_conflict_dialog.py`](../ui/dialogs/save_conflict_dialog.py)
- Compatibility worker definitions: [`ui/threads.py`](../ui/threads.py), [`core/safe_thread.py`](../core/safe_thread.py)

## Verification

The generated files under [`generated/`](generated/) provide complete file, symbol, import, connection, service, schema, and test indexes. Run `.ai/tools/build_manifest.py` after structural changes.
