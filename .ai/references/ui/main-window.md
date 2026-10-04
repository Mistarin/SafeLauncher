# UI Reference: Main Window

[`ui/main_window.py`](../../../ui/main_window.py) remains the long-lived view composition owner. Its shared services are injected from [`ui/application_runtime.py`](../../../ui/application_runtime.py). Feature controllers own migrated request/task/profile/session/restore lifecycles. Card construction, inspector construction and layout transitions have dedicated owners. The shell retains query/selection presentation, top-level chrome, rendering callbacks, dialog actions and compatibility adapters.

Local library reads, immutable snapshot derivation, collection projections, and archive/restore transitions are delegated to [`core/library_service.py`](../../../core/library_service.py). MainWindow remains the visual composition root and consumes the resulting `LibraryProjection` to render views.

Library artwork request grouping is delegated to the Qt-free
[`core/library_artwork_coordinator.py`](../../../core/library_artwork_coordinator.py),
with Qt delivery and compatibility-fetch lifecycle owned by
[`ui/artwork_controller.py`](../../../ui/artwork_controller.py). MainWindow
applies completed results to the local projection and widgets.
`ArtworkResourceService` remains the transport/cache boundary.

[`ui/app_update_controller.py`](../../../ui/app_update_controller.py) owns
release-check/download workers and the AppImage banner interaction. MainWindow
retains the combined startup notice because it joins app and backend health.

The pure archived/running/configured-mode launch decision is delegated to
[`core/launch_policy.py`](../../../core/launch_policy.py). Process/session registration is delegated to
[`core/launch_session_coordinator.py`](../../../core/launch_session_coordinator.py),
transient achievement cache state is delegated to
[`core/achievement_state_store.py`](../../../core/achievement_state_store.py).
Library Steam/update presentation maps and the legacy metadata migration
snapshot are delegated to [`core/library_metadata_state.py`](../../../core/library_metadata_state.py),
while repeated private metadata reconciliation is coalesced by
[`core/cloud_metadata_service.py`](../../../core/cloud_metadata_service.py).
Automatic cloud-save exit work is requested and normalized by
[`core/cloud_exit_sync_service.py`](../../../core/cloud_exit_sync_service.py),
including its typed, presentation-neutral terminal instructions.
`SessionFeatureController` owns exit delivery, recorder/RPC hooks, and playtime service calls. `AchievementSyncController` owns watcher creation/teardown and delayed emulator flush reads. `SteamMetadataController`, `CloudWorkflowController`, and `ProfileController` own their subscriptions and workflow state. `ManualRestoreController` owns manual restore state; MainWindow supplies confirmation/progress presentation. Settings receives explicit shared dependencies and owns its form/drain lifecycle separately.

Before editing it, identify whether the path is local-library rendering, managed resource loading, a compatibility worker fallback, or shutdown. Update the appropriate architecture and map page when a boundary changes.

Shutdown state/re-entry belongs to `ShutdownController`; QThread ownership is centralized in the runtime's `WorkerSupervisor`. Compatibility lists
such as `metadata_fetchers` and the artwork controller's bounded fallback
queue describe fallback work but do not own QThread lifetimes; do not
reintroduce `_background_workers`-style parallel registries.

Connectivity monitoring is also a MainWindow policy boundary: a failed probe
opens the retry/offline prompt and activates a transient request gate without
making the footer claim that persistent Offline Mode is enabled. A successful
probe clears the gate and forces the affected library/cloud/update refreshes.
