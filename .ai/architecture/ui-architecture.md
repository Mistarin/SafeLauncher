# UI Architecture

`MainWindow` is the composition root and long-lived owner of the shared request manager, cache, database, transports, and most feature lifecycles. Library views render snapshots produced by the library controller/state layer. Components and dialogs receive focused data and callbacks.

Remote resources are loaded through stable keys. `ResourceBinding` is the safe Qt bridge: it subscribes to the Qt-free manager, receives results on a queued signal, and closes/unsubscribes when the owner disappears. `bind_request()` additionally filters by request ID and generation for short-lived dialog operations. Direct manager callbacks must not mutate widgets because they execute outside the UI thread.

`LibraryService` owns local library projection reads and query/snapshot derivation; `MainWindow` consumes its `LibraryProjection` for rendering and remains responsible for top-level widget composition.

Library artwork has a similar split. `LibraryArtworkCoordinator` is Qt-free
and owns target preparation, deduplication, resource-key grouping, and opaque
binding bookkeeping. `MainWindow` remains the UI edge that creates
`ResourceBinding` instances and applies artwork to widgets. The coordinator
does not own transport, caching, or Qt delivery.

Compatibility dialogs may still use `SafeQThread` workers when no manager is supplied. New production paths should use the shared manager and keep widget lifetime/account context in the binding owner.

Sources: [`ui/main_window.py`](../../ui/main_window.py), [`ui/resource_binding.py`](../../ui/resource_binding.py), [`core/library_controller.py`](../../core/library_controller.py), [`ui/threads.py`](../../ui/threads.py).
