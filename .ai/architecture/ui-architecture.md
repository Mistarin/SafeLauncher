# UI Architecture

`ApplicationRuntime` is the shared service composition root. MainWindow composes widgets and feature controllers from that graph, exposing references for compatibility rather than constructing duplicate services. Library views render snapshots produced by the library controller/state layer. Components and dialogs receive focused data and callbacks.

Remote resources are loaded through stable keys. `ResourceBinding` is the safe Qt bridge: it subscribes to the Qt-free manager, receives results on a queued signal, and closes/unsubscribes when the owner disappears. `bind_request()` additionally filters by request ID and generation for short-lived dialog operations. Direct manager callbacks must not mutate widgets because they execute outside the UI thread.

`LibraryService` owns local library projection reads and query/snapshot derivation; `MainWindow` consumes its `LibraryProjection` for rendering and remains responsible for top-level widget composition.

Library artwork is split across three layers. `ArtworkResourceService` owns
transport-backed resource specs and caching. The Qt-free
`LibraryArtworkCoordinator` owns target preparation, deduplication, and
resource-key grouping. `ArtworkController` owns `ResourceBinding` delivery,
shared-result fan-out, and the compatibility-fetch queue. `MainWindow` applies
resolved artwork to the local library projection and active widgets.

`AppUpdateController` owns the application release-check and AppImage
download workers and the update-banner action state. MainWindow retains the
startup notice that combines application-release and backend-health results.

Compatibility dialogs may still use `SafeQThread` workers when no manager is supplied. New production paths should use the shared manager and keep widget lifetime/account context in the binding owner.

`SteamMetadataController` owns Steam build/tag/name subscriptions and selected-game descriptions. `CloudWorkflowController` owns automatic upload/restore retry and completion state. `ProfileController` owns public/social request generations and separate private reconciliation; production no longer inherits the legacy profile mixin. `SessionFeatureController` isolates recorder/RPC failures from achievement and exit-cloud hooks. `AchievementSyncController` owns local watcher and delayed-exit read lifetimes, including offline observation.

Save Manager and Game Properties receive `SaveDialogServices` bundles. Parent discovery is confined to the legacy adapter. Save Manager uses `ManagedTaskController` in the production path and refuses destruction until cancelled loaders finish. `ci/architecture_audit.py` guards migrated service-construction, entrypoint, and controller boundaries.

`LibraryCardRenderer` builds/destroys normal cards and virtual proxies from the
same immutable snapshot. `LibraryNavigationController` is the transition
authority for compact/grid/detail/profile layouts; it restores scroll policy,
header visibility, margins and sidebar/footer visibility together.
`GameInspectorWidget` owns inspector construction with injected actions.
MainWindow retains top-level composition, query/selection presentation and
rendering callbacks, not these component lifecycles.

Settings receives `SettingsDialogServices`, while five independent form widgets
receive explicit page inputs and callbacks. Settings uses managed tasks in
production, directly subscribes to account snapshots, and suppresses late
results during a deferred Save/Cancel/window-close drain. `CloudSettingsSession`
owns scoped cloud rollback and accepted nested-action baselines. Media, storage
and plugin-install dialogs live in separate modules; existing imports through
`settings_dialog` remain supported.

Sources: [`ui/main_window.py`](../../ui/main_window.py), [`ui/resource_binding.py`](../../ui/resource_binding.py), [`core/library_controller.py`](../../core/library_controller.py), [`ui/threads.py`](../../ui/threads.py).
