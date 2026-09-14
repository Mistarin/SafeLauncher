# UI Reference: Main Window

[`ui/main_window.py`](../../../ui/main_window.py) is the application composition root and long-lived UI owner. It creates the database-facing library, shared request/cache services, Steam/artwork/cloud/profile clients, view hosts, and feature lifecycle state. It also contains many compatibility fallbacks because dialogs can be constructed independently.

Local library reads, immutable snapshot derivation, collection projections, and archive/restore transitions are delegated to [`core/library_service.py`](../../../core/library_service.py). MainWindow remains the visual composition root and consumes the resulting `LibraryProjection` to render views.

Library artwork request grouping is delegated to the Qt-free
[`core/library_artwork_coordinator.py`](../../../core/library_artwork_coordinator.py).
It owns per-kind resource groups, attempted-game deduplication, and opaque
binding lifetime bookkeeping. MainWindow still creates `ResourceBinding`
objects, receives queued results, applies fallback artwork, and updates
widgets. `ArtworkResourceService` remains the transport/cache boundary.

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
MainWindow remains responsible for Qt signal delivery, dialogs, watcher and
recorder presentation, and policy decisions that require the visible UI.

Before editing it, identify whether the path is local-library rendering, managed resource loading, a compatibility worker fallback, or shutdown. Update the appropriate architecture and map page when a boundary changes.

Shutdown ownership is centralized in `WorkerSupervisor`. Compatibility lists
such as `metadata_fetchers` and `auto_fetchers` describe fallback work but do
not own QThread lifetimes; do not reintroduce `_background_workers`-style
parallel registries.
